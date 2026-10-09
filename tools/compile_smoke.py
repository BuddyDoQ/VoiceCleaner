"""Drive the Compile tab: session exports -> reorder -> preview -> export. Screenshots.

Usage: python tools/compile_smoke.py <screenshot_dir>
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
_os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"  # never contact GitHub from a smoke test

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QModelIndex, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402


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
    speech, sr = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")
    ex = w.session.exports_dir
    takes = [(1.5, 3.0, 0.8), (0.6, 4.0, 2.0), (2.5, 2.5, 1.2)]  # lead, body, tail seconds
    for i, (lead, body, tail) in enumerate(takes, 1):
        seg = speech[int(2.0 * sr): int((2.0 + body) * sr)]
        x = np.concatenate([np.zeros(int(lead * sr)), seg, np.zeros(int(tail * sr))]).astype(np.float32)
        sf.write(ex / f"{w.session.name} - Rec {i:03d} enhanced.wav", x, sr, subtype="PCM_24")
        time.sleep(0.05)  # distinct modification times
    cp = w.compile_panel
    w.tabs.setCurrentWidget(cp)
    pump(app, lambda: cp.list.count() == 3 and len(cp.takes) == 3)
    print("initial order:", [Path(p).name[-20:] for p in cp.paths()])
    print("trimmed:", [f"{cp.takes[p].duration:.2f}->{cp.takes[p].trimmed_duration:.2f}" for p in cp.paths()])
    # drag-reorder path: move row 2 to the top through the model (what an internal drag does)
    cp.list.model().moveRow(QModelIndex(), 2, QModelIndex(), 0)
    pump(app, lambda: True)
    cp.list.setCurrentRow(2)
    cp.move_selected(-1)  # button/keyboard path: last take up one
    print("reordered:", [Path(p).name[-20:] for p in cp.paths()])
    cp.toggle_play()
    pump(app, lambda: cp.compilation is not None)
    pump(app, lambda: cp.player.state == "playing", 10)
    time.sleep(0.3)
    cp.player.stop()
    comp = cp.compilation
    print("compilation:", f"{comp.duration:.2f}s", [s[2][-20:] for s in comp.segments], f"removed {comp.removed_seconds:.2f}s")
    w.grab().save(str(out / "compile_night.png"))
    w.set_theme("day")
    pump(app, lambda: True)
    w.grab().save(str(out / "compile_day.png"))
    w.set_theme("night")

    def accept_dialog():
        for top in app.topLevelWidgets():
            if isinstance(top, ExportDialog) and top.isVisible():
                top.grab().save(str(out / "compile_export_dialog.png"))
                top._accept()
                return
        QTimer.singleShot(100, accept_dialog)

    QTimer.singleShot(300, accept_dialog)
    def close_done_box():  # retry: the export can finish after any fixed delay
        # match by type: macOS ignores QMessageBox window titles
        boxes = [t for t in app.topLevelWidgets() if t.isVisible() and isinstance(t, QMessageBox)]
        for t in boxes:
            t.done(0)  # close() is ignored: the box has no Cancel/escape button
        if not boxes:
            QTimer.singleShot(200, close_done_box)

    QTimer.singleShot(500, close_done_box)
    cp.export()
    pump(app, lambda: any(p.name.endswith("Compilation.wav") for p in ex.iterdir()) and not w._task_running(), 60)
    f = next(p for p in ex.iterdir() if p.name.endswith("Compilation.wav"))
    info = sf.info(f)
    print("exported:", f.name, f"{info.duration:.2f}s", info.samplerate, info.subtype)
    w.close()


if __name__ == "__main__":
    main()
