"""Reusable UI pieces: drop zone, cards, analysis comparison panel, app icon."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    return w


class ElidedLabel(QLabel):
    """Single-line label that shortens long text with an ellipsis instead of widening its parent."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.width())
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignVCenter), text)

    def sizeHint(self):
        sh = super().sizeHint()
        return sh.expandedTo(sh)


def card() -> QFrame:
    f = QFrame()
    f.setProperty("role", "card")
    return f


class DropZone(QFrame):
    """Large dashed drop target shown before a file is loaded."""

    chooseClicked = Signal()
    recordClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(False)  # the main window handles drops
        self.hover = False
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(14)
        self.icon = QLabel()
        from .brand import logo_pixmap

        self.icon.setPixmap(logo_pixmap(96))
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)
        title = label("DROP A WAV FILE HERE", "display")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        sub = label("VoiceCleaner removes background noise, room echo and hum, and makes speech clear and even.",
                    "muted", wrap=True)
        sub.setAlignment(Qt.AlignCenter)
        sub.setFixedWidth(460)
        lay.addWidget(sub, alignment=Qt.AlignCenter)
        btn = QPushButton("Choose WAV File")
        btn.setProperty("role", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.chooseClicked)
        rec = QPushButton("\u25cf  Record Audio")
        rec.setProperty("role", "record")
        rec.setCursor(Qt.PointingHandCursor)
        rec.setToolTip("Record from a microphone (USB, headset, line-in) or from desktop audio")
        rec.clicked.connect(self.recordClicked)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(btn)
        buttons.addWidget(rec)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        hint = label("Mono or stereo • 16/24-bit or 32-bit float • 44.1, 48, 96 kHz and more • "
                     "drop several files for batch processing", "faint", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        steps = QHBoxLayout()
        steps.setSpacing(18)
        for i, s in enumerate(["Drop", "Analyze", "Enhance", "Listen", "Export"], 1):
            steps.addWidget(label(f"{i}  {s}", "faint"))
        steps_w = QWidget()
        steps_w.setLayout(steps)
        lay.addSpacing(10)
        lay.addWidget(steps_w, alignment=Qt.AlignCenter)

    def set_hover(self, on: bool):
        self.hover = on
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        p.setBrush(theme.qcolor(theme.ACCENT, 18) if self.hover else theme.qcolor(theme.SURFACE))
        pen = QPen(theme.qcolor(theme.ACCENT if self.hover else "#3a4150"), 2, Qt.DashLine)
        pen.setDashPattern([6, 5])
        p.setPen(pen)
        p.drawRoundedRect(r, 18, 18)


class MetricsPanel(QFrame):
    """Before/after comparison table plus the estimated clarity score."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(28)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(26)
        self.grid.setVerticalSpacing(6)
        self.grid.addWidget(label("", "section"), 0, 0)
        o = label("ORIGINAL", "section")
        o.setProperty("tone", "original")
        e = label("ENHANCED", "section")
        e.setProperty("tone", "accent")
        self.grid.addWidget(o, 0, 1)
        self.grid.addWidget(e, 0, 2)
        self.rows: dict[str, tuple[QLabel, QLabel]] = {}
        for i, name in enumerate(["Loudness", "True peak", "Noise floor", "Speech-to-noise", "Dynamic range"], 1):
            self.grid.addWidget(label(name, "muted"), i, 0)
            a, b = label("—"), label("—")
            for w in (a, b):
                w.setStyleSheet("font-weight: 600;")
            self.grid.addWidget(a, i, 1)
            self.grid.addWidget(b, i, 2)
            self.rows[name] = (a, b)
        grid_w = QWidget()
        grid_w.setLayout(self.grid)
        outer.addWidget(grid_w)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setProperty("role", "vdivider")
        outer.addWidget(sep)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(label("SPEECH CLARITY", "section"))
        self.score = label("—")
        self.score.setProperty("role", "score")
        right.addWidget(self.score)
        self.score_detail = label("Enhance the recording to compare.", "faint", wrap=True)
        right.addWidget(self.score_detail)
        disclaimer = label("Estimated internal metric, not a standardized intelligibility score.", "faint", wrap=True)
        disclaimer.setProperty("role", "faint")
        right.addWidget(disclaimer)
        right.addSpacing(6)
        self.notes = label("", "muted", wrap=True)
        self.notes.setStyleSheet("font-size: 9pt;")
        right.addWidget(self.notes)
        right.addStretch(1)
        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer.addWidget(right_w, 1)

    def set_original(self, rows: list[tuple[str, str]]):
        for name, value in rows:
            if name in self.rows:
                self.rows[name][0].setText(value)

    def set_enhanced(self, rows: list[tuple[str, str]] | None):
        for name, (_, b) in self.rows.items():
            b.setText("—")
        for name, value in rows or []:
            if name in self.rows:
                self.rows[name][1].setText(value)

    def set_score(self, original: float | None, enhanced: float | None):
        if original is None or enhanced is None:
            self.score.setText("—")
            self.score.setProperty("tone", "")
            theme.restyle(self.score)
            self.score_detail.setText("Enhance the recording to compare.")
            return
        delta = enhanced - original
        tone = "success" if delta >= 3 else ("muted" if delta > -3 else "warning")
        self.score.setText(f"{delta:+.0f}")
        self.score.setProperty("tone", tone)
        theme.restyle(self.score)
        self.score_detail.setText(f"Estimated clarity {original:.0f} → {enhanced:.0f} (out of 100)")

    def set_notes(self, notes: list[str]):
        self.notes.setText("\n".join(f"• {n}" for n in notes))
