"""Desktop audio on macOS: source listing, loopback drivers, the system-audio helper protocol
(with a fake helper, so this runs anywhere) and, on a capable Mac, the real helper."""
import json
import os
import stat
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.audio import mac_system_audio as msa
from app.audio import recorder

FAKE_HELPER = '''#!{python}
import json, sys, threading
import numpy as np
mode = {mode!r}
if mode == "error":
    sys.stderr.write(json.dumps({{"error": "could not create the system audio tap (-1)"}}) + "\\n"); sys.exit(1)
sys.stderr.write(json.dumps({{"ready": True, "rate": 48000, "channels": 2, "tap_rate": 44100}}) + "\\n")
sys.stderr.flush()
stop = threading.Event()
threading.Thread(target=lambda: (sys.stdin.read(), stop.set()), daemon=True).start()
n = 0
block = 480
while not stop.is_set():
    if mode == "ramp":
        t = 0.8 * (np.arange(n, n + block) % 1000) / 1000.0  # below full scale: no clipping warning
        frames = np.stack([t, -t], axis=1).astype(np.float32)
    else:
        frames = np.zeros((block, 2), np.float32)
    try:
        sys.stdout.buffer.write(frames.tobytes()); sys.stdout.buffer.flush()
    except BrokenPipeError:
        break
    n += block
    stop.wait(block / 48000)
'''


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    def make(mode="ramp"):
        path = tmp_path / f"vc-systemaudio-{mode}"
        path.write_text(FAKE_HELPER.format(python=sys.executable, mode=mode))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        if sys.platform == "win32":  # Windows cannot start a "#!" script: launch it through Python
            script, path = path, tmp_path / f"vc-systemaudio-{mode}.cmd"
            path.write_text(f'@"{sys.executable}" "{script}"' + chr(10))
        monkeypatch.setenv("VOICECLEANER_SYSTEMAUDIO_HELPER", str(path))
        return path
    return make


@pytest.mark.parametrize("name, driver", [
    ("BlackHole 2ch", True), ("BlackHole 16ch", True), ("Loopback Audio", True), ("Soundflower (2ch)", True),
    ("MacBook Pro Microphone", False), ("Shure MV7", False), ("External Microphone", False),
])
def test_loopback_driver_names(name, driver):
    assert msa.is_loopback_driver(name) is driver


def test_unavailable_reasons(monkeypatch, tmp_path):
    helper = tmp_path / "vc-systemaudio"
    helper.write_text("x")
    monkeypatch.setenv("VOICECLEANER_SYSTEMAUDIO_HELPER", str(helper))
    monkeypatch.setattr(msa.sys, "platform", "darwin")
    monkeypatch.setattr(msa, "macos_version", lambda: (14, 1))
    assert msa.unavailable_reason() == "needs macOS 14.2 or newer"
    monkeypatch.setattr(msa, "macos_version", lambda: (14, 2))
    assert msa.unavailable_reason() == "" and msa.available()
    helper.unlink()
    assert "missing" in msa.unavailable_reason()
    monkeypatch.setattr(msa.sys, "platform", "win32")
    assert not msa.available()


class _Dev:
    def __init__(self, id, name, channels=1, isloopback=False):
        self.id, self.name, self.channels, self.isloopback = id, name, channels, isloopback


def test_mac_listing_puts_system_audio_and_drivers_under_desktop(monkeypatch):
    calls = []

    class FakeSC:
        @staticmethod
        def all_microphones(include_loopback=False):
            calls.append(include_loopback)
            return [_Dev("m1", "MacBook Pro Microphone"), _Dev("bh", "BlackHole 2ch", 2)]

        @staticmethod
        def default_microphone():
            return _Dev("m1", "MacBook Pro Microphone")

        @staticmethod
        def default_speaker():
            return _Dev("s1", "MacBook Pro Speakers")

    monkeypatch.setattr(recorder, "_soundcard", lambda: FakeSC)
    monkeypatch.setattr(recorder.sys, "platform", "darwin")
    monkeypatch.setattr(msa, "available", lambda: True)
    mics, desktop = recorder.list_sources()
    assert [m.name for m in mics] == ["MacBook Pro Microphone"]
    assert {d.name for d in desktop} == {"BlackHole 2ch", msa.SYSTEM_AUDIO_NAME}
    assert all(d.kind == "desktop" for d in desktop)
    assert calls == [False]  # no loopback request on macOS (soundcard only warns there)

    monkeypatch.setattr(msa, "available", lambda: False)  # e.g. macOS 13: drivers only
    assert [d.name for d in recorder.list_sources()[1]] == ["BlackHole 2ch"]


def test_recorder_streams_frames(fake_helper):
    fake_helper("ramp")
    with msa.SystemAudioRecorder(48_000, exclude_pids=(123,)) as rec:
        assert rec.info["ready"] and rec.info["tap_rate"] == 44100
        a = rec.record(1000)
        b = rec.record(500)
    assert a.shape == (1000, 2) and b.shape == (500, 2) and a.dtype == np.float32
    assert np.allclose(a[:, 1], -a[:, 0])  # channels kept apart, in order
    assert np.allclose(np.diff(a[:200, 0]), 0.0008)  # contiguous frames
    assert rec.proc is None  # helper stopped when the recorder closed


def test_helper_error_is_reported(fake_helper):
    fake_helper("error")
    with pytest.raises(msa.SystemAudioError, match="could not create the system audio tap"):
        with msa.SystemAudioRecorder(48_000):
            pass


def test_session_records_system_audio_and_warns_on_silence(fake_helper, tmp_path, monkeypatch):
    src = recorder.InputSource(msa.SYSTEM_AUDIO_ID, msa.SYSTEM_AUDIO_NAME, "desktop", 2)
    fake_helper("ramp")
    s = recorder.RecordingSession([src], out_dir=tmp_path)
    s.start()
    time.sleep(1.5)  # the helper takes a moment to start (longer on Windows, via cmd)
    res = s.stop()
    x, sr = sf.read(res.path, dtype="float32", always_2d=True)
    assert sr == 48_000 and x.shape[1] == 2 and res.duration > 0.3 and np.abs(x).max() > 0.5
    assert not res.warnings

    fake_helper("silence")
    s = recorder.RecordingSession([src], out_dir=tmp_path)
    s.start()
    time.sleep(0.5)
    res = s.stop()
    assert any("System audio delivered only silence" in w and "System Audio Recording" in w for w in res.warnings)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_real_helper_streams_in_real_time():
    """The built helper (tools/build_mac.py) keeps real time even when nothing plays."""
    if not msa.available():
        pytest.skip(msa.unavailable_reason() or "unavailable")
    t0 = time.time()
    with msa.SystemAudioRecorder(48_000) as rec:
        frames = sum(rec.record(4800).shape[0] for _ in range(10))  # 1 s
    wall = time.time() - t0
    assert frames == 48_000 and wall < 3.0
