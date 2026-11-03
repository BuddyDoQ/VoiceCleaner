"""Settings window, the macOS menu bar, and the header without its own day/night switch."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app import sessions
    from app.teleprompter import window as prompter_window
    from app.ui import main_window, playback_panel, record_panel
    from app.utils.config import UserConfig

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QTimer, "singleShot", lambda _ms, fn=None, *a: None)
    monkeypatch.setattr(sessions, "sessions_root", lambda: tmp_path)
    monkeypatch.setattr(playback_panel.PlaybackPanel, "refresh", lambda self: None)
    monkeypatch.setattr(record_panel, "list_sources", lambda: ([], []))
    monkeypatch.setattr(prompter_window, "list_sources", lambda: ([], []))
    monkeypatch.setattr(main_window.MainWindow, "_refresh_session_recordings", lambda self: None)
    return main_window.MainWindow(UserConfig())


def test_header_has_no_app_level_day_night_switch(window):
    assert not hasattr(window, "theme_btn")
    texts = [a.text() for a in window.menu_btn.menu().actions()]
    assert "Settings…" in texts and "Appearance" not in texts and "Processing device" not in texts


def test_settings_window_applies_choices_at_once(window):
    from app.ui import theme

    window.open_settings()
    dlg = window._settings_dialog
    night = next(b for b in dlg.appearance.buttons() if b.text() == "Night")
    night.setChecked(True)
    assert window.theme_pref == "night" and theme.MODE == "night"
    day = next(b for b in dlg.appearance.buttons() if b.text() == "Day")
    day.setChecked(True)
    assert theme.MODE == "day"
    dlg.updates.setChecked(False)
    assert window.config.extra["update_auto"] is False
    window.open_settings()  # one window, brought back
    assert window._settings_dialog is dlg


def test_mac_menu_bar_reuses_the_same_actions(window):
    from PySide6.QtGui import QAction

    from app.ui.mac_menu import build_menu_bar

    build_menu_bar(window)
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    assert list(menus) == ["File", "Edit", "Playback", "View", "Window", "Help"]
    file_actions = menus["File"].actions()
    # the same QAction objects as the ⋯ menu: each key is bound once
    export = next(a for a in window.menu_btn.menu().actions() if a.text().startswith("Export"))
    assert export in file_actions
    roles = {a.menuRole() for a in file_actions}
    assert {QAction.AboutRole, QAction.PreferencesRole, QAction.ApplicationSpecificRole} <= roles
    playback = [a.text() for a in menus["Playback"].actions() if a.text()]
    assert "Play / Pause" in playback and "Enhance" in playback
    assert [a.text() for a in menus["Edit"].actions()] == ["Undo", "Redo"]


def test_batch_lists_the_saved_file_in_its_own_column(window, tmp_path):
    import numpy as np
    import soundfile as sf

    from app.ui.batch_panel import COL_OUTPUT, COL_STATUS

    src = tmp_path / "take.wav"
    sf.write(src, np.zeros(4800, np.float32), 48000)
    bp = window.batch_panel
    bp.add_files([src])
    bp.file_output(0, str(tmp_path / "take enhanced.wav"))
    bp.file_finished(0, True, "Complete (9.0x realtime)")
    assert bp.table.item(0, COL_OUTPUT).text() == "take enhanced.wav"
    assert bp.table.item(0, COL_STATUS).text() == "✓ Complete (9.0x realtime)"
    assert not bp._bar(0).isVisibleTo(bp.table)


def test_noise_profile_sits_under_noise_reduction_and_not_in_batch(window):
    panel = window.settings_panel
    assert window.learn_btn.parent() is panel.noise_extra
    panel.set_context("batch")
    assert not panel.noise_extra.isVisibleTo(panel)
    panel.set_context("enhance")
    assert panel.noise_extra.isVisibleTo(panel)


def test_header_engine_readout_only_when_it_needs_attention(window):
    window._show_engine_readout(False)
    assert not window.device_chip.isVisibleTo(window)
    window._show_engine_readout(True)
    assert window.device_chip.isVisibleTo(window) and window.model_chip.isVisibleTo(window)
    assert "·" in window.engine_summary()


def test_increase_contrast_strengthens_secondary_text_and_borders(window):
    from app.ui import theme

    window.set_theme("night")
    window.set_contrast("auto")
    normal = (theme.FAINT, theme.BORDER_STRONG, theme.TRACK)
    window.set_contrast("high")
    assert theme.HIGH and (theme.FAINT, theme.BORDER_STRONG, theme.TRACK) == tuple(
        theme.HIGH_CONTRAST["night"][k] for k in ("FAINT", "BORDER_STRONG", "TRACK"))
    assert window.config.extra["contrast"] == "high"
    window.set_contrast("auto")
    assert (theme.FAINT, theme.BORDER_STRONG, theme.TRACK) == normal


def test_tour_pauses_mac_menu_keys_too(window):
    from app.ui.mac_menu import build_menu_bar

    build_menu_bar(window)
    play = next(a for a in window._menu_commands if a.text() == "Play / Pause")
    window.start_tour("basics")
    assert not play.isEnabled()
    window._tour.close_tour()
    assert play.isEnabled()
