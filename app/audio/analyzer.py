"""Recording analysis.

Two streaming passes over a mono mix-down:

1. Frame features (10 ms hop): level, spectral flatness, speech-band ratio.
   These drive a voice-activity decision that separates speech frames from
   background-only frames.
2. Long-term spectra of speech frames and of background frames, used for
   noise profiling, adaptive EQ and rumble detection.

Additional checks: clipping, mains hum (50/60 Hz families), reverberation
time estimated from the free decay after speech offsets, speaker pitch range
and stereo layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, signal

from . import loudness
from .dereverb import detect_echo
from .dsp import (EPS, analysis_fft_size, band_average, band_edges_bins, db, iter_power_frames, next_pow2,
                  third_octave_centers)
from ..utils.logging import get_logger

log = get_logger("analyzer")

FRAME_HOP_S = 0.01


@dataclass
class StereoInfo:
    mode: str = "mono"  # mono | dual_mono | left_only | right_only | stereo
    correlation: float = 1.0
    level_diff_db: float = 0.0

    @property
    def description(self) -> str:
        return {
            "mono": "Mono",
            "dual_mono": "Stereo file with identical channels (processed as mono)",
            "left_only": "Speech on left channel only (processed as mono)",
            "right_only": "Speech on right channel only (processed as mono)",
            "stereo": "True stereo (channels processed separately, linked dynamics)",
        }[self.mode]


@dataclass
class HumInfo:
    detected: bool = False
    fundamental: float = 0.0
    harmonics: list[float] = field(default_factory=list)
    prominence_db: float = 0.0


@dataclass
class AnalysisResult:
    sample_rate: int
    channels: int
    duration: float
    rms_dbfs: float
    peak_dbfs: float
    true_peak_dbtp: float
    lufs: float
    loudness_range_lu: float
    crest_factor_db: float
    noise_floor_dbfs: float
    speech_level_dbfs: float
    snr_db: float
    speech_fraction: float
    speech_detected: bool
    silence_regions: list[tuple[float, float]]
    noise_seconds: float
    clipped_samples: int
    clipped_fraction: float
    clipping_regions: int
    hum: HumInfo
    rt60_s: float | None
    reverb_amount: float  # 0..1 heuristic
    f0_low_hz: float | None
    f0_median_hz: float | None
    spectral_centroid_hz: float
    bandwidth_hz: float
    lf_rumble_db: float  # background energy < 80 Hz relative to 100-1000 Hz
    noise_stationarity: float  # 0 (fluctuating) .. 1 (steady)
    stereo: StereoInfo
    echo_delay_s: float | None = None
    echo_gain: float = 0.0
    # data used by later stages
    frame_hop_s: float = FRAME_HOP_S
    speech_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, bool), repr=False)
    noise_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, bool), repr=False)
    frame_level_db: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)
    band_centers: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)
    speech_ltas_db: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)
    noise_ltas_db: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)
    noise_psd: np.ndarray | None = field(default=None, repr=False)  # fine resolution, for denoiser
    noise_psd_nfft: int = 0

    @property
    def is_clipped(self) -> bool:
        return self.clipped_fraction > 1e-4 or self.clipping_regions > 20

    @property
    def is_clean(self) -> bool:
        return self.snr_db >= 40 and not self.hum.detected and self.reverb_amount < 0.25

    def warnings(self) -> list[str]:
        out = []
        if not self.speech_detected:
            out.append("No clear speech was detected. The file may contain only noise or music.")
        if self.is_clipped:
            out.append("The recording contains clipping (distortion from recording too loud). "
                       "It can be softened but not fully repaired.")
        if self.duration < 1.0:
            out.append("The recording is very short; automatic analysis is less reliable.")
        return out

    def log_summary(self) -> dict:
        return {
            "lufs": round(self.lufs, 1), "peak_dbfs": round(self.peak_dbfs, 1),
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1), "snr_db": round(self.snr_db, 1),
            "speech_fraction": round(self.speech_fraction, 2), "rt60_s": self.rt60_s,
            "hum": self.hum.fundamental if self.hum.detected else None,
            "clipped_fraction": self.clipped_fraction, "stereo": self.stereo.mode,
            "f0_low": self.f0_low_hz, "bandwidth": round(self.bandwidth_hz),
        }


# ------------------------------------------------------------------------------------

def analyze(samples: np.ndarray, sr: int, progress=None) -> AnalysisResult:
    """Analyze a ``(frames, channels)`` float32 recording."""
    def report(p):
        if progress:
            progress(p)

    channels = samples.shape[1]
    duration = samples.shape[0] / sr
    stereo = analyze_stereo(samples, sr)
    mono = analysis_mono(samples, stereo)
    report(0.05)

    loud = loudness.measure(samples, sr)
    report(0.2)
    rms = float(np.sqrt(_mean_square(mono)))
    rms_dbfs = 20 * np.log10(max(rms, 1e-10))

    nfft = analysis_fft_size(sr)
    hop = max(1, int(round(sr * FRAME_HOP_S)))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    speech_band = band_edges_bins(freqs, 300, min(4000, sr / 2 - 1))
    full_band = band_edges_bins(freqs, 80, min(16000, sr / 2 - 1))

    level, flatness = [], []
    for _, power in iter_power_frames(mono, nfft, hop):
        level.append(db(power[:, full_band].sum(axis=1)))
        sb = power[:, speech_band] + EPS
        flatness.append(np.exp(np.mean(np.log(sb), axis=1)) / np.mean(sb, axis=1))
    level_db = np.concatenate(level)
    flatness = np.concatenate(flatness)
    report(0.45)

    speech_mask, noise_mask = voice_activity(level_db, flatness)
    speech_fraction = float(speech_mask.mean()) if speech_mask.size else 0.0

    # Pass 2: long-term spectra
    speech_sum = np.zeros(freqs.shape[0])
    noise_sum = np.zeros(freqs.shape[0])
    noise_frames_db: list[np.ndarray] = []
    centers = third_octave_centers(50, min(16000, sr / 2 * 0.9))
    n_s = n_n = 0
    for f0, power in iter_power_frames(mono, nfft, hop):
        sm = speech_mask[f0 : f0 + power.shape[0]]
        nm = noise_mask[f0 : f0 + power.shape[0]]
        if sm.any():
            speech_sum += power[sm].sum(axis=0)
            n_s += int(sm.sum())
        if nm.any():
            noise_sum += power[nm].sum(axis=0)
            n_n += int(nm.sum())
            if sum(x.shape[0] for x in noise_frames_db) < 3000:
                noise_frames_db.append(db(band_average(freqs, power[nm], centers)))
    report(0.65)
    speech_ltas = speech_sum / max(n_s, 1)
    noise_ltas = noise_sum / max(n_n, 1)
    if n_n == 0:  # no background-only frames: use the quietest frames as a rough estimate
        noise_ltas = speech_ltas * 10 ** ((np.percentile(level_db, 5) - np.percentile(level_db, 50)) / 10)

    noise_floor_db = _frames_power_db(level_db, noise_mask) if n_n else float(np.percentile(level_db, 5))
    speech_level_db = _frames_power_db(level_db, speech_mask) if n_s else float(np.percentile(level_db, 95))
    # level_db is band power of a Hann-normalised frame ~ mean square, i.e. dBFS RMS
    snr = float(np.clip(speech_level_db - noise_floor_db, -10, 90))

    speech_detected = speech_fraction > 0.03 and snr > 4 and _speech_like(flatness, speech_mask)
    silence = mask_to_regions(noise_mask, FRAME_HOP_S, min_len=0.25)
    noise_seconds = float(noise_mask.sum() * FRAME_HOP_S)

    stationarity = _stationarity(noise_frames_db)
    hum = detect_hum(mono, sr)
    report(0.75)
    clipped, clip_regions = detect_clipping(samples)
    rt60 = estimate_rt60(level_db, speech_mask, noise_floor_db)
    reverb_amount = 0.0 if rt60 is None else float(np.clip((rt60 - 0.3) / 0.7, 0, 1))
    f0_low, f0_med = estimate_pitch(mono, sr, speech_mask)
    echo_delay, echo_gain = detect_echo(mono, sr, speech_mask, FRAME_HOP_S)
    report(0.9)

    speech_ltas_db = db(band_average(freqs, speech_ltas, centers))
    noise_ltas_db = db(band_average(freqs, noise_ltas, centers))
    centroid = float(np.sum(freqs * speech_ltas) / max(np.sum(speech_ltas), EPS))
    bandwidth = _bandwidth(freqs, speech_ltas, noise_ltas)
    lf = band_edges_bins(freqs, 20, 80)
    mid = band_edges_bins(freqs, 100, 1000)
    rumble = float(db(noise_ltas[lf].mean()) - db(noise_ltas[mid].mean()))

    result = AnalysisResult(
        sample_rate=sr, channels=channels, duration=duration,
        rms_dbfs=rms_dbfs, peak_dbfs=loud.sample_peak_dbfs, true_peak_dbtp=loud.true_peak_dbtp,
        lufs=loud.integrated_lufs, loudness_range_lu=loud.loudness_range_lu,
        crest_factor_db=loud.sample_peak_dbfs - rms_dbfs,
        noise_floor_dbfs=noise_floor_db, speech_level_dbfs=speech_level_db, snr_db=snr,
        speech_fraction=speech_fraction, speech_detected=speech_detected,
        silence_regions=silence, noise_seconds=noise_seconds,
        clipped_samples=clipped, clipped_fraction=clipped / max(1, samples.size),
        clipping_regions=clip_regions, hum=hum, rt60_s=rt60, reverb_amount=reverb_amount,
        f0_low_hz=f0_low, f0_median_hz=f0_med, spectral_centroid_hz=centroid,
        bandwidth_hz=bandwidth, lf_rumble_db=rumble, noise_stationarity=stationarity, stereo=stereo,
        echo_delay_s=echo_delay, echo_gain=echo_gain,
        speech_mask=speech_mask, noise_mask=noise_mask, frame_level_db=level_db, band_centers=centers,
        speech_ltas_db=speech_ltas_db, noise_ltas_db=noise_ltas_db,
        noise_psd=noise_ltas if n_n else None, noise_psd_nfft=nfft,
    )
    log.info("Analysis: %s", result.log_summary())
    report(1.0)
    return result


# --- helpers ------------------------------------------------------------------------

def analyze_stereo(samples: np.ndarray, sr: int) -> StereoInfo:
    if samples.shape[1] == 1:
        return StereoInfo("mono")
    step = max(1, samples.shape[0] // 2_000_000)  # subsample very long files
    left = np.asarray(samples[::step, 0], dtype=np.float64)
    right = np.asarray(samples[::step, 1], dtype=np.float64)
    pl, pr = np.mean(left**2) + 1e-20, np.mean(right**2) + 1e-20
    diff = float(10 * np.log10(pl / pr))
    corr = float(np.sum(left * right) / np.sqrt(np.sum(left**2) * np.sum(right**2) + 1e-20))
    if diff > 20:
        mode = "left_only"
    elif diff < -20:
        mode = "right_only"
    elif corr > 0.97 and abs(diff) < 1.5:
        mode = "dual_mono"
    else:
        mode = "stereo"
    return StereoInfo(mode, corr, diff)


def analysis_mono(samples: np.ndarray, stereo: StereoInfo) -> np.ndarray:
    if samples.shape[1] == 1:
        return samples[:, 0]
    if stereo.mode == "left_only":
        return samples[:, 0]
    if stereo.mode == "right_only":
        return samples[:, 1]
    out = np.empty(samples.shape[0], dtype=np.float32)
    block = 1 << 20
    for a in range(0, samples.shape[0], block):
        out[a : a + block] = samples[a : a + block].mean(axis=1)
    return out


def voice_activity(level_db: np.ndarray, flatness: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Classify frames into speech / background-only (plus an uncertain middle).

    Background is estimated from the low percentiles of the level histogram;
    speech must rise clearly above it and be less noise-like (lower spectral
    flatness). Masks are smoothed with hangover so syllable gaps stay speech.
    """
    if level_db.size == 0:
        return np.zeros(0, bool), np.zeros(0, bool)
    floor = float(np.percentile(level_db, 10))
    top = float(np.percentile(level_db, 97))
    span = max(top - floor, 1e-3)
    speech_thr = floor + max(6.0, 0.30 * span)
    noise_thr = floor + max(3.0, min(0.15 * span, 8.0))
    flat_ref = float(np.median(flatness[level_db <= noise_thr])) if np.any(level_db <= noise_thr) else 0.5

    speech = (level_db > speech_thr) & (flatness < max(0.15, flat_ref * 0.9) + 0.25 * (level_db > speech_thr + 10))
    speech = ndimage.binary_closing(speech, structure=np.ones(15))  # bridge 150 ms gaps
    # drop blips < 80 ms: a click smeared by the 40 ms analysis window spans ~50 ms,
    # while real speech (syllables bridged above) always lasts longer
    speech = ndimage.binary_opening(speech, structure=np.ones(8))
    speech_wide = ndimage.binary_dilation(speech, structure=np.ones(31))  # 150 ms hangover each side

    noise = (level_db < noise_thr) & ~speech_wide
    noise = ndimage.binary_opening(noise, structure=np.ones(10))  # only runs >= 100 ms
    if noise.sum() < 20:  # nearly continuous speech: fall back to the quietest 5 % of frames
        quiet = level_db <= np.percentile(level_db, 5)
        noise = quiet & ~ndimage.binary_dilation(speech, structure=np.ones(5))
    return speech_wide, noise


