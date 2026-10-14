"""Recording options in the Enhance tab: the 3-second countdown (shown on the Original
track, cancellable) and 1 s of room tone recorded first, which becomes the noise profile.
A fake recorder plays the part of the microphone (room noise, then speech). Screenshots.

Usage: python tools/countdown_smoke.py <screenshot_dir>
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# isolate: never read or write the real user's preferences or Documents folder
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="vc_appdata_")
os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="vc_profile_")
os.environ["HOME"] = os.environ["USERPROFILE"]
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"
os.environ["VOICECLEANER_NO_TOUR"] = "1"

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app.ui.record_panel as rp_mod  # noqa: E402
from app.audio.recorder import InputSource, RecordingResult  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

SR = 48000
SPEECH, _ = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")


class FakeSession:
    """Stands in for RecordingSession: the take is room noise until ``speech_at`` seconds
    into the recording (when the speaker starts), then speech, with noise throughout."""
    speech_at = 0.6
    started_at: list = []

    def __init__(self, sources, gains=None, output_path=None):
        self.path = Path(output_path or tempfile.mktemp(suffix=".wav"))
        self.errors, self.all_failed = [], False

    def start(self):
        self.t0 = time.perf_counter()
        FakeSession.started_at.append(self.t0)

    @property
    def capturing(self):
        return True

    @property
    def elapsed(self):
        return time.perf_counter() - self.t0

    def levels(self):
        return [(-40.0, -30.0)]

    def silent_sources(self):
        return []

    def overview(self):
        z = np.zeros(0, np.float32)
        return z, z, z

    def stop(self):
        n = int(self.elapsed * SR)
        rng = np.random.default_rng(0)
        x = (0.01 * rng.standard_normal(n)).astype(np.float32)  # room noise
        a = int(self.speech_at * SR)
        seg = SPEECH[int(2 * SR): int(2 * SR) + max(0, n - a)]
        x[a: a + seg.size] += seg
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(self.path, x, SR, subtype="PCM_24")
        return RecordingResult(self.path, n / SR, SR, 1, ["fake mic"], [], self.t0)

    def cancel(self):
        pass


def pump(app, cond=lambda: True, timeout=60):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


def wait(app, s):
    pump(app, lambda t=time.time() + s: time.time() > t, s + 5)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    rp_mod.RecordingSession = FakeSession
    fake = InputSource("fake", "USB Microphone", "microphone", 1, True)
    rp_mod.list_sources = lambda: ([fake], [])
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    w = MainWindow(UserConfig())
    w.resize(1440, 900)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    rp = w.record_panel
    w.start_new_recording()
    pump(app)
    check("both options are off by default", not rp.countdown_box.isChecked() and not rp.roomtone_box.isChecked())

    # countdown, cancelled half-way
    rp.countdown_box.setChecked(True)
    rp.toggle()
    pump(app)
    check("countdown starts at 3, shown on the Original track and in a big banner",
          rp.counting_down and "3" in w.wave_orig.busy_text and rp.session is None
          and rp.cue.isVisible() and rp.cue.kind == "ready" and "3" in rp.cue.text)
    w.grab().save(str(out / "1_countdown.png"))
    wait(app, 1.2)
    check("it counts down", "2" in w.wave_orig.busy_text)
    rp.toggle()  # cancel
    pump(app)
    check("clicking again cancels before anything is recorded",
          not rp.counting_down and rp.session is None and not w.wave_orig.busy_text and not FakeSession.started_at)

    # countdown + room tone: recording starts after 3 s, stays quiet, then speak
    rp.roomtone_box.setChecked(True)
    t_click = time.perf_counter()
    rp.toggle()
    pump(app, lambda: rp.session is not None, 10)
    started = FakeSession.started_at[-1] - t_click
    check(f"recording starts after the countdown ({started:.1f} s)", 2.8 < started < 3.6)
    wait(app, 0.3)  # the panel updates every 40 ms
    check("then: a big STAY QUIET banner", rp.cue.isVisible() and rp.cue.kind == "quiet"
          and "STAY QUIET" in rp.cue.text and rp.cue.fraction is not None and "Stay quiet" in rp.status.text())
    w.grab().save(str(out / "2_stay_quiet.png"))
    wait(app, 1.2)
    check("after the room tone: SPEAK NOW", rp.cue.kind == "speak" and rp.status.text() == "Speak now.")
    w.grab().save(str(out / "3_speak_now.png"))
    wait(app, 2.5)
    check("the banner goes away once you are speaking", not rp.cue.isVisible())
    FakeSession.speech_at = 0.6  # a quick speaker: too short a pause for the lead-in detection
    rp.toggle()  # stop
    pump(app, lambda: w.audio is not None and not w._task_running(), 60)
    pump(app)
    check(f"the room tone is the noise profile ({w.profile_label.text()})",
          w.learned_profile is not None and "room tone" in w.profile_label.text()
          and w.wave_orig.highlight_regions and w.wave_orig.highlight_regions[0][1] <= 1.0)
    w.grab().save(str(out / "4_room_tone_learned.png"))

    # Enhance stops playback, and nothing plays until it is done
    w.record_toggle.setChecked(False)
    w._play_source("original")
    pump(app, lambda: w.player.state == "playing", 10)
    w.enhance()
    pump(app)
    check("Enhance pauses the playing recording", w.player.state != "playing")
    check("Play is unavailable while enhancing", not w.play_btn.isEnabled())
    w._toggle_main_play()  # Space
    w._play_source("enhanced")  # a waveform's Play
    pump(app)
    check("Space and Play do not start playback while enhancing", w.player.state != "playing")
    pump(app, lambda: w.result is not None and not w._task_running(), 120)
    check("Play is back when enhancing is done", w.play_btn.isEnabled() and w.player.state != "playing")
    w._toggle_main_play()
    pump(app, lambda: w.player.state == "playing", 10)
    check("and plays again", w.player.state == "playing")
    w.player.stop()

    # Append and Overdub: no room tone (it would be a gap in the middle)
    w.record_toggle.setChecked(True)
    rp.mode.setCurrentIndex(rp.mode.findData("append"))
    pump(app)
    check("room tone is off for Append", not rp.roomtone_box.isEnabled())
    rp.mode.setCurrentIndex(rp.mode.findData("new"))
    check("and back on for a new recording", rp.roomtone_box.isEnabled())

    w.close()
    saved = UserConfig.load().extra.get("record", {})
    check("the choices are remembered", saved == {"countdown": True, "room_tone": True})
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
