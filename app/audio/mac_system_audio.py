"""Desktop audio on macOS.

macOS has no loopback device, so ``soundcard`` cannot record what the Mac plays. Two ways
are offered instead, both listed under *Desktop audio*:

* **System audio (all apps)**, macOS 14.2 and later: the bundled ``vc-systemaudio`` helper
  (packaging/macos/systemaudio.swift) records a Core Audio process tap of everything the Mac
  plays except VoiceCleaner itself, and streams it as 48 kHz stereo Float32. macOS asks once
  for "System Audio Recording" permission.
* **Loopback drivers** such as BlackHole, Loopback or Soundflower: they appear to macOS as
  microphones; they are listed under Desktop audio instead and recorded like a microphone.

``SystemAudioRecorder`` has the same ``record(numframes)`` interface as a ``soundcard``
recorder, so the capture loop in recorder.py treats both alike.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from ..utils.logging import get_logger

log = get_logger("recorder")

SYSTEM_AUDIO_ID = "macos-system-audio"
SYSTEM_AUDIO_NAME = "System audio (all apps)"
HELPER_NAME = "vc-systemaudio"
MIN_MACOS = (14, 2)
# names of virtual loopback drivers (lower case, matched as substrings of the device name)
LOOPBACK_DRIVERS = ("blackhole", "loopback audio", "soundflower", "background music", "vb-cable", "existential audio")
READY_TIMEOUT_S = 10.0


def macos_version() -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in platform.mac_ver()[0].split(".") if p.isdigit())
    except ValueError:
        return ()


def helper_path() -> Path:
    """The helper inside VoiceCleaner.app (Contents/MacOS), or build/macos when run from source."""
    override = os.environ.get("VOICECLEANER_SYSTEMAUDIO_HELPER")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / HELPER_NAME
    return Path(__file__).resolve().parents[2] / "build" / "macos" / HELPER_NAME


def unavailable_reason() -> str:
    """Why system audio cannot be recorded here, or "" when it can."""
    if sys.platform != "darwin":
        return "only on macOS"
    if macos_version() < MIN_MACOS:
        return "needs macOS 14.2 or newer"
    if not helper_path().is_file():
        return "the system audio helper is missing"
    return ""


def available() -> bool:
    return unavailable_reason() == ""


def is_loopback_driver(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in LOOPBACK_DRIVERS)


class SystemAudioError(Exception):
    pass


class SystemAudioRecorder:
    """Context manager streaming system audio from the helper; ``record(n)`` returns
    ``(n, 2)`` float32 frames at 48 kHz, blocking until they are there."""

    def __init__(self, samplerate: int = 48_000, exclude_pids: tuple[int, ...] | None = None):
        self.samplerate = samplerate
        self.exclude_pids = (os.getpid(),) if exclude_pids is None else exclude_pids
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}
        self._stderr_lines: list[str] = []

    def __enter__(self) -> "SystemAudioRecorder":
        cmd = [str(helper_path()), "--rate", str(self.samplerate)]
        for pid in self.exclude_pids:
            cmd += ["--exclude-pid", str(pid)]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise SystemAudioError(f"the system audio helper could not be started ({exc})") from exc
        ready = threading.Event()

        def read_stderr():  # first line says ready or error; keep reading so the pipe never fills
            for raw in self.proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                self._stderr_lines.append(line)
                if not ready.is_set():
                    try:
                        self.info = json.loads(line)
                    except ValueError:
                        self.info = {"error": line}
                    ready.set()
            ready.set()

        threading.Thread(target=read_stderr, name="systemaudio-stderr", daemon=True).start()
        if not ready.wait(READY_TIMEOUT_S) or not self.info.get("ready"):
            err = self.info.get("error") or "no answer from the system audio helper"
            self.__exit__(None, None, None)
            raise SystemAudioError(err)
        if int(self.info.get("rate", 0)) != self.samplerate or int(self.info.get("channels", 0)) != 2:
            self.__exit__(None, None, None)
            raise SystemAudioError(f"unexpected stream format {self.info}")
        log.info("System audio capture started (tap at %s Hz)", self.info.get("tap_rate"))
        return self

    def record(self, numframes: int) -> np.ndarray:
        want = numframes * 2 * 4
        buf = bytearray()
        while len(buf) < want:
            chunk = self.proc.stdout.read(want - len(buf))
            if not chunk:
                raise SystemAudioError(self._stderr_lines[-1] if len(self._stderr_lines) > 1 else
                                       "the system audio helper stopped")
            buf += chunk
        return np.frombuffer(bytes(buf), dtype=np.float32).reshape(numframes, 2)

    def __exit__(self, *exc):
        p = self.proc
        if p is None:
            return False
        try:
            if p.stdin:
                p.stdin.close()  # the helper stops and removes its tap when stdin closes
            p.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)  # teardown must finish before a routing refresh is allowed
        finally:
            if p.stdout:
                p.stdout.close()
        self.proc = None
        return False
