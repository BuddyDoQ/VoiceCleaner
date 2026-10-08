"""Dynamic leveling: slow, speech-aware gain riding.

A compressor reacts to syllables (milliseconds). Leveling works on a scale
of seconds: it brings a quiet sentence, a speaker who leaned away from the
microphone or a softer second voice up to the same loudness as the rest,
the way a broadcast engineer "rides the fader".

1. Short-term loudness (K-weighted, 400 ms windows, 100 ms hop) is measured.
2. Only windows that contain speech decide the gain; in pauses the gain is
   frozen, so background noise is never pumped up between sentences.
3. The wanted correction (target minus local loudness, limited to
   +-``range_db``) is smoothed forward *and* backward (zero lag; the
   processing is offline, so it can anticipate a level change instead of
   reacting late).
4. The resulting slow gain curve is applied to all channels together.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from .loudness import _k_weighting_sos

HOP_S = 0.1
WINDOW_S = 0.4


@dataclass
class LevelerParams:
    amount: float = 0.5  # 0..1, fraction of the correction applied
    range_db: float = 10.0  # maximum boost / cut
    speed_s: float = 2.0  # smoothing time constant (smaller = faster riding)

    @property
    def active(self) -> bool:
        return self.amount > 0.01 and self.range_db > 0.1


class Leveler:
    def __init__(self, sr: int, params: LevelerParams):
        self.sr = sr
        self.p = params
        self.max_boost_db = 0.0
        self.max_cut_db = 0.0

    def gain_curve_db(self, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float) -> np.ndarray:
        """Gain in dB per 100 ms hop."""
        sr = self.sr
        hop = int(round(HOP_S * sr))
        sos = _k_weighting_sos(sr)
        zi = np.zeros((sos.shape[0], 2, x.shape[1]))
        powers = []
        carry = np.zeros(0)
        for a in range(0, x.shape[0], 1 << 19):
            block, zi = signal.sosfilt(sos, np.asarray(x[a : a + (1 << 19)], dtype=np.float64), axis=0, zi=zi)
            pw = np.concatenate([carry, np.sum(block * block, axis=1)])
            n = pw.shape[0] // hop
            powers.append(pw[: n * hop].reshape(n, hop).mean(axis=1))
            carry = pw[n * hop :]
        seg = np.concatenate(powers) if powers else np.zeros(0)
        if seg.size < 5:
            return np.zeros(max(1, seg.size))
        win = int(round(WINDOW_S / HOP_S))
        st = np.convolve(seg, np.ones(win) / win, mode="same")
        loud = -0.691 + 10 * np.log10(st + 1e-12)

        # which hops are speech (majority of the 10 ms mask frames)
        ratio = int(round(HOP_S / mask_hop_s))
        m = speech_mask[: seg.size * ratio]
        m = np.pad(m, (0, seg.size * ratio - m.shape[0]))
        speech = m.reshape(seg.size, ratio).mean(axis=1) > 0.5
        # ignore very quiet "speech" hops (breaths, lip noise), which must not be boosted
        if speech.sum() < 5:
            return np.zeros(seg.size)
        ref = float(np.median(loud[speech]))
        speech &= loud > ref - 20

        want = np.clip(ref - loud, -self.p.range_db, self.p.range_db) * self.p.amount
        # freeze across pauses: carry the last speech value forward, first value backward
        idx = np.where(speech, np.arange(seg.size), -1)
        idx = np.maximum.accumulate(idx)
        first = int(np.argmax(speech))
        idx[idx < 0] = first
        held = want[idx]
        # zero-lag smoothing: forward-backward one-pole, time constant speed_s
        a = np.exp(-HOP_S / max(self.p.speed_s, 0.2))
        smooth = signal.filtfilt([1 - a], [1, -a], held, padtype="odd", padlen=min(held.size - 1, 50))
        # never exceed the requested range after smoothing overshoot
        smooth = np.clip(smooth, -self.p.range_db, self.p.range_db)
        self.max_boost_db = float(smooth.max(initial=0.0))
        self.max_cut_db = float(smooth.min(initial=0.0))
        return smooth

    def process(self, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float, out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        if not self.p.active or not speech_mask.size:
            if out is not x:
                out[:] = x
            return out
        curve = self.gain_curve_db(x, speech_mask, mask_hop_s)
        hop = int(round(HOP_S * self.sr))
        centers = (np.arange(curve.size) + 0.5) * hop
        step = 1 << 19
        for a in range(0, x.shape[0], step):
            n = min(step, x.shape[0] - a)
            g = 10 ** (np.interp(np.arange(a, a + n), centers, curve) / 20)
            out[a : a + n] = np.asarray(x[a : a + n], dtype=np.float32) * g[:, None].astype(np.float32)
        return out

