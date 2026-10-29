"""Showing files and folders in the platform's file manager."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FILE_MANAGER = "Finder" if sys.platform == "darwin" else "File Explorer"


def reveal(path: Path) -> None:
    """Open the file manager with ``path`` selected."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def open_file(path: Path) -> None:
    """Open ``path`` with its default app (a .dmg mounts and shows its window on macOS)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opens File Explorer
    else:
        subprocess.Popen(["xdg-open", str(path)])


# The key Qt calls Backspace is labelled "delete" (⌫) on Mac keyboards.
BACKSPACE_NAME = "Delete (⌫)" if sys.platform == "darwin" else "Backspace"

# Help: F1 everywhere (fn+F1 on most Mac keyboards). On a Mac, Cmd+? opens the menu bar's
# Help menu (macOS keeps that key), where the manual is listed.
HELP_KEYS = "F1"
MANUAL_WHERE = "the ⋯ menu or the Help menu (⌘?)" if sys.platform == "darwin" else "the ⋯ menu"

# Redo is Ctrl+Y on Windows; macOS has no Cmd+Y redo, its standard is Shift+Cmd+Z
# (both are bound in the main window through QKeySequence.Redo and Ctrl+Shift+Z).
REDO_KEYS = "Ctrl+Shift+Z" if sys.platform == "darwin" else "Ctrl+Y"


def shortcut_text(keys: str) -> str:
    """A shortcut as written on this platform: ``Ctrl+Shift+N`` stays as is on Windows and
    becomes ``⇧⌘N`` on macOS, where Qt maps Ctrl to the Command key. Alternatives separated
    by `` / `` are converted one by one."""
    if sys.platform != "darwin":
        return keys
    if " / " in keys:
        return " / ".join(shortcut_text(k) for k in keys.split(" / "))
    parts = keys.split("+")
    mods, key = parts[:-1], parts[-1]
    if key == "mouse wheel":
        return "".join({"Shift": "⇧", "Ctrl": "⌘"}.get(m, m) for m in mods) + " + scroll"
    symbol = {"Enter": "↩", "Return": "↩", "Up": "↑", "Down": "↓", "Left": "←", "Right": "→"}.get(key, key)
    order = ["Shift", "Ctrl"]  # macOS writes ⇧ before ⌘
    mods = sorted(mods, key=lambda m: order.index(m) if m in order else -1)
    return "".join({"Shift": "⇧", "Ctrl": "⌘", "Alt": "⌥"}.get(m, m) for m in mods) + symbol
