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
  4. apply the user's own edits: regions cut out (a pop, a stumble, silence the
     detection missed) or silenced in place (keeps the timing);
  5. apply 5 ms fades at the new edges so joins never click.

Takes are converted to a common sample rate and channel count (the first
take's rate, the widest channel layout), optionally levelled to the same
loudness, joined with an optional gap, and passed through the true-peak
limiter so level matching can never clip.
"""
from __future__ import annotations

import json
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
    edits: list = field(default_factory=list)  # the user's edits: (a, b, "cut" | "mute"), absolute samples

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

    def removals(self) -> list[tuple[int, int]]:
        """Everything taken out inside the trimmed range: shortened pauses and the user's
        cuts, clipped to the range, sorted and merged."""
        spans = list(self.cuts) + [(a, b) for a, b, kind in self.edits if kind == "cut"]
        return merge_spans(spans, self.start, self.end)

    @property
    def edit_seconds(self) -> float:
        """Removed by the user's cuts (beyond what pause shortening already removes)."""
        return max(0, removed_samples(self.removals()) - removed_samples(self.cuts)) / self.sample_rate

    @property
    def final_duration(self) -> float:
        """Length in the compilation: ends trimmed, long pauses shortened, cuts removed."""
        return max(0.0, self.trimmed_duration - removed_samples(self.removals()) / self.sample_rate)

    @property
    def silent(self) -> bool:
        return self.end <= self.start


EDIT_KINDS = ("cut", "mute")


def merge_spans(spans, lo: int = 0, hi: int | None = None) -> list[tuple[int, int]]:
    """``spans`` clipped to [lo, hi), sorted, overlapping ones joined."""
    out: list[list[int]] = []
    for a, b in sorted((int(a), int(b)) for a, b in spans):
        a, b = max(a, lo), b if hi is None else min(b, hi)
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def add_edit(edits: list, a: int, b: int, kind: str) -> list:
    """``edits`` with region [a, b) of ``kind`` added; touching regions of the same kind merge."""
    same = [(x, y) for x, y, k in edits if k == kind] + [(a, b)]
    other = [e for e in edits if e[2] != kind]
    return sorted(other + [(x, y, kind) for x, y in merge_spans(same)])


def _silence(x: np.ndarray, a: int, b: int, sr: int):
    """Silence [a, b) of ``x`` in place with short fades, so it never clicks."""
    a, b = max(0, a), min(x.shape[0], b)
    if b <= a:
        return
    n = min(int(FADE_S * sr), (b - a) // 2)
    gain = np.zeros(b - a, np.float32)
    if n > 1:
        ramp = (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)
        gain[:n], gain[-n:] = ramp, ramp[::-1]
    x[a:b] *= gain[:, None] if x.ndim == 2 else gain


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


EDITS_FILE = "compile_edits.json"  # in the session folder: the user's edits per take


def load_edits(folder: Path | None) -> dict[str, list]:
    """Saved edits by take key: ``[[start s, end s, kind], ...]``. Damaged files read as empty."""
    if folder is None:
        return {}
    try:
        data = json.loads((Path(folder) / EDITS_FILE).read_text(encoding="utf-8"))
        return {str(k): [[float(a), float(b), str(c)] for a, b, c in v if c in EDIT_KINDS]
                for k, v in data.items()}
    except (OSError, ValueError, TypeError):
        return {}


def save_edits(folder: Path | None, edits: dict[str, list]):
    if folder is None:
        return
    path = Path(folder) / EDITS_FILE
    data = {k: v for k, v in edits.items() if v}
    try:
        if data or path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass  # read-only session folder: the edits last until the app closes


def edits_to_seconds(take: Take) -> list:
    sr = take.sample_rate
    return [[round(a / sr, 4), round(b / sr, 4), k] for a, b, k in take.edits]


def edits_from_seconds(take: Take, saved: list) -> list:
    sr, n = take.sample_rate, take.samples.shape[0]
    out = []
    for a, b, k in saved or []:
        a, b = max(0, int(round(a * sr))), min(n, int(round(b * sr)))
        if b > a and k in EDIT_KINDS:
            out.append((a, b, k))
    return sorted(out)


def render_take(take: Take) -> np.ndarray:
    """The take as it appears in the compilation (before rate/channel conversion)."""
    x = take.samples[take.start:take.end].copy()
    for a, b, kind in take.edits:
        if kind == "mute":
            _silence(x, a - take.start, b - take.start, take.sample_rate)
    cuts = take.removals()
    if cuts:
        x = apply_cuts(x, [(a - take.start, b - take.start) for a, b in cuts], take.sample_rate)
    return x


def apply_edits(samples: np.ndarray, sr: int, saved: list) -> np.ndarray:
    """``samples`` with the user's edits (seconds, as saved) applied: the whole recording,
    nothing trimmed. Used when the Enhance tab exports."""
    take = Take(Path(""), samples, sr, 0, samples.shape[0])
    take.edits = edits_from_seconds(take, saved)
    return render_take(take) if take.edits else samples


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
