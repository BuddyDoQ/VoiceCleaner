"""VoiceCleaner entry point."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python app/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "app"


def selftest(src: str, dst: str) -> int:
    """Headless end-to-end check: VoiceCleaner.exe --selftest in.wav out.wav

    Runs the full pipeline (with the AI model) and exports a 24-bit WAV.
    Useful for verifying a packaged build or diagnosing a user's machine.
    """
    from app.ai.model_manager import ModelManager
    from app.audio.analyzer import analyze
    from app.audio.loader import load_wav
    from app.audio.pipeline import EnhancementPipeline
    from app.audio.settings import auto_configure
    from app.export.wav_exporter import ExportOptions, export_wav
    from app.utils.errors import friendly_message
    from app.utils.hardware import ensure_torch_imported
    from app.utils.logging import get_logger, setup_logging

    setup_logging()
    log = get_logger("selftest")
    try:
        ensure_torch_imported()
        audio = load_wav(src)
        analysis = analyze(audio.samples, audio.sample_rate)
        mm = ModelManager("auto")
        result = EnhancementPipeline(mm).run(audio, analysis, auto_configure("Clean Voice", analysis).settings)
        export_wav(result.samples, result.sample_rate, dst, ExportOptions("24"), source_path=Path(src))
        log.info("SELFTEST OK: %s -> %s | stages=%s | %.1fx realtime on %s | LUFS %.1f",
                 src, dst, result.stages, result.realtime_factor, result.device_label, result.enhanced_metrics.lufs)
        return 0
    except Exception as exc:
        log.exception("SELFTEST FAILED")
        print(friendly_message(exc)[1], file=sys.stderr) if sys.stderr else None
        return 1


def list_inputs() -> int:
    """VoiceCleaner.exe --list-inputs: print the recordable devices (diagnostics)."""
    from app.audio.recorder import list_sources
    from app.utils.logging import get_logger, setup_logging

    setup_logging()
    log = get_logger("inputs")
    try:
        mics, desktop = list_sources()
    except Exception:
        log.exception("Listing inputs failed")
        return 1
    for s in mics + desktop:
        log.info("INPUT %s: %s (%d ch)%s", s.kind, s.name, s.channels, " [default]" if s.is_default else "")
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3])
    if len(sys.argv) == 2 and sys.argv[1] == "--list-inputs":
        return list_inputs()
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
