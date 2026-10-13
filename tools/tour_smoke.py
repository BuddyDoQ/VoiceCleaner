"""Getting Started tour: a new install gets the basics, every step points at a visible
control, the next start shows nothing, and an update shows only What's New. Screenshots
of each step (dark) and a few in light.

Usage: python tools/tour_smoke.py <screenshot_dir>
"""
import json
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
os.environ.pop("VOICECLEANER_NO_TOUR", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import theme, tour  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils.config import APP_VERSION, UserConfig  # noqa: E402
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


def wait(app, s):
    t0 = time.time()
    while time.time() - t0 < s:
        app.processEvents()
        time.sleep(0.01)


def main():
    out = Path(sys.argv[1])
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

    def window():
        w = MainWindow(UserConfig.load())
        w.resize(1360, 880)
        w.show()
        pump(app, lambda: w._mm_ready.is_set())
        wait(app, 1.5)  # the tour starts a moment after the window shows
        return w

    # 1. a new install: the basics, every step with a visible target where one is set
    check("no saved settings yet", not UserConfig.path().exists())
    w = window()
    t = w._tour
    check("new install: the Getting Started tour starts", t is not None and t.steps == tour.BASICS)
    for i in range(len(t.steps)):
        s = t.steps[i]
        if s.target is not None:
            check(f"step {i + 1} '{s.title}' points at a visible control", t.hole is not None)
        w.grab().save(str(out / f"basics_{i + 1:02d}_dark.png"))
        if i in (0, 2, 7):
            w.set_theme("day")
            t._show_step()
            wait(app, 0.2)
            w.grab().save(str(out / f"basics_{i + 1:02d}_light.png"))
            w.set_theme("night")
            t._show_step()
        if i < len(t.steps) - 1:
            t.next()
            wait(app, 0.15)
    check("last step offers Done", t.next_btn.text() == "Done")
    t.next()
    wait(app, 0.2)
    check("finished: overlay gone, back on Enhance", w._tour is None and w.tabs.currentIndex() == 0)
    saved = json.loads(UserConfig.path().read_text(encoding="utf-8"))
    check("the version is remembered", saved["extra"].get("tour_seen_version") == APP_VERSION)
    w.close()

    # 2. the next start: nothing
    w = window()
    check("next start: no tour", w._tour is None)
    w.close()

    # 3. an update from 1.1.1 (settings without the key): What's New only
    data = json.loads(UserConfig.path().read_text(encoding="utf-8"))
    data["extra"].pop("tour_seen_version")
    UserConfig.path().write_text(json.dumps(data), encoding="utf-8")
    w = window()
    t = w._tour
    check("update: What's New, not the basics", t is not None and t.steps == tour.news_since("1.1.1")
          and "WHAT'S NEW" in t.count.text())
    w.grab().save(str(out / "news_dark.png"))
    t.close_tour()
    wait(app, 0.2)

    # 4. from the menu, any time; Esc ends it
    w.start_tour("basics")
    check("Getting Started from the menu", w._tour is not None and len(w._tour.steps) == len(tour.BASICS))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(w._tour, Qt.Key_Right)
    check("arrow key moves on", w._tour.i == 1)
    QTest.keyClick(w._tour, Qt.Key_Escape)
    wait(app, 0.2)
    check("Esc ends it", w._tour is None)
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
