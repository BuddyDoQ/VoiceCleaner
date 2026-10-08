"""Application logging.

Logs go to a rotating file in the user's local app-data folder. Only
metadata is logged (paths, settings, timings, devices, errors); audio
contents are never written to the log.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import APP_NAME, APP_VERSION, log_dir

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger(APP_NAME)
    if _configured:
        return root
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir() / "voicecleaner.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if sys.stderr is not None:  # stderr is None in windowed PyInstaller builds
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)

    _quiet_third_party()
    root.info("%s %s starting (Python %s)", APP_NAME, APP_VERSION, sys.version.split()[0])
    _configured = True
    return root


def _quiet_third_party() -> None:
    # DeepFilterNet logs through loguru to stdout; keep it out of the console
    # unless something is actually wrong.
    try:
        from loguru import logger as loguru_logger

        loguru_logger.remove()
        loguru_logger.add(lambda msg: get_logger("deepfilternet").warning(msg.strip()), level="WARNING")
    except ImportError:
        pass


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APP_NAME}.{name}")
