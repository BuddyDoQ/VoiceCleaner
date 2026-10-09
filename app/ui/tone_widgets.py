"""Widgets for the EQ & Tone, Dynamics and Re-synthesis sections."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from ..audio.eq import EQBand, response_db
from ..audio.settings import EQ_BANDS, EQ_RANGE_DB
from . import theme


class CollapsibleSection(QWidget):
    """Titled section with a disclosure arrow; content is created by the caller."""

    toggled = Signal(bool)

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.button = QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.button.setArrowType(Qt.RightArrow)
        self.button.setProperty("role", "disclosure")
        self.button.toggled.connect(self._toggle)
        head.addWidget(self.button)
        head.addStretch(1)
        self.summary = QLabel(subtitle)
        self.summary.setProperty("role", "faint")
        head.addWidget(self.summary)
        lay.addLayout(head)
        self.body = QFrame()
        self.body.setProperty("role", "card")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 10, 12, 12)
        self.body_layout.setSpacing(8)
        self.body.setVisible(False)
        lay.addWidget(self.body)

    def _toggle(self, on: bool):
        self.body.setVisible(on)
        self.button.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.toggled.emit(on)

    def set_open(self, on: bool):
        self.button.setChecked(on)

    @property
    def is_open(self) -> bool:
        return self.button.isChecked()


class EQCurve(QWidget):
    """Log-frequency response plot: automatic tonal balance + your EQ = total."""

    FREQS = np.geomspace(30, 20000, 240)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manual: list[EQBand] = []
        self.auto: list[EQBand] | None = None
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_bands(self, manual: list[EQBand], auto: list[EQBand] | None = None):
        self.manual = manual
        if auto is not None:
            self.auto = auto
        self.update()

    def clear_auto(self):
        self.auto = None
        self.update()

    def _x(self, f: float, r: QRectF) -> float:
        return r.left() + (np.log10(f) - np.log10(30)) / (np.log10(20000) - np.log10(30)) * r.width()

    def _y(self, db: float, r: QRectF) -> float:
        return r.center().y() - np.clip(db, -EQ_RANGE_DB, EQ_RANGE_DB) / EQ_RANGE_DB * (r.height() / 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 6, -4, -16)
        p.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        for db in (-12, -6, 0, 6, 12):
            y = self._y(db, r)
            p.setPen(QPen(theme.qcolor(theme.BORDER if db else "#3a4150"), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        for hz, txt in ((100, "100"), (1000, "1k"), (10000, "10k")):
            x = self._x(hz, r)
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(theme.qcolor(theme.FAINT))
            p.drawText(QRectF(x - 20, r.bottom() + 2, 40, 12), Qt.AlignHCenter, txt)

        def curve(bands, color, width, dash=False):
            resp = response_db(bands, self.FREQS) if bands else np.zeros_like(self.FREQS)
            path = QPainterPath()
            for i, (fr, v) in enumerate(zip(self.FREQS, resp)):
                pt = QPointF(self._x(fr, r), self._y(v, r))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            pen = QPen(color, width)
            if dash:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        if self.auto:
            curve(self.auto, theme.qcolor(theme.ORIGINAL, 200), 1.3, dash=True)
        curve((self.auto or []) + self.manual, QColor(theme.ACCENT), 2.0)


class BandSlider(QWidget):
    """One vertical graphic-EQ band: dB value, slider, label."""

    valueChanged = Signal(int, float)

    def __init__(self, index: int, freq: float, label: str, parent=None):
        super().__init__(parent)
        self.index = index
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.value_label = QLabel("0")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setProperty("role", "eqvalue")
        v.addWidget(self.value_label)
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(int(-EQ_RANGE_DB * 2), int(EQ_RANGE_DB * 2))  # 0.5 dB steps
        self.slider.setFixedHeight(110)
        self.slider.setToolTip(f"{label} ({_fmt_hz(freq)}). Double-click to reset.")
        self.slider.valueChanged.connect(self._changed)
        v.addWidget(self.slider, alignment=Qt.AlignHCenter)
        hz = QLabel(_fmt_hz(freq))
        hz.setAlignment(Qt.AlignCenter)
        hz.setProperty("role", "eqfreq")
        v.addWidget(hz)
        name = QLabel(label)
        name.setAlignment(Qt.AlignCenter)
        name.setProperty("role", "eqname")
        v.addWidget(name)
        self.slider.mouseDoubleClickEvent = lambda _e: self.slider.setValue(0)

    def value(self) -> float:
        return self.slider.value() / 2

    def set_value(self, db: float):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(db * 2)))
        self.slider.blockSignals(False)
        self._label()

    def _label(self):
        v = self.value()
        self.value_label.setText(f"{v:+.1f}" if v else "0")
        self.value_label.setProperty("active", "true" if v else "false")
        theme.restyle(self.value_label)

    def _changed(self, _):
        self._label()
        self.valueChanged.emit(self.index, self.value())


class GraphicEQ(QWidget):
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        self.bands = []
        for i, (freq, label, _kind) in enumerate(EQ_BANDS):
            b = BandSlider(i, freq, label)
            b.valueChanged.connect(lambda *_: self.changed.emit(self.values()))
            grid.addWidget(b, 0, i)
            self.bands.append(b)

    def values(self) -> list[float]:
        return [b.value() for b in self.bands]

    def set_values(self, gains: list[float]):
        for b, g in zip(self.bands, gains):
            b.set_value(float(g))


def _fmt_hz(f: float) -> str:
    return f"{f / 1000:g}k" if f >= 1000 else f"{f:g}"
