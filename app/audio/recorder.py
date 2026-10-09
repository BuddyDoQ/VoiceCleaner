"""Recording from input devices: microphones (USB, headset, line-in) and
desktop audio (WASAPI loopback of any output device).

Capture uses the ``soundcard`` library (BSD-3), which talks to WASAPI in shared
mode, so any device can be recorded at 48 kHz (Windows converts the rate) and
loopback capture of speakers/headphones works without "Stereo Mix".

Design:

* one capture thread per source, each streaming straight to a float32 WAV
  part-file on disk, so a crash or an unplugged USB microphone never loses
  what was already recorded;
* when two sources are recorded together (e.g. microphone + desktop audio for
  a call), they are aligned by their start times and the second source is
  stretched by its measured clock drift before mixing, so the two stay in sync
  over long recordings;
* the finished take is written as a normal WAV file and then opened like any
  other recording (analysis, enhancement, export).
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from ..utils.errors import VoiceCleanerError
from ..utils.logging import get_logger
from .loader import AudioData

log = get_logger("recorder")

RECORD_SR = 48_000
BLOCK_FRAMES = 480  # 10 ms
OVERVIEW_BLOCK = 64  # must match ui.waveform.OVERVIEW_BLOCK
SILENCE_WARN_S = 3.0


class RecordingError(VoiceCleanerError):
    title = "Recording problem"


@dataclass(frozen=True)
class InputSource:
    id: str
    name: str
    kind: str  # "microphone" | "desktop"
    channels: int
    is_default: bool = False

    @property
    def label(self) -> str:
        prefix = "Desktop audio: " if self.kind == "desktop" else ""
        return prefix + self.name + ("  (default)" if self.is_default else "")


def _soundcard():
    try:
        import soundcard

        return soundcard
    except Exception as exc:  # missing package or no audio subsystem
        raise RecordingError("Audio recording is not available on this computer.", str(exc)) from exc


def list_sources() -> tuple[list[InputSource], list[InputSource]]:
    """Return ``(microphones, desktop_sources)`` currently available."""
    sc = _soundcard()
    try:
        default_mic = sc.default_microphone().id
    except Exception:
        default_mic = None
    try:
        default_spk = sc.default_speaker().id
    except Exception:
        default_spk = None
    mics, desktop = [], []
    for m in sc.all_microphones(include_loopback=True):
        if m.isloopback:
            desktop.append(InputSource(m.id, m.name, "desktop", m.channels, m.id == default_spk))
        else:
            mics.append(InputSource(m.id, m.name, "microphone", m.channels, m.id == default_mic))
    key = lambda s: (not s.is_default, s.name.lower())  # noqa: E731
    return sorted(mics, key=key), sorted(desktop, key=key)


def recordings_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "VoiceCleaner Recordings"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _co_initialize():
    """WASAPI is COM based; every capture thread needs its own COM apartment."""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    except Exception:
        pass


@dataclass
class _SourceState:
    source: InputSource
    part_path: Path
    frames: int = 0
    start_time: float | None = None
    end_time: float | None = None
    peak: float = 0.0  # since last read by the UI
    rms: float = 0.0
    max_peak: float = 0.0
    error: str = ""
    thread: threading.Thread | None = None
    mins: list = field(default_factory=list)
    maxs: list = field(default_factory=list)
    rmss: list = field(default_factory=list)
    _carry: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))


@dataclass
class RecordingResult:
    path: Path
    duration: float
    sample_rate: int
    channels: int
    sources: list[str]
    warnings: list[str]
    start_time: float = 0.0  # perf_counter time of the first recorded sample


class RecordingSession:
    """One take from one or two sources."""

    def __init__(self, sources: list[InputSource], out_dir: Path | None = None, gains: list[float] | None = None,
                 output_path: Path | None = None):
        """``output_path`` names the finished take (sessions); otherwise a dated name in ``out_dir``."""
        if not sources:
            raise RecordingError("Choose at least one input to record from.")
        if len(sources) > 2:
            raise RecordingError("Up to two inputs can be recorded at the same time.")
        self.sources = sources
        self.gains = gains or [1.0] * len(sources)
        if output_path is not None:
            self.out_dir = Path(output_path).parent
            self.name = Path(output_path).stem
        else:
            self.out_dir = out_dir or recordings_dir()
            self.name = f"Recording {datetime.now():%Y-%m-%d %H-%M-%S}"
        parts = self.out_dir / ".parts"
        parts.mkdir(parents=True, exist_ok=True)
        self._states = [_SourceState(s, parts / f"{self.name} - {i}.f32.wav") for i, s in enumerate(sources)]
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self.running = False

    # --- control ----------------------------------------------------------------
    def start(self):
        sc = _soundcard()
        self._started_at = time.perf_counter()
        self.running = True
        for st in self._states:
            st.thread = threading.Thread(target=self._capture, args=(sc, st), name=f"rec-{st.source.kind}", daemon=True)
            st.thread.start()
        log.info("Recording started: %s", [s.name for s in self.sources])

    def stop(self) -> RecordingResult:
        self._stop.set()
        for st in self._states:
            if st.thread is not None:
                st.thread.join(timeout=5)
        self.running = False
        try:
            return self._finalize()
        finally:
            self._cleanup_parts()

    def cancel(self):
        self._stop.set()
        for st in self._states:
            if st.thread is not None:
                st.thread.join(timeout=5)
        self.running = False
        self._cleanup_parts()

    # --- live status (polled by the UI) -------------------------------------------
    @property
    def capture_start(self) -> float | None:
        """perf_counter time of the first captured sample (reference source), once known."""
        return self._states[0].start_time if self._states[0].start_time is not None else             next((st.start_time for st in self._states if st.start_time is not None), None)

    @property
    def capturing(self) -> bool:
        """True once audio is actually arriving (devices take ~1 s to open)."""
        return self.capture_start is not None

    @property
    def elapsed(self) -> float:
        start = self.capture_start
        return 0.0 if start is None else time.perf_counter() - start

    @property
    def errors(self) -> list[str]:
        return [st.error for st in self._states if st.error]

    @property
    def all_failed(self) -> bool:
        return all(st.error for st in self._states)

    def levels(self) -> list[tuple[float, float]]:
        """Per source ``(rms_db, peak_db)`` since the previous call."""
        out = []
        with self._lock:
            for st in self._states:
                out.append((_to_db(st.rms), _to_db(st.peak)))
                st.peak = 0.0
        return out

    def silent_sources(self) -> list[InputSource]:
        """Sources that delivered only digital silence for a while (muted, privacy-blocked...)."""
        if self.elapsed < SILENCE_WARN_S:
            return []
        with self._lock:
            return [st.source for st in self._states if st.max_peak < 1e-6 and not st.error]

    def overview(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Min/max/RMS envelope of the take so far (64-sample blocks, all sources)."""
        with self._lock:
            arrays = [(np.asarray(st.mins, np.float32), np.asarray(st.maxs, np.float32),
                       np.asarray(st.rmss, np.float32)) for st in self._states]
        n = max((a[0].shape[0] for a in arrays), default=0)
        mins, maxs, rms = np.zeros(n, np.float32), np.zeros(n, np.float32), np.zeros(n, np.float32)
        for (lo, hi, r), g in zip(arrays, self.gains):
            k = lo.shape[0]
            mins[:k] += lo * g
            maxs[:k] += hi * g
            rms[:k] = np.maximum(rms[:k], r * g)
        return mins, maxs, rms

    # --- capture thread -------------------------------------------------------------
    def _capture(self, sc, st: _SourceState):
        _co_initialize()
        try:
            mic = sc.get_microphone(st.source.id, include_loopback=st.source.kind == "desktop")
            channels = max(1, min(2, st.source.channels))
            with sf.SoundFile(str(st.part_path), "w", samplerate=RECORD_SR, channels=channels, subtype="FLOAT") as f, \
                    warnings.catch_warnings():
                # soundcard warns about "data discontinuity" when loopback has nothing playing
                warnings.simplefilter("ignore")
                with mic.recorder(samplerate=RECORD_SR, channels=channels, blocksize=BLOCK_FRAMES) as rec:
                    while not self._stop.is_set():
                        block = np.asarray(rec.record(numframes=BLOCK_FRAMES * 5), dtype=np.float32)
                        if block.ndim == 1:
                            block = block[:, None]
                        now = time.perf_counter()
                        if st.start_time is None:
                            # the block's first sample was captured block-duration ago
                            st.start_time = now - block.shape[0] / RECORD_SR
                        f.write(block)
                        self._update_stats(st, block)
                        st.end_time = now
        except Exception as exc:
            msg = str(exc) or type(exc).__name__
            log.warning("Capture from %s stopped: %s", st.source.name, msg)
            st.error = (f"Recording from “{st.source.name}” stopped unexpectedly. "
                        "The device may have been disconnected or is in use by another program.")

    def _update_stats(self, st: _SourceState, block: np.ndarray):
        mono = block.mean(axis=1)
        data = np.concatenate([st._carry, mono])
        nb = data.shape[0] // OVERVIEW_BLOCK
        blocks = data[: nb * OVERVIEW_BLOCK].reshape(nb, OVERVIEW_BLOCK)
        peak = float(np.abs(block).max(initial=0.0))
        with self._lock:
            st.frames += block.shape[0]
            st.peak = max(st.peak, peak)
            st.max_peak = max(st.max_peak, peak)
            st.rms = float(np.sqrt(np.mean(block * block)))
            if nb:
                st.mins.extend(blocks.min(axis=1).tolist())
                st.maxs.extend(blocks.max(axis=1).tolist())
                st.rmss.extend(np.sqrt(np.mean(blocks * blocks, axis=1)).tolist())
            st._carry = data[nb * OVERVIEW_BLOCK :]

    # --- finishing -------------------------------------------------------------------
    def _finalize(self) -> RecordingResult:
        usable = [st for st in self._states if st.frames > 0]
        if not usable:
            errors = " ".join(self.errors)
            raise RecordingError(errors or "Nothing was recorded. Check that the input device is connected.")
        warn = list(self.errors)
        silent = [st.source.name for st in usable if st.max_peak < 1e-6]
        for name in silent:
            warn.append(f"“{name}” delivered only silence. If it is a microphone, check that it is "
                        "not muted and that Windows allows apps to use it (Settings → Privacy → Microphone).")

        tracks = []
        for st in usable:
            data, _ = sf.read(str(st.part_path), dtype="float32", always_2d=True)
            tracks.append((st, data))

        if len(tracks) == 1:
            st, mix = tracks[0]
            mix = mix * self.gains[self._states.index(st)]
        else:
            mix = self._mix_aligned(tracks)
        peak = float(np.abs(mix).max(initial=0.0))
        if peak > 0.999:
            warn.append("The recording reached full scale and may contain clipping. Lower the input level next time.")
        mix = np.clip(mix, -1.0, 1.0)

        path = self.out_dir / f"{self.name}.wav"
        sf.write(str(path), mix, RECORD_SR, subtype="PCM_24")
        log.info("Recording saved: %s (%.1f s, %d ch, sources=%s)", path, mix.shape[0] / RECORD_SR, mix.shape[1],
                 [st.source.name for st, _ in tracks])
        return RecordingResult(path, mix.shape[0] / RECORD_SR, RECORD_SR, mix.shape[1],
                               [st.source.name for st, _ in tracks], warn, tracks[0][0].start_time or 0.0)

    def _mix_aligned(self, tracks) -> np.ndarray:
        """Mix two independently clocked captures.

        Track 0 is the reference clock. Track 1 is shifted by the difference in
        start times and stretched by the ratio of the measured durations (its
        device clock runs slightly fast or slow), then the two are summed.
        """
        (st0, a), (st1, b) = tracks
        g0, g1 = self.gains[self._states.index(st0)], self.gains[self._states.index(st1)]
        offset = int(round(((st1.start_time or 0) - (st0.start_time or 0)) * RECORD_SR))
        wall0 = (st0.end_time or 0) - (st0.start_time or 0)
        wall1 = (st1.end_time or 0) - (st1.start_time or 0)
        rate0 = a.shape[0] / wall0 if wall0 > 1 else RECORD_SR
        rate1 = b.shape[0] / wall1 if wall1 > 1 else RECORD_SR
        ratio = rate1 / rate0  # >1: track 1's clock runs fast
        if abs(ratio - 1) > 0.02:  # implausible: timing noise on a very short take
            ratio = 1.0
        if abs(ratio - 1) > 1e-6:
            n_new = int(round(b.shape[0] / ratio))
            pos = np.arange(n_new) * ratio
            b = np.stack([np.interp(pos, np.arange(b.shape[0]), b[:, c]) for c in range(b.shape[1])], axis=1)
        channels = max(a.shape[1], b.shape[1])
        a = _to_channels(a, channels)
        b = _to_channels(b, channels)
        start_b = max(0, offset)
        if offset < 0:
            b = b[-offset:]
        n = max(a.shape[0], start_b + b.shape[0])
        out = np.zeros((n, channels), np.float32)
        out[: a.shape[0]] += a * g0
        out[start_b : start_b + b.shape[0]] += b * g1
        log.info("Mixed two sources: offset %d samples, clock ratio %.6f", offset, ratio)
        return out

    def _cleanup_parts(self):
        for st in self._states:
            try:
                st.part_path.unlink(missing_ok=True)
            except OSError:
                pass


