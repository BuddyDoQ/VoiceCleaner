"""Clipping repair: rebuilding the peaks that were cut off when a recording was made too loud.

Where a recording clipped, the waveform's tops are sliced flat at the clipping level; the
real peaks are missing. This restores them with consistent iterative hard thresholding
(sparse declipping, after Kitić, Jacques, Madhu, Hopwood & Gribonval, "Consistent iterative
hard thresholding for signal declipping", ICASSP 2013):

* every sample that did not clip is kept exactly as recorded;
* in short frames around the clipped stretches, the frame is rebuilt from its strongest
  frequency components, allowing one more component per iteration, until the rebuilt
  waveform agrees with what was recorded;
* the rebuilt clipped samples must lie *beyond* the clipping level (above it at positive
  peaks, below it at negative ones): that is what clipping tells us about the lost peaks.

Only frames that contain clipping are processed (all of them at once, vectorised), so
clean audio is untouched and the cost is small. The result can exceed full scale, as the
real peaks did; the pipeline's leveling and limiter bring it back within range.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME = 1024  # samples per frame (about 21 ms at 48 kHz)
HOP = FRAME // 4
OVERSAMPLE = 2  # zero-padded DFT: a redundant dictionary, as in SPADE
MAX_ITERATIONS = 120
TOLERANCE = 2e-3  # relative mismatch at which a frame counts as consistent
MIN_RUN = 3  # as in analyzer.detect_clipping: shorter runs at the peak are natural crests
LEVEL_FRACTION = 0.98  # within 2 % of the peak counts as clipped (dither added after the clip)


@dataclass
class DeclipReport:
    clipped_samples: int = 0
    frames: int = 0
    iterations: int = 0


def clip_mask(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Samples of one channel in flat-topped runs at its extreme values, and the clip level."""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 0.25:
        return np.zeros(x.shape, bool), peak
    level = peak * LEVEL_FRACTION
    hot = np.abs(x) >= level
    padded = np.concatenate([[False], hot, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    mask = np.zeros(x.shape, bool)
    runs = 0
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= MIN_RUN:
            mask[a:b] = True
            runs += 1
    # the analyzer's rule (AnalysisResult.is_clipped): a natural recording reaches its peak
    # once or twice; clipping flattens it again and again
    if not (mask.sum() > 1e-4 * x.size or runs > 20):
        mask[:] = False
    return mask, level


def _top_k(spec: np.ndarray, k: int) -> np.ndarray:
    """Keep the ``k`` largest coefficients of each row of ``spec``."""
    mag = np.abs(spec)
    k = int(min(max(k, 1), spec.shape[1]))
    thr = np.partition(mag, spec.shape[1] - k, axis=1)[:, spec.shape[1] - k]  # k-th largest per row
    return np.where(mag >= thr[:, None], spec, 0)


def _declip_frames(y: np.ndarray, pos: np.ndarray, neg: np.ndarray, level: float, report: DeclipReport) -> np.ndarray:
    """Consistent IHT on a stack of frames ``y`` (frames x FRAME)."""
    n_fft = FRAME * OVERSAMPLE
    reliable = ~(pos | neg)
    x = y.copy()
    k = 1  # every frame keeps the same number of components (they all start together)
    active = np.ones(len(y), bool)
    scale = np.maximum(np.sqrt(np.mean(y ** 2, axis=1)), 1e-6)
    for it in range(MAX_ITERATIONS):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        spec = np.fft.rfft(x[idx], n=n_fft, axis=1)
        approx = np.fft.irfft(_top_k(spec, k), n=n_fft, axis=1)[:, :FRAME]
        # project onto what the recording allows: reliable samples as recorded,
        # clipped ones beyond the clipping level
        proj = np.where(reliable[idx], y[idx], approx)
        proj = np.where(pos[idx], np.maximum(approx, level), proj)
        proj = np.where(neg[idx], np.minimum(approx, -level), proj)
        x[idx] = proj
        mismatch = np.sqrt(np.mean((approx - proj) ** 2, axis=1)) / scale[idx]
        done = mismatch < TOLERANCE
        active[idx[done]] = False
        k += max(1, k // 10)  # more components each round, faster as it grows
        report.iterations = it + 1
    return x


def declip(samples: np.ndarray, report: DeclipReport | None = None, out: np.ndarray | None = None,
           progress=None, check=None) -> np.ndarray:
    """``samples`` (frames x channels) with the clipped peaks rebuilt, in ``out`` (a new
    array when None; the input is never changed). Unclipped audio comes back identical.
    ``progress(fraction)`` reports, ``check()`` may raise to cancel."""
    report = report if report is not None else DeclipReport()
    if out is None:
        out = np.array(samples, dtype=np.float32, copy=True)
    else:
        out[:] = samples
    n = out.shape[0]
    if n < FRAME:
        return out
    window = np.hanning(FRAME + 2)[1:-1].astype(np.float64)
    for ch in range(out.shape[1]):
        x = out[:, ch].astype(np.float64)
        mask, level = clip_mask(x)
        if not mask.any():
            continue
        report.clipped_samples += int(mask.sum())
        starts = np.arange(0, n - FRAME + 1, HOP)
        if starts[-1] != n - FRAME:
            starts = np.append(starts, n - FRAME)
        has_clip = np.array([mask[s:s + FRAME].any() for s in starts])
        starts = starts[has_clip]
        report.frames += len(starts)
        for a in range(0, len(starts), 256):  # bounded memory on long, badly clipped files
            if check is not None:
                check()
            if progress is not None:
                progress((ch + a / max(1, len(starts))) / out.shape[1])
            batch = starts[a:a + 256]
            idx = batch[:, None] + np.arange(FRAME)[None, :]
            y, m = x[idx], mask[idx]
            rebuilt = _declip_frames(y, m & (y > 0), m & (y < 0), level, report)
            acc = np.zeros(n)
            wsum = np.zeros(n)
            np.add.at(acc, idx, rebuilt * window)
            np.add.at(wsum, idx, np.broadcast_to(window, rebuilt.shape))
            touched = wsum > 1e-9
            # only clipped samples change; everything else stays exactly as recorded
            change = touched & mask
            x[change] = acc[change] / wsum[change]
        out[:, ch] = x.astype(np.float32)
    return out
