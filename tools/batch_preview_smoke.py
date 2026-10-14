"""Batch tab Preview switch: while on, the selected file is rendered as a 10-second sample
with the current settings (A/B at equal loudness); clicking another file previews that one,
setting changes render again, off hides it. Screenshots.

Usage: python tools/batch_preview_smoke.py <screenshot_dir>
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
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

FILES = [ROOT / "test_audio" / "speech_hvac.wav", ROOT / "test_audio" / "speech_white_noise.wav",
         ROOT / "test_audio" / "speech_hum.wav"]


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

    bp = w.batch_panel

    def rendered(name):
        return lambda: bp.preview_state.text().startswith("Rendered") and bp.preview_title.text().startswith(name)

    w.tabs.setCurrentWidget(w.batch_page)
    check("the switch is off and disabled without files", not bp.preview_on and not bp.preview_btn.isEnabled())
    bp.add_files(FILES)
    pump(app)
    check("Preview is a switch", bp.preview_btn.isCheckable() and bp.preview_btn.isEnabled())
    bp.preview_btn.click()  # on, nothing selected yet
    pump(app)
    check("on without a selection: asks for a file",
          bp.preview_on and bp.preview_card.isVisible() and "Select a file" in bp.preview_title.text())
    w.grab().save(str(out / "0_select_a_file.png"))

    bp.table.selectRow(0)  # clicking a file previews it
    pump(app, rendered(FILES[0].name), 120)
    enh, sr = bp.player._buffers["enhanced"], bp.player._sr
    check(f"clicking a file renders its sample ({enh.shape[0] / sr:.1f} s)", 9.5 < enh.shape[0] / sr < 11.0)
    check("original and enhanced at equal loudness", bp.player._match["original"] != 1.0)
    w.grab().save(str(out / "1_preview.png"))

    w._toggle_play_current()
    pump(app, lambda: bp.player.state == "playing", 10)
    check("Space plays the sample", bp.player.state == "playing")
    w._ab_shortcut("original")
    pump(app)
    check("A switches to the original", bp.player.active_source == "original" and bp.ab_orig.isChecked())
    w._ab_shortcut("enhanced")
    bp.player.stop()

    bp.table.selectRow(1)  # another file: previewed by itself
    pump(app, rendered(FILES[1].name), 120)
    check("clicking another file previews that one", bp.preview_path == FILES[1])

    before = bp.player._buffers["enhanced"].copy()
    w.settings_panel.preset.setCurrentText("Podcast")
    pump(app, lambda: bp.preview_state.text().startswith("Rendering"), 10)
    pump(app, rendered(FILES[1].name), 120)
    after = bp.player._buffers["enhanced"]
    check("a setting change renders the sample again", after.shape == before.shape and not np.array_equal(after, before))
    w.set_theme("day")
    pump(app)
    w.grab().save(str(out / "2_preview_light.png"))
    w.set_theme("night")

    bp.preview_btn.click()  # off
    pump(app)
    check("off: the sample stops and the card hides",
          not bp.preview_on and not bp.preview_card.isVisible() and bp.player.state != "playing")
    bp.table.selectRow(2)
    pump(app)
    check("off: clicking a file does nothing", not bp.preview_card.isVisible())

    bp.toggle_preview()  # what P does
    pump(app, rendered(FILES[2].name), 120)
    check("P turns it on again for the selected file", bp.preview_on and bp.preview_path == FILES[2])

    bp._remove_selected()  # the previewed file leaves the list
    pump(app)
    target = bp.selected_path()
    if target is not None:
        pump(app, rendered(target.name), 120)
        check(f"removing the previewed file moves on to {target.name}", bp.preview_path == target)
    else:
        check("removing the previewed file asks for another", "Select a file" in bp.preview_title.text())

    bp.set_running(True)  # Enhance All
    check("Enhance All switches Preview off", not bp.preview_on and not bp.preview_card.isVisible())
    bp.set_running(False)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
