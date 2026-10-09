"""Drive "Shorten long pauses" in the Enhance and Compile tabs. Screenshots.

Usage: python tools/pause_smoke.py <screenshot_dir>
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

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
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


def auto_accept(app, out_png=None):
    def tick():
        for top in app.topLevelWidgets():
            if isinstance(top, ExportDialog) and top.isVisible():
                if out_png:
                    top.grab().save(str(out_png))
                top._accept()
                break  # keep ticking: the "Export complete" box follows
            # match by type (macOS ignores QMessageBox titles); done(): close() is ignored
            # because the box has no Cancel/escape button
            if top.isVisible() and isinstance(top, QMessageBox):
                top.done(0)
                return
        QTimer.singleShot(100, tick)
    QTimer.singleShot(200, tick)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    w = MainWindow(UserConfig())
    w.resize(1440, 980)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    speech, sr = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")
    sentences = [speech[int(a * sr): int(b * sr)] for a, b in ((1.6, 4.4), (4.6, 7.4), (7.6, 10.6))]
    gaps = [2.5, 3.0]  # long pauses a speaker might leave while thinking
    x = sentences[0]
    for g, s in zip(gaps, sentences[1:]):
        x = np.concatenate([x, np.zeros(int(g * sr), np.float32), s])
    x = np.concatenate([np.zeros(int(0.5 * sr), np.float32), x, np.zeros(int(0.5 * sr), np.float32)])
    rec = w.session.recording_path()
    sf.write(rec, x, sr, subtype="PCM_24")

    # ---- Enhance tab
    sp = w.settings_panel
    print("enhance: pause option on by default?", sp.pause_box.isChecked(), "| sliders enabled?",
          sp._advanced["pause_keep_ms"].isEnabled())
    w.open_file(rec)
    pump(app, lambda: w.audio is not None and not w._task_running())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    print("enhance: notes before:", [n for n in w._last_notes if "pause" in n.lower()] or "none")
    sp.restore_section_states({"dynamics": True, "tone": False})
    sp.pause_box.setChecked(True)
    pump(app, lambda: True)
    print("enhance: note after enabling:", w.metrics.notes.text().split("\n")[-1])
    print("enhance: button still says", repr(w.enhance_btn.text()), "| preview length", f"{w.result.duration:.2f}s")
    side = sp.parentWidget().parentWidget().parentWidget()
    side.verticalScrollBar().setValue(side.verticalScrollBar().maximum())
    pump(app, lambda: True)
    w.grab().save(str(out / "pause_enhance.png"))
    auto_accept(app)
    w.export()
    exported = lambda: [p for p in w.session.exports_dir.glob("*.wav")]  # noqa: E731
    pump(app, lambda: exported() and not w._task_running(), 60)
    e = exported()[0]
    print("enhance: exported", e.name, f"{sf.info(e).duration:.2f}s (enhanced preview {w.result.duration:.2f}s)")

    # ---- Compile tab
    cp = w.compile_panel
    w.tabs.setCurrentWidget(cp)
    cp.add_files([rec])
    pump(app, lambda: len(cp.takes) == 2)
    print("compile: pause option on by default?", cp.pause_box.isChecked(), "| spin enabled?", cp.pause_keep.isEnabled())
    cp.pause_box.setChecked(True)
    pump(app, lambda: True)
    for p in cp.paths():
        t = cp.takes[p]
        print(f"compile: {Path(p).name[-26:]}: {t.duration:.2f}s -> {t.final_duration:.2f}s, pauses {len(t.cuts)} "
              f"(-{t.pause_seconds:.2f}s)")
    cp.build()
    pump(app, lambda: cp.compilation is not None)
    print("compile: preview", cp.preview_state.text())
    w.grab().save(str(out / "pause_compile.png"))
    cp.pause_keep.setValue(0)
    pump(app, lambda: True)
    print("compile: keep box shows", repr(cp.pause_keep.text()), "->",
          [f"{cp.takes[p].final_duration:.2f}s" for p in cp.paths()])
    w.close()


if __name__ == "__main__":
    main()
