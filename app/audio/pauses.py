"""Shortening long pauses inside a recording.

A pause is a run of 10 ms frames below :func:`silence_threshold_db` (the same
noise-floor-relative threshold used to trim takes). Only pauses *inside* the
recording count; silence before the first and after the last sound is left to
the trimming step.

Every pause longer than ``min_pause_s`` is shortened to ``keep_ms``: half of the
kept pause comes from the start of the silence (breath tails, room decay) and
half from its end (the lead-in to the next word), joined with a 20 ms
equal-gain crossfade so the room tone continues without clicks or dropouts.
``keep_ms = 0`` removes such pauses completely, which is an explicit choice.

Speech itself is never cut: word gaps and stop-consonant closures are a few
hundred milliseconds at most, well below the minimum pause length.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME_S = 0.01
CROSSFADE_S = 0.02
DEFAULT_MIN_PAUSE_S = 0.7
DEFAULT_KEEP_MS = 250.0
MIN_PAUSE_RANGE = (0.3, 5.0)


@dataclass
class PauseSettings:
    enabled: bool = False  # off by default: shortening pauses is always the user's choice
    min_pause_s: float = DEFAULT_MIN_PAUSE_S
    keep_ms: float = DEFAULT_KEEP_MS  # 0 = remove long pauses completely

    @property
    def active(self) -> bool:
        return self.enabled and self.min_pause_s > 0


def silence_threshold_db(level_db: np.ndarray) -> float:
    """Level separating sound from silence, from a recording's 10 ms frame levels.

    8 dB above the background (10th percentile of frame levels), but never closer
    than 20 dB nor further than 45 dB below the loudest frame. Works for enhanced
    takes with a near-digital-silence floor as well as for raw recordings with
    audible room noise.
    """
    peak = float(level_db.max())
    floor = float(np.percentile(level_db, 10))
    return max(peak - 45.0, min(peak - 20.0, floor + 8.0))


def frame_levels(samples: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    hop = max(1, int(round(FRAME_S * sr)))
    n = mono.shape[0] // hop
    frames = np.asarray(mono[: n * hop], dtype=np.float64).reshape(n, hop)
    return 10 * np.log10(np.mean(frames**2, axis=1) + 1e-12), hop


def find_cuts(samples: np.ndarray, sr: int, min_pause_s: float = DEFAULT_MIN_PAUSE_S,
              keep_ms: float = DEFAULT_KEEP_MS) -> list[tuple[int, int]]:
    """Sample ranges ``[a, b)`` to remove so each long inner pause lasts ``keep_ms``."""
    level, hop = frame_levels(samples, sr)
    if level.size == 0:
        return []
    threshold = silence_threshold_db(level)
    loud = level > threshold
    sound = np.flatnonzero(loud)
    if sound.size < 2:
        return []
    cuts = []
    keep = int(round(keep_ms / 1000 * sr))
    half_xf = int(round(CROSSFADE_S * sr / 2))
    # quiet runs strictly between the first and the last sound
    quiet = ~loud
    idx = sound[0]
    while idx <= sound[-1]:
        if quiet[idx]:
            start = idx
            while idx <= sound[-1] and quiet[idx]:
                idx += 1
            s0, s1 = start * hop, idx * hop  # silence in samples
            if (s1 - s0) / sr >= min_pause_s:
                a = s0 + keep // 2 + half_xf
                b = s1 - (keep - keep // 2) - half_xf
                if b - a >= 2 * half_xf:
                    cuts.append((a, b))
        else:
            idx += 1
    return cuts


def apply_cuts(x: np.ndarray, cuts: list[tuple[int, int]], sr: int) -> np.ndarray:
    """Remove ``cuts`` from ``x`` (frames first), crossfading across each join."""
    if not cuts:
        return x
    h = int(round(CROSSFADE_S * sr / 2))
    two_d = x.ndim == 2
    pieces, pos = [], 0
    ramp = np.linspace(0.0, 1.0, 2 * h, dtype=np.float32) if h else np.zeros(0, np.float32)
    if two_d:
        ramp = ramp[:, None]
    for a, b in cuts:
        a0, b1 = max(pos, a - h), min(x.shape[0], b + h)
        pieces.append(x[pos:a0])
        out_part = x[a0: a0 + 2 * h]
        in_part = x[b1 - 2 * h: b1]
        n = min(out_part.shape[0], in_part.shape[0], ramp.shape[0])
        if n:
            pieces.append(out_part[:n] * (1 - ramp[:n]) + in_part[:n] * ramp[:n])
        pos = b1
    pieces.append(x[pos:])
    return np.concatenate(pieces).astype(np.float32)


def removed_samples(cuts: list[tuple[int, int]]) -> int:
    return int(sum(b - a for a, b in cuts))


def shorten_pauses(x: np.ndarray, sr: int, settings: PauseSettings) -> tuple[np.ndarray, float]:
    """Apply ``settings`` to ``x``. Returns the edited audio and the seconds removed."""
    if not settings.active:
        return x, 0.0
    cuts = find_cuts(x, sr, settings.min_pause_s, settings.keep_ms)
    return apply_cuts(x, cuts, sr), removed_samples(cuts) / sr
