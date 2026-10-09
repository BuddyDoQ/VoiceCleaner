"""Screenshots of the branded UI in night and day mode: Enhance, Playback, About, New Session.

Uses a temporary profile folder, so no test sessions are created in Documents.
Usage: python tools/brand_smoke.py <screenshot_dir>
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# isolate: never read or write the real user's preferences or Documents folder
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402

_os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="vc_appdata_")
_os.environ["USERPROFILE"] = _tempfile.mkdtemp(prefix="vc_profile_")
_os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"  # never contact GitHub from a smoke test

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import sessions  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.brand import AboutDialog, SessionNameDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402


def pump(app, cond, timeout=180):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


def grab_dialog(app, dlg, path):
    def snap():
        dlg.grab().save(str(path))
        dlg.reject()
    QTimer.singleShot(400, snap)
    dlg.exec()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    w = MainWindow(UserConfig())
    w.resize(1440, 960)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    print("session:", w.session.name, w.session.path)
    # populate the session with a "recording" and an export, plus an invalid file
    for name in ("speech_hvac.wav", "clean_speech.wav"):
        shutil.copy(ROOT / "test_audio" / name, w.session.recording_path())
    (w.session.recordings_dir / "broken.wav").write_bytes(b"RIFF....garbage")
    w.open_file(w.session.recording_path().with_name(f"{w.session.name} - Rec 001.wav"))
    pump(app, lambda: w.audio is not None and not w._task_running())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    for mode in ("night", "day"):
        w.set_theme(mode)
        w.tabs.setCurrentIndex(0)
        pump(app, lambda: True)
        w.grab().save(str(out / f"{mode}_enhance.png"))
        w.tabs.setCurrentWidget(w.playback)
        pump(app, lambda: w.playback.table.rowCount() > 0, 30)
        w.playback.table.selectRow(0)
        pump(app, lambda: w.playback.player.has_source("original"), 30)
        w.grab().save(str(out / f"{mode}_playback.png"))
        print(mode, "| playback rows:", w.playback.table.rowCount(), "|", w.playback.status.text())
        grab_dialog(app, AboutDialog(["AI speech enhancement: DeepFilterNet3 (MIT / Apache-2.0)",
                                      "Voice re-synthesis: NVIDIA BigVGAN-v2 (MIT)"], w), out / f"{mode}_about.png")
    d = SessionNameDialog("New Session", "Episode 12: Client Interview / Final", "Create Session", w)
    grab_dialog(app, d, out / "day_new_session.png")
    print("sanitized name:", d.name)
    # all-recordings and browse-folder sources
    w.playback.mode = "all"
    w.playback.refresh()
    pump(app, lambda: "playable" in w.playback.status.text(), 30)
    print("all recordings:", w.playback.status.text())
    w.playback.mode, w.playback.folder = "folder", ROOT / "test_audio"
    w.playback.refresh()
    pump(app, lambda: "playable" in w.playback.status.text() and str(ROOT / "test_audio") in w.playback.location.text(), 60)
    print("browse test_audio:", w.playback.status.text())
    # new session switches folders and names
    s2 = sessions.create("Client Interview")
    w._switch_session(s2)
    print("new session recording name:", w.session.recording_path().name, "| title:", w.windowTitle())
    w.close()


if __name__ == "__main__":
    main()
