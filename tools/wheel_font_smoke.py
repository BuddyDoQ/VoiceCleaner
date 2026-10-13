"""Mouse wheel over settings scrolls instead of changing them (unless the control was
clicked), the System / Brand font switch, and the transport row staying inside the window
when a noise profile is learned or cleared. Screenshots of both font styles.

Usage: python tools/wheel_font_smoke.py <screenshot_dir>
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

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

WAV = ROOT / "test_audio" / "reference" / "clean_speech.wav"


def pump(app, cond=lambda: True, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


def wheel(app, widget, steps=-1):
    """A real wheel event over the middle of ``widget`` (negative: scroll down)."""
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    glob = QPointF(widget.mapToGlobal(pos.toPoint()))
    ev = QWheelEvent(pos, glob, QPoint(0, 0), QPoint(0, 120 * steps), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)
    pump(app)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night", fonts="system")
    w = MainWindow(UserConfig())
    w.resize(1440, 900)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    sp = w.settings_panel
    scroll = sp
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parent()
    bar = scroll.verticalScrollBar()
    check("the settings sidebar scrolls", bar.maximum() > 0)
    vs = next(iter(sp._advanced.values()))
    sp.dynamics_section.set_open(True)
    pump(app)
    # the first visible slider that is not locked by Smart settings
    slider = next(s for s in (x.slider for x in sp.findChildren(type(vs))) if s.isVisible() and s.isEnabled())
    bar.setValue(slider.mapTo(scroll.widget(), slider.rect().topLeft()).y() - 40)  # the slider in view
    pump(app)
    before, top = slider.value(), bar.value()
    wheel(app, slider, -1)
    check("wheel over an unclicked slider: the value stays", slider.value() == before)
    check("... and the panel scrolls instead", bar.value() > top)
    preset = sp.preset
    bar.setValue(0)
    pump(app)
    name = preset.currentText()
    wheel(app, preset, -1)
    check("wheel over the preset box: the preset stays", preset.currentText() == name)
    # a clicked (focused) slider still takes the wheel
    bar.setValue(slider.mapTo(scroll.widget(), slider.rect().topLeft()).y() - 40)
    pump(app)
    slider.setFocus(Qt.MouseFocusReason)
    pump(app)
    before = slider.value()
    wheel(app, slider, 1)
    check("wheel over a clicked slider adjusts it", slider.value() != before)
    w.grab().save(str(out / "1_focused_slider.png"))
    w.compile_panel.setFocus()
    sp.preset.clearFocus()

    # fonts: System by default, Brand on request, back again; the logo stays the brand face
    check("System fonts by default", theme.FONT_STYLE == "system" and theme.FONT_UI != theme.BRAND_UI)
    w.open_file(WAV)
    pump(app, lambda: w.audio is not None and not w._task_running())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    w.grab().save(str(out / "2_system_fonts.png"))
    w.set_theme("day")
    pump(app)
    w.grab().save(str(out / "3_system_fonts_day.png"))
    w.set_theme("night")
    w.set_fonts("brand")
    pump(app)
    check("Brand fonts on request", theme.FONT_UI == theme.BRAND_UI and theme.FONT_DISPLAY == theme.BRAND_DISPLAY)
    check("the choice is remembered", w.config.extra.get("fonts") == "brand")
    w.grab().save(str(out / "4_brand_fonts.png"))
    w.set_fonts("system")
    pump(app)
    check("back to System fonts", theme.FONT_STYLE == "system")
    w.tabs.setCurrentWidget(w.compile_panel)
    pump(app)
    w.grab().save(str(out / "5_system_fonts_compile.png"))
    # learning / clearing a noise profile never makes the editor wider than the window
    w.tabs.setCurrentIndex(0)
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.question = lambda *a, **k: QMessageBox.Yes  # "selection contains speech?" (test audio)
    check("clean recording without noise before the first word: nothing learned", w.learned_profile is None)
    # a noisy recording that starts with over a second of background noise: learned on opening
    w.open_file(ROOT / "test_audio" / "speech_hvac.wav")
    pump(app, lambda: w.audio is not None and w.audio.info.path.name == "speech_hvac.wav" and not w._task_running())
    check("noise before the first word is learned automatically",
          w.learned_profile is not None and "from the start" in w.profile_label.text()
          and w.learn_btn.text() == "Clear Noise Profile" and w.wave_orig.highlight_regions)
    w.learn_noise_profile()  # Clear: back to automatic detection
    check("Clear undoes it", w.learned_profile is None and not w.wave_orig.highlight_regions)
    for width in (1500, 1380, 1340, 1280, 1150):
        w.resize(width, 900)
        pump(app)
        hbar = w.editor_scroll.horizontalScrollBar()
        rows = w.transport_box.direction()
        sel = (0.0, 0.6)
        w.wave_orig.set_selection(sel)
        w.wave_orig.selectionChanged.emit(sel)
        pump(app)
        w.learn_noise_profile()
        pump(app)
        check(f"{width} px: the selection is learned", w.learned_profile is not None)
        learned, rows_learned = hbar.maximum(), w.transport_box.direction()
        both = w.wave_orig.selection is None and w.wave_enh.selection is None
        w.learn_noise_profile()  # clear
        pump(app)
        check(f"{width} px: learn and clear keep the editor inside the window, the row in place",
              learned == 0 and hbar.maximum() == 0 and rows == rows_learned == w.transport_box.direction())
        check(f"{width} px: learning clears the selection on both waveforms", both)
        sel = (1.0, 1.8)
        w.wave_enh.set_selection(sel)  # a selection made on the Enhanced waveform works too
        w.wave_enh.selectionChanged.emit(sel)
        pump(app)
        w.learn_noise_profile()
        check(f"{width} px: learn again after Clear, from the Enhanced waveform", w.learned_profile is not None)
        w.learn_noise_profile()
        pump(app)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
