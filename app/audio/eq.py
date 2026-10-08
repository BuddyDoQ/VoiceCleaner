"""Filtering and voice EQ.

``CleanupFilter``  - DC removal, adaptive high-pass, mains-hum notches.
``VoiceEQ``        - adaptive tonal correction toward a natural speech
                     spectrum, plus user presence / clarity / low-end trims.

The voice EQ never applies a fixed preset curve. It measures the long-term
spectrum of the *speech* in this recording, compares it with the average
spectrum of natural speech (Byrne et al. 1994, LTASS), and corrects only
clear deviations - muddiness, missing presence, boomy low end - with a few
broad, gentle filters (each limited to a few dB).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from . import dsp
from .dsp import analysis_fft_size, band_average, db, iter_power_frames, third_octave_centers

# Long-term average speech spectrum (Byrne et al. 1994, combined male/female,
# 1/3-octave levels in dB). Only the shape matters.
LTASS_CENTERS = np.array([63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
                          2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000], dtype=float)
LTASS_DB = np.array([38.6, 43.5, 54.4, 57.7, 56.8, 58.2, 59.7, 60.0, 62.1, 62.1, 60.5, 56.8, 53.7, 53.0, 52.0,
                     48.7, 48.1, 46.8, 45.6, 44.5, 44.3, 43.7, 43.4, 41.3, 40.7])

STREAM_BLOCK = 1 << 19


ZERO_PHASE_CHUNK_S = 30.0
ZERO_PHASE_OVERLAP_S = 1.0


def apply_sos_zero_phase(x: np.ndarray, sos: np.ndarray, sr: int, out: np.ndarray | None = None) -> np.ndarray:
    """Zero-phase (forward-backward) IIR filtering in overlapping chunks.

    Processing is offline, so filters need not be causal: forward-backward
    filtering leaves the phase of the voice untouched (no smearing of
    transients, no low-frequency group delay) and squares the magnitude
    response, which callers account for by halving their dB gains.
    """
    if out is None:
        out = np.empty_like(x, dtype=np.float32)
    if sos.shape[0] == 0:
        if out is not x:
            for a in range(0, x.shape[0], STREAM_BLOCK):
                out[a : a + STREAM_BLOCK] = x[a : a + STREAM_BLOCK]
        return out

    def run(chunk: np.ndarray) -> np.ndarray:
        pad = min(chunk.shape[0] - 1, int(0.25 * sr))
        return signal.sosfiltfilt(sos, chunk.astype(np.float64), axis=0, padtype="even", padlen=pad).astype(np.float32)

    return dsp.process_in_chunks(x, run, int(ZERO_PHASE_CHUNK_S * sr), int(ZERO_PHASE_OVERLAP_S * sr), out=out)


def apply_sos_streaming(x: np.ndarray, sos: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
    """IIR filtering in blocks with carried state (works on disk-backed arrays)."""
    if out is None:
        out = np.empty_like(x, dtype=np.float32)
    if sos.shape[0] == 0:
        if out is not x:
            for a in range(0, x.shape[0], STREAM_BLOCK):
                out[a : a + STREAM_BLOCK] = x[a : a + STREAM_BLOCK]
        return out
    channels = x.shape[1] if x.ndim == 2 else 1
    zi = np.zeros((sos.shape[0], 2, channels)) if x.ndim == 2 else np.zeros((sos.shape[0], 2))
    for a in range(0, x.shape[0], STREAM_BLOCK):
        block = np.asarray(x[a : a + STREAM_BLOCK], dtype=np.float64)
        y, zi = signal.sosfilt(sos, block, axis=0, zi=zi)
        out[a : a + STREAM_BLOCK] = y
    return out


# --- cleanup ------------------------------------------------------------------------

@dataclass
class CleanupParams:
    highpass_hz: float = 70.0  # 0 disables
    highpass_order: int = 2
    hum_freqs: list[float] = field(default_factory=list)
    hum_q: float = 30.0


def choose_highpass(lf_cleanup: float, rumble_db: float, f0_low: float | None) -> float:
    """Adaptive high-pass cutoff: higher for rumbly recordings, but always
    safely below the speaker's lowest pitch so the voice keeps its body."""
    cutoff = 65.0 + 35.0 * lf_cleanup
    if rumble_db > 6:
        cutoff += min(15.0, (rumble_db - 6) * 1.5)
    if f0_low:
        cutoff = min(cutoff, 0.8 * f0_low)
    return float(np.clip(cutoff, 40.0, 120.0))


