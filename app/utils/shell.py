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


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opens File Explorer
    else:
        subprocess.Popen(["xdg-open", str(path)])


def shortcut_text(keys: str) -> str:
    """A shortcut as written on this platform: ``Ctrl+Shift+N`` stays as is on Windows and
    becomes ``⇧⌘N`` on macOS, where Qt maps Ctrl to the Command key."""
    if sys.platform != "darwin":
        return keys
    parts = keys.split("+")
    mods, key = parts[:-1], parts[-1]
    if key == "mouse wheel":
        return "".join({"Shift": "⇧", "Ctrl": "⌘"}.get(m, m) for m in mods) + " + scroll"
    symbol = {"Enter": "↩", "Return": "↩", "Up": "↑", "Down": "↓", "Left": "←", "Right": "→"}.get(key, key)
    order = ["Shift", "Ctrl"]  # macOS writes ⇧ before ⌘
    mods = sorted(mods, key=lambda m: order.index(m) if m in order else -1)
    return "".join({"Shift": "⇧", "Ctrl": "⌘", "Alt": "⌥"}.get(m, m) for m in mods) + symbol
