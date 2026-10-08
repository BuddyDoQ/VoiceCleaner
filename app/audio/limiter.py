"""True-peak lookahead limiter (final stage before export).

1. Estimate the true (inter-sample) peak of every sample by 4x polyphase
   oversampling, as BS.1770 meters do.
2. Required gain = min(1, ceiling / true_peak).
3. A sliding minimum over +-lookahead followed by a moving average gives a
   smooth gain that starts falling *before* each peak and is guaranteed to
   be at or below the required gain at the peak itself.
4. Release is a one-pole recovery on a coarse control grid.
5. A final verification pass trims any residual overshoot.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

from .loudness import oversample_factor, true_peak_db

CHUNK_S = 20.0


@dataclass
class LimiterParams:
    ceiling_dbtp: float = -1.0
    lookahead_ms: float = 2.0
    release_ms: float = 80.0


def _true_peak_envelope(x: np.ndarray, sr: int) -> np.ndarray:
    """Per-sample true-peak magnitude (max over channels and oversampled phases)."""
    factor = oversample_factor(sr)
    mono_abs = np.abs(x).max(axis=1)
    if factor == 1:
        return mono_abs
    up = signal.resample_poly(x, factor, 1, axis=0)
    env = np.abs(up).max(axis=1)
    env = env[: x.shape[0] * factor].reshape(-1, factor).max(axis=1)
    return np.maximum(env, mono_abs)


class TruePeakLimiter:
    def __init__(self, sr: int, params: LimiterParams):
        self.sr = sr
        self.p = params
        self.max_reduction_db = 0.0

    def process(self, x: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        sr = self.sr
        ceiling = 10 ** (self.p.ceiling_dbtp / 20)
        la = max(1, int(self.p.lookahead_ms / 1000 * sr))
        ctrl = max(1, int(0.001 * sr))
        rel_coef = np.exp(-1.0 / max(1e-3, self.p.release_ms / 1000 * sr / ctrl))
        chunk = int(CHUNK_S * sr)
        margin = la * 2 + 64
        n = x.shape[0]
        state = 1.0
        worst = 1.0
        for a in range(0, n, chunk):
            b = min(n, a + chunk)
            lo, hi = max(0, a - margin), min(n, b + margin)
            seg = np.asarray(x[lo:hi], dtype=np.float32)
            env = _true_peak_envelope(seg, sr)
            need = np.minimum(1.0, ceiling / np.maximum(env, 1e-9))
            g = ndimage.minimum_filter1d(need, size=2 * la + 1, mode="nearest")
            g = ndimage.uniform_filter1d(g, size=la + 1, mode="nearest")
            g = np.minimum(g, need)  # numerical safety at the very peaks
            g = g[a - lo : a - lo + (b - a)]
            # release on a 1 ms control grid
            nb = int(np.ceil(g.shape[0] / ctrl))
            padded = np.pad(g, (0, nb * ctrl - g.shape[0]), constant_values=1.0)
            gmin = padded.reshape(nb, ctrl).min(axis=1)
            sm = np.empty(nb)
            for i, t in enumerate(gmin):
                state = t if t < state else t + rel_coef * (state - t)
                sm[i] = state
            curve = np.interp(np.arange(g.shape[0]), (np.arange(nb) + 0.5) * ctrl, sm)
            final = np.minimum(curve, g)
            worst = min(worst, float(final.min(initial=1.0)))
            out[a:b] = seg[a - lo : a - lo + (b - a)] * final[:, None].astype(np.float32)
        self.max_reduction_db = float(20 * np.log10(max(worst, 1e-9)))
        # verify; resampling estimates can be off by a hair
        tp = true_peak_db(out, sr)
        if tp > self.p.ceiling_dbtp:
            trim = 10 ** ((self.p.ceiling_dbtp - tp - 0.05) / 20)
            for a in range(0, n, chunk):
                out[a : a + chunk] *= trim
        for a in range(0, n, chunk):  # sample-peak backstop; true peak is already below
            np.clip(out[a : a + chunk], -ceiling, ceiling, out=out[a : a + chunk])
        return out
