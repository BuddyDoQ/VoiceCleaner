"""The enhancement pipeline.

    input --> channel layout --> cleanup filters (DC, high-pass, hum)
          --> noise profile --> profile denoiser (steady noise)
          --> AI speech enhancement (DeepFilterNet3)
          --> de-reverb / echo reduction --> adaptive voice EQ      [restoration]
          --> pre-gain --> compressor --> expander --> pause clean-up
          --> loudness normalisation --> true-peak limiter         [mastering]
          --> quality control (may reprocess with gentler settings)

Every stage is its own class in its own module; this file only decides
which stages run, in what order, with which parameters, and moves buffers
between them. Long recordings are processed in overlapping chunks, and
buffers are disk-backed when the recording would not fit in memory.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np

from . import loudness, noise_profile as noise_profile_mod, quality
from .analyzer import FRAME_HOP_S, AnalysisResult, estimate_rt60, mask_to_regions
from .declip import DeclipReport, declip
from .dsp import analysis_fft_size, band_edges_bins, db, iter_power_frames
from .compressor import Compressor, CompressorParams, GateParams, NoiseGate
from .deesser import DeEsser, DeEsserParams
from .denoise import DenoiseParams, SpectralDenoiser, TransientSuppressor
from .dereverb import DereverbParams, Dereverberator
from .dsp import process_in_chunks
from .eq import CleanupFilter, CleanupParams, VoiceEQ, VoiceEQParams, choose_highpass
from .leveler import Leveler, LevelerParams
from .limiter import LimiterParams, TruePeakLimiter
from .loader import AudioData, allocate
from .noise_profile import NoiseProfile
from .pause_cleanup import PauseCleaner, PauseCleanupParams
from .settings import ProcessingSettings
from .speech_enhancer import SpeechEnhancer, SpeechEnhancerParams
from ..utils.errors import ModelUnavailableError
from ..utils.logging import get_logger

log = get_logger("pipeline")

MAX_QC_ATTEMPTS = 3

# share of the progress bar per step
WEIGHTS = {"cleanup": 0.03, "denoise": 0.10, "ai": 0.40, "dereverb": 0.08, "resynth": 0.25, "eq": 0.04,
           "dynamics": 0.08, "loudness": 0.04, "limiter": 0.06, "qc": 0.10}


@dataclass
class PipelineResult:
    samples: np.ndarray
    sample_rate: int
    settings: ProcessingSettings
    original_metrics: quality.SignalMetrics
    enhanced_metrics: quality.SignalMetrics
    qc: quality.QCReport
    attempts: int
    stages: list[str]
    notes: list[str]
    processing_seconds: float
    device_label: str
    model_name: str | None
    noise_profile: str
    eq_bands: list = field(default_factory=list)
    auto_eq_bands: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.samples.shape[0] / self.sample_rate

    @property
    def realtime_factor(self) -> float:
        return self.duration / max(self.processing_seconds, 1e-6)

    @property
    def clarity_improvement(self) -> float:
        return self.enhanced_metrics.clarity - self.original_metrics.clarity


class _Progress:
    """Maps per-step progress onto the overall bar, with a stage message."""

    def __init__(self, ctx, steps: list[str]):
        self.ctx = ctx
        total = sum(WEIGHTS[s] for s in steps) or 1.0
        self.offsets, acc = {}, 0.0
        for s in steps:
            self.offsets[s] = (acc / total, WEIGHTS[s] / total)
            acc += WEIGHTS[s]
        self.attempt_span = (0.0, 1.0)

    def __call__(self, step: str, frac: float, message: str):
        if self.ctx is None:
            return
        off, w = self.offsets.get(step, (0.0, 0.0))
        a0, a1 = self.attempt_span
        self.ctx.progress(a0 + (a1 - a0) * (off + w * frac), message)

    def check(self):
        if self.ctx is not None:
            self.ctx.check()


PREVIEW_CONTEXT_S = 2.0  # audio processed on each side of a preview window, then dropped
PREVIEW_XFADE_S = 0.03  # the preview is blended into the enhanced track over this length


@dataclass
class PreviewResult:
    start_frame: int  # where ``samples`` belong in the recording
    samples: np.ndarray  # includes PREVIEW_XFADE_S of margin at each end for blending
    sample_rate: int
    processing_seconds: float
    window: tuple[float, float]  # the requested start/end (seconds)
    settings: ProcessingSettings


_profile_cache: dict = {}


def preview_noise_profile(pipeline, audio: AudioData, work_full: np.ndarray, sr: int, analysis: AnalysisResult,
                          s: ProcessingSettings, learned: NoiseProfile | None) -> NoiseProfile | None:
    """The noise profile a full run would use (measured on the whole cleanup-filtered
    recording), cached: it only depends on the recording and the cleanup filter."""
    hp = s.highpass_hz or choose_highpass(s.lf_cleanup, analysis.lf_rumble_db, analysis.f0_low_hz)
    hum = tuple(analysis.hum.harmonics) if (s.hum_removal and analysis.hum.detected) else ()
    key = (id(audio.samples), audio.frames, round(hp, 1), hum, id(learned))
    if key not in _profile_cache:
        filtered = np.empty_like(work_full, dtype=np.float32)
        CleanupFilter(sr, CleanupParams(highpass_hz=hp, hum_freqs=list(hum))).process(work_full, out=filtered)
        _profile_cache.clear()  # one recording at a time
        _profile_cache[key] = pipeline._noise_profile(filtered, sr, analysis, learned)
    return _profile_cache[key]


def slice_analysis(analysis: AnalysisResult, a: int, b: int, sr: int) -> AnalysisResult:
    """The analysis of the whole recording, cut to frames ``a``..``b``: per-frame masks are
    sliced and time ranges shifted; whole-file measurements (noise, hum, reverb) stay."""
    f0, f1 = int(a / sr / FRAME_HOP_S), int(np.ceil(b / sr / FRAME_HOP_S))
    t0, t1 = a / sr, b / sr
    regions = [(max(x, t0) - t0, min(y, t1) - t0) for x, y in analysis.silence_regions if y > t0 and x < t1]
    return replace(analysis, duration=t1 - t0, speech_mask=analysis.speech_mask[f0:f1],
                   noise_mask=analysis.noise_mask[f0:f1], frame_level_db=analysis.frame_level_db[f0:f1],
                   silence_regions=[(x, y) for x, y in regions if y - x > 0.05])


# One enhancement at a time: the AI models are not shared between threads safely, and
# two jobs on one GPU would only slow each other down (Enhance, live preview, and the
# teleprompter's background enhancing of takes all go through here).
PIPELINE_LOCK = threading.RLock()


def _serialized(fn):
    def wrapper(*args, **kwargs):
        with PIPELINE_LOCK:
            return fn(*args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


class EnhancementPipeline:
    def __init__(self, model_manager=None, chunk_seconds: float = 20.0, overlap_seconds: float = 1.0):
        self.model_manager = model_manager
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds

    # ------------------------------------------------------------------------------
    @_serialized
    def run(self, audio: AudioData, analysis: AnalysisResult, settings: ProcessingSettings,
            ctx=None, learned_profile: NoiseProfile | None = None) -> PipelineResult:
        t0 = time.perf_counter()
        sr = audio.sample_rate
        disk = audio.is_disk_backed
        steps = ["cleanup", "denoise", "ai", "dereverb", "eq", "dynamics", "loudness", "limiter", "qc"]
        if settings.resynthesis > 0.01:
            steps.insert(4, "resynth")
        prog = _Progress(ctx, steps)
        notes: list[str] = []
        log.info("Pipeline start: %s, %.1f s, settings=%s", audio.info.path if audio.info else "<memory>",
                 audio.duration, settings.to_dict())

        work_in, layout_note = self._channel_input(audio, analysis)
        if layout_note:
            notes.append(layout_note)

        restored = None
        restored_key = None
        restore_info: dict = {}
        output = None
        qc_report = quality.QCReport()
        attempt = 0
        qc_notes: list[str] = []
        for attempt in range(1, MAX_QC_ATTEMPTS + 1):
            # later attempts are usually cheaper (cached restoration); give each a shrinking share
            prog.attempt_span = (0.0, 1.0) if attempt == 1 else (0.85, 0.97)
            key = quality.restoration_key(settings)
            if restored is None or key != restored_key:
                restored, restore_info = self._restore(work_in, sr, analysis, settings, learned_profile, prog, disk)
                restored_key = key
            # Loudness is measured on the working buffer; when a mono working
            # signal is written to both output channels, BS.1770 sums the two,
            # so the working target is 3 dB lower.
            channel_offset = 10 * np.log10(audio.channels / work_in.shape[1])
            if settings.normalize_loudness and analysis.speech_detected:
                target = settings.target_lufs - channel_offset
            else:
                # without speech, "normalising" would only turn the background back up
                target = (analysis.lufs if np.isfinite(analysis.lufs) else -23.0) - channel_offset
            mastered = self._master(restored, sr, settings, prog, disk, target, analysis)
            output = self._channel_output(mastered, audio.channels)
            if not settings.quality_control:
                break
            prog("qc", 0.1, "Checking quality...")
            qc_report, _ = quality.check(audio.samples, output, sr, analysis, settings)
            prog("qc", 1.0, "Checking quality...")
            log.info("QC attempt %d: issues=%s stats=%s", attempt, [i.code for i in qc_report.issues],
                     {k: v for k, v in qc_report.stats.items() if k != "enhanced_metrics"})
            if qc_report.ok or attempt == MAX_QC_ATTEMPTS:
                break
            settings, changes = quality.propose_rollback(qc_report, settings)
            if not changes:
                break
            qc_notes.extend(changes)
            prog.check()

        if qc_notes:
            notes.append("Quality check: " + "; ".join(dict.fromkeys(qc_notes)) + ".")
        enhanced_metrics = qc_report.stats.get("enhanced_metrics") or quality.measure(output, sr, analysis)
        original_metrics = quality.original_metrics(audio.samples, sr, analysis)
        elapsed = time.perf_counter() - t0
        if ctx is not None:
            ctx.progress(1.0, "Done")
        device_label = self.model_manager.device.label if (self.model_manager and restore_info.get("ai")) else "CPU (DSP)"
        if (self.model_manager and settings.resynthesis > 0.01
                and self.model_manager.resynth_device != self.model_manager.device):
            device_label += f" + {self.model_manager.resynth_device.label} (re-synthesis)"
        result = PipelineResult(
            samples=output, sample_rate=sr, settings=settings,
            original_metrics=original_metrics, enhanced_metrics=enhanced_metrics, qc=qc_report,
            attempts=attempt, stages=restore_info.get("stages", []), notes=notes + restore_info.get("notes", []),
            processing_seconds=elapsed, device_label=device_label,
            model_name=restore_info.get("model"), noise_profile=restore_info.get("profile", "none"),
            eq_bands=restore_info.get("eq_bands", []), auto_eq_bands=restore_info.get("auto_eq_bands", []),
        )
        log.info("Pipeline done in %.2f s (%.1fx realtime), attempts=%d, stages=%s, device=%s",
                 elapsed, result.realtime_factor, attempt, result.stages, device_label)
        return result

    # --- live preview --------------------------------------------------------------
    @_serialized
    def preview(self, audio: AudioData, analysis: AnalysisResult, settings: ProcessingSettings,
                start_s: float, end_s: float, ctx=None, learned_profile: NoiseProfile | None = None,
                context_s: float = PREVIEW_CONTEXT_S) -> "PreviewResult":
        """Process only ``start_s``..``end_s`` (plus ``context_s`` either side so filters,
        leveling and compression settle) to hear new settings within a second or two.

        The window is treated as part of the whole recording: the noise profile comes from
        the whole file and loudness is judged as for the whole file, so the window sounds
        and measures (nearly) like the same passage of a full Enhance. Quality control is
        file-wide and not run here."""
        t0 = time.perf_counter()
        sr, n = audio.sample_rate, audio.frames
        a = max(0, int((start_s - context_s) * sr))
        b = min(n, int((end_s + context_s) * sr))
        core_a = max(0, int(start_s * sr) - int(PREVIEW_XFADE_S * sr))
        core_b = min(n, int(end_s * sr) + int(PREVIEW_XFADE_S * sr))
        s = settings.copy(quality_control=False)
        steps = ["cleanup", "denoise", "ai", "dereverb", "eq", "dynamics", "loudness", "limiter"]
        if s.resynthesis > 0.01:
            steps.insert(4, "resynth")
        prog = _Progress(ctx, steps)

        work_full, _ = self._channel_input(audio, analysis)
        profile = preview_noise_profile(self, audio, work_full, sr, analysis, s, learned_profile)
        prog.check()
        piece = np.ascontiguousarray(work_full[a:b], dtype=np.float32)
        part = slice_analysis(analysis, a, b, sr)
        restored, info = self._restore(piece, sr, part, s, learned_profile, prog, False, fixed_profile=profile)

        channel_offset = 10 * np.log10(audio.channels / work_full.shape[1])
        if s.normalize_loudness and analysis.speech_detected:
            target = s.target_lufs - channel_offset
        else:
            target = (analysis.lufs if np.isfinite(analysis.lufs) else -23.0) - channel_offset
        full_lufs = analysis.lufs
        win_lufs = loudness.integrated_loudness(np.asarray(audio.samples[a:b], dtype=np.float32), sr)
        offset = float(win_lufs - full_lufs) if np.isfinite(win_lufs) and np.isfinite(full_lufs) else 0.0
        mastered = self._master(restored, sr, s, prog, False, target, part, loudness_offset_db=offset)
        out = self._channel_output(mastered, audio.channels)[core_a - a : core_b - a]
        if ctx is not None:
            ctx.progress(1.0, "Preview ready")
        elapsed = time.perf_counter() - t0
        log.info("Preview %.1f-%.1f s in %.2f s (window offset %+.1f LU), stages=%s",
                 start_s, end_s, elapsed, offset, info.get("stages"))
        return PreviewResult(core_a, np.ascontiguousarray(out, dtype=np.float32), sr, elapsed,
                             (start_s, end_s), settings)

    # --- channel layout ----------------------------------------------------------
    @staticmethod
    def _channel_input(audio: AudioData, analysis: AnalysisResult) -> tuple[np.ndarray, str]:
        x = audio.samples
        mode = analysis.stereo.mode
        if audio.channels == 1 or mode == "mono":
            return x, ""
        if mode == "left_only":
            return x[:, 0:1], "Speech was only on the left channel; it is now centred on both channels."
        if mode == "right_only":
            return x[:, 1:2], "Speech was only on the right channel; it is now centred on both channels."
        if mode == "dual_mono":
            mono = allocate(x.shape[0], 1, isinstance(x, np.memmap))
            for a in range(0, x.shape[0], 1 << 20):
                mono[a : a + (1 << 20), 0] = x[a : a + (1 << 20)].mean(axis=1)
            return mono, ""
        return x, ""

    @staticmethod
    def _channel_output(x: np.ndarray, channels: int) -> np.ndarray:
        if x.shape[1] == channels:
            return x
        out = allocate(x.shape[0], channels, isinstance(x, np.memmap))
        for a in range(0, x.shape[0], 1 << 20):
            out[a : a + (1 << 20)] = x[a : a + (1 << 20), :1]
        return out

    # --- restoration ---------------------------------------------------------------
    def _restore(self, x: np.ndarray, sr: int, analysis: AnalysisResult, s: ProcessingSettings,
                 learned: NoiseProfile | None, prog: _Progress, disk: bool,
                 fixed_profile: NoiseProfile | None = None) -> tuple[np.ndarray, dict]:
        n, ch = x.shape
        info: dict = {"stages": [], "notes": []}
        chunk = int(self.chunk_seconds * sr)
        overlap = int(self.overlap_seconds * sr)
        buf_a = allocate(n, ch, disk)
        buf_b = allocate(n, ch, disk)

        # 0. clipping repair: before any filter smears the flat tops
        if s.declip and analysis.is_clipped:
            rep = DeclipReport()
            declip(x, rep, out=buf_b, progress=lambda f: prog("cleanup", 0.5 * f, "Repairing clipping..."),
                   check=prog.check)
            if rep.clipped_samples:
                x = buf_b
                buf_b = allocate(n, ch, disk)
                info["stages"].append(f"clipping repair ({rep.clipped_samples / max(1, n * ch) * 100:.2f}% of samples)")

        # 1. cleanup filters
        prog("cleanup", 0.5 if "clipping" in " ".join(info["stages"]) else 0.0, "Removing rumble and hum...")
        hp = s.highpass_hz or choose_highpass(s.lf_cleanup, analysis.lf_rumble_db, analysis.f0_low_hz)
        hum = analysis.hum.harmonics if (s.hum_removal and analysis.hum.detected) else []
        CleanupFilter(sr, CleanupParams(highpass_hz=hp, hum_freqs=hum)).process(x, out=buf_a)
        info["stages"].append(f"high-pass {hp:.0f} Hz")
        if hum:
            info["stages"].append(f"hum notch {analysis.hum.fundamental:.0f} Hz")
        cur, spare = buf_a, buf_b
        prog("cleanup", 1.0, "Removing rumble and hum...")
        prog.check()

        # 2. noise profile
        prog("denoise", 0.0, "Estimating noise...")
        ai_possible = s.use_ai and self.model_manager is not None and self.model_manager.available \
            and s.noise_reduction > 0.01
        # a preview passes the whole recording's profile: its pauses may lie outside the window
        profile = fixed_profile or self._noise_profile(cur, sr, analysis, learned)
        info["profile"] = profile.describe() if profile else "none"

        # 3. profile denoiser
        dsp_atten = 0.0
        learned_used = profile is not None and profile.source == "learned"
        if s.noise_reduction > 0.01 and profile is not None and (profile.reliable or not ai_possible or learned_used):
            if ai_possible and learned_used:
                # the user showed exactly what the noise is: trust it, remove it more firmly,
                # and leave the AI its full range for everything else
                dsp_atten = min(18.0, s.max_attenuation_db * 0.7)
                strength = 1.0
            elif ai_possible:
                # the AI model does the heavy lifting; DSP takes the edge off steady noise first
                dsp_atten = min(12.0, s.max_attenuation_db * 0.5)
                strength = min(1.0, 0.4 + 0.6 * s.noise_reduction)
            else:
                dsp_atten = s.max_attenuation_db
                strength = s.noise_reduction if profile.reliable else s.noise_reduction * 0.7
            params = DenoiseParams(strength=strength, oversubtraction=s.spectral_subtraction,
                                   noise_offset_db=s.noise_floor_offset_db, max_attenuation_db=dsp_atten)
            den = SpectralDenoiser(profile.psd, sr, profile.cfg, params)
            process_in_chunks(cur, lambda c: self._checked(prog, den.process, c), chunk, overlap, out=spare,
                              progress=lambda f: prog("denoise", f, "Reducing background noise..."))
            cur, spare = spare, cur
            info["stages"].append("profile denoise (learned noise)" if learned_used else "profile denoise")
        prog("denoise", 1.0, "Reducing background noise...")

        # 4. AI enhancement
        if ai_possible:
            try:
                from ..ai.inference import ModelInference

                inference = ModelInference(self.model_manager, self.chunk_seconds, self.overlap_seconds)
                # a learned profile's reduction comes on top: the AI keeps its full range
                max_att = s.max_attenuation_db if learned_used else max(3.0, s.max_attenuation_db - dsp_atten)
                enh = SpeechEnhancer(inference, SpeechEnhancerParams(s.noise_reduction, max_att))
                prog("ai", 0.0, "Enhancing speech...")
                enh.process(cur, sr, spare, progress=lambda f: prog("ai", f, "Enhancing speech..."),
                            check_cancel=prog.check)
                cur, spare = spare, cur
                info["ai"] = True
                info["model"] = self.model_manager.model.display_name
                info["stages"].append(f"AI ({info['model']})")
            except ModelUnavailableError as exc:
                info["notes"].append(exc.user_message)
                log.warning("AI stage skipped: %s", exc.user_message)
                if "profile denoise" not in info["stages"] and profile is not None:
                    params = DenoiseParams(strength=s.noise_reduction * 0.8, oversubtraction=s.spectral_subtraction,
                                           noise_offset_db=s.noise_floor_offset_db,
                                           max_attenuation_db=s.max_attenuation_db)
                    den = SpectralDenoiser(profile.psd, sr, profile.cfg, params)
                    process_in_chunks(cur, den.process, chunk, overlap, out=spare)
                    cur, spare = spare, cur
                    info["stages"].append("profile denoise (fallback)")
        elif s.use_ai and s.noise_reduction > 0.01 and self.model_manager is not None:
            info["notes"].append("AI model unavailable: traditional noise reduction was used.")
        prog("ai", 1.0, "Enhancing speech...")
        prog.check()

        # 4b. impulsive noises in pauses (clicks, taps)
        if s.noise_reduction > 0.25 and analysis.speech_mask.size:
            ts = TransientSuppressor(sr, strength=min(1.0, s.noise_reduction * 1.2))
            ts.process(cur, analysis.speech_mask, FRAME_HOP_S, spare)
            if ts.events:
                cur, spare = spare, cur
                info["stages"].append(f"click suppression ({ts.events} events)")

        # 5. de-reverb / echo
        rt60 = analysis.rt60_s
        if rt60 is None and s.reverb_reduction > 0.01:
            # background noise hides reverb tails in the original; measure again now it is gone
            rt60 = self._estimate_rt60(cur, sr, analysis)
            if rt60 is not None:
                info["notes"].append(f"Room reverb measured after noise removal: about {rt60:.1f} s.")
        rt60 = rt60 or 0.4
        drp = DereverbParams(reverb_reduction=s.reverb_reduction, echo_reduction=s.echo_reduction, rt60=rt60,
                             echo_delay_s=analysis.echo_delay_s, echo_gain=analysis.echo_gain)
        if drp.active:
            der = Dereverberator(sr, drp)
            process_in_chunks(cur, lambda c: self._checked(prog, der.process, c), chunk, overlap, out=spare,
                              progress=lambda f: prog("dereverb", f, "Reducing reverb..."))
            cur, spare = spare, cur
            info["stages"].append(f"de-reverb (RT60 {rt60:.2f} s)")
        prog("dereverb", 1.0, "Reducing reverb...")

        # 6. neural voice re-synthesis
        if s.resynthesis > 0.01:
            from ..ai.resynthesis import Resynthesis

            if self.model_manager is None or not self.model_manager.resynth_available:
                info["notes"].append("Voice re-synthesis was skipped: its model is not downloaded yet. "
                                      "Download it (490 MB, one time) in the Voice Re-synthesis section.")
            else:
                try:
                    prog("resynth", 0.0, "Re-synthesizing voice...")
                    y = Resynthesis(self.model_manager).process(
                        cur, sr, s.resynthesis, progress=lambda f: prog("resynth", f, "Re-synthesizing voice..."),
                        check_cancel=prog.check)
                    for a in range(0, n, 1 << 20):
                        spare[a : a + (1 << 20)] = y[a : a + (1 << 20)]
                    cur, spare = spare, cur
                    info["stages"].append(f"re-synthesis {s.resynthesis:.0%} ({self.model_manager.resynth.display_name})")
                except ModelUnavailableError as exc:
                    info["notes"].append(exc.user_message)
            prog("resynth", 1.0, "Re-synthesizing voice...")
            prog.check()

        # 7. voice EQ: automatic tonal balance + graphic EQ + tilt
        prog("eq", 0.0, "Balancing tone...")
        eq = VoiceEQ(sr, VoiceEQParams(amount=s.tonal_balance, presence=s.voice_presence,
                                       hf_clarity=s.hf_clarity, lf_cleanup=s.lf_cleanup,
                                       bandwidth_hz=analysis.bandwidth_hz, tilt=s.eq_tilt,
                                       manual_gains=list(s.eq_gains)))
        eq.process(cur, analysis.speech_mask, FRAME_HOP_S, out=spare)
        cur, spare = spare, cur
        info["eq_bands"] = eq.bands
        info["auto_eq_bands"] = eq.auto_bands
        if eq.bands:
            info["stages"].append("voice EQ: " + ", ".join(f"{b.kind} {b.freq:.0f} Hz {b.gain_db:+.1f} dB"
                                                           for b in eq.bands))
        prog("eq", 1.0, "Improving clarity...")
        del spare
        return cur, info

    def _noise_profile(self, x: np.ndarray, sr: int, analysis: AnalysisResult,
                       learned: NoiseProfile | None) -> NoiseProfile | None:
        mono = x[:, 0] if x.shape[1] == 1 else x.mean(axis=1)
        if learned is not None:
            # re-measure the learned region on the filtered signal so the profile matches what the denoiser sees
            # (no extra margin: a selection or an automatic lead-in already keeps clear of speech)
            regions = learned.regions
            if regions:
                p = noise_profile_mod.estimate(mono, sr, regions, source="learned", margin_s=0.0)
                if p is not None:
                    return p
            return learned
        if analysis.silence_regions:
            p = noise_profile_mod.estimate(mono, sr, analysis.silence_regions, source="auto")
            if p is not None:
                return p
        # no clean pauses: fall back to the quietest frames
        lv = analysis.frame_level_db
        if lv.size:
            quiet = lv <= np.percentile(lv, 8)
            regions = mask_to_regions(quiet, FRAME_HOP_S, min_len=0.05)
            p = noise_profile_mod.estimate(mono, sr, regions, source="auto", margin_s=0.0)
            if p is not None:
                p.stationarity = min(p.stationarity, 0.2)  # unreliable by construction
            return p
        return None

    @staticmethod
    def _estimate_rt60(x: np.ndarray, sr: int, analysis: AnalysisResult) -> float | None:
        mono = x[:, 0] if x.shape[1] == 1 else x.mean(axis=1)
        nfft = analysis_fft_size(sr)
        hop = int(round(sr * FRAME_HOP_S))
        freqs = np.fft.rfftfreq(nfft, 1 / sr)
        band = band_edges_bins(freqs, 80, min(16000, sr / 2 - 1))
        levels = np.concatenate([db(p[:, band].sum(axis=1)) for _, p in iter_power_frames(mono, nfft, hop)])
        floor = float(np.percentile(levels, 5))
        return estimate_rt60(levels, analysis.speech_mask[: levels.shape[0]], floor)

    @staticmethod
    def _checked(prog: _Progress, fn, chunk):
        prog.check()
        return fn(chunk)

    # --- mastering ----------------------------------------------------------------------
    def _master(self, x: np.ndarray, sr: int, s: ProcessingSettings, prog: _Progress, disk: bool,
                target: float, analysis: AnalysisResult, loudness_offset_db: float = 0.0) -> np.ndarray:
        """``loudness_offset_db``: for a preview window, how much louder the window is than
        the whole recording; loudness is then judged as for the whole file, so the window
        gets the gain the full run would give it."""
        n, ch = x.shape
        step = 1 << 20
        prog("dynamics", 0.0, "Balancing levels...")
        y = allocate(n, ch, disk)
        lufs = loudness.integrated_loudness(x, sr) - loudness_offset_db
        g = loudness.gain_to_target(lufs, target, max_gain_db=40.0)
        for a in range(0, n, step):
            y[a : a + step] = x[a : a + step] * g
        prog.check()
        # slow leveling first (seconds), then the compressor handles syllables (milliseconds)
        lev = Leveler(sr, LevelerParams(amount=s.leveler_amount, range_db=s.leveler_range_db,
                                        speed_s=s.leveler_speed_s))
        lev.process(y, analysis.speech_mask, FRAME_HOP_S, out=y)
        self.last_leveling = (lev.max_boost_db, lev.max_cut_db)
        prog("dynamics", 0.35, "Leveling speech...")
        comp = Compressor(sr, CompressorParams(amount=s.compressor_amount, threshold_db=s.comp_threshold_db,
                                               ratio=s.comp_ratio, attack_ms=s.comp_attack_ms,
                                               release_ms=s.comp_release_ms))
        comp.process(y, out=y)
        # compression lifts consonants against vowels: take the edge off sibilants
        DeEsser(sr, DeEsserParams(amount=s.deesser)).process(y, out=y)
        prog("dynamics", 0.7, "Balancing levels...")
        NoiseGate(sr, GateParams(threshold_db=s.noise_gate_db)).process(y, out=y)
        # what the AI left after phrases (and the compressor lifted) comes down to an even room tone
        PauseCleaner(sr, PauseCleanupParams(amount=s.pause_cleanup)).process(y, analysis.speech_mask, FRAME_HOP_S, out=y)
        prog("dynamics", 1.0, "Balancing levels...")
        prog.check()

        prog("loudness", 0.0, "Normalizing loudness...")
        lufs = loudness.integrated_loudness(y, sr) - loudness_offset_db
        g = loudness.gain_to_target(lufs, target, max_gain_db=40.0)
        for a in range(0, n, step):
            y[a : a + step] *= g
        prog("loudness", 1.0, "Normalizing loudness...")

        # The limiter removes a little energy from peaky material; compensate
        # so the final loudness lands on target (within 0.2 LU) while the
        # true peak stays under the ceiling.
        prog("limiter", 0.0, "Limiting peaks...")
        z = allocate(n, ch, disk)
        limiter = TruePeakLimiter(sr, LimiterParams(ceiling_dbtp=s.peak_ceiling_dbtp))
        for i in range(3):
            limiter.process(y, out=z)
            short = target - (loudness.integrated_loudness(z, sr) - loudness_offset_db)
            if short < 0.2 or not np.isfinite(short) or limiter.max_reduction_db < -12:
                break
            g = 10 ** (min(short, 6.0) / 20)
            for a in range(0, n, step):
                y[a : a + step] *= g
            prog("limiter", (i + 1) / 3, "Limiting peaks...")
        prog("limiter", 1.0, "Limiting peaks...")
        return z


def best_preview_window(analysis: AnalysisResult, length_s: float = 10.0) -> tuple[float, float]:
    """The ``length_s`` stretch with the most speech (a fair sample of a whole recording);
    the whole recording when it is shorter."""
    total = analysis.duration
    if total <= length_s or analysis.speech_mask.size == 0:
        return 0.0, min(total, length_s)
    n = max(1, int(round(length_s / FRAME_HOP_S)))
    speech = np.concatenate([[0], np.cumsum(analysis.speech_mask.astype(np.int32))])
    counts = speech[n:] - speech[:-n]
    if counts.size == 0:
        return 0.0, length_s
    start = int(np.argmax(counts)) * FRAME_HOP_S
    start = float(min(max(0.0, start), total - length_s))
    return round(start, 2), round(start + length_s, 2)
