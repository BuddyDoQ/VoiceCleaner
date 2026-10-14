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
# shorter background is trusted too when it is clearly steady (fan, hiss, room tone):
# a take that starts 0.6 s before the first word is common
SHORT_RELIABLE_SECONDS = 0.4
SHORT_MIN_STATIONARITY = 0.6
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
        if self.seconds >= MIN_RELIABLE_SECONDS and self.stationarity >= MIN_STATIONARITY:
            return True
        return self.seconds >= SHORT_RELIABLE_SECONDS and self.stationarity >= SHORT_MIN_STATIONARITY

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


# --- background noise before the first word ------------------------------------------------
LEAD_MIN_S = 0.3  # at least this much noise-only audio at the start
LEAD_STEADY_S = 1.0  # a shorter lead-in is only learned when its noise is steady
LEAD_SKIP_S = 0.05  # skip the very first frames (a click when recording starts)
LEAD_MARGIN_S = 0.1  # stop this long before the first word (on top of the speech mask's hangover)
DIGITAL_SILENCE_DB = -90.0


def leading_noise_region(speech_mask: np.ndarray, frame_level_db: np.ndarray, hop_s: float,
                         min_s: float = LEAD_MIN_S) -> tuple[float, float] | None:
    """The background-only stretch before the first word, when there is at least ``min_s``
    of it (after a small margin on both sides) and it holds real noise, not digital silence.
    Recordings with no speech at all return None."""
    if speech_mask.size == 0 or not speech_mask.any():
        return None
    first = int(np.argmax(speech_mask))
    a, b = LEAD_SKIP_S, first * hop_s - LEAD_MARGIN_S
    if b - a < min_s:
        return None
    i0, i1 = int(a / hop_s), int(b / hop_s)
    levels = frame_level_db[i0:i1]
    if levels.size == 0 or np.median(levels) < DIGITAL_SILENCE_DB:
        return None  # nothing to learn from silence
    return (round(a, 3), round(b, 3))


def learn_leading_noise(samples: np.ndarray, sr: int, speech_mask: np.ndarray, frame_level_db: np.ndarray,
                        hop_s: float) -> NoiseProfile | None:
    """The noise profile of the background before the first word, as if the user had
    selected it, or None. A lead-in shorter than LEAD_STEADY_S is only used when its noise
    is steady: a short, changing stretch could hold a breath or a door rather than the noise.

    ``samples`` is mono (1-D) or (frames, channels); only the lead-in is read and mixed
    down, so a long disk-backed recording is never loaded whole."""
    region = leading_noise_region(speech_mask, frame_level_db, hop_s)
    if region is None:
        return None
    lead = np.asarray(samples[: int(np.ceil(region[1] * sr)) + 1], dtype=np.float32)
    if lead.ndim == 2:
        lead = lead.mean(axis=1)
    profile = estimate(lead, sr, [region], source="learned", margin_s=0.0)
    if profile is None:
        return None
    if region[1] - region[0] < LEAD_STEADY_S and profile.stationarity < SHORT_MIN_STATIONARITY:
        return None
    return profile
