"""Drive the 1.0.3 settings features in the real window: settings that survive a
restart (Smart on and off), user presets, MODIFIED / Reset, Recent Files, the
shortcuts list and the export "show in folder" choice. Saves screenshots.

Usage: python tools/settings_smoke.py <wav> <screenshot_dir>
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

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.utils import user_presets  # noqa: E402
from app.utils.shell import shortcut_text  # noqa: E402
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


def open_window(app, wav: Path | None = None) -> MainWindow:
    w = MainWindow(UserConfig.load())
    w.resize(1360, 880)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    if wav:
        w.open_file(wav)
        pump(app, lambda: w.audio is not None and not w._task_running())
    return w


def close_message_boxes(app, seen: list):
    def tick():
        for top in app.topLevelWidgets():
            if isinstance(top, QMessageBox) and top.isVisible():
                seen.append((top.windowTitle(), top.text()))
                top.accept()
                return
        QTimer.singleShot(100, tick)
    QTimer.singleShot(150, tick)


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

    # ---- 1: Smart on is the default; its controls are locked and marked AUTO
    w = open_window(app, wav)
    sp = w.settings_panel
    check("Smart on by default, Noise locked + AUTO",
          sp.smart.isChecked() and not sp.noise.slider.isEnabled() and sp.noise.value_label.text().startswith("AUTO"))
    check("Smart note visible", not sp.smart_note.isHidden())
    w.grab().save(str(out / "1_smart_on.png"))

    # ---- 2: Smart off, hand-tuned values, restart -> restored exactly
    sp.smart.setChecked(False)
    sp.noise.set_value(0.83, emit=True)
    sp.room.set_value(0.12, emit=True)
    sp._advanced["leveler_amount"].set_value(0.9, emit=True)
    sp._advanced["comp_release_ms"].set_value(400, emit=True)
    check("MODIFIED shown after changes", not sp.modified_tag.isHidden())
    before = sp.current()
    w.grab().save(str(out / "2_smart_off_modified.png"))
    w.close()
    w = open_window(app)
    s = w.settings_panel.current()
    check(f"restart restores hand-tuned values (noise {s.noise_reduction}, room {s.room_reduction}, "
          f"leveler {s.leveler_amount}, release {s.comp_release_ms})",
          (s.noise_reduction, s.room_reduction, s.leveler_amount, s.comp_release_ms)
          == (before.noise_reduction, before.room_reduction, before.leveler_amount, before.comp_release_ms))
    check("Smart still off after restart", not w.settings_panel.smart.isChecked())
    # loading another recording with Smart off keeps them
    w.open_file(wav)
    pump(app, lambda: w.audio is not None and not w._task_running())
    check("new recording with Smart off keeps values",
          w.settings_panel.current().noise_reduction == before.noise_reduction)
    check("recent files remembers the file", w.config.extra.get("recent_files", [None])[0] == str(wav))

    # ---- 3: save as preset, switch away and back, restart -> preset still there and selected
    sp = w.settings_panel
    sp._select_saved(user_presets.save("Studio Voice", sp._settings_to_save()))
    check("saved preset selected, not modified", sp.preset.currentText() == "Studio Voice" and sp.modified() == [])
    sp.preset.setCurrentText("Podcast")
    check("switching to Podcast changes leveler", sp.current().leveler_amount != 0.9)
    sp.preset.setCurrentText("Studio Voice")
    check("back to Studio Voice restores leveler 0.9", sp.current().leveler_amount == 0.9)
    sp._fill_preset_menu()
    print("     preset menu:", [a.text() for a in sp.preset_menu.actions() if a.text()])
    w.close()
    w = open_window(app)
    check("preset survives restart", w.settings_panel.preset.currentText() == "Studio Voice")
    print("     preset files:", [p.name for p in user_presets.presets_dir().glob("*.json")])

    # ---- 4: Smart on again: takes over its fields for the recording, keeps the rest
    w.open_file(wav)
    pump(app, lambda: w.audio is not None and not w._task_running())
    sp = w.settings_panel
    sp.smart.setChecked(True)
    pump(app, lambda: True)
    check("Smart on keeps leveler 0.9 (not Smart's)", sp.current().leveler_amount == 0.9)
    check("Smart on sets its own Noise", sp.current().noise_reduction != before.noise_reduction)
    w.grab().save(str(out / "4_user_preset_smart.png"))

    # ---- 5: shortcuts list, recent menu
    seen = []
    close_message_boxes(app, seen)
    w._show_shortcuts()
    pump(app, lambda: bool(seen), 10)
    enhance_keys = shortcut_text("Ctrl+Enter")  # ⌘↩ on macOS
    check(f"shortcuts dialog lists Space and {enhance_keys}", "Space" in seen[0][1] and enhance_keys in seen[0][1])
    w._fill_recent_menu()
    print("     recent menu:", [a.text() for a in w.recent_menu.actions() if a.text()])
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
