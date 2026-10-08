"""Gentle speech dynamics: compressor and downward expander.

Both work on a 1 ms control grid: the level detector and the attack/release
smoothing run per block (cheap, even for hour-long files), and the gain
curve is interpolated back to sample rate so changes stay smooth. Stereo
channels share one gain (linked) so the image never shifts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CONTROL_BLOCK_S = 0.001
STREAM_BLOCK_S = 30.0


@dataclass
class CompressorParams:
    amount: float = 0.5  # 0..1; scales the gain reduction (0 = bypass)
    threshold_db: float = -24.0  # dBFS, signal pre-normalised to the loudness target
    ratio: float = 2.5
    attack_ms: float = 12.0
    release_ms: float = 150.0
    knee_db: float = 8.0
    rms_window_ms: float = 10.0


@dataclass
class GateParams:
    threshold_db: float = -80.0  # dBFS; <= -80 disables
    ratio: float = 2.0  # downward expansion ratio
    range_db: float = 12.0  # never reduce more than this
    attack_ms: float = 3.0
    release_ms: float = 120.0
    hold_ms: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.threshold_db > -79.5


def _block_levels_db(x: np.ndarray, block: int, window_blocks: int) -> np.ndarray:
    """RMS level per control block (max over channels), smoothed over a short window."""
    n = x.shape[0]
    nb = int(np.ceil(n / block))
    pad = nb * block - n
    sq = np.asarray(x, dtype=np.float32) ** 2
    if pad:
        sq = np.pad(sq, ((0, pad), (0, 0)))
    ms = sq.reshape(nb, block, -1).mean(axis=1).max(axis=1)
    if window_blocks > 1:
        kernel = np.ones(window_blocks) / window_blocks
        ms = np.convolve(ms, kernel, mode="same")
    return 10 * np.log10(ms + 1e-12)


def _smooth_gain(target_db: np.ndarray, attack_blocks: float, release_blocks: float, state: float) -> tuple[np.ndarray, float]:
    """One-pole attack/release smoothing of a gain (dB, <= 0) curve."""
    a_att = np.exp(-1.0 / max(attack_blocks, 1e-3))
    a_rel = np.exp(-1.0 / max(release_blocks, 1e-3))
    out = np.empty_like(target_db)
    g = state
    for i, t in enumerate(target_db):
        coef = a_att if t < g else a_rel
        g = t + coef * (g - t)
        out[i] = g
    return out, g


def _apply_gain_blocks(x: np.ndarray, gain_db: np.ndarray, block: int) -> np.ndarray:
    n = x.shape[0]
    centers = (np.arange(gain_db.shape[0]) + 0.5) * block
    g = 10 ** (np.interp(np.arange(n), centers, gain_db) / 20)
    return (x * g[:, None].astype(np.float32)).astype(np.float32)


class Compressor:
    def __init__(self, sr: int, params: CompressorParams):
        self.sr = sr
        self.p = params
        self.block = max(1, int(sr * CONTROL_BLOCK_S))
        self.gain_reduction_db: list[float] = []

    def gain_computer(self, level_db: np.ndarray) -> np.ndarray:
        p = self.p
        over = level_db - p.threshold_db
        slope = 1.0 - 1.0 / max(p.ratio, 1.0)
        knee = max(p.knee_db, 1e-3)
        gr = np.where(
            over <= -knee / 2, 0.0,
            np.where(over >= knee / 2, -slope * over, -slope * (over + knee / 2) ** 2 / (2 * knee)),
        )
        return gr * float(np.clip(p.amount, 0, 1))

    def process(self, x: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        if self.p.amount <= 0.001 or self.p.ratio <= 1.0:
            out[:] = x
            return out
        step = int(STREAM_BLOCK_S * self.sr) // self.block * self.block
        state = 0.0
        win = max(1, int(self.p.rms_window_ms / 1000 * self.sr / self.block))
        att = self.p.attack_ms / 1000 * self.sr / self.block
        rel = self.p.release_ms / 1000 * self.sr / self.block
        worst = 0.0
        for a in range(0, x.shape[0], step):
            seg = np.asarray(x[a : a + step], dtype=np.float32)
            level = _block_levels_db(seg, self.block, win)
            gr, state = _smooth_gain(self.gain_computer(level), att, rel, state)
            worst = min(worst, float(gr.min(initial=0.0)))
            out[a : a + step] = _apply_gain_blocks(seg, gr, self.block)
        self.gain_reduction_db = [worst]
        return out


class NoiseGate:
    """Soft downward expander: quiet passages are turned down a limited amount.

    Unlike a hard gate it never cuts to silence, so breaths and room tone
    remain natural; it only helps push residual background further down.
    """

    def __init__(self, sr: int, params: GateParams):
        self.sr = sr
        self.p = params
        self.block = max(1, int(sr * CONTROL_BLOCK_S))

    def process(self, x: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if out is None:
            out = np.empty_like(x, dtype=np.float32)
        if not self.p.enabled:
            out[:] = x
            return out
        p = self.p
        step = int(STREAM_BLOCK_S * self.sr) // self.block * self.block
        hold = max(1, int(p.hold_ms / 1000 * self.sr / self.block))
        state = 0.0
        for a in range(0, x.shape[0], step):
            seg = np.asarray(x[a : a + step], dtype=np.float32)
            level = _block_levels_db(seg, self.block, 20)
            # hold: a block counts as "open" if any block within hold time was above threshold
            above = np.convolve((level > p.threshold_db).astype(float), np.ones(2 * hold + 1), mode="same") > 0
            under = np.minimum(level - p.threshold_db, 0.0)
            target = np.where(above, 0.0, np.maximum(under * (p.ratio - 1.0), -p.range_db))
            # closing (gain falling) uses the slow release, opening uses the fast attack
            gr, state = _smooth_gain(target, p.release_ms / 1000 * self.sr / self.block,
                                     p.attack_ms / 1000 * self.sr / self.block, state)
            out[a : a + step] = _apply_gain_blocks(seg, gr, self.block)
        return out