def _speech_like(flatness: np.ndarray, speech_mask: np.ndarray) -> bool:
    if not speech_mask.any():
        return False
    # voiced speech is strongly harmonic: many frames with low flatness
    return float(np.percentile(flatness[speech_mask], 30)) < 0.35


def _frames_power_db(level_db: np.ndarray, mask: np.ndarray) -> float:
    return float(db(np.mean(10 ** (level_db[mask] / 10))))


def _mean_square(x: np.ndarray) -> float:
    acc = 0.0
    block = 1 << 20
    for a in range(0, x.shape[0], block):
        seg = np.asarray(x[a : a + block], dtype=np.float64)
        acc += float(np.dot(seg, seg))
    return acc / max(1, x.shape[0])


def mask_to_regions(mask: np.ndarray, hop_s: float, min_len: float = 0.0) -> list[tuple[float, float]]:
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    regions = []
    for a, b in zip(edges[::2], edges[1::2]):
        if (b - a) * hop_s >= min_len:
            regions.append((a * hop_s, b * hop_s))
    return regions


def _stationarity(noise_frames_db: list[np.ndarray]) -> float:
    if not noise_frames_db:
        return 0.0
    frames = np.concatenate(noise_frames_db)
    if frames.shape[0] < 10:
        return 0.0
    # std over time per band; steady fans/hiss ~1-2 dB, babble/traffic 6+ dB
    spread = float(np.median(np.std(frames, axis=0)))
    return float(np.clip(1.0 - (spread - 1.5) / 5.0, 0.0, 1.0))


