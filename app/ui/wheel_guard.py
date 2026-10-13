"""Scrolling never changes a setting by accident.

The mouse wheel over a slider, drop-down or number box scrolls the panel around it, unless
that control is active (it was clicked, or reached with Tab): then the wheel adjusts it, as
before. One rule for the whole application, so new controls follow it too.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox,
                               QScrollBar)

GUARDED = (QAbstractSlider, QComboBox, QAbstractSpinBox)


def _control(obj) -> QObject | None:
    """The guarded control ``obj`` belongs to (a spin box's text field counts as the box)."""
    w = obj
    for _ in range(3):
        if w is None:
            return None
        if isinstance(w, QScrollBar):
            return None  # scroll bars are sliders too, and scrolling is their job
        if isinstance(w, GUARDED):
            return w
        w = w.parent() if hasattr(w, "parent") else None
    return None


def _active(control) -> bool:
    """The control is the one selected in its window (clicked, or reached with Tab)."""
    return control.window().focusWidget() is control or control.hasFocus()


def _scroll_area(w):
    w = w.parentWidget()
    while w is not None and not isinstance(w, QAbstractScrollArea):
        w = w.parentWidget()
    return w


class WheelGuard(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            control = _control(obj)
            if control is not None and not _active(control):
                area = _scroll_area(control)
                if area is not None:  # scroll the panel around it instead
                    vp = area.viewport()
                    glob = event.globalPosition()
                    pos = QPointF(vp.mapFromGlobal(glob.toPoint()))
                    QApplication.sendEvent(vp, QWheelEvent(
                        pos, glob, event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                        event.phase(), event.inverted()))
                    return True
                event.ignore()  # nothing to scroll: let Qt pass it on
                return True
        return False


_guard: WheelGuard | None = None


def install(app: QApplication | None = None) -> None:
    """Install once for the whole application."""
    global _guard
    app = app or QApplication.instance()
    if app is None or _guard is not None:
        return
    _guard = WheelGuard(app)
    app.installEventFilter(_guard)
