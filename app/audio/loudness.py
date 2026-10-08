"""Loudness measurement and normalization (ITU-R BS.1770-4 / EBU R128).

The meter streams over the signal in blocks, so it works on disk-backed
recordings of any length. Measured values:

* integrated loudness (LUFS) with absolute and relative gating
* loudness range (LRA, EBU Tech 3342) used as the "dynamic range" figure
* true peak (dBTP) via 4x oversampling
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

ABS_GATE_LUFS = -70.0
BLOCK_SEGMENT_S = 0.1  # 100 ms hop shared by momentary (400 ms) and short-term (3 s) windows
CHUNK_FRAMES = 1 << 19


def _k_weighting_sos(sr: int) -> np.ndarray:
    """K-weighting for any sample rate.

    Bilinear transform of the analog prototypes behind BS.1770 (as derived in
    libebur128); at 48 kHz this reproduces the coefficients printed in the
    standard. Stage 1: high-shelf (head acoustics), stage 2: RLB high-pass.
    """
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = np.tan(np.pi * f0 / sr)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / sr)
    a0 = 1 + k / q + k * k
    hp = [1.0, -2.0, 1.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return np.array([shelf, hp])


def _segment_energies(samples: np.ndarray, sr: int) -> np.ndarray:
    """Sum of K-weighted channel power per 100 ms segment, shape (segments,)."""
    sos = _k_weighting_sos(sr)
    seg = int(round(sr * BLOCK_SEGMENT_S))
    channels = samples.shape[1]
    zi = np.zeros((sos.shape[0], 2, channels))
    sums: list[np.ndarray] = []
    carry = np.zeros(0)
    for start in range(0, samples.shape[0], CHUNK_FRAMES):
        block = np.asarray(samples[start : start + CHUNK_FRAMES], dtype=np.float64)
        filtered, zi = signal.sosfilt(sos, block, axis=0, zi=zi)
        power = np.sum(filtered * filtered, axis=1)  # channel weights are 1.0 for mono/stereo
        power = np.concatenate([carry, power])
        n_full = power.shape[0] // seg
        if n_full:
            sums.append(power[: n_full * seg].reshape(n_full, seg).sum(axis=1))
        carry = power[n_full * seg :]
    return np.concatenate(sums) / seg if sums else np.zeros(0)


def _windowed_loudness(seg_energy: np.ndarray, n_segments: int) -> np.ndarray:
    if seg_energy.shape[0] < n_segments:
        if seg_energy.shape[0] == 0:
            return np.zeros(0)
        return np.array([seg_energy.mean()])  # shorter than one window: use what exists
    csum = np.concatenate([[0.0], np.cumsum(seg_energy)])
    mean_power = (csum[n_segments:] - csum[:-n_segments]) / n_segments
    return mean_power


def _gated_integrated(block_power: np.ndarray) -> float:
    if block_power.size == 0:
        return float("-inf")
    loud = -0.691 + 10 * np.log10(block_power + 1e-20)
    abs_gated = block_power[loud > ABS_GATE_LUFS]
    if abs_gated.size == 0:
        return float("-inf")
    rel_threshold = -0.691 + 10 * np.log10(abs_gated.mean()) - 10.0
    gated = block_power[(loud > ABS_GATE_LUFS) & (loud > rel_threshold)]
    if gated.size == 0:
        return float("-inf")
    return float(-0.691 + 10 * np.log10(gated.mean()))


def _loudness_range(short_term_power: np.ndarray) -> float:
    if short_term_power.size < 2:
        return 0.0
    loud = -0.691 + 10 * np.log10(short_term_power + 1e-20)
    abs_gated = short_term_power[loud > ABS_GATE_LUFS]
    if abs_gated.size < 2:
        return 0.0
    rel = -0.691 + 10 * np.log10(abs_gated.mean()) - 20.0
    values = loud[(loud > ABS_GATE_LUFS) & (loud > rel)]
    if values.size < 2:
        return 0.0
    return float(np.percentile(values, 95) - np.percentile(values, 10))


@dataclass
class LoudnessResult:
    integrated_lufs: float
    loudness_range_lu: float
    true_peak_dbtp: float
    sample_peak_dbfs: float
    short_term_lufs: np.ndarray  # 3 s windows at 100 ms hop


def integrated_loudness(samples: np.ndarray, sr: int) -> float:
    samples = _as_2d(samples)
    seg = _segment_energies(samples, sr)
    return _gated_integrated(_windowed_loudness(seg, 4))


def measure(samples: np.ndarray, sr: int) -> LoudnessResult:
    samples = _as_2d(samples)
    seg = _segment_energies(samples, sr)
    momentary = _windowed_loudness(seg, 4)
    short_term = _windowed_loudness(seg, 30)
    return LoudnessResult(
        integrated_lufs=_gated_integrated(momentary),
        loudness_range_lu=_loudness_range(short_term),
        true_peak_dbtp=true_peak_db(samples, sr),
        sample_peak_dbfs=sample_peak_db(samples),
        short_term_lufs=-0.691 + 10 * np.log10(short_term + 1e-20),
    )


def sample_peak_db(samples: np.ndarray) -> float:
    peak = 0.0
    for start in range(0, samples.shape[0], CHUNK_FRAMES):
        peak = max(peak, float(np.abs(samples[start : start + CHUNK_FRAMES]).max(initial=0.0)))
    return 20 * np.log10(peak) if peak > 0 else float("-inf")


def oversample_factor(sr: int) -> int:
    return 4 if sr < 96_000 else (2 if sr < 192_000 else 1)


def true_peak_db(samples: np.ndarray, sr: int) -> float:
    """Inter-sample peak estimate by polyphase oversampling (BS.1770-4 Annex 2)."""
    samples = _as_2d(samples)
    factor = oversample_factor(sr)
    if factor == 1:
        return sample_peak_db(samples)
    pad = 64
    peak = 0.0
    for start in range(0, samples.shape[0], CHUNK_FRAMES):
        lo = max(0, start - pad)
        block = np.asarray(samples[lo : start + CHUNK_FRAMES + pad], dtype=np.float64)
        up = signal.resample_poly(block, factor, 1, axis=0)
        # discard the overlap regions so edges are not double-counted
        a = (start - lo) * factor
        b = a + min(CHUNK_FRAMES, samples.shape[0] - start) * factor
        peak = max(peak, float(np.abs(up[a:b]).max(initial=0.0)))
    return 20 * np.log10(peak) if peak > 0 else float("-inf")


def gain_to_target(current_lufs: float, target_lufs: float, max_gain_db: float = 30.0) -> float:
    """Linear gain bringing ``current_lufs`` to ``target_lufs`` (clamped)."""
    if not np.isfinite(current_lufs):
        return 1.0
    gain_db = float(np.clip(target_lufs - current_lufs, -40.0, max_gain_db))
    return 10 ** (gain_db / 20)


def _as_2d(samples: np.ndarray) -> np.ndarray:
    return samples[:, None] if samples.ndim == 1 else samples
