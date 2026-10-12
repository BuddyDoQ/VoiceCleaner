"""Edit one take of the compilation: cut out or silence regions the automatic trimming
missed (a pop, a click, a stumble, a pause that was not detected).

The original file is never changed; the edits are applied when the compilation is built.
Cut removes the region (crossfaded, so the join is smooth); Silence keeps the timing
and fades the region to silence.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from ..audio.compile import Take, add_edit, render_take
from ..audio.loader import format_duration
from ..utils.shell import shortcut_text
from . import theme
from .audio_player import AudioPlayer
from .waveform import WaveformOverview, WaveformView
from .widgets import label

KIND_LABEL = {"cut": "CUT", "mute": "SILENCE"}


class TakeEditView(WaveformView):
    """The whole take with what the compilation leaves out drawn on top: trimmed ends,
    shortened pauses and the user's edits (the picked one outlined)."""

    MIN_SELECTION_S = 0.004  # pops and clicks are only a few milliseconds long

    def __init__(self, take: Take, parent=None):
        super().__init__("enhanced", "", parent)
        self.take = take
        self.edits: list = []
        self.picked: int | None = None
        self.set_active(True)
        self.setMinimumHeight(220)

    def _shade(self, p: QPainter, r: QRectF, a: float, b: float, color, alpha: int) -> QRectF | None:
        xa, xb = max(r.left(), self._t_to_x(a)), min(r.right(), self._t_to_x(b))
        if xb <= xa:
            return None
        box = QRectF(xa, r.top(), max(1.5, xb - xa), r.height())
        p.fillRect(box, theme.qcolor(color, alpha))
        return box

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.overview is None:
            return
        t, sr = self.take, self.take.sample_rate
        p = QPainter(self)
        r = self._plot_rect()
        f = QFont(self.font())
        f.setPointSizeF(theme.pt(7.5))
        f.setBold(True)
        p.setFont(f)
        # trimmed ends (automatic)
        for a, b in ((0, t.start), (t.end, t.samples.shape[0])):
            box = self._shade(p, r, a / sr, b / sr, theme.FAINT, 45)
            if box is not None and box.width() > 56:
                p.setPen(theme.qcolor(theme.FAINT))
                p.drawText(box.adjusted(4, 4, -4, 0), Qt.AlignLeft | Qt.AlignTop, "TRIMMED")
        # shortened pauses (automatic)
        for a, b in t.cuts:
            box = self._shade(p, r, a / sr, b / sr, theme.FAINT, 45)
            if box:
                p.fillRect(QRectF(box.left(), r.bottom() - 3, box.width(), 3), theme.qcolor(theme.WARNING, 200))
        # the user's edits
        for i, (a, b, kind) in enumerate(self.edits):
            color = theme.RECORD if kind == "cut" else theme.MUTED
            box = self._shade(p, r, a / sr, b / sr, color, 70 if kind == "cut" else 90)
            if box is None:
                continue
            p.fillRect(QRectF(box.left(), r.top(), box.width(), 3), theme.qcolor(color, 230))
            if box.width() > 36:
                p.setPen(theme.qcolor(color))
                p.drawText(QRectF(box.left() + 3, r.top() + 4, box.width() - 4, 14), Qt.AlignLeft | Qt.AlignTop,
                           KIND_LABEL[kind])
            if i == self.picked:
                p.setPen(QPen(theme.qcolor(theme.TEXT), 1.5, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()

    def edit_at(self, seconds: float) -> int | None:
        s = seconds * self.take.sample_rate
        # a few pixels of slack, so very short edits can still be clicked
        slack = 3 * (self.t1 - self.t0) / max(1.0, self._plot_rect().width()) * self.take.sample_rate
        for i, (a, b, _k) in enumerate(self.edits):
            if a - slack <= s <= b + slack:
                return i
        return None


class TakeEditor(QDialog):
    """Select a region and Cut or Silence it. Edits can be clicked and removed."""

    def __init__(self, take: Take, title: str, parent=None):
        super().__init__(parent)
        self.take = take
        self.setWindowTitle(f"Edit Take — {title}")
        self.resize(1100, 470)
        self._history: list[list] = []
        self._play_end: float | None = None  # stop here (playing a selection)
        self._result = False  # the player holds the edited take, not the original
        self.player = AudioPlayer(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.stateChanged.connect(self._on_state)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        v.addWidget(label(
            "Drag across a pop, click or unwanted pause, then Cut it out or Silence it. Click an edit to pick it; "
            f"Delete removes it. {shortcut_text('Ctrl')}+wheel zooms, the wheel scrolls. "
            "The original file is not changed.", "muted", wrap=True))
        self.view = TakeEditView(take)
        self.view.edits = list(take.edits)
        self.view.set_overview(WaveformOverview.compute(take.samples, take.sample_rate))
        self.view.selectionChanged.connect(self._selection_changed)
        self.view.seekRequested.connect(self._clicked)
        v.addWidget(self.view, 1)
        self.info = label("", "mono")
        v.addWidget(self.info)

        row = QHBoxLayout()
        self.play_btn = self._btn("▶ Play", self.toggle_play, "Play the selection, or from the playhead (Space)")
        self.result_btn = self._btn("Hear Result", self.play_result,
                                    "Play the take as it will sound in the compilation (R)")
        row.addWidget(self.play_btn)
        row.addWidget(self.result_btn)
        row.addSpacing(16)
        self.cut_btn = self._btn("✂ Cut", lambda: self.apply("cut"), "Remove the selection (X or Delete)")
        self.cut_btn.setProperty("role", "primary")
        self.mute_btn = self._btn("Silence", lambda: self.apply("mute"), "Silence the selection, keeping its length (S)")
        self.remove_btn = self._btn("Remove Edit", self.remove_picked, "Remove the picked edit (Delete)")
        self.undo_btn = self._btn("Undo", self.undo, f"Undo ({shortcut_text('Ctrl+Z')})")
        self.clear_btn = self._btn("Clear All", self.clear_all, "Remove all edits of this take")
        for b in (self.cut_btn, self.mute_btn, self.remove_btn, self.undo_btn, self.clear_btn):
            row.addWidget(b)
        row.addSpacing(16)
        row.addWidget(self._btn("Zoom to Selection", self.zoom_selection, "Zoom in on the selection (Z)"))
        row.addWidget(self._btn("Fit", self.fit, "Show the whole take (F)"))
        row.addStretch(1)
        cancel = self._btn("Cancel", self.reject, "Close without changing the take (Esc)")
        done = self._btn("Done", self.accept, "Keep the edits (Enter)")
        done.setProperty("role", "primary")
        done.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(done)
        v.addLayout(row)

        for keys, fn in (("Space", self.toggle_play), ("R", self.play_result), ("X", lambda: self.apply("cut")),
                         ("Delete", self._delete), ("Backspace", self._delete), ("S", lambda: self.apply("mute")),
                         ("Ctrl+Z", self.undo), ("Z", self.zoom_selection), ("F", self.fit)):
            QShortcut(QKeySequence(keys), self, fn)
        self._update()

    def _btn(self, text, fn, tip) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)  # Space plays instead of pressing the last button
        b.clicked.connect(fn)
        return b

    # --- editing --------------------------------------------------------------------------
    @property
    def edits(self) -> list:
        return self.view.edits

    def _set_edits(self, edits: list, picked: int | None = None):
        self._history.append(list(self.view.edits))
        self.view.edits = edits
        self.view.picked = picked
        self._update()

    def apply(self, kind: str):
        sel = self.view.selection
        if not sel:
            return
        sr = self.take.sample_rate
        a, b = int(sel[0] * sr), int(np.ceil(sel[1] * sr))
        self._set_edits(add_edit(self.view.edits, a, b, kind))
        self.view.set_selection(None)

    def remove_picked(self):
        i = self.view.picked
        if i is None:
            return
        self._set_edits([e for j, e in enumerate(self.view.edits) if j != i])

    def _delete(self):
        if self.view.picked is not None:
            self.remove_picked()
        else:
            self.apply("cut")

    def undo(self):
        if self._history:
            self.view.edits = self._history.pop()
            self.view.picked = None
            self._update()

    def clear_all(self):
        if self.view.edits:
            self._set_edits([])

    def _selection_changed(self, sel):
        if sel:
            self.view.picked = None
        self._update()

    def _clicked(self, t: float):
        self.view.picked = self.view.edit_at(t)
        self.view.set_selection(None)
        self._seek_original(t)
        self._update()

    def _update(self):
        t, sr = self.take, self.take.sample_rate
        preview = Take(t.path, t.samples, sr, t.start, t.end, t.lufs, t.overview, t.cuts, self.view.edits)
        n = len(self.view.edits)
        msg = (f"{format_duration(t.duration)} → {format_duration(preview.final_duration)} in the compilation"
               f"   ·   {n} edit{'s' if n != 1 else ''}")
        if preview.edit_seconds:
            msg += f", −{preview.edit_seconds:.2f} s cut"
        sel = self.view.selection
        if sel:
            msg += f"   ·   selection {sel[0]:.3f}–{sel[1]:.3f} s ({(sel[1] - sel[0]) * 1000:.0f} ms)"
        elif self.view.picked is not None:
            a, b, k = self.view.edits[self.view.picked]
            msg += f"   ·   picked: {KIND_LABEL[k].lower()} {a / sr:.3f}–{b / sr:.3f} s"
        self.info.setText(msg)
        self.cut_btn.setEnabled(bool(sel))
        self.mute_btn.setEnabled(bool(sel))
        self.remove_btn.setEnabled(self.view.picked is not None)
        self.undo_btn.setEnabled(bool(self._history))
        self.clear_btn.setEnabled(bool(self.view.edits))
        self.view.update()

    # --- view -----------------------------------------------------------------------------
    def zoom_selection(self):
        sel = self.view.selection
        if sel:
            pad = max(0.05, (sel[1] - sel[0]) * 0.5)
            self.view.set_view(sel[0] - pad, sel[1] + pad)

    def fit(self):
        self.view.set_view(0.0, self.take.duration)

    # --- playback -------------------------------------------------------------------------
    def _seek_original(self, t: float):
        if self._result:
            self.player.stop()
            self._use_original()
        self.player.seek(t)
        self.view.set_playhead(t)

    def _use_original(self):
        self._result = False
        self.player.set_source("original", self.take.samples, self.take.sample_rate)
        self.player.set_active("original")

    def toggle_play(self):
        if self.player.state == "playing":
            self.player.pause()
            return
        if self._result or not self.player.has_source("original"):
            self._use_original()
        sel = self.view.selection
        if sel:
            self.player.seek(sel[0])
            self._play_end = sel[1]
        else:
            self.player.seek(self.view.playhead)
            self._play_end = None
        self.player.play()

    def play_result(self):
        if self._result and self.player.state == "playing":
            self.player.pause()
            return
        t = self.take
        preview = Take(t.path, t.samples, t.sample_rate, t.start, t.end, t.lufs, t.overview, t.cuts, self.view.edits)
        x = render_take(preview)
        if x.shape[0] == 0:
            return
        self.player.stop()
        self._result, self._play_end = True, None
        self.player.set_source("original", x, t.sample_rate)
        self.player.set_active("original")
        self.player.seek(0)
        self.player.play()

    def _on_position(self, s: float):
        if self._result:
            return  # the edited take has its own timeline
        self.view.set_playhead(s)
        if self._play_end is not None and s >= self._play_end:
            self._play_end = None
            self.player.pause()

    def _on_state(self, state: str):
        playing = state == "playing"
        self.play_btn.setText("❚❚ Pause" if playing and not self._result else "▶ Play")
        self.result_btn.setText("❚❚ Pause" if playing and self._result else "Hear Result")

    def done(self, r):
        self.player.shutdown()
        super().done(r)


def edit_take(take: Take, title: str, parent=None) -> list | None:
    """Show the editor; the new edits, or None when cancelled."""
    dlg = TakeEditor(take, title or Path(take.path).name, parent)
    return list(dlg.edits) if dlg.exec() == QDialog.Accepted else None