def _bandwidth(freqs: np.ndarray, speech: np.ndarray, noise: np.ndarray) -> float:
    """Highest frequency where speech still clearly exceeds the background."""
    s = db(speech) - db(noise + EPS)
    s = ndimage.uniform_filter1d(s, 9)
    valid = np.flatnonzero((s > 6) & (freqs > 1000))
    return float(freqs[valid[-1]]) if valid.size else float(freqs[-1])


def detect_hum(mono: np.ndarray, sr: int) -> HumInfo:
    """Look for stable spectral lines at 50 or 60 Hz and their harmonics."""
    target_sr = 4000
    x = np.asarray(mono[: sr * 600], dtype=np.float32)  # the first 10 minutes are plenty
    x = signal.resample_poly(x, target_sr, sr) if sr != target_sr else x
    if x.shape[0] < target_sr * 2:
        return HumInfo()
    nper = min(x.shape[0], target_sr * 4)
    f, p = signal.welch(x, fs=target_sr, nperseg=nper, noverlap=nper // 2)
    pdb = db(p)
    best = HumInfo()
    for base in (50.0, 60.0):
        found, proms = [], []
        for k in range(1, 11):
            fk = base * k
            if fk > target_sr / 2 - 50:
                break
            near = (f > fk - 1.5) & (f < fk + 1.5)
            ring = ((f > fk - 25) & (f < fk - 6)) | ((f > fk + 6) & (f < fk + 25))
            if not near.any() or not ring.any():
                continue
            peak_i = np.flatnonzero(near)[np.argmax(pdb[near])]
            prom = pdb[peak_i] - np.median(pdb[ring])
            if prom > 10:
                found.append(float(f[peak_i]))
                proms.append(prom)
        strong = [p for p in proms if p > 15]
        if (len(found) >= 2 or strong) and (sum(proms) > best.prominence_db):
            fund = float(np.median([fr / round(fr / base) for fr in found]))
            harmonics = [fund * k for k in range(1, 9) if any(abs(fund * k - fr) < 3 for fr in found) or k <= 3]
            best = HumInfo(True, fund, harmonics, float(max(proms)))
    return best


def detect_clipping(samples: np.ndarray) -> tuple[int, int]:
    """Count samples in flat-topped runs at the signal's extreme values."""
    peak = 10 ** (loudness.sample_peak_db(samples) / 20)
    if peak < 0.25:
        return 0, 0
    # within 2 % of the maximum: tolerates dither/noise added after the
    # clip while a natural waveform crest yields only one or two such runs
    thr = peak * 0.98
    total, regions = 0, 0
    block = 1 << 20
    for ch in range(samples.shape[1]):
        for a in range(0, samples.shape[0], block):
            hot = np.abs(np.asarray(samples[a : a + block, ch])) >= thr
            if not hot.any():
                continue
            padded = np.concatenate([[False], hot, [False]])
            edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
            lengths = edges[1::2] - edges[::2]
            runs = lengths[lengths >= 3]
            total += int(runs.sum())
            regions += int(runs.size)
    return total, regions


def estimate_rt60(level_db: np.ndarray, speech_mask: np.ndarray, noise_floor_db: float) -> float | None:
    """Reverberation time from free decays in the level curve.

    Looks for places where the level falls steadily from a loud peak by at
    least 20 dB (the end of a word or phrase). In a room, such decays cannot be
    faster than the room's decay, so the median slope of the long, clean
    decays (fitted between -5 dB and -25 dB) estimates RT60. This does not
    depend on the voice-activity mask, which reverb tails tend to blur.
    """
    if level_db.size < 100:
        return None
    hop = FRAME_HOP_S
    lv = ndimage.uniform_filter1d(level_db, 3)
    loud = np.percentile(lv, 90)
    peaks = np.flatnonzero((lv == ndimage.maximum_filter1d(lv, 15)) & (lv > loud - 12))
    slopes = []
    last_end = -1
    for p in peaks:
        if p <= last_end:
            continue
        i = p
        # follow the decay while it keeps falling (small bumps allowed)
        while i + 1 < lv.size and lv[i + 1] < lv[i] + 1.0 and i - p < 150:
            i += 1
        drop = lv[p] - lv[p : i + 1]
        usable = min(25.0, lv[p] - noise_floor_db - 5.0)
        # need >= 20 dB of clean decay above the noise floor, otherwise the slow
        # approach into the background would masquerade as reverb
        if drop.max(initial=0) < 20 or usable < 20:
            continue
        a = int(np.argmax(drop >= 5))
        b = int(np.argmax(drop >= usable)) if np.any(drop >= usable) else -1
        if b <= a + 3:
            continue
        t = np.arange(a, b + 1) * hop
        slope = np.polyfit(t, lv[p + a : p + b + 1], 1)[0]
        if slope < -10:
            slopes.append(slope)
        last_end = i
    if len(slopes) < 3:
        return None
    rt60 = -60.0 / float(np.median(slopes))
    return float(np.clip(rt60, 0.05, 3.0))


def yin_pitch(x: np.ndarray, sr: int, fmin: float = 55, fmax: float = 420,
              frame: int = 1024, hop: int = 320, threshold: float = 0.15) -> np.ndarray:
    """Vectorised YIN (de Cheveigné & Kawahara 2002). NaN where unvoiced."""
    if x.shape[0] < frame:
        return np.zeros(0)
    frames = np.lib.stride_tricks.sliding_window_view(x, frame)[::hop].astype(np.float64)
    w = frame // 2
    lag_min, lag_max = int(sr / fmax), min(int(sr / fmin), w - 1)
    nfft = next_pow2(frame + w)
    # difference function d(tau) = sum (x_j - x_{j+tau})^2 over j < w, via FFT correlation
    spec_a = np.fft.rfft(frames[:, :w], nfft)
    spec_b = np.fft.rfft(frames, nfft)
    corr = np.fft.irfft(np.conj(spec_a) * spec_b, nfft)[:, : w]
    sq = np.cumsum(frames**2, axis=1)
    energy0 = sq[:, w - 1][:, None]
    taus = np.arange(w)
    energy_tau = sq[:, taus + w - 1] - np.concatenate([np.zeros((frames.shape[0], 1)), sq[:, : w - 1]], axis=1)
    diff = energy0 + energy_tau - 2 * corr
    diff[:, 0] = 0
    cmnd = diff[:, 1:] * np.arange(1, w) / np.maximum(np.cumsum(diff[:, 1:], axis=1), 1e-12)
    cmnd = np.concatenate([np.ones((frames.shape[0], 1)), cmnd], axis=1)
    region = cmnd[:, lag_min:lag_max]
    below = region < threshold
    has = below.any(axis=1)
    first = np.argmax(below, axis=1)
    # walk to the local minimum after the first threshold crossing
    idx = first.copy()
    for _ in range(20):
        nxt = np.minimum(idx + 1, region.shape[1] - 1)
        better = region[np.arange(region.shape[0]), nxt] < region[np.arange(region.shape[0]), idx]
        if not better.any():
            break
        idx = np.where(better, nxt, idx)
    f0 = sr / (idx + lag_min)
    f0[~has] = np.nan
    return f0


def estimate_pitch(mono: np.ndarray, sr: int, speech_mask: np.ndarray) -> tuple[float | None, float | None]:
    """Speaker pitch range from up to ~60 s of detected speech (YIN)."""
    regions = mask_to_regions(speech_mask, FRAME_HOP_S, min_len=0.3)
    if not regions:
        return None, None
    target_sr = 16000
    pieces, total = [], 0.0
    stride = max(1, len(regions) // 60)
    for a, b in regions[::stride]:
        seg = np.asarray(mono[int(a * sr) : int(b * sr)], dtype=np.float32)
        pieces.append(seg)
        total += b - a
        if total > 60:
            break
    x = np.concatenate(pieces)
    if sr != target_sr:
        x = signal.resample_poly(x, target_sr, sr).astype(np.float32)
    try:
        f0 = yin_pitch(x, target_sr)
        f0 = f0[np.isfinite(f0)]
    except Exception as exc:  # pitch is a refinement only
        log.debug("pitch estimate failed: %s", exc)
        return None, None
    if f0.size < 20:
        return None, None
    return float(np.percentile(f0, 5)), float(np.median(f0))
