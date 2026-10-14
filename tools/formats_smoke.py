"""Recordings that are not WAV: a phone memo (M4A) opened, enhanced and exported in the
Enhance tab, and a Zoom-style MP4 plus an MP3 and the memo through Batch. Screenshots.

Usage: python tools/formats_smoke.py <screenshot_dir>
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

import subprocess  # noqa: E402

import soundfile as sf  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.audio import decode  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

SRC = ROOT / "test_audio" / "speech_hvac.wav"


def pump(app, cond=lambda: True, timeout=180):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    ff = decode.ffmpeg_path()
    if ff is None:
        sys.exit("FFmpeg not found: run python tools/fetch_ffmpeg.py ffmpeg")
    media = Path(tempfile.mkdtemp(prefix="vc_media_"))
    q = [str(ff), "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(q + ["-i", str(SRC), "-c:a", "aac", "-b:a", "96k", str(media / "Voice Memo 14.m4a")], check=True)
    subprocess.run(q + ["-f", "lavfi", "-i", "color=c=navy:s=320x240:r=15", "-i", str(SRC), "-shortest",
                        "-c:v", "mpeg4", "-c:a", "aac", str(media / "zoom_0.mp4")], check=True)
    subprocess.run(q + ["-i", str(SRC), "-c:a", "libmp3lame", str(media / "interview.mp3")], check=True)

    setup_logging()
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

    w.grab().save(str(out / "1_drop_zone.png"))
    memo = media / "Voice Memo 14.m4a"
    w.open_file(memo)
    pump(app, lambda: w.audio is not None and not w._task_running())
    meta = w.meta_chips.itemAt(0).widget().text()
    check(f"the phone memo opens ({meta})", w.audio.info.path == memo and "AAC (M4A)" in meta)
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    w.grab().save(str(out / "2_memo_enhanced.png"))

    def accept():
        for t in app.topLevelWidgets():
            if isinstance(t, ExportDialog) and t.isVisible():
                t._accept()
                QTimer.singleShot(300, close_box)
                return
        QTimer.singleShot(100, accept)

    def close_box():
        boxes = [t for t in app.topLevelWidgets() if isinstance(t, QMessageBox) and t.isVisible()]
        for b in boxes:
            b.done(0)
        if not boxes:
            QTimer.singleShot(200, close_box)

    QTimer.singleShot(300, accept)
    w.export()
    exports = w.session.exports_dir
    pump(app, lambda: any(exports.glob("*Voice Memo 14*")) and not w._task_running(), 60)
    exported = next(exports.glob("*Voice Memo 14*"))
    info = sf.info(str(exported))
    check(f"exported as a WAV named after the memo ({exported.name})",
          exported.suffix == ".wav" and abs(info.duration - w.audio.duration) < 0.05)

    # batch: a Zoom recording (video), an MP3 and the memo
    files = [media / "zoom_0.mp4", media / "interview.mp3", memo]
    w.batch_panel.add_files(files)
    w.tabs.setCurrentWidget(w.batch_page)
    pump(app)
    w.grab().save(str(out / "3_batch_added.png"))
    w._start_batch(files, "session")
    pump(app, lambda: w._batch is None, 300)
    w.grab().save(str(out / "4_batch_done.png"))
    for f in files:
        hits = [p for p in exports.glob("*.wav") if f.stem in p.name]
        check(f"batch: {f.name} enhanced to {hits[0].name if hits else 'nothing'}", bool(hits))
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
