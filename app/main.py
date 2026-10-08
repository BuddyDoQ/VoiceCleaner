"""VoiceCleaner entry point."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python app/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "app"


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.utils.config import APP_NAME, UserConfig
    from app.utils.errors import friendly_message
    from app.utils.logging import get_logger, setup_logging

    setup_logging()
    log = get_logger("main")

    try:  # own taskbar group/icon instead of python.exe's
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VoiceCleaner.App")
    except Exception:
        pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.STYLESHEET)

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        title, message = friendly_message(exc)
        try:
            QMessageBox.warning(None, title, message)
        except Exception:
            pass

    sys.excepthook = excepthook

    window = MainWindow(UserConfig.load())
    window.show()
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.suffix.lower() in (".wav", ".wave") and p.exists():
            window.open_file(p)
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
