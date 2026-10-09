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
