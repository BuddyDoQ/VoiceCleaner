"""Background execution of loading, analysis and enhancement.

Heavy work runs on a ``QThread`` so the interface stays responsive. NumPy,
SciPy and PyTorch release the GIL inside their kernels, so a thread is
enough; a separate process would mean copying large audio buffers.

Each task gets a ``TaskContext`` for progress reporting and cooperative
cancellation. Exceptions never escape as stack traces: they are logged and
converted to a friendly ``(title, message)`` pair.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..utils.errors import CancelledError, friendly_message
from ..utils.logging import get_logger

log = get_logger("worker")


class TaskContext:
    def __init__(self, emit_progress: Callable[[float, str], None]):
        self._emit = emit_progress
        self._cancel = threading.Event()
        self._last = (-1.0, "")

    def progress(self, fraction: float, message: str = ""):
        fraction = max(0.0, min(1.0, fraction))
        # throttle signal traffic; always forward stage-name changes
        if message != self._last[1] or fraction - self._last[0] >= 0.005 or fraction >= 1.0:
            self._last = (fraction, message)
            self._emit(fraction, message)

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self):
        if self._cancel.is_set():
            raise CancelledError()


class _Worker(QObject):
    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, str, bool)  # title, message, was_cancelled
    done = Signal()

    def __init__(self, fn: Callable[[TaskContext], Any]):
        super().__init__()
        self.fn = fn
        self.ctx = TaskContext(lambda f, m: self.progress.emit(f, m))

    @Slot()
    def run(self):
        try:
            result = self.fn(self.ctx)
        except CancelledError:
            self.failed.emit("Cancelled", "Processing was cancelled.", True)
        except BaseException as exc:  # noqa: BLE001 - convert everything to a friendly message
            log.error("Background task failed: %s\n%s", exc, traceback.format_exc())
            title, message = friendly_message(exc)
            self.failed.emit(title, message, False)
        else:
            self.succeeded.emit(result)
        finally:
            self.done.emit()


class TaskHandle(QObject):
    """Owns the thread for one background task."""

    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, str, bool)
    finished = Signal()

    def __init__(self, fn: Callable[[TaskContext], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.succeeded.connect(self.succeeded)
        self._worker.failed.connect(self.failed)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._on_finished)
        self.running = False

    def start(self):
        self.running = True
        self._thread.start()
        return self

    def cancel(self):
        self._worker.ctx.cancel()

    def wait(self, ms: int = 5000) -> bool:
        return self._thread.wait(ms)

    def _on_finished(self):
        self.running = False
        self.finished.emit()


def run_task(fn: Callable[[TaskContext], Any], parent: QObject | None = None, *,
             on_success=None, on_error=None, on_progress=None, on_finished=None) -> TaskHandle:
    handle = TaskHandle(fn, parent)
    if on_success:
        handle.succeeded.connect(on_success)
    if on_error:
        handle.failed.connect(on_error)
    if on_progress:
        handle.progress.connect(on_progress)
    if on_finished:
        handle.finished.connect(on_finished)
    return handle.start()
