"""Playback starts quickly and quietly: VoiceCleaner's own PortAudio (no ASIO driver
probing, no driver windows) and no console window for the Windows version."""
import subprocess
import sys

import pytest

import app  # noqa: F401 - installs the guards

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="Windows and macOS")
def test_a_system_portaudio_is_never_picked():
    import ctypes.util

    assert ctypes.util.find_library("portaudio") is None
    assert ctypes.util.find_library(r"bin\libportaudio-2.dll") is None
    import sounddevice as sd

    assert "_sounddevice_data" in sd._libname  # the library that ships with sounddevice
    assert "ASIO" not in [h["name"] for h in sd.query_hostapis()]


@windows
def test_windows_version_without_starting_a_process(monkeypatch):
    import platform

    def no_process(*a, **k):
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "check_output", no_process)
    platform._uname_cache = None
    assert platform.system() == "Windows"
    assert platform.version().count(".") == 2
