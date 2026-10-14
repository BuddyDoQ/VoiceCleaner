"""Pause clean-up: residual background turned down between phrases.

The AI model keeps its gains open for a while after speech (its memory of
the last word), so noise right after a phrase, and above all at the end of
a recording, is removed much less than before the first word. The
compressor then lifts all pauses a little more. This stage evens that out.

1. Pauses are taken from the analysis' speech detector (not from a level
   threshold, so a soft word is never mistaken for a pause), with a guard
   before each word and after each phrase so onsets and decays stay intact.
2. Each pause gets one steady gain: enough to bring its background down to a room
   tone ``depth`` below the speech, limited to ``max_cut``. The background is
   the pause's quieter frames (20th percentile), so a breath, click or laugh
   does not decide it. One gain per pause keeps the room tone's own texture
   and never pumps.
3. A sound in a pause that stands out from its background and is not far
   below the speech (a laugh, a breath, a word the detector missed) is left
   as it is; the background around it is still turned down.
4. Gain changes are faded in and out over a few tens of milliseconds.

It only ever turns down, and never to digital silence.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

PRE_GUARD_S = 0.05  # untouched before a word (the speech mask already has 150 ms of hangover)
POST_GUARD_S = 0.10  # untouched after a phrase, for soft word endings
MIN_PAUSE_S = 0.12  # shorter gaps between words are left alone
RAMP_S = 0.05
FRAME_S = 0.02  # level measurement frames
PROTECT_DB = 20.0  # a sound in a pause within this of the speech level is not touched ...
PROTECT_SPAN_DB = 8.0  # ... (partly up to this much further below) ...
PROTECT_CONTRAST_DB = 8.0  # ... when it stands out this far from the pause's own background
PROTECT_HOLD_S = 0.04  # protection reaches this far either side of such a sound


@dataclass
class PauseCleanupParams:
    amount: float = 0.6  # 0..1, 0 = off

    @property
    def active(self) -> bool:
        return self.amount > 0.01

    @property
    def depth_db(self) -> float:
        """How far below the speech the background in pauses ends up."""
        return 40.0 + 25.0 * self.amount

    @property
    def max_cut_db(self) -> float:
        return 12.0 + 24.0 * self.amount


class PauseCleaner:
    def __init__(self, sr: int, params: PauseCleanupParams):
        self.sr = sr
        self.p = params
        self.pauses = 0  # pauses turned down by the last process()
        self.max_cut_applied_db = 0.0

    def gain_curve_db(self, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float) -> np.ndarray:
        """Gain in dB per speech-mask frame (``mask_hop_s``), for the length of ``x``."""
        self.pauses, self.max_cut_applied_db = 0, 0.0
        n_frames = int(np.ceil(x.shape[0] / self.sr / mask_hop_s))
        gain = np.zeros(n_frames)
        if not self.p.active or speech_mask.size == 0 or not speech_mask.any():
            return gain  # no speech: nothing to compare the background with
        speech = np.zeros(n_frames, bool)
        m = min(n_frames, speech_mask.shape[0])
        speech[:m] = speech_mask[:m]

        level = self._frame_levels_db(x, mask_hop_s, n_frames)
        live = np.isfinite(level)
        sp = speech & live
        if sp.sum() < 10:
            return gain
        # speech level: energy mean over speech frames (what a listener calls "the voice level")
        speech_db = float(10 * np.log10(np.mean(10 ** (level[sp] / 10))))

        pre, post = int(round(PRE_GUARD_S / mask_hop_s)), int(round(POST_GUARD_S / mask_hop_s))
        guarded = speech.copy()
        if post:
            guarded |= ndimage.binary_dilation(speech, structure=np.r_[np.zeros(post), np.ones(post + 1)].astype(bool))
        if pre:
            guarded |= ndimage.binary_dilation(speech, structure=np.r_[np.ones(pre + 1), np.zeros(pre)].astype(bool))
        labels, count = ndimage.label(~guarded)
        min_len = int(round(MIN_PAUSE_S / mask_hop_s))
        target = speech_db - self.p.depth_db
        for i, sl in enumerate(ndimage.find_objects(labels), 1):
            idx = sl[0]
            if idx.stop - idx.start < min_len:
                continue
            lv = level[idx][live[idx]]
            if lv.size == 0:
                continue  # digital silence: nothing to turn down
            background = float(np.percentile(lv, 20))
            cut = min(self.p.max_cut_db, max(0.0, background - target))
            if cut < 0.5:
                continue
            seg = np.where(live[idx], level[idx], -np.inf)
            loud = np.clip((seg - (speech_db - PROTECT_DB - PROTECT_SPAN_DB)) / PROTECT_SPAN_DB, 0.0, 1.0)
            loud[seg < background + PROTECT_CONTRAST_DB] = 0.0  # steady background is never protected
            hold = 2 * int(round(PROTECT_HOLD_S / mask_hop_s)) + 1
            protect = ndimage.maximum_filter1d(loud, hold, mode="nearest")
            gain[idx] = -cut * (1.0 - protect)
            self.pauses += 1
            self.max_cut_applied_db = max(self.max_cut_applied_db, cut)
        if self.pauses:
            # fade: a cut never starts or ends abruptly; the guards leave room for the ramp
            ramp = max(1, int(round(RAMP_S / mask_hop_s)))
            gain = np.minimum(ndimage.uniform_filter1d(gain, ramp, mode="nearest"), 0.0)
        return gain

    def process(self, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float,
                out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        curve = self.gain_curve_db(x, speech_mask, mask_hop_s)
        if not self.pauses:
            if out is not x:
                out[:] = x
            return out
        hop = mask_hop_s * self.sr
        centers = (np.arange(curve.size) + 0.5) * hop
        step = 1 << 19
        for a in range(0, x.shape[0], step):
            n = min(step, x.shape[0] - a)
            g = 10 ** (np.interp(np.arange(a, a + n), centers, curve) / 20)
            out[a : a + n] = np.asarray(x[a : a + n], dtype=np.float32) * g[:, None].astype(np.float32)
        return out

    def _frame_levels_db(self, x: np.ndarray, mask_hop_s: float, n_frames: int) -> np.ndarray:
        """Level per mask frame (mean square over ~FRAME_S around it); -inf for digital silence.
        Streams through ``x`` so disk-backed recordings are never loaded whole."""
        hop = max(1, int(round(mask_hop_s * self.sr)))
        power = np.zeros(n_frames)
        step = (1 << 19) // hop * hop
        for a in range(0, x.shape[0], step):
            seg = np.asarray(x[a : a + step], dtype=np.float64)
            seg = seg.mean(axis=1) if seg.ndim == 2 else seg
            k = int(np.ceil(seg.shape[0] / hop))
            seg = np.pad(seg, (0, k * hop - seg.shape[0]))
            f0 = a // hop
            power[f0 : f0 + k] = (seg.reshape(k, hop) ** 2).mean(axis=1)[: n_frames - f0]
        span = max(1, int(round(FRAME_S / mask_hop_s)) + 1)
        power = ndimage.uniform_filter1d(power, span, mode="nearest")
        out = np.full(n_frames, -np.inf)
        ok = power > 1e-14
        out[ok] = 10 * np.log10(power[ok])
        return out
