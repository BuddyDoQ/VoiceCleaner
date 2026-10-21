"""Gap guard: the pauses between words never end up louder than in the original.

Loudness normalization and compression turn the whole recording up, and with it
whatever background noise reduction left in the pauses. Pause clean-up evens that
out against the speech level, but it looks at the overall level only: hiss that
grows in the highs while the rumble below is removed, or a noise in a pause that
comes out louder than it was, gets through. This last stage compares the finished
result with the original, band by band, in the pauses only:

1. Pauses are the same as for pause clean-up (speech detector, with guards before
   words and after phrases, so onsets and decays are never touched).
2. Per frame, levels in a few broad bands are measured (smoothed over ~100 ms) in
   the original and in the result. The rise is the largest of the band increases.
3. Where the rise is above a small tolerance, the frame is turned down by the
   excess, all bands together (the background keeps its own sound). The cut is
   held a little and faded; the fades run in the guard zones beside the words, so
   the whole pause gets the full cut while speech frames are never touched.

It only ever turns down, and only in pauses.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .dsp import iter_power_frames
from .pause_cleanup import RAMP_S, pause_frames

BAND_EDGES_HZ = (60.0, 500.0, 2000.0, 5000.0, 16000.0)  # body, mids, presence, highs
SMOOTH_S = 0.03  # level smoothing: steadies the noise estimate, short enough for a click or rustle
TOLERANCE_DB = 1.0  # rises below this are measurement noise and left alone
MAX_CUT_DB = 40.0
FLOOR_POWER = 1e-12  # -120 dB: below this a band counts as silent in both signals
FFT_SIZE = 1024


@dataclass
class GapRise:
    rise_db: np.ndarray  # per frame: the largest band rise (result minus original), 0 outside pauses
    pauses: np.ndarray  # per frame: inside a pause
    speech: np.ndarray  # per frame: speech (never turned down)

    def worst_db(self, percentile: float = 95.0) -> float:
        """How much louder the pauses got (a high percentile over pause frames), 0 without pauses."""
        return float(np.percentile(self.rise_db[self.pauses], percentile)) if self.pauses.any() else 0.0


def _mono(x: np.ndarray) -> np.ndarray:
    if x.ndim == 1:
        return x
    return x[:, 0] if x.shape[1] == 1 else x.mean(axis=1)


def _band_levels_db(x: np.ndarray, sr: int, hop: int, n_frames: int) -> np.ndarray:
    """Smoothed level per frame and band, (n_frames, bands), in dB."""
    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / sr)
    edges = [e for e in BAND_EDGES_HZ if e < sr / 2] + ([sr / 2] if BAND_EDGES_HZ[-1] >= sr / 2 else [])
    sel = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    power = np.zeros((n_frames, len(sel)))
    mono = _mono(x)
    for f0, spec in iter_power_frames(mono, FFT_SIZE, hop):
        k = min(spec.shape[0], n_frames - f0)
        if k <= 0:
            break
        power[f0 : f0 + k] = np.stack([spec[:k, m].sum(axis=1) for m in sel], axis=1)
    span = max(1, int(round(SMOOTH_S * sr / hop)))
    power = ndimage.uniform_filter1d(power, span, axis=0, mode="nearest")
    return 10 * np.log10(np.maximum(power, FLOOR_POWER))


def gap_rise(original: np.ndarray, processed: np.ndarray, sr: int, speech_mask: np.ndarray,
             mask_hop_s: float) -> GapRise:
    """How much louder than the original each pause frame of ``processed`` is."""
    n = min(original.shape[0], processed.shape[0])
    hop = max(1, int(round(mask_hop_s * sr)))
    n_frames = int(np.ceil(n / hop))
    speech = np.zeros(n_frames, bool)
    m = min(n_frames, speech_mask.shape[0])
    speech[:m] = speech_mask[:m]
    if not speech.any():
        return GapRise(np.zeros(n_frames), np.zeros(n_frames, bool), speech)  # no speech: no pauses to compare
    pauses = pause_frames(speech_mask, n_frames, mask_hop_s)
    if not pauses.any():
        return GapRise(np.zeros(n_frames), pauses, speech)
    rise = (_band_levels_db(processed[:n], sr, hop, n_frames) - _band_levels_db(original[:n], sr, hop, n_frames)).max(axis=1)
    return GapRise(np.where(pauses, rise, 0.0), pauses, speech)


class GapGuard:
    def __init__(self, sr: int):
        self.sr = sr
        self.max_cut_applied_db = 0.0
        self.frames_cut = 0

    def gain_curve_db(self, original: np.ndarray, processed: np.ndarray, speech_mask: np.ndarray,
                      mask_hop_s: float) -> np.ndarray:
        g = gap_rise(original, processed, self.sr, speech_mask, mask_hop_s)
        cut = np.where(g.pauses, np.clip(g.rise_db - TOLERANCE_DB, 0.0, MAX_CUT_DB), 0.0)
        self.frames_cut = int((cut > 0.1).sum())
        self.max_cut_applied_db = float(cut.max()) if cut.size else 0.0
        if not self.frames_cut:
            return np.zeros_like(cut)
        # hold the cut half a fade beyond each frame that needs it (into the guard zones
        # beside words, never into speech), then fade: the whole pause gets the full cut
        ramp = max(1, int(round(RAMP_S / mask_hop_s)))
        # the gain is interpolated between frame centres: frames touching speech stay at 0 dB too
        keep = ndimage.binary_dilation(g.speech, iterations=1)
        cut = ndimage.maximum_filter1d(cut, ramp + 1, mode="nearest") * ~keep
        cut = ndimage.uniform_filter1d(cut, ramp, mode="nearest") * ~keep
        return -cut

    def process(self, original: np.ndarray, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float) -> np.ndarray:
        """Turn ``x`` down in place where its pauses are louder than ``original``'s."""
        curve = self.gain_curve_db(original, x, speech_mask, mask_hop_s)
        if not self.frames_cut:
            return x
        hop = mask_hop_s * self.sr
        centers = (np.arange(curve.size) + 0.5) * hop
        step = 1 << 19
        for a in range(0, x.shape[0], step):
            n = min(step, x.shape[0] - a)
            gain = 10 ** (np.interp(np.arange(a, a + n), centers, curve) / 20)
            x[a : a + n] = np.asarray(x[a : a + n], dtype=np.float32) * gain[:, None].astype(np.float32)
        return x
