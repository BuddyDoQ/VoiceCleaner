"""A visible ring on the control reached with the keyboard.

The stylesheet replaces Qt's native drawing, and with it the focus rectangle, so a person
moving through the window with Tab could not see where they were. This filter marks the
control that got focus from the keyboard (``focusring="true"``) and the stylesheet draws a
ring on it. A click gives no ring, as in the system's own controls. One rule for the whole
application, so new controls follow it too.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
PROPERTY = "focusring"


def _restyle(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class FocusRing(QObject):
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.FocusIn, QEvent.FocusOut) and isinstance(obj, QWidget):
            on = kind == QEvent.FocusIn and event.reason() in KEYBOARD_REASONS
            if bool(obj.property(PROPERTY)) != on:
                obj.setProperty(PROPERTY, on)
                _restyle(obj)
        return False


_ring: FocusRing | None = None


def install(app: QApplication | None = None) -> None:
    """Install once for the whole application."""
    global _ring
    app = app or QApplication.instance()
    if app is None or _ring is not None:
        return
    _ring = FocusRing(app)
    app.installEventFilter(_ring)
