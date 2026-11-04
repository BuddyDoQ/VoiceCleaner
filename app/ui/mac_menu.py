"""The macOS menu bar.

Mac users look for an app's commands in the menu bar and learn its keys there, and Full
Keyboard Access reaches commands through it. On Windows the commands stay in the header's
⋯ and Session menus; on a Mac they are in both, as the *same* actions, so each key is bound
once.

App menu (filled by Qt from the action roles): About, Settings… (Cmd+,), Check for Updates…
File · Edit · Playback · View · Window · Help
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu


class _SettingsTitle(QTranslator):
    """Qt titles the PreferencesRole item with its own "Preferences..." whatever the action's
    text; macOS has called it Settings… since Ventura. Qt looks that title up through the
    translators, so answer it here (the role keeps the item's place and its Cmd+,)."""

    def translate(self, context, source, disambiguation=None, n=-1):
        if context == "MAC_APPLICATION_MENU" and source == "Preferences...":
            return "Settings…"
        return None

    def isEmpty(self):
        return False


def _by_text(*menus: QMenu) -> dict[str, QAction]:
    found = {}
    for m in menus:
        for a in m.actions():
            if a.text():
                found[a.text()] = a
    return found


def build_menu_bar(window) -> None:
    w = window
    w._settings_title = _SettingsTitle(w)  # kept alive with the window
    QCoreApplication.installTranslator(w._settings_title)
    acts = _by_text(w.session_btn.menu(), w.menu_btn.menu())
    bar = w.menuBar()
    w._menu_commands = []

    def add(menu: QMenu, *names: str | None):
        for n in names:
            if n is None:
                menu.addSeparator()
            elif n in acts:
                menu.addAction(acts[n])

    def command(menu: QMenu, text: str, key, fn) -> QAction:
        a = QAction(text, w)
        a.setShortcut(QKeySequence(key))
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        w._menu_commands.append(a)  # paused with the window's shortcuts during the tour
        return a

    file = bar.addMenu("File")
    add(file, "New Session…", "Open Session…", "Rename Session…", None, "Open Recording…")
    file.addMenu(w.recent_menu)
    add(file, "Export Enhanced Audio…", None, "Open Session Folder", "Open Default Recordings Folder")
    # the app menu: Qt moves these there by their role (About, Settings…, Check for Updates…)
    for a in w.menu_btn.menu().actions():
        if a.menuRole() in (QAction.AboutRole, QAction.PreferencesRole, QAction.ApplicationSpecificRole):
            file.addAction(a)

    edit = bar.addMenu("Edit")
    for text, key, fn in w._edit_commands:
        command(edit, text, key, fn)

    playback = bar.addMenu("Playback")
    for cmd in w._playback_commands:
        if cmd is None:
            playback.addSeparator()
        else:
            command(playback, *cmd)

    view = bar.addMenu("View")
    for i in range(w.tabs.count()):
        name = w.tabs.tabText(i).replace("&", "").title()
        command(view, name, f"Ctrl+{i + 1}", lambda i=i: w.tabs.setCurrentIndex(i))
    view.addSeparator()
    add(view, "Teleprompter…")
    view.addMenu(w.output_menu)

    win = bar.addMenu("Window")
    command(win, "Minimize", "Ctrl+M", w.showMinimized)
    zoom = QAction("Zoom", w)
    zoom.triggered.connect(lambda: w.showNormal() if w.isMaximized() else w.showMaximized())
    win.addAction(zoom)

    help_menu = bar.addMenu("Help")
    add(help_menu, "User Manual", "Getting Started", "What's New", "Keyboard Shortcuts", None,
        "Show Tips Again", "Models and Licenses", "Open Log Folder")
