"""Waveform display with playhead, click-to-seek, zoom and region selection."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

OVERVIEW_BLOCK = 64  # samples per overview bin


class WaveformOverview:
    """Min/max/RMS envelope at a fixed block size, cheap to redraw at any zoom."""

    def __init__(self, mins: np.ndarray, maxs: np.ndarray, rms: np.ndarray, sr: int, frames: int):
        self.mins, self.maxs, self.rms = mins, maxs, rms
        self.sr, self.frames = sr, frames

    @property
    def duration(self) -> float:
        return self.frames / self.sr

    @classmethod
    def compute(cls, samples: np.ndarray, sr: int) -> "WaveformOverview":
        n = samples.shape[0]
        nb = int(np.ceil(n / OVERVIEW_BLOCK))
        mins = np.empty(nb, np.float32)
        maxs = np.empty(nb, np.float32)
        rms = np.empty(nb, np.float32)
        step = OVERVIEW_BLOCK * 16384
        for a in range(0, n, step):
            seg = np.asarray(samples[a : a + step], dtype=np.float32)
            mono = seg.mean(axis=1) if seg.ndim == 2 else seg
            pad = (-mono.shape[0]) % OVERVIEW_BLOCK
            if pad:
                mono = np.pad(mono, (0, pad))
            blocks = mono.reshape(-1, OVERVIEW_BLOCK)
            i = a // OVERVIEW_BLOCK
            mins[i : i + blocks.shape[0]] = blocks.min(axis=1)
            maxs[i : i + blocks.shape[0]] = blocks.max(axis=1)
            rms[i : i + blocks.shape[0]] = np.sqrt(np.mean(blocks * blocks, axis=1))
        return cls(mins, maxs, rms, sr, n)

    def patched(self, start_frame: int, samples: np.ndarray, base: np.ndarray) -> "WaveformOverview":
        """A copy with ``samples`` (laid over ``base`` from ``start_frame``) redrawn; only
        the bins it touches are recomputed, so this is cheap for long recordings."""
        b0 = start_frame // OVERVIEW_BLOCK
        b1 = min(self.mins.shape[0], -(-(start_frame + samples.shape[0]) // OVERVIEW_BLOCK))
        seg = np.array(base[b0 * OVERVIEW_BLOCK : b1 * OVERVIEW_BLOCK], dtype=np.float32)
        off = start_frame - b0 * OVERVIEW_BLOCK
        n = min(samples.shape[0], seg.shape[0] - off)
        seg[off : off + n] = samples[:n]
        part = WaveformOverview.compute(seg, self.sr)
        mins, maxs, rms = self.mins.copy(), self.maxs.copy(), self.rms.copy()
        k = min(b1 - b0, part.mins.shape[0])
        mins[b0 : b0 + k], maxs[b0 : b0 + k], rms[b0 : b0 + k] = part.mins[:k], part.maxs[:k], part.rms[:k]
        return WaveformOverview(mins, maxs, rms, self.sr, self.frames)

    def columns(self, t0: float, t1: float, width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        b0 = int(np.clip(t0 * self.sr / OVERVIEW_BLOCK, 0, self.mins.shape[0]))
        b1 = int(np.clip(np.ceil(t1 * self.sr / OVERVIEW_BLOCK), b0 + 1, self.mins.shape[0]))
        if b1 <= b0:
            z = np.zeros(width, np.float32)
            return z, z, z
        edges = np.linspace(b0, b1, width + 1).astype(int)
        edges = np.minimum(edges, b1 - 1)
        starts = edges[:-1]
        lo = np.minimum.reduceat(self.mins[b0:b1], np.clip(starts - b0, 0, b1 - b0 - 1))
        hi = np.maximum.reduceat(self.maxs[b0:b1], np.clip(starts - b0, 0, b1 - b0 - 1))
        r = np.maximum.reduceat(self.rms[b0:b1], np.clip(starts - b0, 0, b1 - b0 - 1))
        return lo, hi, r


class WaveformView(QWidget):
    seekRequested = Signal(float)
    selectionChanged = Signal(object)  # (start, end) seconds or None
    selectionFinished = Signal(object)  # the mouse was released on a selection: (start, end)
    viewChanged = Signal(float, float)  # visible (t0, t1)
    activated = Signal()
    MIN_SELECTION_S = 0.05  # shorter drags are dropped

    def __init__(self, color_role: str, placeholder: str = "", parent=None):
        """``color_role`` is "original" or "enhanced"; the colour follows the theme."""
        super().__init__(parent)
        self.color_role = color_role
        self.placeholder = placeholder
        self.overview: WaveformOverview | None = None
        self.playhead: float = 0.0
        self.selection: tuple[float, float] | None = None
        self.highlight_regions: list[tuple[float, float]] = []
        self.loop_region: tuple[float, float] | None = None  # drawn as a looped passage
        self.preview_region: tuple[float, float] | None = None  # live preview of new settings
        self.edit_regions: list[tuple[float, float, str]] = []  # the user's edits: (start, end, "cut" | "mute") s
        self.active = False
        self.busy_text = ""
        self.busy_fraction: float | None = None  # progress shown with busy_text (None = no bar)
        self._busy_t0 = 0.0
        self._busy_timer = QTimer(self)  # animates the "working" sweep
        self._busy_timer.setInterval(33)
        self._busy_timer.timeout.connect(self.update)
        self.live: tuple[WaveformOverview, float] | None = None  # recording in progress, offset (s)
        self.t0, self.t1 = 0.0, 1.0
        self._drag_start: float | None = None
        self._drag_px = 0
        self._hover_x: float | None = None
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.IBeamCursor)

    # --- data -------------------------------------------------------------------
    def set_overview(self, overview: WaveformOverview | None, keep_view: bool = False):
        self.overview = overview
        if overview is not None and not keep_view:
            self.t0, self.t1 = 0.0, overview.duration
        self.update()

    def set_playhead(self, seconds: float):
        if abs(seconds - self.playhead) > 1e-4:
            self.playhead = seconds
            self.update()

    def set_selection(self, sel: tuple[float, float] | None):
        self.selection = sel
        self.update()

    def set_active(self, active: bool):
        self.active = active
        self.update()

    def set_live(self, overview: WaveformOverview | None, offset_s: float = 0.0):
        """Show a recording in progress as a red layer starting at ``offset_s``."""
        self.live = None if overview is None else (overview, offset_s)
        if self.live is not None:
            end = offset_s + overview.duration
            if self.overview is None:
                self.t0, self.t1 = 0.0, max(10.0, end * 1.15)
            elif end > self.t1:
                span = self.t1 - self.t0
                self.t0, self.t1 = max(0.0, end - span * 0.85), max(0.0, end - span * 0.85) + span
        self.update()

    @property
    def total_duration(self) -> float:
        d = self.overview.duration if self.overview else 0.0
        if self.live:
            d = max(d, self.live[1] + self.live[0].duration, self.t1)
        return d

    def set_busy(self, text: str, fraction: float | None = None):
        """Show that this track is being worked on: ``text`` (e.g. the processing step)
        with a progress bar when ``fraction`` is given, and a moving sweep either way."""
        if text and not self.busy_text:
            self._busy_t0 = time.monotonic()
        self.busy_text = text
        self.busy_fraction = fraction if text else None
        if text and not self._busy_timer.isActive():
            self._busy_timer.start()
        elif not text:
            self._busy_timer.stop()
        self.update()

    def set_view(self, t0: float, t1: float):
        if self.overview is None:
            return
        dur = max(self.overview.duration, self.total_duration if self.live else 0.0)
        span = float(np.clip(t1 - t0, min(0.05, dur), dur))
        t0 = float(np.clip(t0, 0, dur - span))
        if (t0, t0 + span) != (self.t0, self.t1):
            self.t0, self.t1 = t0, t0 + span
            self.update()

    # --- coordinate helpers ----------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        return QRectF(12, 10, self.width() - 24, self.height() - 34)

    def _x_to_t(self, x: float) -> float:
        r = self._plot_rect()
        frac = (x - r.left()) / max(1.0, r.width())
        return float(np.clip(self.t0 + frac * (self.t1 - self.t0), 0, self.total_duration))

    def _t_to_x(self, t: float) -> float:
        r = self._plot_rect()
        return r.left() + (t - self.t0) / max(1e-9, self.t1 - self.t0) * r.width()

    # --- painting -------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._plot_rect()
        mid = r.center().y()

        if self.overview is None and self.live is not None:
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1, Qt.DashLine))
            p.drawLine(QPointF(r.left(), mid), QPointF(r.right(), mid))
            self._draw_live(p, r)
            self._draw_ruler(p, r)
            return
        if self.overview is None:
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1, Qt.DashLine))
            p.drawLine(QPointF(r.left(), mid), QPointF(r.right(), mid))
            if self.busy_text:
                self._draw_busy(p, r, dim=False)
                return
            p.setPen(theme.qcolor(theme.FAINT))
            f = QFont(self.font())
            f.setPointSizeF(theme.pt(10.5))
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.placeholder)
            return

        # live preview: this part already reflects the new settings
        if self.preview_region:
            a, b = self.preview_region
            xa, xb = max(r.left(), self._t_to_x(a)), min(r.right(), self._t_to_x(b))
            if xb > xa:
                p.fillRect(QRectF(xa, r.top(), xb - xa, r.height()), theme.qcolor(theme.SUCCESS, 18))
                p.fillRect(QRectF(xa, r.top() - 6, xb - xa, 3), theme.qcolor(theme.SUCCESS, 200))
                f = QFont(self.font())
                f.setPointSizeF(theme.pt(7.5))
                p.setFont(f)
                p.setPen(theme.qcolor(theme.SUCCESS))
                p.drawText(QRectF(xa + 4, r.top() - 3, max(60.0, xb - xa), 14), Qt.AlignLeft | Qt.AlignTop,
                           "LIVE PREVIEW")
        # loop: accent edges so it is clear what repeats
        if self.loop_region:
            a, b = self.loop_region
            xa, xb = self._t_to_x(a), self._t_to_x(b)
            p.fillRect(QRectF(xa, r.top(), xb - xa, r.height()), theme.qcolor(theme.ACCENT, 22))
            p.setPen(QPen(theme.qcolor(theme.ACCENT, 220), 2))
            for x in (xa, xb):
                if r.left() <= x <= r.right():
                    p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        # selection
        if self.selection:
            a, b = self.selection
            xa, xb = self._t_to_x(a), self._t_to_x(b)
            p.fillRect(QRectF(xa, r.top(), xb - xa, r.height()), theme.qcolor(theme.WARNING, 40))
            p.setPen(QPen(theme.qcolor(theme.WARNING, 160), 1))
            p.drawLine(QPointF(xa, r.top()), QPointF(xa, r.bottom()))
            p.drawLine(QPointF(xb, r.top()), QPointF(xb, r.bottom()))

        for a, b in self.highlight_regions:
            if b < self.t0 or a > self.t1:
                continue
            xa, xb = self._t_to_x(a), self._t_to_x(b)
            p.fillRect(QRectF(xa, r.bottom() - 3, xb - xa, 3), theme.qcolor(theme.WARNING, 120))

        width = max(1, int(r.width()))
        lo, hi, rms = self.overview.columns(self.t0, self.t1, width)
        half = r.height() / 2 * 0.95
        xs = r.left() + np.arange(width) + 0.5

        color = QColor(theme.WAVE_ORIGINAL if self.color_role == "original" else theme.WAVE_ENHANCED)
        if not self.active:
            color.setAlpha(150)
        # peak envelope
        path = QPainterPath()
        path.moveTo(xs[0], mid - hi[0] * half)
        for x, v in zip(xs[1:], hi[1:]):
            path.lineTo(x, mid - v * half)
        for x, v in zip(xs[::-1], lo[::-1]):
            path.lineTo(x, mid - v * half)
        path.closeSubpath()
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        c_soft = QColor(color)
        c_soft.setAlpha(int(color.alpha() * 0.55))
        grad.setColorAt(0, c_soft)
        grad.setColorAt(0.5, color)
        grad.setColorAt(1, c_soft)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(path)
        # RMS core
        core = QColor(color).lighter(135)
        core.setAlpha(int(color.alpha() * 0.9))
        rpath = QPainterPath()
        rpath.moveTo(xs[0], mid - rms[0] * half)
        for x, v in zip(xs[1:], rms[1:]):
            rpath.lineTo(x, mid - v * half)
        for x, v in zip(xs[::-1], rms[::-1]):
            rpath.lineTo(x, mid + v * half)
        rpath.closeSubpath()
        p.setBrush(core)
        p.drawPath(rpath)

        if self.edit_regions:
            self._draw_edits(p, r)

        if self.live is not None:
            self._draw_live(p, r)

        # time ruler
        self._draw_ruler(p, r)

        # playhead
        if self.t0 <= self.playhead <= self.t1:
            x = self._t_to_x(self.playhead)
            p.setPen(QPen(theme.qcolor(theme.PLAYHEAD) if self.active else theme.qcolor(theme.MUTED), 1.5))
            p.drawLine(QPointF(x, r.top() - 4), QPointF(x, r.bottom() + 2))

        # hover time
        if self._hover_x is not None:
            t = self._x_to_t(self._hover_x)
            p.setPen(QPen(theme.qcolor(theme.TEXT, 70), 1))
            p.drawLine(QPointF(self._hover_x, r.top()), QPointF(self._hover_x, r.bottom()))

        if self.busy_text:
            self._draw_busy(p, r, dim=True)

    def _draw_edits(self, p: QPainter, r: QRectF):
        """Cut regions in red (left out when played and exported), silenced ones in grey."""
        f = QFont(self.font())
        f.setPointSizeF(theme.pt(7.5))
        f.setBold(True)
        p.setFont(f)
        for a, b, kind in self.edit_regions:
            xa, xb = max(r.left(), self._t_to_x(a)), min(r.right(), self._t_to_x(b))
            if xb <= xa:
                continue
            color = theme.RECORD if kind == "cut" else theme.MUTED
            box = QRectF(xa, r.top(), max(1.5, xb - xa), r.height())
            p.fillRect(box, theme.qcolor(color, 70 if kind == "cut" else 90))
            p.fillRect(QRectF(box.left(), r.top(), box.width(), 3), theme.qcolor(color, 230))
            if box.width() > 36:
                p.setPen(theme.qcolor(color))
                p.drawText(QRectF(box.left() + 3, r.top() + 4, box.width() - 4, 14), Qt.AlignLeft | Qt.AlignTop,
                           "CUT" if kind == "cut" else "SILENCE")

    def _draw_busy(self, p: QPainter, r: QRectF, dim: bool):
        """Step text, percentage, progress bar and a sweep that keeps moving."""
        if dim:
            p.fillRect(self.rect(), theme.qcolor(theme.SURFACE, 200))
        # sweep: a soft band gliding across the track
        phase = ((time.monotonic() - self._busy_t0) / 1.6) % 1.0
        band = r.width() * 0.22
        x = r.left() - band + (r.width() + band) * phase
        grad = QLinearGradient(x, 0, x + band, 0)
        c0, c1 = theme.qcolor(theme.ACCENT, 0), theme.qcolor(theme.ACCENT, 46)
        grad.setColorAt(0, c0)
        grad.setColorAt(0.5, c1)
        grad.setColorAt(1, c0)
        p.fillRect(QRectF(max(r.left(), x), r.top(), min(band, r.right() - x), r.height()), grad)
        mid = r.center().y()
        f = QFont(self.font())
        f.setPointSizeF(theme.pt(10.5))
        p.setFont(f)
        p.setPen(theme.qcolor(theme.TEXT))
        text = self.busy_text
        if self.busy_fraction is not None:
            text = f"{text.rstrip('.').rstrip(chr(0x2026))}  ·  {self.busy_fraction * 100:.0f}%"
        p.drawText(QRectF(r.left(), mid - 26, r.width(), 22), Qt.AlignCenter, text)
        if self.busy_fraction is not None:
            bw = min(320.0, r.width() * 0.6)
            track = QRectF(r.center().x() - bw / 2, mid + 4, bw, 4)
            p.fillRect(track, theme.qcolor(theme.SURFACE_3))
            p.fillRect(QRectF(track.left(), track.top(), track.width() * self.busy_fraction, track.height()),
                       theme.qcolor(theme.ACCENT))

    def _draw_live(self, p: QPainter, r: QRectF):
        ov, offset = self.live
        a, b = max(self.t0, offset), min(self.t1, offset + ov.duration)
        if b <= a:
            return
        x0, x1 = self._t_to_x(a), self._t_to_x(b)
        width = max(1, int(x1 - x0))
        lo, hi, _ = ov.columns(a - offset, b - offset, width)
        mid = r.center().y()
        half = r.height() / 2 * 0.95
        xs = x0 + np.arange(width) + 0.5
        path = QPainterPath()
        path.moveTo(xs[0], mid - hi[0] * half)
        for x, v in zip(xs[1:], hi[1:]):
            path.lineTo(x, mid - v * half)
        for x, v in zip(xs[::-1], lo[::-1]):
            path.lineTo(x, mid - v * half)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(theme.RECORD, 200))
        p.drawPath(path)
        # recording head
        p.setPen(QPen(theme.qcolor(theme.RECORD), 2))
        p.drawLine(QPointF(x1, r.top() - 4), QPointF(x1, r.bottom() + 2))

    def _draw_ruler(self, p: QPainter, r: QRectF):
        span = self.t1 - self.t0
        steps = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]
        target = span / max(1, r.width() / 90)
        step = next((s for s in steps if s >= target), steps[-1])
        f = QFont(self.font())
        f.setPointSizeF(theme.pt(8))
        p.setFont(f)
        t = np.ceil(self.t0 / step) * step
        y = r.bottom() + 4
        while t <= self.t1 + 1e-9:
            x = self._t_to_x(t)
            p.setPen(QPen(theme.qcolor(theme.BORDER), 1))
            p.drawLine(QPointF(x, y), QPointF(x, y + 4))
            p.setPen(theme.qcolor(theme.FAINT))
            p.drawText(QRectF(x - 40, y + 4, 80, 14), Qt.AlignHCenter | Qt.AlignTop, _fmt_time(t, step))
            t += step

    # --- interaction ----------------------------------------------------------------
    def mousePressEvent(self, e):
        if self.overview is None or e.button() != Qt.LeftButton:
            return
        self.activated.emit()
        self._drag_start = self._x_to_t(e.position().x())
        self._drag_px = e.position().x()

    def mouseMoveEvent(self, e):
        self._hover_x = e.position().x() if self.overview is not None else None
        if self._drag_start is not None and abs(e.position().x() - self._drag_px) > 4:
            t = self._x_to_t(e.position().x())
            a, b = sorted((self._drag_start, t))
            self.selection = (a, b)
            self.selectionChanged.emit(self.selection)
        self.update()

    def leaveEvent(self, _e):
        self._hover_x = None
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_start is None:
            return
        if abs(e.position().x() - self._drag_px) <= 4:
            self.seekRequested.emit(self._x_to_t(e.position().x()))
        elif self.selection and self.selection[1] - self.selection[0] < self.MIN_SELECTION_S:
            self.selection = None
            self.selectionChanged.emit(None)
        elif self.selection:
            self.selectionFinished.emit(self.selection)
        self._drag_start = None
        self.update()

    def mouseDoubleClickEvent(self, _e):
        if self.selection:
            self.selection = None
            self.selectionChanged.emit(None)
            self.update()

    def wheelEvent(self, e):
        if self.overview is None:
            return
        delta = e.angleDelta().y() or e.angleDelta().x()
        span = self.t1 - self.t0
        if e.modifiers() & Qt.ControlModifier:
            anchor = self._x_to_t(e.position().x())
            factor = 0.8 if delta > 0 else 1.25
            new_span = span * factor
            frac = (anchor - self.t0) / span if span else 0.5
            self.set_view(anchor - frac * new_span, anchor - frac * new_span + new_span)
        else:
            if span >= self.overview.duration - 1e-6:
                e.ignore()
                return
            shift = -np.sign(delta) * span * 0.1
            self.set_view(self.t0 + shift, self.t1 + shift)
        self.viewChanged.emit(self.t0, self.t1)


def _fmt_time(t: float, step: float) -> str:
    m, s = divmod(t, 60)
    if step < 1:
        return f"{int(m)}:{s:04.1f}" if m else f"{s:.1f}s" if step >= 0.1 else f"{s:.2f}s"
    return f"{int(m)}:{int(round(s)):02d}"
