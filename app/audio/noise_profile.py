"""Noise profile estimation.

A noise profile is the average power spectrum of background-only audio,
computed with exactly the same STFT the denoiser uses. It can come from

* the speech-free regions found by the analyzer (automatic), or
* a region the user selected and asked to "Learn Noise Profile".

A profile is only trusted when there is enough background audio and the
background is reasonably steady; otherwise the pipeline relies on the AI
model, which needs no profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .denoise import StftConfig, stft
from .dsp import EPS

MIN_RELIABLE_SECONDS = 0.75
MIN_STATIONARITY = 0.35
MAX_PROFILE_SECONDS = 60.0


@dataclass
class NoiseProfile:
    psd: np.ndarray  # (bins,) mean power per STFT bin
    sr: int
    cfg: StftConfig
    seconds: float
    source: str  # "auto" | "learned"
    stationarity: float  # 0..1
    regions: list = field(default_factory=list)  # (start, end) seconds the profile came from

    @property
    def reliable(self) -> bool:
        if self.source == "learned":
            return self.seconds >= 0.3
        return self.seconds >= MIN_RELIABLE_SECONDS and self.stationarity >= MIN_STATIONARITY

    @property
    def level_dbfs(self) -> float:
        # scipy "spectrum" scaling with a Hann window: one-sided bin powers
        # sum to 0.75 x the mean square of the signal
        return float(10 * np.log10(self.psd.sum() / 0.75 + EPS))

    def describe(self) -> str:
        kind = "Learned from your selection" if self.source == "learned" else "Detected automatically"
        return f"{kind} • {self.seconds:.1f} s of background"


def estimate(mono: np.ndarray, sr: int, regions: list[tuple[float, float]], source: str = "auto",
             margin_s: float = 0.05) -> NoiseProfile | None:
    """Average spectrum over ``regions`` (seconds) of ``mono``."""
    cfg = StftConfig.for_rate(sr)
    frames_power = []
    total = 0.0
    for a, b in regions:
        a, b = a + margin_s, b - margin_s
        if b - a < cfg.nfft / sr * 2:
            continue
        seg = np.asarray(mono[int(a * sr) : int(b * sr)], dtype=np.float32)
        z = stft(seg, sr, cfg)[:, 2:-2]  # drop frames touching the region edges
        if z.shape[1] == 0:
            continue
        frames_power.append(np.abs(z) ** 2)
        total += b - a
        if total >= MAX_PROFILE_SECONDS:
            break
    if not frames_power:
        return None
    power = np.concatenate(frames_power, axis=1)
    # mean is the right estimate for a Wiener filter; a robust trim of the
    # top 10 % per bin keeps occasional clicks/breaths from inflating it
    hi = np.percentile(power, 90, axis=1, keepdims=True)
    psd = np.where(power <= hi, power, hi).mean(axis=1)
    # bin-wise level fluctuation over time -> stationarity
    if power.shape[1] >= 8:
        bands = _octave_bands(power)
        spread = float(np.median(np.std(10 * np.log10(bands + EPS), axis=1)))
        stationarity = float(np.clip(1.0 - (spread - 1.5) / 5.0, 0.0, 1.0))
    else:
        stationarity = 0.0
    return NoiseProfile(psd, sr, cfg, total, source, stationarity, list(regions))


def _octave_bands(power: np.ndarray) -> np.ndarray:
    n = power.shape[0]
    edges = np.unique(np.clip(np.round(np.geomspace(2, n, 12)).astype(int), 1, n))
    return np.stack([power[a:b].mean(axis=0) for a, b in zip(edges[:-1], edges[1:]) if b > a])
