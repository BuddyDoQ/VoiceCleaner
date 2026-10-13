"""Drive the take editor from the Compile tab: open a take with a pop and an undetected
pause, cut the pop, silence the pause, check the compilation and that the edits are saved
with the session. Screenshots of the editor (dark and light) and the edited row.

Usage: python tools/take_edit_smoke.py <screenshot_dir>
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
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"
os.environ["VOICECLEANER_NO_TOUR"] = "1"  # the Getting Started tour would cover the window

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.audio.compile import load_edits  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.take_editor import TakeEditor  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

POP_S = 2.0  # a click in the middle of the first take
HUM = (3.2, 3.9)  # a stretch of noise the trimming does not treat as silence


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

    speech, sr = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")
    if speech.ndim == 2:
        speech = speech.mean(axis=1)
    ex = w.session.exports_dir
    a = speech[int(2.0 * sr): int(6.5 * sr)].copy()
    n = int(0.004 * sr)
    a[int(POP_S * sr): int(POP_S * sr) + n] += np.hanning(n).astype(np.float32) * 0.9  # the pop
    rng = np.random.default_rng(1)
    h0, h1 = int(HUM[0] * sr), int(HUM[1] * sr)
    a[h0:h1] = (0.02 * rng.standard_normal(h1 - h0)).astype(np.float32)  # an undetected "pause"
    b = speech[int(8.0 * sr): int(11.0 * sr)]
    for i, x in enumerate((a, b), 1):
        x = np.concatenate([np.zeros(int(0.5 * sr)), x, np.zeros(int(0.5 * sr))]).astype(np.float32)
        sf.write(ex / f"{w.session.name} - Rec {i:03d} enhanced.wav", x, sr, subtype="PCM_24")
        time.sleep(0.05)
    cp = w.compile_panel
    w.tabs.setCurrentWidget(cp)
    pump(app, lambda: cp.list.count() == 2 and len(cp.takes) == 2)
    pump(app, lambda: cp.preview_ready, 20)
    before = cp.compilation.duration
    path = cp.paths()[0]
    take = cp.takes[path]
    off = 0.5  # the silence written before each take

    def drive():
        dlg = next((t for t in app.topLevelWidgets() if isinstance(t, TakeEditor) and t.isVisible()), None)
        if dlg is None:
            QTimer.singleShot(100, drive)
            return
        dlg.resize(1200, 480)
        pump(app, lambda: True)
        dlg.grab().save(str(out / "1_editor_open.png"))
        # cut the pop: select it, zoom in, cut
        p0 = off + POP_S
        dlg.view.set_selection((p0 - 0.003, p0 + 0.007))
        dlg._selection_changed(dlg.view.selection)
        dlg.zoom_selection()
        pump(app, lambda: True)
        dlg.grab().save(str(out / "2_pop_selected_zoomed.png"))
        dlg.apply("cut")
        dlg.fit()
        # silence the undetected pause
        dlg.view.set_selection((off + HUM[0], off + HUM[1]))
        dlg._selection_changed(dlg.view.selection)
        dlg.apply("mute")
        # an extra cut, then undo it
        dlg.view.set_selection((off + 4.2, off + 4.4))
        dlg.apply("cut")
        dlg.undo()
        check("undo removes the last edit", len(dlg.edits) == 2)
        dlg._clicked(off + (HUM[0] + HUM[1]) / 2)  # pick the silenced region
        check("clicking an edit picks it", dlg.view.picked is not None and dlg.edits[dlg.view.picked][2] == "mute")
        pump(app, lambda: True)
        dlg.grab().save(str(out / "3_editor_edits_dark.png"))
        w.set_theme("day")
        pump(app, lambda: True)
        dlg.grab().save(str(out / "4_editor_edits_light.png"))
        w.set_theme("night")
        dlg.play_result()
        pump(app, lambda: dlg.player.state == "playing", 5)
        check("Hear Result plays the edited take", dlg._result and dlg.player.state == "playing")
        dlg.player.stop()
        dlg.accept()

    QTimer.singleShot(200, drive)
    cp.list.setCurrentRow(0)
    cp.edit_selected()  # modal: driven by drive()
    kinds = sorted(k for _a, _b, k in take.edits)
    check("edits kept on the take", kinds == ["cut", "mute"])
    pi = int((off + POP_S) * sr)
    cut = next(e for e in take.edits if e[2] == "cut")
    check("the cut covers the pop", cut[0] <= pi and cut[1] >= pi + n)
    saved = load_edits(w.session.path)
    check("saved with the session", len(saved.get(Path(path).name, [])) == 2)
    pump(app, lambda: cp.preview_ready, 20)
    after = cp.compilation.duration
    print(f"compilation {before:.3f} s -> {after:.3f} s")
    check("the cut shortens the compilation (silence keeps the timing)", 0.005 < before - after < 0.05)
    w.grab().save(str(out / "5_compile_row_edited.png"))
    # reopen the session: edits come back
    cp.takes.clear()
    cp.set_session(w.session)
    cp.clear()
    cp.add_session_exports(quiet=True)
    pump(app, lambda: len(cp.takes) == 2)
    check("edits restored for the session", len(cp.takes[path].edits) == 2)
    # a new session starts with an empty Compile tab, not the previous session's takes
    from app import sessions

    w._switch_session(sessions.create("Second Session"))
    pump(app, lambda: True)
    check("new session: no takes from the previous session", cp.list.count() == 0 and not cp.takes
          and cp.compilation is None)
    w._switch_session(sessions.open_session(Path(path).parent.parent))
    pump(app, lambda: cp.list.count() == 2 and len(cp.takes) == 2)
    check("back to the first session: its takes and edits return", len(cp.takes[path].edits) == 2)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
