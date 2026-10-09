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

log = get_logger("player")

SOURCES = ("original", "enhanced")
SWITCH_FADE_S = 0.012
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

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # --- configuration ----------------------------------------------------------
    def set_source(self, name: str, samples: np.ndarray | None, sr: int | None = None):
        with self._lock:
            self._buffers[name] = samples
            if sr is not None:
                if sr != self._sr and self._stream is not None:
                    self._close_stream()
                self._sr = sr
            if name == self._active and samples is None:
                self._active = "original"
        if all(b is None for b in self._buffers.values()):
            self.stop()

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
        if self._buffers.get(self._active) is None:
            return
        if self._pos >= self._buffers[self._active].shape[0] - 1:
            self._pos = 0
        try:
            if self._stream is not None and not self._stream.active:
                self._close_stream()  # a stream that ran to the end cannot be restarted
            self._ensure_stream()
            self._stream.start()
        except Exception as exc:
            log.warning("Audio output failed: %s", exc)
            self._close_stream()
            self.error.emit(
                "No audio output device could be opened. Check that speakers or headphones "
                "are connected and not in use by another application."
            )
            return
        self._ended = False
        self._set_state("playing")
        self._timer.start()

    def pause(self):
        if self._stream is not None and self._state == "playing":
            self._stream.stop()
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
        self.positionChanged.emit(self.position)

    def shutdown(self):
        self._timer.stop()
        self._close_stream()

    # --- internals --------------------------------------------------------------------
    def _set_state(self, state: str):
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    def _ensure_stream(self):
        if self._stream is not None:
            return
        import sounddevice as sd

        if sys.platform == "darwin":
            blocksize, latency = int(self._sr * MAC_BLOCK_S), MAC_LATENCY_S
        else:
            blocksize, latency = 0, "low"
        self._stream = sd.OutputStream(
            samplerate=self._sr, channels=2, dtype="float32", callback=self._callback,
            blocksize=blocksize, latency=latency,
        )

    def _close_stream(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _read(self, name: str, pos: int, frames: int) -> np.ndarray:
        buf = self._buffers.get(name)
        out = np.zeros((frames, 2), dtype=np.float32)
        if buf is None or pos >= buf.shape[0]:
            return out
        chunk = np.asarray(buf[pos : pos + frames], dtype=np.float32)
        if chunk.shape[1] == 1:
            out[: chunk.shape[0]] = chunk  # broadcast mono to both speakers
        else:
            out[: chunk.shape[0]] = chunk[:, :2]
        return out * self._gains[name]

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            pos = self._pos
            data = self._read(self._active, pos, frames)
            if self._fade_from is not None:
                fade_len = int(SWITCH_FADE_S * self._sr)
                old = self._read(self._fade_from, pos, frames)
                idx = np.arange(self._fade_pos, self._fade_pos + frames)
                w = np.clip(idx / fade_len, 0, 1).astype(np.float32)[:, None]
                data = data * w + old * (1 - w)
                self._fade_pos += frames
                if self._fade_pos >= fade_len:
                    self._fade_from = None
            np.clip(data, -1.0, 1.0, out=data)
            outdata[:] = data
            buf = self._buffers.get(self._active)
            self._pos = pos + frames
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
        self.positionChanged.emit(self.position)
