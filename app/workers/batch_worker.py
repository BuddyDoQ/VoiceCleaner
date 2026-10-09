"""Batch enhancement: each file is loaded, analyzed, enhanced and exported
independently, so one damaged file never stops the rest of the batch."""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..audio.analyzer import analyze
from ..audio.loader import load_wav
from ..audio.pipeline import EnhancementPipeline
from ..audio.settings import ProcessingSettings, auto_configure
from ..export.mp3_exporter import export_mp3
from ..export.wav_exporter import ExportOptions, export_wav, suggest_output_path
from ..utils.errors import CancelledError, VoiceCleanerError, friendly_message
from ..utils.hardware import ensure_torch_imported
from ..utils.logging import get_logger
from .processing_worker import WORKER_STACK_SIZE, TaskContext

log = get_logger("batch")


@dataclass
class BatchOptions:
    settings: ProcessingSettings
    smart: bool
    output_dir: Path | None  # None = next to each source file
    fmt: str = "wav"
    bit_depth: str = "24"
    sample_rate: int = 0
    mp3_quality: str = "high"
    session: object = None  # app.sessions.Session: names exports "<Session> - <file> enhanced"


def enhance_file(path: Path, options: BatchOptions, model_manager, ctx: TaskContext) -> tuple[Path, object]:
    """Full single-file workflow used by batch mode."""
    ctx.progress(0.0, "Analyzing audio...")
    audio = load_wav(path)
    ctx.check()
    analysis = analyze(audio.samples, audio.sample_rate, progress=lambda f: ctx.progress(0.1 * f, "Analyzing audio..."))
    settings = auto_configure(options.settings.preset, analysis, options.settings).settings if options.smart \
        else options.settings
    if options.smart:  # user loudness choices win over the preset
        settings = settings.copy(target_lufs=options.settings.target_lufs,
                                 peak_ceiling_dbtp=options.settings.peak_ceiling_dbtp,
                                 normalize_loudness=options.settings.normalize_loudness,
                                 use_ai=options.settings.use_ai)

    class Sub:
        def progress(self, f, m=""):
            ctx.progress(0.1 + 0.8 * f, m)

        def check(self):
            ctx.check()

    result = EnhancementPipeline(model_manager).run(audio, analysis, settings, Sub())
    if settings.pause_settings().active:
        from ..audio.pauses import shorten_pauses

        ctx.progress(0.88, "Shortening long pauses...")
        result.samples, removed = shorten_pauses(result.samples, result.sample_rate, settings.pause_settings())
        log.info("Batch: shortened long pauses in %s by %.1f s", path.name, removed)
    ctx.progress(0.9, "Exporting...")
    ext = ".wav" if options.fmt == "wav" else ".mp3"
    if options.session is not None:
        out = options.session.export_path(path, ext=ext, directory=options.output_dir or path.parent)
    else:
        out = suggest_output_path(path, options.output_dir, ext=ext)
    prog = lambda f: ctx.progress(0.9 + 0.1 * f, "Exporting...")  # noqa: E731
    if options.fmt == "mp3":
        out = export_mp3(result.samples, result.sample_rate, out, options.mp3_quality, options.sample_rate,
                         source_path=path, progress=prog)
    else:
        out = export_wav(result.samples, result.sample_rate, out, ExportOptions(options.bit_depth, options.sample_rate),
                         source_path=path, progress=prog)
    ctx.progress(1.0, "Complete")
    return out, result


class BatchRunner(QObject):
    fileStarted = Signal(int)
    fileProgress = Signal(int, float, str)
    fileFinished = Signal(int, bool, str)  # index, success, message
    finished = Signal()

    def __init__(self, files: list[Path], options: BatchOptions, model_manager_getter):
        super().__init__()
        self.files = files
        self.options = options
        self.get_model_manager = model_manager_getter
        self._cancel = threading.Event()
        self.current_ctx: TaskContext | None = None

    def cancel(self):
        self._cancel.set()
        if self.current_ctx:
            self.current_ctx.cancel()

    @Slot()
    def run(self):
        ensure_torch_imported()
        mm = self.get_model_manager()
        for i, path in enumerate(self.files):
            if self._cancel.is_set():
                self.fileFinished.emit(i, False, "Cancelled")
                continue
            self.fileStarted.emit(i)
            ctx = TaskContext(lambda f, m, i=i: self.fileProgress.emit(i, f, m))
            self.current_ctx = ctx
            t0 = time.perf_counter()
            try:
                out, result = enhance_file(path, self.options, mm, ctx)
                msg = f"Complete → {out.name} ({result.realtime_factor:.1f}x realtime)"
                self.fileFinished.emit(i, True, msg)
                log.info("Batch file %s done in %.1fs -> %s", path, time.perf_counter() - t0, out)
            except CancelledError:
                self.fileFinished.emit(i, False, "Cancelled")
            except BaseException as exc:  # noqa: BLE001 - one bad file must not stop the batch
                log.error("Batch file %s failed: %s\n%s", path, exc, traceback.format_exc())
                self.fileFinished.emit(i, False, friendly_message(exc)[1])
        self.finished.emit()


def start_batch(files: list[Path], options: BatchOptions, model_manager_getter) -> tuple[QThread, BatchRunner]:
    thread = QThread()
    thread.setStackSize(WORKER_STACK_SIZE)
    runner = BatchRunner(files, options, model_manager_getter)
    runner.moveToThread(thread)
    thread.started.connect(runner.run)
    runner.finished.connect(thread.quit)
    thread.start()
    return thread, runner
