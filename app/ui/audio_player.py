"""Dual-source audio player for instant A/B comparison.

Both sources (original and enhanced) share one output stream and one
playhead. Switching sources happens inside the audio callback with a short
crossfade, so the comparison is instant and click-free at the exact same
point in the recording.
"""
from __future__ import annotations

import sys
import threading

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..utils.logging import get_logger
from ..audio.playback_routing import audio_lifecycle

log = get_logger("player")

SOURCES = ("original", "enhanced")
SWITCH_FADE_S = 0.012
LOOP_FADE_S = 0.004  # gentle fade at both loop ends so the jump back does not click
MUTE_FADE_S = 0.005  # a silenced region fades out and back in, as it does when exported
# CoreAudio honours latency="low" with callbacks of a few frames (15 frames = 0.7 ms at
# 22.05 kHz), far too short for a Python callback: any GIL pause becomes a dropout.
# ~20 ms blocks keep playback clean; the A/B switch still lands within one block.
MAC_BLOCK_S = 0.02
MAC_LATENCY_S = 0.05  # output buffer: headroom for UI redraws and background processing


class AudioPlayer(QObject):
    positionChanged = Signal(float)
    stateChanged = Signal(str)  # playing | paused | stopped
    sourceChanged = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None, *, routing=None):
        super().__init__(parent)
        self.routing = routing
        self.output_device = None
        self._lifecycle = routing.lifecycle if routing is not None else audio_lifecycle
        if routing is not None:
            self.error.connect(lambda message: routing.report_error(message, parent))
        self._buffers: dict[str, np.ndarray | None] = {s: None for s in SOURCES}
        self._gains = {s: 1.0 for s in SOURCES}  # volume * level-match gain
        self._volume = {s: 0.9 for s in SOURCES}
        self._match = {s: 1.0 for s in SOURCES}
        self._sr = 48000
        self._pos = 0
        self._active = "original"
        self._fade_from: str | None = None
        self._fade_pos = 0
        self._state = "stopped"
        self._stream = None
        self._lock = threading.Lock()
        self._ended = False
        self._loop: tuple[int, int] | None = None  # frames [start, end) played over and over
        self._overlay: dict[str, tuple[int, np.ndarray]] = {}  # source -> (start frame, samples) laid over it
        self._edit_s: list = []  # the user's edits on both sources: (start s, end s, "cut" | "mute")
        self._cuts: list[tuple[int, int]] = []  # frames skipped while playing
        self._mutes: list[tuple[int, int]] = []  # frames played silent
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        if routing is not None:
            routing.register(self)

    # --- configuration ----------------------------------------------------------
    def set_source(self, name: str, samples: np.ndarray | None, sr: int | None = None):
        # Closing can wait for the callback, which itself needs _lock.
        if sr is not None and sr != self._sr and self._stream is not None:
            if not self._close_stream():
                self.fail_output("The previous audio stream could not be closed.")
                return
        with self._lock:
            self._buffers[name] = samples
            if sr is not None:
                if sr != self._sr:
                    self._sr = sr
                    self._edit_frames()
            if name == self._active and samples is None:
                self._active = "original"
        if all(b is None for b in self._buffers.values()):
            self.stop()

    def set_overlay(self, name: str, start_frame: int | None = None, samples: np.ndarray | None = None):
        """Play ``samples`` instead of source ``name`` from ``start_frame`` on (a live
        preview of new settings over the enhanced track). ``None`` removes it."""
        with self._lock:
            if samples is None:
                self._overlay.pop(name, None)
            else:
                self._overlay[name] = (int(start_frame), samples)

    def set_edits(self, edits: list | None):
        """Play both sources with the user's edits, ``(start s, end s, kind)``: "cut" regions
        are skipped and "mute" regions are silent. The timeline is not changed."""
        with self._lock:
            self._edit_s = [tuple(e) for e in edits or []]
            self._edit_frames()

    def _edit_frames(self):
        spans = {"cut": [], "mute": []}
        for a, b, kind in self._edit_s:
            a, b = int(round(a * self._sr)), int(round(b * self._sr))
            if b > a and kind in spans:
                spans[kind].append((a, b))
        cuts = []
        for a, b in sorted(spans["cut"]):
            if cuts and a <= cuts[-1][1]:
                cuts[-1] = (cuts[-1][0], max(cuts[-1][1], b))
            else:
                cuts.append((a, b))
        self._cuts, self._mutes = cuts, sorted(spans["mute"])

    def has_source(self, name: str) -> bool:
        return self._buffers.get(name) is not None

    def set_volume(self, name: str, volume: float):
        self._volume[name] = float(np.clip(volume, 0, 1))
        self._update_gains()

    def volume(self, name: str) -> float:
        return self._volume[name]

    def set_match_gain(self, name: str, gain: float):
        self._match[name] = gain
        self._update_gains()

    def _update_gains(self):
        for s in SOURCES:
            # perceptual (squared) volume curve
            self._gains[s] = (self._volume[s] ** 2) * self._match[s]

    @property
    def active_source(self) -> str:
        return self._active

    def set_active(self, name: str):
        if name == self._active or self._buffers.get(name) is None:
            return
        with self._lock:
            self._fade_from = self._active
            self._fade_pos = 0
            self._active = name
        self.sourceChanged.emit(name)

    def toggle_source(self):
        self.set_active("enhanced" if self._active == "original" else "original")

    # --- loop -----------------------------------------------------------------------
    def set_loop(self, region: tuple[float, float] | None):
        """Play ``(start, end)`` seconds over and over; ``None`` plays normally."""
        with self._lock:
            if region is None:
                self._loop = None
                return
            a, b = sorted(region)
            a, b = int(max(0.0, a) * self._sr), int(max(0.0, b) * self._sr)
            self._loop = (a, b) if b - a >= int(0.05 * self._sr) else None
            if self._loop and not (a <= self._pos < b):
                self._pos = a  # start inside the loop

    @property
    def loop(self) -> tuple[float, float] | None:
        return None if self._loop is None else (self._loop[0] / self._sr, self._loop[1] / self._sr)

    # --- transport ----------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def duration(self) -> float:
        buf = self._buffers.get(self._active)
        return 0.0 if buf is None else buf.shape[0] / self._sr

    @property
    def output_latency(self) -> float:
        """Seconds between a sample being handed to the device and it being heard."""
        try:
            return float(self._stream.latency) if self._stream is not None else 0.0
        except Exception:
            return 0.0

    @property
    def position(self) -> float:
        return self._pos / self._sr

    def play(self):
        with self._lifecycle.lock:
            self._play()

    def _play(self):
        if self._buffers.get(self._active) is None:
            return
        if self._pos >= self._buffers[self._active].shape[0] - 1:
            self._pos = 0
        if self._loop is not None and not (self._loop[0] <= self._pos < self._loop[1]):
            self._pos = self._loop[0]
        try:
            device = self.routing.prepare(self) if self.routing is not None else None
            if self._stream is not None and self.output_device != device:
                if not self._close_stream():
                    raise RuntimeError("The previous audio stream could not be closed.")
            if self._stream is not None and not self._stream.active:
                if not self._close_stream():
                    raise RuntimeError("The previous audio stream could not be closed.")
            self._ensure_stream(device)
            self._stream.start()
        except Exception as exc:
            log.warning("Audio output failed: %s", exc)
            self.fail_output(f"Audio playback could not start: {exc}")
            return
        self._ended = False
        self._set_state("playing")
        self._timer.start()

    def pause(self):
        if self._stream is not None and self._state == "playing":
            try:
                self._stream.stop()
            except Exception as exc:
                self.fail_output(f"Audio playback stopped unexpectedly: {exc}")
                return
        self._set_state("paused")
        self._timer.stop()
        self.positionChanged.emit(self.position)

    def toggle_play(self):
        if self._state == "playing":
            self.pause()
        else:
            self.play()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
        self._pos = 0
        self._set_state("stopped")
        self._timer.stop()
        self.positionChanged.emit(0.0)

    def seek(self, seconds: float):
        buf = self._buffers.get(self._active)
        if buf is None:
            return
        self._pos = int(np.clip(seconds * self._sr, 0, buf.shape[0] - 1))
        if self._loop is not None and not (self._loop[0] <= self._pos < self._loop[1]):
            self._pos = self._loop[0]  # while looping, a click outside the loop restarts it
        self.positionChanged.emit(self.position)

    def shutdown(self):
        self._timer.stop()
        if self._close_stream() and self.routing is not None:
            self.routing.unregister(self)

    def suspend_output(self):
        """Release the device without resetting the playhead, sources, edits or loop."""
        self._timer.stop()
        self._close_stream()
        if self._state == "playing":
            self._set_state("paused")
        self.positionChanged.emit(self.position)

    def resume_output(self):
        # Teardown waits for the callback, which may finish the track meanwhile.
        # Automatic rerouting must not turn normal completion into a replay.
        if self._ended:
            self._tick()
        else:
            self.play()

    def fail_output(self, message: str):
        log.warning("%s", message)
        self.suspend_output()
        self._set_state("paused")
        self.error.emit(message)

    # --- internals --------------------------------------------------------------------
    def _set_state(self, state: str):
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    def _ensure_stream(self, device=None):
        if self._stream is not None:
            return
        if self.routing is not None:
            sd = self.routing.backend
        else:
            import sounddevice as sd

        if sys.platform == "darwin":
            blocksize, latency = int(self._sr * MAC_BLOCK_S), MAC_LATENCY_S
        else:
            blocksize, latency = 0, "low"
        channels = min(2, device.channels) if device is not None else 2
        index = device.index if device is not None else None
        sd.check_output_settings(device=index, samplerate=self._sr, channels=channels, dtype="float32")
        self._stream = sd.OutputStream(
            device=index, samplerate=self._sr, channels=channels, dtype="float32", callback=self._callback,
            blocksize=blocksize, latency=latency,
        )
        self.output_device = device

    def _close_stream(self):
        with self._lifecycle.lock:
            if self._stream is None:
                return True
            try:
                self._stream.abort()
            except Exception as exc:
                log.warning("Could not abort audio stream: %s", exc)
            try:
                self._stream.close()
            except Exception as exc:
                log.warning("Could not close audio stream: %s", exc)
                return False  # retain the handle; a global refresh must fail closed
            self._stream = None
            self.output_device = None
            return True

    def _positions(self, pos: int, frames: int) -> tuple[np.ndarray, int]:
        """The frames to play next from ``pos``, wrapping at the loop end and jumping over
        cut regions (-1: nothing to play), and the position after them."""
        loop, cuts = self._loop, self._cuts
        if loop is None and not cuts:
            return np.arange(pos, pos + frames), pos + frames
        idx = np.full(frames, -1, np.int64)
        n, p, wraps = 0, pos, 0
        while n < frames:
            if loop is not None and not (loop[0] <= p < loop[1]):
                p, wraps = loop[0], wraps + 1
                if wraps > 2:
                    break  # the whole loop is cut out
            for a, b in cuts:
                if a <= p < b:
                    p = b
            if loop is not None and p >= loop[1]:
                continue
            stop = p + frames - n
            if loop is not None:
                stop = min(stop, loop[1])
            nxt = next((a for a, _b in cuts if a > p), None)
            if nxt is not None:
                stop = min(stop, nxt)
            idx[n:n + stop - p] = np.arange(p, stop)
            n, p = n + stop - p, stop
        return idx, p

    def _envelope(self, idx: np.ndarray) -> np.ndarray | None:
        """Short fades where playback jumps (both loop ends, both sides of a cut), so the
        jump never clicks. None when there is no jump nearby."""
        k = max(1, int(LOOP_FADE_S * self._sr))
        lo, hi = int(idx.min()), int(idx.max())
        env = None
        if self._loop is not None:
            a, b = self._loop
            env = np.minimum(np.clip((idx - a) / k, 0, 1), np.clip((b - 1 - idx) / k, 0, 1))
        for a, b in self._cuts:
            if a - k > hi or b + k < lo:
                continue
            g = np.where(idx < a, np.clip((a - 1 - idx) / k, 0, 1), np.clip((idx - b) / k, 0, 1))
            env = g if env is None else env * g
        if env is not None:
            env[idx < 0] = 0.0
        return None if env is None else env.astype(np.float32)

    def _read(self, name: str, idx: np.ndarray) -> np.ndarray:
        """Source ``name`` at the frames ``idx`` (runs of consecutive frames)."""
        frames = idx.shape[0]
        out = np.zeros((frames, 2), dtype=np.float32)
        start = 0
        for end in [*(np.flatnonzero(np.diff(idx) != 1) + 1), frames]:
            if idx[start] >= 0:
                out[start:end] = self._read_plain(name, int(idx[start]), int(end) - start)
            start = int(end)
        return out

    def _read_plain(self, name: str, pos: int, frames: int) -> np.ndarray:
        buf = self._buffers.get(name)
        out = np.zeros((frames, 2), dtype=np.float32)
        if buf is None or pos >= buf.shape[0]:
            return out
        chunk = np.asarray(buf[pos : pos + frames], dtype=np.float32)
        ov = self._overlay.get(name)
        if ov is not None:
            o0, osamp = ov
            lo, hi = max(pos, o0), min(pos + chunk.shape[0], o0 + osamp.shape[0])
            if hi > lo:
                chunk = chunk.copy()
                chunk[lo - pos : hi - pos] = osamp[lo - o0 : hi - o0]
        if chunk.shape[1] == 1:
            out[: chunk.shape[0]] = chunk  # broadcast mono to both speakers
        else:
            out[: chunk.shape[0]] = chunk[:, :2]
        gain = self._mute_gain(pos, frames)
        if gain is not None:
            out *= gain[:, None]
        return out * self._gains[name]

    def _mute_gain(self, pos: int, frames: int) -> np.ndarray | None:
        """The gain of the silenced regions over ``frames`` from ``pos``, None when none is
        there. Each region fades out over its first 5 ms and back in over its last."""
        gain = None
        for a, b in self._mutes:
            if b <= pos or a >= pos + frames:
                continue
            if gain is None:
                gain = np.ones(frames, np.float32)
            k = max(1, min(int(MUTE_FADE_S * self._sr), (b - a) // 2))
            i = np.arange(max(a, pos), min(b, pos + frames))
            d = np.clip(np.minimum(i - a, b - 1 - i) / k, 0, 1)
            gain[i - pos] *= (0.5 + 0.5 * np.cos(np.pi * d)).astype(np.float32)
        return gain

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            pos = self._pos
            idx, nxt = self._positions(pos, frames)
            data = self._read(self._active, idx)
            if self._fade_from is not None:
                fade_len = int(SWITCH_FADE_S * self._sr)
                old = self._read(self._fade_from, idx)
                i = np.arange(self._fade_pos, self._fade_pos + frames)
                w = np.clip(i / fade_len, 0, 1).astype(np.float32)[:, None]
                data = data * w + old * (1 - w)
                self._fade_pos += frames
                if self._fade_pos >= fade_len:
                    self._fade_from = None
            env = self._envelope(idx)
            if env is not None:
                data *= env[:, None]
            np.clip(data, -1.0, 1.0, out=data)
            outdata[:] = data if outdata.shape[1] == 2 else data.mean(axis=1, keepdims=True)
            buf = self._buffers.get(self._active)
            self._pos = nxt
            if self._loop is not None:
                return
            if buf is None or self._pos >= buf.shape[0]:
                self._ended = True
                import sounddevice as sd

                raise sd.CallbackStop()

    def _tick(self):
        if self._ended:
            self._ended = False
            self._timer.stop()
            buf = self._buffers.get(self._active)
            self._pos = 0 if buf is None else buf.shape[0]
            self._set_state("stopped")
            self._pos = 0
            self.positionChanged.emit(0.0)
            return
        if self._state == "playing":
            try:
                if self._stream is None or not self._stream.active:
                    self.fail_output("Audio playback stopped unexpectedly. Check the output device "
                                     "and choose Play to retry.")
                    return
            except Exception as exc:
                self.fail_output(f"Audio playback stopped unexpectedly: {exc}")
                return
        self.positionChanged.emit(self.position)
