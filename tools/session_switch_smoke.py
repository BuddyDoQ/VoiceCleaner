"""New session / open session with a recording open on the Enhance tab: the recording is
closed, after a warning when its enhanced version was never exported (Export first, Don't
Export, Cancel). Screenshots of the warning and the cleared Enhance tab.

Usage: python tools/session_switch_smoke.py <screenshot_dir>
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
os.environ["HOME"] = os.environ["USERPROFILE"]  # macOS keeps settings under HOME (~/Library), not APPDATA
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"
os.environ["VOICECLEANER_NO_TOUR"] = "1"  # the Getting Started tour would cover the window

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.ui import main_window as mw  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

WAV = ROOT / "test_audio" / "reference" / "clean_speech.wav"


def pump(app, cond, timeout=180):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


class FakeNameDialog:
    """Stands in for the New Session name prompt."""
    Accepted = 1
    count = 0

    def __init__(self, *a, **k):
        FakeNameDialog.count += 1
        self.name = f"Smoke Session {FakeNameDialog.count}"

    def exec(self):
        return self.Accepted


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    mw.SessionNameDialog = FakeNameDialog
    w = mw.MainWindow(UserConfig())
    w.resize(1440, 960)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    def load_and_enhance():
        w.open_file(WAV)
        pump(app, lambda: w.audio is not None and not w._task_running())
        w.enhance()
        pump(app, lambda: w.result is not None and not w._task_running())

    def answer(button_text, shot=None):
        """Click ``button_text`` in the warning once it shows."""
        def go():
            box = next((t for t in app.topLevelWidgets() if isinstance(t, QMessageBox) and t.isVisible()), None)
            if box is None:
                QTimer.singleShot(100, go)
                return
            if shot:
                box.grab().save(str(out / shot))
            btn = next(b for b in box.buttons() if b.text().replace("&", "") == button_text)
            btn.click()
        QTimer.singleShot(200, go)

    def close_boxes_and_export():
        """Accept the export dialog, then close the "Export complete" box."""
        def go():
            for t in app.topLevelWidgets():
                if isinstance(t, ExportDialog) and t.isVisible():
                    t._accept()
                    QTimer.singleShot(200, go)
                    return
                if isinstance(t, QMessageBox) and t.isVisible() and "Saved" in t.text():
                    t.done(0)
                    return
            QTimer.singleShot(100, go)
        QTimer.singleShot(300, go)

    # THIS SESSION: the session's newest recordings on the drop zone
    import shutil

    for i in (1, 2):
        shutil.copy(WAV, w.session.recordings_dir / f"{w.session.name} - Rec {i:03d}.wav")
        time.sleep(0.05)
    w.tabs.setCurrentWidget(w.compile_panel)
    w.tabs.setCurrentIndex(0)
    pump(app, lambda: True)
    items = [w.drop_zone.session_list.itemAt(i).widget() for i in range(w.drop_zone.session_list.count())]
    texts = [b.text() for b in items if b is not None]
    check(f"THIS SESSION lists its recordings, newest first, short names {texts}",
          w.drop_zone.session_box.isVisible() and texts == ["Rec 002.wav", "Rec 001.wav"])
    w.grab().save(str(out / "0_this_session.png"))
    items[0].click()
    pump(app, lambda: w.audio is not None and not w._task_running())
    check("clicking one opens it", w.audio.info.path.name.endswith("Rec 002.wav"))
    w.tabs.setCurrentWidget(w.compile_panel)
    w.new_session()
    check("a new session's list is empty", not w.drop_zone.session_box.isVisibleTo(w.drop_zone))
    check("a new session starts on the Enhance tab", w.tabs.currentIndex() == 0)
    load_and_enhance()
    first = w.session.path
    check("enhanced, not exported: flagged", w.unexported_result)

    # Cancel: nothing changes
    answer("Cancel", "1_warning.png")
    w.new_session()
    check("Cancel keeps the session and the recording", w.session.path == first and w.audio is not None)

    # Export first: exported, then the new session opens with an empty Enhance tab
    answer("Export…")
    close_boxes_and_export()
    w.new_session()
    pump(app, lambda: w.session.path != first, 60)
    exported = list(Path(first, "Exports").glob("*.wav"))
    check("Export… saves the enhanced file first", len(exported) == 1)
    check("then the new session opens, Enhance tab cleared",
          w.audio is None and w.result is None and w.stack.currentIndex() == 0)
    w.grab().save(str(out / "2_cleared.png"))

    # Don't Export: cleared without saving
    load_and_enhance()
    second = w.session.path
    answer("Don’t Export")
    w.new_session()
    pump(app, lambda: w.session.path != second, 30)
    check("Don't Export: new session, recording closed, nothing exported",
          w.audio is None and not list(Path(second, "Exports").glob("*.wav")))

    # already exported: no warning at all
    load_and_enhance()
    third = w.session.path
    close_boxes_and_export()
    w.export()
    pump(app, lambda: not w.unexported_result and not w._task_running(), 60)
    shown = []
    QTimer.singleShot(300, lambda: shown.extend(t for t in app.topLevelWidgets()
                                                if isinstance(t, QMessageBox) and t.isVisible()))
    w.new_session()
    pump(app, lambda: w.session.path != third, 10)
    pump(app, lambda: True)
    check("exported: switches without a warning", not shown and w.audio is None)

    # a recording opened but never enhanced: no warning either
    w.open_file(WAV)
    pump(app, lambda: w.audio is not None and not w._task_running())
    fourth = w.session.path
    w.new_session()
    pump(app, lambda: w.session.path != fourth, 10)
    check("not enhanced: closes without a warning", w.audio is None)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
