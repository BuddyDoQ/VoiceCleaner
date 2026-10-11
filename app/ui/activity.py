"""One place that knows what VoiceCleaner is busy with, and shows it everywhere.

Long tasks (analysis, enhance, export, batch, compile, live preview) report here with
``start`` / ``update`` / ``finish``. The tracker drives:

* the header activity chip ("● ENHANCING · 45 %"), visible from every tab, which jumps
  to the busy tab when clicked;
* a badge on the busy tab ("BATCH · 3/10");
* the Windows taskbar button's progress bar (green fill; nothing on macOS);
* elapsed time and an estimate of the time left;
* a taskbar flash / Dock bounce when a long job finishes while the app is in the
  background.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ..audio.loader import format_duration
from ..utils.logging import get_logger

log = get_logger("activity")

ALERT_AFTER_S = 5.0  # only long jobs flash the taskbar when they finish in the background


@dataclass
class Job:
    key: str
    label: str  # "Enhancing", "Exporting", ...
    tab: object = None  # the tab page this job belongs to, or None
    started: float = field(default_factory=time.monotonic)
    fraction: float | None = None  # None = indeterminate
    message: str = ""
    badge: str = ""  # short text for the tab, e.g. "3/10"; defaults to the percentage

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float | None:
        """Estimated seconds left, once there is enough progress to judge."""
        f = self.fraction
        if f is None or f < 0.05 or self.elapsed < 2.0 or f >= 1.0:
            return None
        return self.elapsed * (1.0 - f) / f


def eta_text(job: Job | None) -> str:
    if job is None:
        return ""
    text = f"{format_duration(job.elapsed).split('.')[0]} elapsed"
    left = job.remaining()
    if left is not None:
        text += f" • about {_rough(left)} left"
    return text


def _rough(seconds: float) -> str:
    if seconds < 10:
        return "a few seconds"
    if seconds < 60:
        return f"{int(round(seconds / 5.0) * 5)} s"
    m = seconds / 60
    return f"{m:.0f} min" if m >= 1.5 else "1 min"


class ActivityTracker(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.jobs: dict[str, Job] = {}
        self._taskbar = TaskbarProgress(window) if sys.platform == "win32" else None

    # --- API for tasks -----------------------------------------------------------
    def start(self, key: str, label: str, tab=None, fraction: float | None = 0.0):
        self.jobs[key] = Job(key, label, tab, fraction=fraction)
        self._refresh()

    def update(self, key: str, fraction: float | None = None, message: str = "", badge: str | None = None):
        job = self.jobs.get(key)
        if job is None:
            return
        if fraction is not None:
            job.fraction = max(0.0, min(1.0, fraction))
        if message:
            job.message = message
        if badge is not None:
            job.badge = badge
        self._refresh()

    def finish(self, key: str, ok: bool = True):
        job = self.jobs.pop(key, None)
        self._refresh()
        if job is not None and ok and job.elapsed >= ALERT_AFTER_S:
            self._alert()

    def job(self, key: str) -> Job | None:
        return self.jobs.get(key)

    @property
    def current(self) -> Job | None:
        """The job to show: the most recently started one."""
        return max(self.jobs.values(), key=lambda j: j.started, default=None)

    def busy_tabs(self) -> dict:
        out = {}
        for j in sorted(self.jobs.values(), key=lambda j: j.started):
            if j.tab is not None:
                out[j.tab] = j
        return out

    # --- internals -----------------------------------------------------------------
    def _refresh(self):
        if self._taskbar is not None:
            job = self.current
            if job is None:
                self._taskbar.clear()
            elif job.fraction is None:
                self._taskbar.indeterminate()
            else:
                self._taskbar.set(job.fraction)
        self.changed.emit()

    def _alert(self):
        """Flash the taskbar button (Windows) or bounce the Dock icon (macOS) when the
        user is in another app; nothing when VoiceCleaner is in front."""
        if QApplication.activeWindow() is None or self.window.isMinimized():
            QApplication.alert(self.window, 0)


class TaskbarProgress:
    """Progress on the Windows taskbar button (ITaskbarList3), called through ctypes so no
    extra package is needed. Every call is best-effort: failures are logged once and the
    feature switches itself off."""

    CLSID = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
    IID = "{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}"
    NOPROGRESS, INDETERMINATE, NORMAL = 0x0, 0x1, 0x2

    def __init__(self, window):
        self.window = window
        self._obj = None
        self._fns = None
        self._failed = False
        self._state = None

    def _ensure(self) -> bool:
        if self._fns is not None:
            return True
        if self._failed:
            return False
        try:
            import ctypes
            from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_long, c_ulonglong, c_void_p

            class GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                            ("d4", ctypes.c_ubyte * 8)]

            def guid(s: str) -> GUID:
                g = GUID()
                ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), byref(g))
                return g

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)  # already initialised by Qt on this thread: harmless
            obj = c_void_p()
            hr = ole32.CoCreateInstance(byref(guid(self.CLSID)), None, 1, byref(guid(self.IID)), byref(obj))
            if hr != 0 or not obj:
                raise OSError(f"CoCreateInstance failed (0x{hr & 0xFFFFFFFF:08x})")
            vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            hr_init = WINFUNCTYPE(c_long, c_void_p)(vtbl[3])
            set_value = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_ulonglong, c_ulonglong)(vtbl[9])
            set_state = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_int)(vtbl[10])
            hr_init(obj)
            self._obj, self._fns = obj, (set_value, set_state)
            return True
        except Exception as exc:  # noqa: BLE001 - a missing taskbar is not an error
            log.info("Taskbar progress unavailable: %s", exc)
            self._failed = True
            return False

    def _hwnd(self):
        return int(self.window.winId()) if self.window.isVisible() else None

    def _set_state(self, state: int):
        hwnd = self._hwnd()
        if hwnd and self._ensure():
            self._fns[1](self._obj, hwnd, state)
            self._state = state

    def set(self, fraction: float):
        hwnd = self._hwnd()
        if not hwnd or not self._ensure():
            return
        if self._state != self.NORMAL:
            self._set_state(self.NORMAL)
        self._fns[0](self._obj, hwnd, int(fraction * 1000), 1000)

    def indeterminate(self):
        if self._state != self.INDETERMINATE:
            self._set_state(self.INDETERMINATE)

    def clear(self):
        if self._state not in (None, self.NOPROGRESS):
            self._set_state(self.NOPROGRESS)
