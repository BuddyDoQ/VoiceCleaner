"""Drive the real window through recording: new take, append, overdub.

Plays test speech into a (silent) virtual output device and records it back via
desktop-audio loopback, so nothing is heard on the speakers.

Usage: python tools/record_smoke.py <screenshot_dir> [output device name fragment]
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402


def pump(app, cond, timeout=60):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(5):
        app.processEvents()


def sleep_pumping(app, seconds):
    t0 = time.time()
    pump(app, lambda: time.time() - t0 >= seconds, seconds + 5)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    fragment = sys.argv[2] if len(sys.argv) > 2 else "Steam Streaming Speakers"
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.STYLESHEET)
    w = MainWindow(UserConfig())
    w.resize(1440, 900)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())

    dev = next(i for i, d in enumerate(sd.query_devices()) if fragment in d["name"]
               and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI" and d["max_output_channels"])
    speech, sr = sf.read(ROOT / "test_audio" / "speech_white_noise.wav", dtype="float32")

    def take(seconds, mode=None):
        rp = w.record_panel
        if mode:
            rp.mode.setCurrentIndex(rp.mode.findData(mode))
        rp.monitor.setChecked(False)
        rp.mic_row.combo.setCurrentIndex(0)  # no microphone
        idx = next(i for i in range(1, rp.desk_row.combo.count()) if fragment in rp.desk_row.combo.itemText(i))
        rp.desk_row.combo.setCurrentIndex(idx)
        rp.toggle()
        pump(app, lambda: rp.session is not None and rp.session.capturing, 15)
        clip = speech[: int(seconds * sr)]
        sd.play(np.stack([clip, clip], 1), sr, device=dev)
        sleep_pumping(app, seconds * 0.6)
        w.grab().save(str(out / f"rec_{mode or 'new'}_live.png"))
        sleep_pumping(app, seconds * 0.4 + 0.3)
        rp.toggle()
        before = w.audio
        pump(app, lambda: w.audio is not None and w.audio is not before and not w._task_running(), 60)
        sleep_pumping(app, 0.3)
        w.grab().save(str(out / f"rec_{mode or 'new'}_done.png"))
        a = w.audio
        print(f"{mode or 'new':8s} -> {a.info.path.name}  {a.duration:.2f}s  {a.channels}ch  "
              f"peak {np.abs(a.samples).max():.3f}  warnings={w.warning_label.text()[:120]!r}")
        return a

    w.start_new_recording()
    sleep_pumping(app, 0.5)
    w.grab().save(str(out / "rec_panel_open.png"))
    first = take(3.0)
    w.record_toggle.setChecked(True)
    w.player.seek(0.0)
    appended = take(2.0, "append")
    assert abs(appended.duration - (first.duration + 2.0)) < 0.6, (appended.duration, first.duration)
    w.record_toggle.setChecked(True)
    w.player.seek(1.0)
    w.record_panel.playhead = 1.0
    over = take(2.0, "overdub")
    assert abs(over.duration - appended.duration) < 0.6 or over.duration >= appended.duration
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running(), 120)
    w.grab().save(str(out / "rec_enhanced.png"))
    print("enhanced:", w.result.stages)
    w.close()


if __name__ == "__main__":
    main()
