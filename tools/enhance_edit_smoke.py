"""Drive Edit Take in the Enhance tab: open a recording, enhance it, drag a selection (the
playhead goes to its start), right-click > Edit Take, cut and silence a part, check both
tracks show and play the edits, that they are saved where Compile finds them, and that
the export leaves the cut out. Screenshots of the menu, the editor and the edited tracks.

Usage: python tools/enhance_edit_smoke.py <screenshot_dir>
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

import soundfile as sf  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

from app.audio.compile import load_edits  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.take_editor import TakeEditor  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

CUT = (1.0, 1.5)
MUTE = (2.0, 2.3)


def pump(app, cond, timeout=120):
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
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    w = MainWindow(UserConfig())
    w.resize(1440, 960)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    speech, sr = sf.read(ROOT / "test_audio" / "reference" / "speech_white_noise.wav", dtype="float32")
    rec = w.session.recording_path()
    rec.parent.mkdir(parents=True, exist_ok=True)
    sf.write(rec, speech[: int(5.0 * sr)], sr, subtype="PCM_24")
    w.open_file(rec)
    pump(app, lambda: w.audio is not None and not w._task_running())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running(), 300)

    # drag a selection on the enhanced track: the playhead goes to its start
    wv = w.wave_enh
    r = wv._plot_rect()

    def mouse(kind, x):
        btns = Qt.NoButton if kind == QMouseEvent.MouseButtonRelease else Qt.LeftButton
        return QMouseEvent(kind, QPointF(x, r.center().y()), QPointF(x, r.center().y()), Qt.LeftButton, btns,
                           Qt.NoModifier)

    w._seek(0.0)
    xa, xb = wv._t_to_x(3.0), wv._t_to_x(3.8)
    wv.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, xb))  # right to left: the start is the left end
    wv.mouseMoveEvent(mouse(QMouseEvent.MouseMove, xa))
    wv.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, xa))
    pump(app, lambda: True)
    sel = w.selection
    check("dragging selects on both tracks", sel is not None and w.wave_orig.selection == sel)
    check(f"the playhead is at the start of the selection ({w.player.position:.3f} s)",
          sel is not None and abs(w.player.position - sel[0]) < 0.01 and abs(wv.playhead - sel[0]) < 0.01)
    w._selection_changed(None)

    # right-click > Edit Take
    def drive_menu():
        menu = next((t for t in app.topLevelWidgets() if isinstance(t, QMenu) and t.isVisible()), None)
        if menu is None:
            QTimer.singleShot(100, drive_menu)
            return
        menu.grab().save(str(out / "1_context_menu.png"))
        act = next(a for a in menu.actions() if "Edit Take" in a.text())
        check("the menu offers Edit Take", act.isEnabled())
        menu.close()
        QTimer.singleShot(0, act.trigger)

    def drive_editor():
        dlg = next((t for t in app.topLevelWidgets() if isinstance(t, TakeEditor) and t.isVisible()), None)
        if dlg is None:
            QTimer.singleShot(100, drive_editor)
            return
        dlg.resize(1200, 480)
        check("the editor shows the enhanced track", "Enhanced" in dlg.windowTitle()
              and dlg.take.samples is w.result.samples)
        check("it speaks of the export", "in the export" in dlg.info.text())
        dlg.view.set_selection(CUT)
        dlg.apply("cut")
        dlg.view.set_selection(MUTE)
        dlg.apply("mute")
        pump(app, lambda: True)
        dlg.grab().save(str(out / "2_editor.png"))
        dlg.accept()

    QTimer.singleShot(200, drive_menu)
    QTimer.singleShot(300, drive_editor)
    w._wave_menu(wv, QPoint(200, 60))  # modal
    pump(app, lambda: bool(w.edits), 30)
    kinds = sorted(k for _a, _b, k in w.edits)
    check("the edits are on the recording", kinds == ["cut", "mute"])
    check("the Edit Take button shows the count", w.edit_btn.isEnabled() and w.edit_btn.text().endswith("2"))
    check("both tracks show them", w.wave_orig.edit_regions == w.wave_enh.edit_regions == w.edits)
    check("the player skips the cut and silences the other", len(w.player._cuts) == 1 and len(w.player._mutes) == 1)
    idx, _ = w.player._positions(int(0.9 * sr), int(0.2 * sr))
    check("playing over the cut jumps past it", int(idx.max()) >= int(CUT[1] * sr)
          and not ((idx >= int(CUT[0] * sr) + 1) & (idx < int(CUT[1] * sr) - 1)).any())
    saved = load_edits(w.session.path).get(rec.name, [])
    check("saved with the session under the recording's name (Compile's key)", len(saved) == 2)
    pump(app, lambda: True)
    w.grab().save(str(out / "3_enhance_edited_dark.png"))
    w.set_theme("day")
    pump(app, lambda: True)
    w.grab().save(str(out / "4_enhance_edited_light.png"))
    w.set_theme("night")
    check("an edited result counts as not exported", w.unexported_result)

    # export: the cut is left out
    def accept_export():
        for top in app.topLevelWidgets():
            if isinstance(top, ExportDialog) and top.isVisible():
                top._accept()
                break
            if top.isVisible() and isinstance(top, QMessageBox):
                top.done(0)
                return
        QTimer.singleShot(100, accept_export)

    QTimer.singleShot(200, accept_export)
    w.export()
    exported = lambda: list(w.session.exports_dir.glob("*.wav"))  # noqa: E731
    pump(app, lambda: exported() and not w._task_running(), 60)
    d = sf.info(exported()[0]).duration
    print(f"export {d:.3f} s, enhanced {w.result.duration:.3f} s")
    check("the export leaves the cut out", abs((w.result.duration - d) - (CUT[1] - CUT[0])) < 0.02)
    check("exported with these edits", not w.unexported_result)

    def reopen_by_button():
        seen = []

        def close_editor():
            dlg = next((t for t in app.topLevelWidgets() if isinstance(t, TakeEditor) and t.isVisible()), None)
            if dlg is None:
                QTimer.singleShot(100, close_editor)
                return
            seen.append(dlg)
            dlg.reject()

        QTimer.singleShot(100, close_editor)
        w.edit_btn.click()
        return bool(seen)

    # reopening the recording brings the edits back; Clear Edits removes them
    w.open_file(rec)
    pump(app, lambda: not w._task_running())
    check("edits restored when the recording is opened again", len(w.edits) == 2 and w.wave_enh.edit_regions)
    w._set_edits([])
    check("Clear Edits removes them everywhere", not w.edits and not w.player._cuts
          and not load_edits(w.session.path).get(rec.name))
    check("the Edit Take button opens the editor", reopen_by_button())
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
