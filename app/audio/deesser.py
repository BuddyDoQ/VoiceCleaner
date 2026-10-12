"""De-esser: turns down only the harsh sibilant moments ("s", "sh", "t", "ch").

Compression and leveling lift consonants relative to vowels, and noise reduction leaves
sibilants exposed, which makes a voice sound harsh. A static EQ cut would dull the whole
voice; this reduces the 4.5-10 kHz band only while it is unusually strong compared with
the rest of the voice, by up to a few dB.

The band is split with a zero-phase filter, so subtracting part of it leaves the rest of
the signal untouched (no phase smear, no change when nothing is reduced).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

BAND_HZ = (4500.0, 10000.0)


@dataclass
class DeEsserParams:
    amount: float = 0.5  # 0 = off .. 1 = strong
    max_reduction_db: float = 8.0  # at amount 1

    @property
    def active(self) -> bool:
        return self.amount > 0.01


class DeEsser:
    def __init__(self, sr: int, params: DeEsserParams):
        self.sr, self.p = sr, params
        hi = min(BAND_HZ[1], 0.45 * sr)
        self.sos = signal.butter(2, [BAND_HZ[0], hi], btype="bandpass", fs=sr, output="sos")
        self.max_reduction_db = 0.0  # deepest reduction applied, for notes and tests

    def _envelope(self, x: np.ndarray, ms: float) -> np.ndarray:
        n = max(1, int(self.sr * ms / 1000))
        k = np.ones(n) / n
        return np.sqrt(np.convolve(x * x, k, mode="same") + 1e-12)

    def gain_db(self, mono: np.ndarray, band: np.ndarray) -> np.ndarray:
        """Reduction in dB (>= 0) per sample."""
        ratio = 20 * np.log10(self._envelope(band, 5) / self._envelope(mono, 5))
        level = 20 * np.log10(self._envelope(mono, 5))
        voiced = level > level.max() - 45  # ignore pauses
        if voiced.sum() < self.sr * 0.2:
            return np.zeros_like(mono)
        # sibilance = the band clearly stronger than it usually is in this voice
        thresh = np.percentile(ratio[voiced], 80) + 2.0
        over = np.clip(ratio - thresh, 0, None) * voiced
        gr = np.minimum(over * (0.4 + 0.6 * self.p.amount), self.p.max_reduction_db * self.p.amount)
        # fast attack, smooth release (~40 ms)
        rel = np.exp(-1.0 / (0.040 * self.sr))
        smoothed = signal.lfilter([1 - rel], [1, -rel], gr)
        return np.maximum(gr, smoothed)

    def process(self, x: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        if not self.p.active:
            out[:] = x
            return out
        chunk = int(30 * self.sr)
        for a in range(0, x.shape[0], chunk):
            seg = np.asarray(x[a: a + chunk], dtype=np.float64)
            band = signal.sosfiltfilt(self.sos, seg, axis=0)
            mono, bmono = seg.mean(axis=1), band.mean(axis=1)
            gr = self.gain_db(mono, bmono)
            self.max_reduction_db = max(self.max_reduction_db, float(gr.max(initial=0.0)))
            keep = 10 ** (-gr / 20)
            out[a: a + chunk] = (seg - band * (1.0 - keep)[:, None]).astype(np.float32)
        return out

