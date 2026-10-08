"""Quality control: compare enhanced audio with the original.

Guiding principle: *better speech, not obviously processed speech*. After
processing, the enhanced signal is measured against the original on the
same time grid (using the original's speech / background segmentation).
Each check that fails proposes a specific rollback, and the pipeline
reprocesses with gentler settings.

Also computes the before/after figures shown in the UI and the estimated
"speech clarity" score, which is an internal heuristic, not a standardised
intelligibility measure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from . import loudness
from .analyzer import FRAME_HOP_S, AnalysisResult, analysis_mono, analyze_stereo
from .dsp import EPS, analysis_fft_size, band_average, band_edges_bins, db, iter_power_frames, third_octave_centers
from .settings import ProcessingSettings


@dataclass
class SignalMetrics:
    lufs: float
    true_peak_dbtp: float
    sample_peak_dbfs: float
    loudness_range_lu: float
    noise_floor_dbfs: float
    speech_level_dbfs: float
    snr_db: float
    clarity: float  # 0..100, estimated

    def as_rows(self) -> list[tuple[str, str]]:
        return [
            ("Loudness", _fmt(self.lufs, "LUFS")),
            ("True peak", _fmt(self.true_peak_dbtp, "dBTP")),
            ("Noise floor", _fmt(self.noise_floor_dbfs, "dBFS")),
            ("Speech-to-noise", _fmt(self.snr_db, "dB")),
            ("Dynamic range", _fmt(self.loudness_range_lu, "LU")),
        ]


def _fmt(v: float, unit: str) -> str:
    if not np.isfinite(v):
        return "—"
    return f"{v:.1f} {unit}"


@dataclass
class QCIssue:
    code: str
    message: str


@dataclass
class QCReport:
    issues: list[QCIssue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class _Frames:
    level_db: np.ndarray  # broadband (80 Hz - 16 kHz) mean square per frame
    hf_db: np.ndarray  # > 6 kHz
    speech_ltas_db: np.ndarray
    centers: np.ndarray


def _frame_features(samples: np.ndarray, sr: int, speech_mask: np.ndarray, mono: np.ndarray | None = None) -> _Frames:
    if mono is None:
        mono = analysis_mono(samples, analyze_stereo(samples, sr))
    nfft = analysis_fft_size(sr)
    hop = int(round(sr * FRAME_HOP_S))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    full = band_edges_bins(freqs, 80, min(16000, sr / 2 - 1))
    hf = band_edges_bins(freqs, 6000, min(16000, sr / 2 - 1))
    centers = third_octave_centers(50, min(16000, sr / 2 * 0.9))
    levels, hfs = [], []
    acc = np.zeros(freqs.shape[0])
    count = 0
    for f0, power in iter_power_frames(mono, nfft, hop):
        levels.append(db(power[:, full].sum(axis=1)))
        hfs.append(db(power[:, hf].sum(axis=1)))
        m = speech_mask[f0 : f0 + power.shape[0]]
        if m.shape[0] == power.shape[0] and m.any():
            acc += power[m].sum(axis=0)
            count += int(m.sum())
    ltas = db(band_average(freqs, acc / max(count, 1), centers))
    return _Frames(np.concatenate(levels), np.concatenate(hfs), ltas, centers)


def _masked_power_db(level_db: np.ndarray, mask: np.ndarray) -> float:
    n = min(level_db.shape[0], mask.shape[0])
    sel = level_db[:n][mask[:n]]
    if sel.size == 0:
        return float("nan")
    return float(db(np.mean(10 ** (sel / 10))))


def clarity_score(snr_db: float, level_db: np.ndarray, speech_mask: np.ndarray) -> float:
    """Estimated clarity 0..100 (internal heuristic).

    Combines the speech-to-background ratio with envelope modulation depth
    in speech regions: clear speech has deep, fast level fluctuations
    (syllables), while noise and reverb fill the gaps and flatten them.
    """
    n = min(level_db.shape[0], speech_mask.shape[0])
    lv = level_db[:n]
    sm = speech_mask[:n]
    if sm.sum() < 50:
        depth = 0.0
    else:
        sp = lv[sm]
        depth = float(np.percentile(sp, 90) - np.percentile(sp, 15))
    snr_part = np.clip((snr_db - 3.0) / 37.0, 0, 1)
    depth_part = np.clip((depth - 6.0) / 24.0, 0, 1)
    return float(100 * (0.65 * snr_part + 0.35 * depth_part))


def measure(samples: np.ndarray, sr: int, analysis: AnalysisResult, frames: _Frames | None = None) -> SignalMetrics:
    frames = frames or _frame_features(samples, sr, analysis.speech_mask)
    loud = loudness.measure(samples, sr)
    noise = _masked_power_db(frames.level_db, analysis.noise_mask) if analysis.noise_mask.any() \
        else float(np.percentile(frames.level_db, 5))
    speech = _masked_power_db(frames.level_db, analysis.speech_mask) if analysis.speech_mask.any() \
        else float(np.percentile(frames.level_db, 95))
    noise = max(noise, -120.0)
    snr = float(np.clip(speech - noise, -10, 80))
    return SignalMetrics(
        lufs=loud.integrated_lufs, true_peak_dbtp=loud.true_peak_dbtp, sample_peak_dbfs=loud.sample_peak_dbfs,
        loudness_range_lu=loud.loudness_range_lu, noise_floor_dbfs=noise, speech_level_dbfs=speech, snr_db=snr,
        clarity=clarity_score(snr, frames.level_db, analysis.speech_mask),
    )


def original_metrics(samples: np.ndarray, sr: int, analysis: AnalysisResult) -> SignalMetrics:
    frames = _Frames(analysis.frame_level_db, np.zeros(0), analysis.speech_ltas_db, analysis.band_centers)
    snr = float(np.clip(analysis.snr_db, -10, 80))
    return SignalMetrics(
        lufs=analysis.lufs, true_peak_dbtp=analysis.true_peak_dbtp, sample_peak_dbfs=analysis.peak_dbfs,
        loudness_range_lu=analysis.loudness_range_lu, noise_floor_dbfs=analysis.noise_floor_dbfs,
        speech_level_dbfs=analysis.speech_level_dbfs, snr_db=snr,
        clarity=clarity_score(snr, frames.level_db, analysis.speech_mask),
    )


def check(original: np.ndarray, enhanced: np.ndarray, sr: int, analysis: AnalysisResult,
          settings: ProcessingSettings, enhanced_metrics: SignalMetrics | None = None) -> tuple[QCReport, _Frames]:
    """Run all checks. Returns the report and the enhanced frame features."""
    rep = QCReport()
    enh = _frame_features(enhanced, sr, analysis.speech_mask)
    n = min(enh.level_db.shape[0], analysis.frame_level_db.shape[0], analysis.speech_mask.shape[0])
    o_lv, e_lv = analysis.frame_level_db[:n], enh.level_db[:n]
    sm = analysis.speech_mask[:n]
    if sm.sum() < 20:
        rep.stats["skipped"] = "too little speech for quality checks"
        return rep, enh

    # Frames where speech clearly dominates the background (>= 10 dB above the
    # noise floor): removing *all* noise could lower these by < 0.5 dB, so a
    # big drop there means speech itself was removed.
    # Only syllable nuclei count (frames within 6 dB of their local +-100 ms
    # maximum): those are dominated by direct sound, whereas reverb tails,
    # which de-reverb legitimately removes, sit well below the nuclei.
    nuclei = o_lv >= ndimage.maximum_filter1d(o_lv, size=21, mode="nearest") - 6.0
    strong = sm & nuclei & (o_lv > max(analysis.speech_level_dbfs - 12, analysis.noise_floor_dbfs + 10))
    ref = strong if strong.sum() > 20 else sm
    offset = float(np.median(e_lv[ref] - o_lv[ref]))  # overall gain change
    rep.stats["gain_db"] = offset

    # 1. speech loss: strong speech frames that became much quieter than the overall gain change
    if strong.sum() > 20:
        drop = (e_lv - o_lv - offset)[strong]
        loss = float(np.mean(drop < -10.0))
        rep.stats["speech_loss"] = loss
        if loss > 0.06:
            rep.issues.append(QCIssue("speech_loss", f"{loss:.0%} of speech frames were strongly attenuated"))

    # 2. spectral change in bands where speech clearly dominates the background
    dominant = analysis.speech_ltas_db - analysis.noise_ltas_db > 12
    m = min(dominant.shape[0], enh.speech_ltas_db.shape[0])
    dom = dominant[:m] & (analysis.band_centers[:m] >= 150) & (analysis.band_centers[:m] <= 6000)
    if dom.sum() >= 4:
        diff = enh.speech_ltas_db[:m] - analysis.speech_ltas_db[:m]
        diff -= np.median(diff[dom])
        change = float(np.mean(np.abs(diff[dom])))
        rep.stats["spectral_change_db"] = change
        if change > 4.5:
            rep.issues.append(QCIssue("spectral_change", f"tonal balance changed by {change:.1f} dB on average"))

    # 3. high-frequency artefacts: HF energy rising in the gaps between words
    gaps = analysis.noise_mask[:n]
    if gaps.sum() > 30 and enh.hf_db.size >= n:
        e_hf = _masked_power_db(enh.hf_db[:n], gaps) - offset
        e_full = _masked_power_db(e_lv, gaps) - offset
        o_full = _masked_power_db(o_lv, gaps)
        # HF making up much more of the residual background than before = hiss/whistle artefacts
        hf_share_enh = e_hf - e_full
        rep.stats["hf_share_gaps_db"] = hf_share_enh
        if np.isfinite(hf_share_enh) and hf_share_enh > -1.0 and e_full > o_full - 3:
            rep.issues.append(QCIssue("hf_artifacts", "high-frequency artefacts in pauses"))

    # 4. over-compression
    em = enhanced_metrics or measure(enhanced, sr, analysis, enh)
    # leveling narrows the range on purpose; only a nearly flat result is a problem
    if analysis.loudness_range_lu > 4 and em.loudness_range_lu < min(0.3 * analysis.loudness_range_lu, 2.0):
        rep.issues.append(QCIssue("over_compressed",
                                  f"dynamic range reduced from {analysis.loudness_range_lu:.1f} to {em.loudness_range_lu:.1f} LU"))

    # 5. excessive gain (very quiet source pushed hard: noise comes up too)
    total_gain = em.lufs - analysis.lufs if np.isfinite(em.lufs) and np.isfinite(analysis.lufs) else 0.0
    rep.stats["loudness_gain_db"] = total_gain
    if total_gain > 30:
        rep.issues.append(QCIssue("excessive_gain", f"the recording needed {total_gain:.0f} dB of gain"))

    # 6. clipping
    if em.true_peak_dbtp > settings.peak_ceiling_dbtp + 0.2:
        rep.issues.append(QCIssue("clipping", f"true peak {em.true_peak_dbtp:.1f} dBTP above the ceiling"))
    rep.stats["enhanced_metrics"] = em
    return rep, enh


def propose_rollback(report: QCReport, s: ProcessingSettings) -> tuple[ProcessingSettings, list[str]]:
    """Gentler settings addressing each issue. Returns (settings, change notes)."""
    s = s.copy()
    notes = []
    codes = {i.code for i in report.issues}
    if "speech_loss" in codes:
        s.noise_reduction = round(s.noise_reduction * 0.75, 3)
        s.max_attenuation_db = max(10.0, s.max_attenuation_db * 0.75)
        s.spectral_subtraction = max(1.0, s.spectral_subtraction * 0.85)
        s.reverb_reduction = round(s.reverb_reduction * 0.8, 3)
        s.noise_gate_db = min(s.noise_gate_db, -70.0) if s.noise_gate_db > -80 else s.noise_gate_db
        notes.append("reduced noise reduction to protect speech")
    if "spectral_change" in codes:
        s.speech_enhancement = round(s.speech_enhancement * 0.7, 3)
        s.tonal_balance = round(s.tonal_balance * 0.7, 3)
        s.resynthesis = round(s.resynthesis * 0.6, 3)
        s.voice_presence *= 0.6
        s.hf_clarity *= 0.6
        s.noise_reduction = round(s.noise_reduction * 0.9, 3)
        notes.append("softened EQ to keep the natural voice")
    if "hf_artifacts" in codes:
        s.hf_clarity = min(s.hf_clarity, 0.0) - 0.2
        s.resynthesis = round(s.resynthesis * 0.6, 3)
        s.spectral_subtraction = max(1.0, s.spectral_subtraction * 0.85)
        notes.append("reduced high-frequency emphasis")
    if "over_compressed" in codes:
        s.compressor_amount = round(s.compressor_amount * 0.6, 3)
        s.leveler_amount = round(s.leveler_amount * 0.6, 3)
        s.comp_ratio = max(1.5, s.comp_ratio * 0.8)
        notes.append("made compression gentler")
    return s, notes


RESTORATION_FIELDS = (
    "noise_reduction", "speech_enhancement", "room_reduction", "use_ai", "noise_floor_offset_db",
    "spectral_subtraction", "max_attenuation_db", "voice_presence", "hf_clarity", "lf_cleanup",
    "reverb_reduction", "echo_reduction", "hum_removal", "highpass_hz", "resynthesis", "tonal_balance", "eq_tilt",
)


def restoration_key(s: ProcessingSettings) -> tuple:
    return tuple(round(float(getattr(s, f)), 4) for f in RESTORATION_FIELDS) + tuple(
        round(float(g), 3) for g in s.eq_gains)
