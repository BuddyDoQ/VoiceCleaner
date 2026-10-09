"""Drive the window: open a file, use EQ & Tone / Dynamics / Re-synthesis, enhance, screenshot.

Usage: python tools/tone_smoke.py <wav> <screenshot_dir>
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

from PySide6.QtWidgets import QApplication  # noqa: E402

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
    wav, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    w = MainWindow(UserConfig())
    w.resize(1440, 1000)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    w.open_file(wav)
    pump(app, lambda: w.audio is not None and not w._task_running())
    sp = w.settings_panel
    sp.restore_section_states({"resynth": True, "tone": True, "dynamics": True})
    sp.graphic_eq.bands[2].slider.setValue(-6)   # Body -3 dB
    sp.graphic_eq.bands[6].slider.setValue(5)    # Presence +2.5 dB
    sp._advanced["eq_tilt"].set_value(0.25, emit=True)
    sp._advanced["resynthesis"].set_value(0.3, emit=True)
    sp._advanced["leveler_amount"].set_value(0.7, emit=True)
    pump(app, lambda: True)
    s = sp.current()
    print("settings:", "eq", s.eq_gains, "tilt", s.eq_tilt, "resynth", s.resynthesis, "leveler", s.leveler_amount)
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    print("stages:", w.result.stages)
    print("speed:", round(w.result.realtime_factor, 1), "x", w.result.device_label, "| LUFS", round(w.result.enhanced_metrics.lufs, 1))
    pump(app, lambda: True)
    w.grab().save(str(out / "tone_enhanced.png"))
    # sidebar scrolled to the EQ/dynamics sections
    side_scroll = sp.parentWidget().parentWidget().parentWidget()
    side_scroll.verticalScrollBar().setValue(380)
    pump(app, lambda: True)
    w.grab().save(str(out / "tone_sidebar.png"))
    w.close()


if __name__ == "__main__":
    main()
