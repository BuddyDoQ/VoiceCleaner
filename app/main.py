"""VoiceCleaner entry point."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python app/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "app"


SPLASH_MAX_S = 15.0  # longest the splash may hide the window on a slow first start


def selftest(src: str, dst: str, preset: str = "Clean Voice") -> int:
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
        result = EnhancementPipeline(mm).run(audio, analysis, auto_configure(preset, analysis).settings)
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


def check_updates_cli() -> int:
    """VoiceCleaner --check-updates: query GitHub once and print the result (diagnostics)."""
    from app.utils import updates
    from app.utils.config import APP_VERSION

    try:
        release = updates.fetch_latest()
    except updates.UpdateError as exc:
        print(f"Update check failed: {exc} ({exc.__cause__})")
        return 1
    asset = updates.pick_asset(release)
    state = "newer version available" if updates.is_newer(release.version) else "up to date"
    print(f"Running {APP_VERSION}; latest release {release.version} ({release.published}): {state}")
    print(f"Download for this computer: {asset.url if asset else release.page_url}")
    return 0


def main() -> int:
    if len(sys.argv) in (4, 5) and sys.argv[1] == "--selftest":
        return selftest(*sys.argv[2:])
    if len(sys.argv) == 2 and sys.argv[1] == "--list-inputs":
        return list_inputs()
    if len(sys.argv) == 2 and sys.argv[1] == "--check-updates":
        return check_updates_cli()
    # Only Qt, the theme and the splash are imported before the splash is on screen;
    # everything heavy (NumPy, SciPy, the main window, the AI model) loads behind it.
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui import theme
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
    config = UserConfig.load()
    theme.apply(app, config.extra.get("theme", "auto"))

    class _FinderOpen(QObject):
        """macOS delivers files opened from Finder (double-click, drop on the Dock icon)
        as QFileOpenEvent, not as command-line arguments. A file that opened the app
        arrives while the splash is up, before the window exists: keep it until then."""

        def __init__(self, parent):
            super().__init__(parent)
            self.window = None
            self.pending: list[Path] = []

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.FileOpen:
                p = Path(event.file())
                if p.suffix.lower() in (".wav", ".wave") and p.exists():
                    if self.window is None:
                        self.pending.append(p)
                    else:
                        self.window.open_file(p)
                return True
            return False

    # installed before the splash processes any events, so no FileOpen is lost
    finder_open = _FinderOpen(app)
    app.installEventFilter(finder_open)

    from app.ui.splash import SplashScreen

    splash = SplashScreen()
    splash.start()

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        title, message = friendly_message(exc)
        try:
            QMessageBox.warning(None, title, message)
        except Exception:
            pass

    sys.excepthook = excepthook

    # import in steps so the splash can repaint between them
    splash.set_status("Loading audio engine…")
    import numpy  # noqa: F401
    import scipy.signal  # noqa: F401

    splash.set_status("Loading interface…")
    from app.ui.main_window import MainWindow

    window = MainWindow(config)
    try:  # follow Windows' light/dark setting live when the theme is "Automatic"
        app.styleHints().colorSchemeChanged.connect(window._system_scheme_changed)
    except AttributeError:
        pass

    # The window appears once the AI engine is ready (or failed), so it opens ready to
    # work; a slow first start never keeps it hidden for more than SPLASH_MAX_S.
    shown = False

    def reveal():
        nonlocal shown
        if shown:
            return
        shown = True
        window.show()
        splash.finish(window)

    window.engineStatus.connect(splash.set_status)
    window.engineReady.connect(reveal)
    QTimer.singleShot(int(SPLASH_MAX_S * 1000), reveal)
    if window.engine_ready:
        reveal()

    finder_open.window = window
    for p in finder_open.pending[-1:]:  # the window shows one recording: the last one asked for
        window.open_file(p)
    finder_open.pending.clear()
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.suffix.lower() in (".wav", ".wave") and p.exists():
            window.open_file(p)
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
