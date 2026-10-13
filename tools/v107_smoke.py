"""Drive the 1.0.7 changes in the real window: settings sidebar in the Batch tab, batch
results (open folder, double-click to compare), recent files on the start screen, the
last tab reopened, confirmation before quitting mid-work, hiding the Smart note, and
sliders tall enough for their knobs. Saves screenshots in both themes.

Usage: python tools/v107_smoke.py <wav> <screenshot_dir>
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# isolate: never read or write the real user's preferences or Documents folder
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402

_os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="vc_appdata_")
_os.environ["USERPROFILE"] = _tempfile.mkdtemp(prefix="vc_profile_")
_os.environ["HOME"] = _os.environ["USERPROFILE"]
_os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"
_os.environ["VOICECLEANER_NO_TOUR"] = "1"  # the Getting Started tour would cover the window

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSlider  # noqa: E402

from app.ui import theme  # noqa: E402
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
    for _ in range(10):
        app.processEvents()


def window(app, theme_name="night"):
    cfg = UserConfig.load()
    cfg.extra["theme"] = theme_name
    theme.apply(app, theme_name)
    w = MainWindow(cfg)
    w.resize(1360, 880)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    return w


def main():
    wav, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    # ---- start screen with recent files
    cfg = UserConfig.load()
    cfg.extra["recent_files"] = [str(wav), str(ROOT / "test_audio" / "missing.wav")]
    cfg.save()
    w = window(app)
    names = [w.drop_zone.recent_list.itemAt(i).widget().text() for i in range(w.drop_zone.recent_list.count())]
    check(f"start screen lists recent files that exist ({names})", names == [wav.name])
    w.grab().save(str(out / "1_start_night.png"))
    w.drop_zone.recentClicked.emit(str(wav))
    pump(app, lambda: w.audio is not None and not w._task_running())
    check("clicking a recent file opens it", w.audio.info.path == wav)

    # ---- sliders are tall enough for their knobs
    short = [s for s in w.findChildren(QSlider) if s.isVisible() and
             (s.height() if s.orientation().name == "Horizontal" else s.width()) < 18]
    check(f"every visible slider leaves room for its knob ({len(short)} too small)", not short)

    # ---- the settings sidebar in the Batch tab
    sp = w.settings_panel
    w.tabs.setCurrentWidget(w.batch_page)
    pump(app, lambda: True)
    check("Batch tab shows the settings sidebar", w.sidebar.parent() is w.batch_page and w.sidebar.isVisible())
    check("Enhance/Export hidden there", not w.sidebar_actions.isVisible())
    check("Smart note speaks of the batch", "each file in the batch" in sp.smart_note_text.text())
    w.batch_panel.add_files([wav])
    w.grab().save(str(out / "2_batch_night.png"))
    w.tabs.setCurrentIndex(0)
    pump(app, lambda: True)
    check("back on Enhance the sidebar returns with its buttons",
          w.sidebar.parent() is w.tabs.widget(0) and w.sidebar_actions.isVisible())
    check("Smart note speaks of the recording again", "this recording" in sp.smart_note_text.text())

    # ---- batch results: open folder, double-click to compare
    w.tabs.setCurrentWidget(w.batch_page)
    w.batch_panel._start()
    pump(app, lambda: w._batch is None and w.batch_panel.outputs, 300)
    bp = w.batch_panel
    check("Open Output Folder appears when the batch is done", bp.open_out_btn.isVisible())
    result = bp.outputs[0]
    check(f"result recorded ({result.name})", result.exists())
    bp._open_row(0, 0)
    pump(app, lambda: w.audio is not None and w.audio.info.path == result and not w._task_running(), 60)
    check("double-click opens the result in Enhance", w.tabs.currentIndex() == 0 and w.audio.info.path == result)

    # ---- hide the Smart note; Show Tips Again
    sp.hide_smart_note()
    check("× hides the Smart note", sp.smart_note.isHidden() and w.config.extra.get("smart_note_hidden") is True)
    w._show_tips()
    check("Show Tips Again brings it back", not sp.smart_note.isHidden())
    sp.hide_smart_note()

    # ---- confirm before quitting mid-work
    asked = []

    def answer():
        for top in app.topLevelWidgets():
            if isinstance(top, QMessageBox) and top.isVisible():
                asked.append(top.informativeText())
                top.grab().save(str(out / "3_quit_prompt.png"))
                keep = [b for b in top.buttons() if b.text() == "Keep Working"][0]
                keep.click()
                return
        QTimer.singleShot(50, answer)

    w.enhance()
    pump(app, lambda: w._task_running(), 10)
    QTimer.singleShot(100, answer)
    w.close()
    pump(app, lambda: bool(asked), 10)
    check(f"quitting mid-enhance asks first ({asked[0][:45]}...)", "still enhancing" in asked[0])
    check("Keep Working keeps the window open", w.isVisible())
    pump(app, lambda: not w._task_running(), 120)

    # ---- last tab and hidden note survive a restart
    w.tabs.setCurrentWidget(w.batch_page)
    w.close()
    w = window(app, "day")
    check("reopens on the Batch tab", w.tabs.currentWidget() is w.batch_page)
    check("Batch tab has the sidebar right away", w.sidebar.parent() is w.batch_page)
    check("Smart note stays hidden after a restart", w.settings_panel.smart_note.isHidden())
    w.grab().save(str(out / "4_batch_day.png"))
    w.tabs.setCurrentIndex(0)
    pump(app, lambda: True)
    w.grab().save(str(out / "5_start_day.png"))
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
