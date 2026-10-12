"""Enhancing teleprompter takes in the background, one at a time, as they are recorded.

Each take is enhanced with the settings of the moment it was recorded (Smart settings,
when on, adapt them to the take) and exported as a 24-bit WAV to the session's Exports
folder; the take book then points Compile at the enhanced file.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from ..audio.analyzer import analyze
from ..audio.loader import load_wav
from ..audio.pipeline import EnhancementPipeline
from ..audio.settings import ProcessingSettings, smart_adapt
from ..export.wav_exporter import ExportOptions, export_wav
from ..utils.hardware import ensure_torch_imported
from ..utils.logging import get_logger
from .takes import TakeBook

log = get_logger("teleprompter")


@dataclass
class _Job:
    take: Path
    export: Path
    settings: ProcessingSettings
    smart: bool
    book: TakeBook


class TakeEnhancer(QObject):
    enhanced = Signal(str, str)  # take file name, enhanced file path
    failed = Signal(str, str)  # take file name, message
    pendingChanged = Signal(int)  # takes waiting or in progress

    def __init__(self, get_model_manager: Callable[[], object], parent=None):
        super().__init__(parent)
        self._get_mm = get_model_manager
        self._q: queue.Queue[_Job | None] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="take-enhancer", daemon=True)
        self._thread.start()

    @property
    def pending(self) -> int:
        return self._pending

    def enqueue(self, take: Path, export: Path, settings: ProcessingSettings, smart: bool, book: TakeBook):
        with self._lock:
            self._pending += 1
        self.pendingChanged.emit(self._pending)
        self._q.put(_Job(Path(take), Path(export), settings.copy(), smart, book))

    def shutdown(self):
        self._q.put(None)

    def _loop(self):
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                ensure_torch_imported()
                audio = load_wav(job.take)
                analysis = analyze(audio.samples, audio.sample_rate)
                settings = smart_adapt(job.settings, analysis).settings if job.smart else job.settings
                result = EnhancementPipeline(self._get_mm()).run(audio, analysis, settings)
                export_wav(result.samples, result.sample_rate, job.export, ExportOptions("24", 0), source_path=job.take)
                job.book.set_enhanced(job.take.name, job.export.name)
                log.info("Enhanced take %s -> %s (%.1fx realtime)", job.take.name, job.export.name,
                         result.realtime_factor)
                self.enhanced.emit(job.take.name, str(job.export))
            except Exception as exc:  # noqa: BLE001 - one bad take must not stop the others
                log.exception("Enhancing take %s failed", job.take)
                self.failed.emit(job.take.name, str(exc))
            finally:
                with self._lock:
                    self._pending -= 1
                self.pendingChanged.emit(self._pending)
