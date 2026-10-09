"""Drive the real main window through load -> enhance -> export and save screenshots.

Usage: python tools/ui_smoke.py <wav> <out_dir> [--offscreen]
"""
import os
import sys
import time
from pathlib import Path

if "--offscreen" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
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

from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402


def wait(app, cond, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
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
    w.resize(1440, 900)
    w.show()
    wait(app, lambda: True)
    w.grab().save(str(out / "0_empty.png"))
    wait(app, lambda: w._mm_ready.is_set())
    w.open_file(wav)
    wait(app, lambda: w.audio is not None and not w._task_running())
    w.grab().save(str(out / "1_loaded.png"))
    w.enhance()
    wait(app, lambda: w.result is not None and not w._task_running())
    w.settings_panel.adv_button.setChecked(True)
    wait(app, lambda: True)
    w.grab().save(str(out / "2_enhanced.png"))
    r = w.result
    print("RESULT", r.realtime_factor, r.device_label, r.enhanced_metrics.lufs, r.stages)
    w.tabs.setCurrentIndex(1)
    w.batch_panel.add_files([wav, ROOT / "test_audio" / "noise_only.wav", ROOT / "test_audio" / "missing.wav"])
    wait(app, lambda: True)
    w.batch_panel._start()
    wait(app, lambda: w._batch is None and w.batch_panel.summary.text().endswith("finished"), 300)
    w.grab().save(str(out / "3_batch.png"))
    print("BATCH", w.batch_panel.summary.text())
    w.close()


if __name__ == "__main__":
    main()