class CleanupFilter:
    def __init__(self, sr: int, params: CleanupParams):
        self.sr = sr
        self.params = params
        rows = []
        if params.highpass_hz > 0:
            rows.append(dsp.highpass_sos(params.highpass_hz, sr, params.highpass_order))
        if params.hum_freqs:
            # skip harmonics the high-pass already removes
            freqs = [f for f in params.hum_freqs if f > params.highpass_hz * 0.7]
            notches = dsp.notch_sos(freqs, sr, params.hum_q)
            if notches.shape[0]:
                rows.append(notches)
        self.sos = np.vstack(rows) if rows else np.zeros((0, 6))

    def process(self, x: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        return apply_sos_zero_phase(x, self.sos, self.sr, out)


# --- voice EQ --------------------------------------------------------------------------

@dataclass
class VoiceEQParams:
    amount: float = 0.5  # 0..1 adaptive correction depth
    presence: float = 0.0  # -1..1 user trim (+-4 dB around 3 kHz)
    hf_clarity: float = 0.0  # -1..1 user trim (+-4 dB shelf from 7.5 kHz)
    lf_cleanup: float = 0.3  # 0..1 low-end tightening
    bandwidth_hz: float = 20000.0  # where real content ends; no boosts above it


@dataclass
class EQBand:
    kind: str
    freq: float
    gain_db: float
    q: float = 0.9


def _speech_ltas(x: np.ndarray, sr: int, speech_mask: np.ndarray, hop_s: float) -> tuple[np.ndarray, np.ndarray]:
    nfft = analysis_fft_size(sr)
    hop = int(round(sr * hop_s))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    acc = np.zeros(freqs.shape[0])
    count = 0
    mono = x.mean(axis=1) if x.ndim == 2 and x.shape[1] > 1 else (x[:, 0] if x.ndim == 2 else x)
    for f0, power in iter_power_frames(mono, nfft, hop):
        m = speech_mask[f0 : f0 + power.shape[0]]
        if m.shape[0] < power.shape[0]:
            m = np.pad(m, (0, power.shape[0] - m.shape[0]))
        if m.any():
            acc += power[m].sum(axis=0)
            count += int(m.sum())
    centers = third_octave_centers(50, min(16000, sr / 2 * 0.9))
    return centers, db(band_average(freqs, acc / max(count, 1), centers))


def design_voice_eq(centers: np.ndarray, measured_db: np.ndarray, params: VoiceEQParams) -> list[EQBand]:
    ref = np.interp(np.log(centers), np.log(LTASS_CENTERS), LTASS_DB)
    core = (centers >= 250) & (centers <= 2500)
    dev = measured_db - ref
    dev -= np.mean(dev[core])
    dev = np.convolve(np.pad(dev, 1, mode="edge"), np.ones(3) / 3, mode="valid")  # 1-octave smoothing

    def band_dev(lo, hi):
        sel = (centers >= lo) & (centers <= hi)
        return float(np.mean(dev[sel])) if sel.any() else 0.0

    a = float(np.clip(params.amount, 0, 1))
    bands: list[EQBand] = []
    # boomy / proximity-effect low end
    low = band_dev(80, 160)
    lf_gain = -min(max(low - 3.0, 0.0), 6.0) * a * 0.7 - 4.0 * params.lf_cleanup * a
    if lf_gain < -0.3:
        bands.append(EQBand("lowshelf", 160.0, lf_gain))
    # mud
    mud = band_dev(200, 500)
    if mud > 1.5:
        sel = (centers >= 160) & (centers <= 630)
        f_mud = float(centers[sel][np.argmax(dev[sel])])
        bands.append(EQBand("peak", f_mud, -min(mud - 1.5, 6.0) * a * 0.8, 1.0))
    # presence (intelligibility)
    pres = band_dev(2000, 5000)
    pres_gain = 0.0
    if pres < -1.5:
        pres_gain = min(-pres - 1.5, 5.0) * a * 0.7
    elif pres > 3.0:
        pres_gain = -(pres - 3.0) * a * 0.5  # tame harshness
    pres_gain += 4.0 * params.presence
    if abs(pres_gain) > 0.3:
        bands.append(EQBand("peak", 3200.0, float(np.clip(pres_gain, -6, 6)), 0.8))
    # air / clarity: only boost where the recording actually has content
    has_air = params.bandwidth_hz > 10000
    air = band_dev(8000, 12500)
    air_gain = min(max(-air - 2.0, 0.0), 4.0) * a * 0.5 if has_air else 0.0
    user_hf = 4.0 * params.hf_clarity
    if user_hf > 0 and not has_air:
        user_hf = min(user_hf, 1.0)  # boosting empty highs only raises hiss
    air_gain += user_hf
    if abs(air_gain) > 0.3:
        bands.append(EQBand("highshelf", 7500.0, float(np.clip(air_gain, -6, 6))))
    return bands


def bands_to_sos(bands: list[EQBand], sr: int, zero_phase: bool = True) -> np.ndarray:
    """Biquads for ``bands``; gains are halved for forward-backward filtering."""
    rows = []
    k = 0.5 if zero_phase else 1.0
    for b in bands:
        if b.freq >= sr * 0.45:
            continue
        if b.kind == "peak":
            rows.append(dsp.peaking(b.freq, b.gain_db * k, b.q, sr))
        elif b.kind == "lowshelf":
            rows.append(dsp.low_shelf(b.freq, b.gain_db * k, sr))
        elif b.kind == "highshelf":
            rows.append(dsp.high_shelf(b.freq, b.gain_db * k, sr))
    return np.array(rows) if rows else np.zeros((0, 6))


class VoiceEQ:
    def __init__(self, sr: int, params: VoiceEQParams):
        self.sr = sr
        self.params = params
        self.bands: list[EQBand] = []

    def process(self, x: np.ndarray, speech_mask: np.ndarray, hop_s: float, out: np.ndarray | None = None) -> np.ndarray:
        centers, measured = _speech_ltas(x, self.sr, speech_mask, hop_s)
        self.bands = design_voice_eq(centers, measured, self.params)
        return apply_sos_zero_phase(x, bands_to_sos(self.bands, self.sr), self.sr, out)
