"""Drive the 1.0.6 features in the real window: processing indicators (waveform progress,
header activity chip, tab badge, elapsed time), loop playback, undo/redo of settings,
live preview and the export prompt after changes. Saves screenshots.

Usage: python tools/v106_smoke.py <wav> <screenshot_dir>
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

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

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

    w = MainWindow(UserConfig.load())
    w.resize(1360, 880)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    w.open_file(wav)
    pump(app, lambda: w.audio is not None and not w._task_running())
    sp = w.settings_panel
    sp.smart.setChecked(False)
    pump(app, lambda: True)

    # ---- processing indicators during Enhance
    seen = {"chip": False, "badge": False, "wave": False, "eta": False}

    def watch():
        if w.activity_chip.isVisible() and "ENHANCING" in w.activity_chip.text():
            seen["chip"] = True
        if "·" in w.tabs.tabText(0) or "•" in w.tabs.tabText(0):
            seen["badge"] = True
        if w.wave_enh.busy_fraction is not None:
            seen["wave"] = True
        if "elapsed" in w.speed_label.text():
            seen["eta"] = True
        if not seen.get("shot") and seen["chip"] and seen["wave"]:
            w.grab().save(str(out / "1_enhancing.png"))
            seen["shot"] = True

    sp._advanced["resynthesis"].set_value(0.3, emit=True)  # a longer job, so the indicators show
    pump(app, lambda: True)
    w.enhance()
    while w._task_running():
        watch()
        app.processEvents()
        time.sleep(0.02)
    pump(app, lambda: True)
    check("header activity chip while enhancing", seen["chip"])
    check("ENHANCE tab badge while enhancing", seen["badge"])
    check("progress on the Enhanced waveform", seen["wave"])
    check("elapsed time under the progress bar", seen["eta"])
    check("chip and badge gone afterwards", not w.activity_chip.isVisible() and w.tabs.tabText(0) == "ENHANCE")

    # ---- loop
    w.player.seek(0.0)
    w._selection_changed((2.0, 3.0))
    w.loop_btn.setChecked(True)
    check("loop region set on the player", w.player.loop == (2.0, 3.0))
    w._activate("enhanced")
    w.player.play()
    pump(app, lambda: w.player.state == "playing", 5)
    t0 = time.time()
    positions = []
    while time.time() - t0 < 2.6:
        app.processEvents()
        positions.append(w.player.position)
        time.sleep(0.02)
    w.player.pause()
    check(f"playback stays inside the loop (min {min(positions):.2f}, max {max(positions):.2f})",
          min(positions) >= 1.99 and max(positions) <= 3.01)
    check("playback wrapped at least once", any(b < a - 0.5 for a, b in zip(positions, positions[1:])))
    w.grab().save(str(out / "2_loop.png"))
    w.loop_btn.setChecked(False)
    w._selection_changed(None)
    check("loop off", w.player.loop is None)

    # ---- undo / redo
    before = sp.current().leveler_amount
    sp._advanced["leveler_amount"].set_value(0.95, emit=True)
    pump(app, lambda: not sp._settle.isActive(), 5)
    sp._advanced["comp_ratio"].set_value(6.0, emit=True)
    pump(app, lambda: not sp._settle.isActive(), 5)
    check(f"undo label names the change ({sp.undo_text()!r})", sp.undo_text() == "Undo Ratio")
    sp.undo()
    check("undo restores the ratio", sp.current().comp_ratio != 6.0 and sp.current().leveler_amount == 0.95)
    sp.undo()
    check("second undo restores leveling", sp.current().leveler_amount == before)
    sp.redo()
    check("redo re-applies leveling", sp.current().leveler_amount == 0.95)

    # ---- live preview
    w.live_box.setChecked(True)
    w.player.seek(4.0)
    base = np.array(w.result.samples[int(4.5 * w.result.sample_rate): int(9 * w.result.sample_rate)])
    sp._advanced["eq_tilt"].set_value(0.8, emit=True)  # a clearly audible change
    t0 = time.time()
    pump(app, lambda: w.wave_enh.preview_region is not None, 30)
    dt = time.time() - t0
    region = w.wave_enh.preview_region
    check(f"live preview appears {dt:.1f} s after the change, region {region[0]:.1f}-{region[1]:.1f} s",
          region[0] <= 4.0 <= region[1])
    a = int(4.5 * w.result.sample_rate)
    heard = w.player._read_plain("enhanced", a, base.shape[0])[:, 0] / max(1e-9, w.player._gains["enhanced"])
    diff = float(np.sqrt(np.mean((heard - base[:, 0]) ** 2)) / (np.sqrt(np.mean(base[:, 0] ** 2)) + 1e-12))
    check(f"the enhanced track plays the preview there (relative change {diff:.2f})", diff > 0.05)
    w.grab().save(str(out / "3_live_preview.png"))
    print("     status:", w.speed_label.text())

    # ---- export after changes asks first
    asked = []

    def answer():
        for top in app.topLevelWidgets():
            if isinstance(top, QMessageBox) and top.isVisible():
                asked.append(top.text())
                top.grab().save(str(out / "4_export_prompt.png"))
                top.reject()
                return
        QTimer.singleShot(100, answer)

    QTimer.singleShot(150, answer)
    w.export()
    pump(app, lambda: bool(asked), 10)
    check("export asks to enhance again after changes", "changed since the last Enhance" in asked[0])

    # ---- returning to the enhanced settings clears the preview
    sp.undo()  # the tilt change
    sp.undo()  # the leveling change: now exactly what was enhanced
    pump(app, lambda: not sp._settle.isActive(), 5)
    check("back at the enhanced settings", w.enhance_btn.text() == "Enhance")
    pump(app, lambda: w.wave_enh.preview_region is None, 5)
    check("undoing back to the enhanced settings removes the preview", w.wave_enh.preview_region is None)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
