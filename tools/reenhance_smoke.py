"""Compile's Re-enhance: several takes at once from their original recordings, one backup
each, Revert, a preset, edits kept, takes without an original skipped. Screenshots.

Usage: python tools/reenhance_smoke.py <screenshot_dir>
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

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from app.sessions import previous_version  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402


def pump(app, cond, timeout=300):
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
    rng = np.random.default_rng(3)
    s = w.session
    fakes = []
    for i, (a, b) in enumerate(((1.0, 6.0), (7.0, 11.0)), 1):
        raw = (speech[int(a * sr): int(b * sr)] + 0.01 * rng.standard_normal(int((b - a) * sr))).astype(np.float32)
        rec = s.recordings_dir / f"{s.name} - Rec {i:03d}.wav"
        sf.write(rec, raw, sr, subtype="PCM_24")
        exp = s.export_path(rec)
        sf.write(exp, raw * 0.5, sr, subtype="PCM_24")  # stands in for an earlier enhancement
        fakes.append(exp)
        time.sleep(0.05)
    imported = s.exports_dir / f"{s.name} - imported enhanced.wav"  # no original recording
    sf.write(imported, speech[: 3 * sr], sr, subtype="PCM_24")
    cp = w.compile_panel
    w.tabs.setCurrentWidget(cp)
    pump(app, lambda: cp.list.count() == 3 and len(cp.takes) == 3)
    rows = cp.paths()
    first = str(fakes[0])
    # an edit on the first take: it must survive re-enhancing
    t = cp.takes[first]
    t.edits = [(sr, sr + sr // 2, "cut")]
    from app.audio.compile import edits_to_seconds

    cp.edits[cp._edit_key(first)] = edits_to_seconds(t)
    before = {p: Path(p).read_bytes() for p in rows}

    # the menu for all three rows
    cp.list.selectAll()
    m = QMenu(w)
    cp._fill_reenhance_menu(m, cp._selected_paths())
    texts = [a.text() for a in m.actions()]
    print("menu:", texts)
    check("menu offers current settings and presets", "With Current Settings" in texts and "With Preset" in texts)
    check("the take without an original is skipped", any("1 take skipped" in x for x in texts))
    check("Open in Enhance Tab needs a single take", "Open in Enhance Tab (select one take)" in texts)
    m.popup(cp.reenhance_btn.mapToGlobal(cp.reenhance_btn.rect().bottomLeft()))
    pump(app, lambda: True)
    m.grab().save(str(out / "1_menu.png"))
    m.close()
    usable = [p for p in cp._selected_paths() if p != str(imported)]

    # re-enhance two takes at once with the current settings
    w._reenhance_takes(usable, "")
    pump(app, lambda: True)
    check("rows show they are being re-enhanced", cp._busy_rows() == set(usable))
    w.grab().save(str(out / "2_reenhancing.png"))
    pump(app, lambda: not cp.reenhancing and w.take_enhancer.pending == 0 and len(cp.takes) == 3)
    pump(app, lambda: cp.preview_ready, 30)
    w.grab().save(str(out / "3_done.png"))
    for p in usable:
        check(f"{Path(p).name}: replaced, old version kept",
              Path(p).read_bytes() != before[p] and previous_version(Path(p)).read_bytes() == before[p])
    check("the imported take is untouched", Path(imported).read_bytes() == before[str(imported)]
          and not previous_version(imported).exists())
    check("edits kept after re-enhancing", cp.takes[first].edits == [(sr, sr + sr // 2, "cut")])
    check("not added to the list twice / no backup files listed", cp.list.count() == 3)
    new_first = Path(first).read_bytes()

    # revert, and revert again (swap back)
    cp.revert([first])
    pump(app, lambda: first in cp.takes)
    check("revert restores the previous version", Path(first).read_bytes() == before[first]
          and previous_version(Path(first)).read_bytes() == new_first)
    cp.revert([first])
    pump(app, lambda: first in cp.takes)
    check("reverting again swaps back", Path(first).read_bytes() == new_first)

    # a preset: only one backup is kept (the version just replaced)
    w._reenhance_takes([first], "Podcast")
    pump(app, lambda: not cp.reenhancing and w.take_enhancer.pending == 0 and first in cp.takes)
    check("preset re-enhance: one backup, the version it replaced",
          previous_version(Path(first)).read_bytes() == new_first and Path(first).read_bytes() != new_first)
    backups = list((Path(first).parent / "Previous Versions").glob("*"))
    check(f"no leftover temporary files ({len(backups)} backups)", len(backups) == 2)
    # Open in Enhance Tab -> Enhance -> Update Take in Compile
    second = str(fakes[1])
    m = QMenu(w)
    cp._fill_reenhance_menu(m, [second])
    op = next(a for a in m.actions() if a.text().startswith("Open in Enhance Tab"))
    check("Open in Enhance Tab offered for one take", op.isEnabled())
    m.popup(cp.reenhance_btn.mapToGlobal(cp.reenhance_btn.rect().bottomLeft()))
    pump(app, lambda: True)
    m.grab().save(str(out / "4_menu_single.png"))
    m.close()
    op.trigger()
    pump(app, lambda: w.audio is not None and not w._task_running())
    check("the original recording opens in the Enhance tab",
          w.tabs.currentIndex() == 0 and w.audio.info.path.name.endswith("Rec 002.wav"))
    check("Update Take in Compile shown (disabled until enhanced)",
          w.update_take_btn.isVisible() and not w.update_take_btn.isEnabled())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running())
    check("enabled once enhanced", w.update_take_btn.isEnabled())
    w.grab().save(str(out / "5_update_take_button.png"))
    replaced = Path(second).read_bytes()
    stamp = Path(second).stat().st_mtime_ns
    w.update_take_btn.click()
    pump(app, lambda: not w._task_running() and second in cp.takes and w.tabs.currentWidget() is cp)
    check("the take is updated, the version it replaced kept",
          Path(second).stat().st_mtime_ns != stamp and previous_version(Path(second)).read_bytes() == replaced)
    # (same settings as the earlier re-enhance give the same audio: enhancement is deterministic)
    check("back on Compile, nothing left unexported", w.tabs.currentWidget() is cp and not w.unexported_result)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
