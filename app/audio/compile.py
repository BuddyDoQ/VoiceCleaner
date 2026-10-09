"""Compile several takes into one WAV.

For every take:
  1. find where sound starts and ends: the first/last 10 ms frame above a
     threshold that follows the take's own noise floor (see
     :func:`app.audio.pauses.silence_threshold_db`), so enhanced takes with a near
     silent floor and raw takes with audible room noise are both handled;
  2. keep up to ``handle_ms`` (default 100 ms) of the silence before and after
     it, so breaths and soft consonant onsets are not clipped;
  3. optionally shorten long pauses inside the take (off unless chosen, see
     :mod:`app.audio.pauses`);
  4. apply 5 ms fades at the new edges so joins never click.

Takes are converted to a common sample rate and channel count (the first
take's rate, the widest channel layout), optionally levelled to the same
loudness, joined with an optional gap, and passed through the true-peak
limiter so level matching can never clip.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd
from pathlib import Path

import numpy as np
from scipy import signal

from . import loudness
from .limiter import LimiterParams, TruePeakLimiter
from .loader import AudioData, load_wav
from .pauses import PauseSettings, apply_cuts, find_cuts, frame_levels, removed_samples, silence_threshold_db

FRAME_S = 0.01
FADE_S = 0.005
DEFAULT_HANDLE_MS = 100.0


@dataclass
class Take:
    path: Path
    samples: np.ndarray  # (frames, channels) float32, full (untrimmed) take
    sample_rate: int
    start: int = 0  # trimmed region [start, end) in samples
    end: int = 0
    lufs: float = float("-inf")
    overview: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32), repr=False)
    cuts: list = field(default_factory=list)  # long inner pauses to remove, absolute [a, b)

    @property
    def duration(self) -> float:
        return self.samples.shape[0] / self.sample_rate

    @property
    def trimmed_duration(self) -> float:
        """Length after trimming the ends (before pause shortening)."""
        return max(0, self.end - self.start) / self.sample_rate

    @property
    def pause_seconds(self) -> float:
        return removed_samples(self.cuts) / self.sample_rate

    @property
    def final_duration(self) -> float:
        """Length in the compilation: ends trimmed and long pauses shortened."""
        return max(0.0, self.trimmed_duration - self.pause_seconds)

    @property
    def silent(self) -> bool:
        return self.end <= self.start


def detect_sound_bounds(samples: np.ndarray, sr: int) -> tuple[int, int]:
    """Sample range from the first to the last frame that contains sound."""
    level, hop = frame_levels(samples, sr)
    if level.size == 0 or level.max() < -90:  # empty or (near) digital silence
        return 0, 0
    loud = np.flatnonzero(level > silence_threshold_db(level))
    if loud.size == 0:
        return 0, 0
    return int(loud[0] * hop), int(min(samples.shape[0], (loud[-1] + 1) * hop))


def trim_points(samples: np.ndarray, sr: int, handle_ms: float = DEFAULT_HANDLE_MS) -> tuple[int, int]:
    """Trim range keeping at most ``handle_ms`` of silence on either side of the sound."""
    first, last = detect_sound_bounds(samples, sr)
    if last <= first:
        return 0, 0
    handle = int(round(handle_ms / 1000 * sr))
    return max(0, first - handle), min(samples.shape[0], last + handle)


def _overview(samples: np.ndarray, buckets: int = 400) -> np.ndarray:
    mono = np.abs(samples.mean(axis=1) if samples.ndim == 2 else samples)
    if mono.shape[0] == 0:
        return np.zeros(buckets, np.float32)
    edges = np.linspace(0, mono.shape[0], buckets + 1).astype(int)
    return np.array([mono[a:b].max(initial=0.0) for a, b in zip(edges[:-1], edges[1:])], np.float32)


def _pause_cuts(take: Take, pauses: PauseSettings | None) -> list:
    if pauses is None or not pauses.active or take.end <= take.start:
        return []
    inner = find_cuts(take.samples[take.start:take.end], take.sample_rate, pauses.min_pause_s, pauses.keep_ms)
    return [(a + take.start, b + take.start) for a, b in inner]


def load_take(path: Path, handle_ms: float = DEFAULT_HANDLE_MS, pauses: PauseSettings | None = None) -> Take:
    audio: AudioData = load_wav(path)
    x = np.asarray(audio.samples, dtype=np.float32)
    start, end = trim_points(x, audio.sample_rate, handle_ms)
    lufs = loudness.integrated_loudness(x[start:end], audio.sample_rate) if end > start else float("-inf")
    take = Take(Path(path), x, audio.sample_rate, start, end, lufs, _overview(x))
    take.cuts = _pause_cuts(take, pauses)
    return take


def retrim(take: Take, handle_ms: float, pauses: PauseSettings | None = None) -> Take:
    take.start, take.end = trim_points(take.samples, take.sample_rate, handle_ms)
    take.cuts = _pause_cuts(take, pauses)
    return take


def render_take(take: Take) -> np.ndarray:
    """The take as it appears in the compilation (before rate/channel conversion)."""
    x = take.samples[take.start:take.end].copy()
    if take.cuts:
        x = apply_cuts(x, [(a - take.start, b - take.start) for a, b in take.cuts], take.sample_rate)
    return x


def _convert(x: np.ndarray, sr_from: int, sr_to: int, channels: int) -> np.ndarray:
    if sr_from != sr_to:
        g = gcd(sr_from, sr_to)
        x = signal.resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)
    if x.shape[1] != channels:
        x = np.repeat(x[:, :1], channels, axis=1) if x.shape[1] == 1 else x.mean(axis=1, keepdims=True)
    return x


def _fade(x: np.ndarray, sr: int) -> np.ndarray:
    n = min(int(FADE_S * sr), x.shape[0] // 2)
    if n > 1:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)[:, None]
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


@dataclass
class Compilation:
    samples: np.ndarray
    sample_rate: int
    segments: list[tuple[float, float, str]]  # (start s, end s, take name)
    removed_seconds: float  # total: trimmed ends + shortened pauses
    pause_seconds: float = 0.0  # of which: shortened pauses

    @property
    def duration(self) -> float:
        return self.samples.shape[0] / self.sample_rate


def assemble(takes: list[Take], gap_ms: float = 0.0, match_loudness: bool = True,
             ceiling_dbtp: float = -1.0, progress=None) -> Compilation:
    """Join the trimmed takes in order into one signal."""
    usable = [t for t in takes if not t.silent]
    if not usable:
        raise ValueError("None of the takes contains audible sound.")
    sr = usable[0].sample_rate
    channels = max(t.samples.shape[1] for t in usable)
    target = float(np.median([t.lufs for t in usable if np.isfinite(t.lufs)])) \
        if match_loudness and any(np.isfinite(t.lufs) for t in usable) else None
    gap = np.zeros((int(round(gap_ms / 1000 * sr)), channels), np.float32)
    parts, segments, pos = [], [], 0
    removed = pauses = 0.0
    for i, t in enumerate(usable):
        x = _fade(_convert(render_take(t), t.sample_rate, sr, channels), sr)
        if target is not None and np.isfinite(t.lufs):
            x *= np.float32(10 ** ((target - t.lufs) / 20))
        if i and gap.shape[0]:
            parts.append(gap)
            pos += gap.shape[0]
        parts.append(x)
        segments.append((pos / sr, (pos + x.shape[0]) / sr, t.path.name))
        pos += x.shape[0]
        removed += t.duration - t.final_duration
        pauses += t.pause_seconds
        if progress:
            progress((i + 1) / len(usable) * 0.8)
    out = np.concatenate(parts).astype(np.float32)
    out = TruePeakLimiter(sr, LimiterParams(ceiling_dbtp=ceiling_dbtp)).process(out)
    if progress:
        progress(1.0)
    return Compilation(out, sr, segments, removed, pauses)
