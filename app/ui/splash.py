"""Startup splash: shown the moment Qt is up, before the rest of the app is imported.

Loading the main window's modules (NumPy, SciPy, ...) and the AI model takes several
seconds, longer on a cold start; the splash shows that VoiceCleaner is starting and
what it is doing. Only Qt and the brand module are imported here, so it appears fast.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..utils.config import APP_NAME, APP_VERSION
from . import theme
from .brand import STUDIO_NAME, logo_pixmap

W, H = 460, 250


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.setWindowTitle(APP_NAME)
        self.status = "Starting…"
        self._logo = logo_pixmap(84)
        self._phase = 0.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.move(g.center().x() - W // 2, g.center().y() - H // 2)

    # --- API ----------------------------------------------------------------------
    def start(self):
        self.show()
        self.raise_()
        self._timer.start()
        self.pump()

    def set_status(self, text: str):
        self.status = text
        self.repaint()
        self.pump()

    @staticmethod
    def pump():
        """Let the splash paint while the main thread is busy importing."""
        app = QGuiApplication.instance()
        if app is not None:
            app.processEvents()

    def finish(self, window=None):
        """Fade out once ``window`` is on screen."""
        self._timer.stop()
        if window is not None:
            window.raise_()
            window.activateWindow()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(220)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InQuad)
        anim.finished.connect(self.close)
        anim.finished.connect(self.deleteLater)
        anim.start()

    # --- painting -----------------------------------------------------------------
    def _tick(self):
        self._phase = (time.monotonic() - self._t0) / 1.4 % 1.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(6, 6, W - 12, H - 12)
        path = QPainterPath()
        path.addRoundedRect(card, 14, 14)
        p.fillPath(path, theme.qcolor(theme.BG))
        p.setPen(QPen(theme.qcolor(theme.BORDER_STRONG), 1))
        p.drawPath(path)

        p.drawPixmap(34, 44, self._logo)
        x = 140
        f = QFont(theme.FONT_MONO)
        f.setPointSizeF(theme.pt(8))
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.ACCENT))
        p.drawText(QRectF(x, 44, W - x - 24, 16), Qt.AlignLeft | Qt.AlignVCenter, STUDIO_NAME.upper())
        f = QFont(theme.FONT_DISPLAY)
        f.setPointSizeF(34)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.TEXT))
        p.drawText(QRectF(x, 60, W - x - 24, 52), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME.upper())
        f = QFont(theme.FONT_MONO)
        f.setPointSizeF(theme.pt(8))
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.FAINT))
        p.drawText(QRectF(x, 112, W - x - 24, 16), Qt.AlignLeft | Qt.AlignVCenter, f"VERSION {APP_VERSION}")

        # status line and an indeterminate sweep: something is happening
        f = QFont(theme.FONT_UI)
        f.setPointSizeF(9.5)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.MUTED))
        p.drawText(QRectF(34, H - 70, W - 68, 20), Qt.AlignLeft | Qt.AlignVCenter, self.status)
        track = QRectF(34, H - 42, W - 68, 4)
        p.fillRect(track, theme.qcolor(theme.SURFACE_3))
        seg = track.width() * 0.28
        left = track.left() - seg + (track.width() + seg) * self._phase
        a, b = max(track.left(), left), min(track.right(), left + seg)
        if b > a:
            p.fillRect(QRectF(a, track.top(), b - a, track.height()), theme.qcolor(theme.ACCENT))