def _to_db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-6))


def _to_channels(x: np.ndarray, channels: int) -> np.ndarray:
    if x.shape[1] == channels:
        return x
    if x.shape[1] == 1:
        return np.repeat(x, channels, axis=1)
    return x.mean(axis=1, keepdims=True)


# --- combining a take with the existing Original ------------------------------------------

MODES = {
    "new": "New recording (replaces the Original)",
    "append": "Append to the end of the Original",
    "overdub": "Overdub: layer onto the Original at the playhead",
}


def combine_with_original(original: AudioData, take_path: Path, mode: str, position_s: float = 0.0,
                          latency_s: float = 0.0, take_gain: float = 1.0, out_path: Path | None = None) -> Path:
    """Build a new WAV from the Original plus a take; the Original file is untouched.

    ``append`` adds the take after the Original. ``overdub`` mixes the take
    into the Original starting at ``position_s`` (the take was recorded while
    the Original played from there); ``latency_s`` is the playback+capture
    delay, removed so the layers line up.
    """
    if mode == "new":
        return take_path
    take, take_sr = sf.read(str(take_path), dtype="float32", always_2d=True)
    sr = original.sample_rate
    if take_sr != sr:
        from math import gcd

        from scipy import signal

        g = gcd(take_sr, sr)
        take = signal.resample_poly(take, sr // g, take_sr // g, axis=0).astype(np.float32)
    ch = original.channels
    take = _to_channels(take, ch) * take_gain
    orig = np.asarray(original.samples, dtype=np.float32)
    if mode == "append":
        out = np.concatenate([orig, take])
    elif mode == "overdub":
        skip = int(round(latency_s * sr))
        take = take[skip:]
        start = int(round(np.clip(position_s, 0, orig.shape[0] / sr) * sr))
        n = max(orig.shape[0], start + take.shape[0])
        out = np.zeros((n, ch), np.float32)
        out[: orig.shape[0]] = orig
        out[start : start + take.shape[0]] += take
    else:
        raise ValueError(mode)
    peak = float(np.abs(out).max(initial=0.0))
    if peak > 1.0:  # layering can exceed full scale; scale down rather than clip
        out /= peak / 0.98
    if out_path is not None:
        path = Path(out_path)
    else:
        stem = original.info.path.stem if original.info else "Original"
        suffix = "with recording" if mode == "append" else "overdub"
        path = take_path.with_name(f"{stem} - {suffix} {take_path.stem.removeprefix('Recording ')}.wav")
    sf.write(str(path), out, sr, subtype="PCM_24" if original.info is None or original.info.subtype != "FLOAT" else "FLOAT")
    log.info("Combined take with original (%s) -> %s", mode, path)
    return path
