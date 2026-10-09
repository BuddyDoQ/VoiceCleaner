"""Compile tab: join enhanced takes into one WAV.

Starts with the current session's enhanced exports. Takes can be added from
the session, from files (button or drag-and-drop from Explorer) or from the
Playback tab, re-ordered by dragging, previewed and exported. Leading and
trailing silence of every take is trimmed, keeping up to 100 ms (adjustable)
at each end.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from ..audio.compile import DEFAULT_HANDLE_MS, Take, assemble, load_take, retrim
from ..audio.pauses import DEFAULT_KEEP_MS, DEFAULT_MIN_PAUSE_S, PauseSettings
from ..audio.loader import format_duration
from ..utils.logging import get_logger
from ..workers.processing_worker import run_task
from . import theme
from .audio_player import AudioPlayer
from .waveform import WaveformOverview, WaveformView
from .widgets import card, label

log = get_logger("compile")
PATH_ROLE = Qt.UserRole
ROW_HEIGHT = 64
WAV = (".wav", ".wave")


class TakeDelegate(QStyledItemDelegate):
    """Paints a take row: order, grip, name, durations, mini waveform with trimmed ends shaded."""

    def __init__(self, panel: "CompilePanel"):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 3, -4, -3)
        selected = bool(option.state & QStyle.State_Selected)
        p.setPen(QPen(theme.qcolor(theme.ACCENT if selected else theme.BORDER), 1))
        p.setBrush(theme.qcolor(theme.SURFACE_3 if selected else theme.SURFACE_2))
        p.drawRoundedRect(r, 6, 6)

        path = index.data(PATH_ROLE)
        take: Take | None = self.panel.takes.get(path)
        x = r.left() + 10
        # order number
        f = QFont(theme.FONT_DISPLAY)
        f.setPixelSize(26)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.ACCENT))
        p.drawText(QRectF(x, r.top(), 34, r.height()), Qt.AlignVCenter | Qt.AlignHCenter, str(index.row() + 1))
        x += 38
        # grip
        p.setPen(theme.qcolor(theme.FAINT))
        f = QFont(theme.FONT_UI)
        f.setPixelSize(14)
        p.setFont(f)
        p.drawText(QRectF(x, r.top(), 14, r.height()), Qt.AlignVCenter, "⋮⋮")
        x += 22
        # name + durations
        text_w = min(360.0, r.width() * 0.38)
        f = QFont(theme.FONT_UI)
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.TEXT))
        name = Path(path).name
        p.drawText(QRectF(x, r.top() + 10, text_w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(name, Qt.ElideMiddle, int(text_w)))
        f = QFont(theme.FONT_MONO)
        f.setPixelSize(11)
        p.setFont(f)
        if take is None:
            detail, tone = "Loading…", theme.FAINT
        elif take.silent:
            detail, tone = "No audible sound: will be skipped", theme.WARNING
        else:
            ends = take.duration - take.trimmed_duration
            detail = f"{format_duration(take.duration)} → {format_duration(take.final_duration)}   (ends −{ends:.1f} s"
            if take.cuts:
                detail += f", {len(take.cuts)} pause{'s' if len(take.cuts) != 1 else ''} −{take.pause_seconds:.1f} s"
            detail += ")"
            tone = theme.MUTED
        p.setPen(theme.qcolor(tone))
        p.drawText(QRectF(x, r.top() + 32, text_w, 18), Qt.AlignLeft | Qt.AlignVCenter, detail)
        x += text_w + 12
        # mini waveform
        wr = QRectF(x, r.top() + 8, r.right() - x - 10, r.height() - 16)
        if take is not None and take.overview.size and wr.width() > 20:
            ov = take.overview
            n = ov.shape[0]
            total = max(1, take.samples.shape[0])
            k0, k1 = take.start / total * n, take.end / total * n
            peak = float(ov.max()) or 1.0
            mid = wr.center().y()
            for i, v in enumerate(ov):
                xx = wr.left() + (i + 0.5) / n * wr.width()
                h = max(1.0, v / peak * wr.height() / 2)
                keep = k0 <= i < k1
                p.setPen(QPen(theme.qcolor(theme.WAVE_ENHANCED if keep else theme.FAINT, 255 if keep else 110), 1.2))
                p.drawLine(QRectF(xx, mid - h, 0, 2 * h).topLeft(), QRectF(xx, mid - h, 0, 2 * h).bottomLeft())
            # trimmed areas
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(theme.BG, 120))
            p.drawRect(QRectF(wr.left(), wr.top(), wr.width() * k0 / n, wr.height()))
            p.drawRect(QRectF(wr.left() + wr.width() * k1 / n, wr.top(), wr.width() * (1 - k1 / n), wr.height()))
            # shortened pauses: dimmed, with a thin marker
            for a, b in take.cuts:
                xa, xb = wr.left() + a / total * wr.width(), wr.left() + b / total * wr.width()
                p.setBrush(theme.qcolor(theme.BG, 150))
                p.setPen(Qt.NoPen)
                p.drawRect(QRectF(xa, wr.top(), xb - xa, wr.height()))
                p.setBrush(theme.qcolor(theme.WARNING, 200))
                p.drawRect(QRectF(xa, wr.bottom() - 2, xb - xa, 2))
        p.restore()


class TakeList(QListWidget):
    """Reorders by internal drag; also accepts WAV files dropped from Explorer."""

    filesDropped = Signal(list)
    orderChanged = Signal()
    moveRequested = Signal(int)  # -1 up, +1 down (Ctrl+Up / Ctrl+Down)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSpacing(1)
        self.setUniformItemSizes(True)
        self.setAcceptDrops(True)
        self.model().rowsMoved.connect(lambda *_: self.orderChanged.emit())

    def _wav_urls(self, e) -> list[Path]:
        if not e.mimeData().hasUrls():
            return []
        out = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                out += sorted(q for q in p.glob("*") if q.suffix.lower() in WAV)
            elif p.suffix.lower() in WAV:
                out.append(p)
        return out

    def dragEnterEvent(self, e):
        if self._wav_urls(e):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        files = self._wav_urls(e)
        if files:
            e.acceptProposedAction()
            self.filesDropped.emit(files)
        else:
            super().dropEvent(e)
            self.orderChanged.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and e.modifiers() & Qt.ControlModifier:
            self.moveRequested.emit(-1 if e.key() == Qt.Key_Up else 1)
            return
        if e.key() == Qt.Key_Delete:
            for it in self.selectedItems():
                self.takeItem(self.row(it))
            self.orderChanged.emit()
            return
        super().keyPressEvent(e)


class CompilePanel(QWidget):
    exportRequested = Signal(object, str)  # Compilation, suggested name
    playStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.takes: dict[str, Take] = {}
        self.compilation = None
        self._populated_for = None
        self._preview_task = None
        self._load_task = None
        self.player = AudioPlayer(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.stateChanged.connect(lambda s: self.play_btn.setText("❚❚" if s == "playing" else "▶"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Compile", "title"))
        head.addSpacing(12)
        sub = label("Join enhanced takes into one WAV. Drag rows to change the order; silence at the start and "
                    "end of each take is trimmed.", "muted", wrap=True)
        head.addWidget(sub, 1)
        outer.addLayout(head)

        tools = QHBoxLayout()
        self.add_session_btn = QPushButton("Add Session Exports")
        self.add_session_btn.setToolTip("Add the enhanced takes from this session's Exports folder")
        self.add_session_btn.clicked.connect(self.add_session_exports)
        add_files = QPushButton("Add Files…")
        add_files.clicked.connect(self._choose_files)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        up = QPushButton("▲")
        up.setProperty("role", "icon")
        up.setToolTip("Move up (Ctrl+Up)")
        up.clicked.connect(lambda: self.move_selected(-1))
        down = QPushButton("▼")
        down.setProperty("role", "icon")
        down.setToolTip("Move down (Ctrl+Down)")
        down.clicked.connect(lambda: self.move_selected(1))
        clear = QPushButton("Clear")
        clear.setProperty("role", "ghost")
        clear.clicked.connect(self.clear)
        for w in (self.add_session_btn, add_files, self.remove_btn, up, down):
            tools.addWidget(w)
        tools.addStretch(1)
        tools.addWidget(clear)
        outer.addLayout(tools)

        self.list = TakeList(self)
        self.list.setItemDelegate(TakeDelegate(self))
        self.list.filesDropped.connect(self.add_files)
        self.list.orderChanged.connect(self._changed)
        self.list.moveRequested.connect(self.move_selected)
        self.list.itemDoubleClicked.connect(lambda it: self.preview_take(it.data(PATH_ROLE)))
        self.list.setToolTip("Drag to reorder • Double-click to hear a take • Drop WAV files here to add them")
        outer.addWidget(self.list, 1)
        self.empty_hint = label("No takes yet. Enhance and export recordings in this session, or drop WAV files here.",
                                "faint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty_hint)

        opts = QHBoxLayout()
        opts.setSpacing(14)
        opts.addWidget(label("Keep silence at each end", "muted"))
        self.handle = QSpinBox()
        self.handle.setRange(0, 1000)
        self.handle.setSingleStep(10)
        self.handle.setSuffix(" ms")
        self.handle.setMinimumWidth(110)
        self.handle.setValue(int(DEFAULT_HANDLE_MS))
        self.handle.setToolTip("Silence before the first sound and after the last sound of each take is trimmed "
                               "down to at most this much.")
        self.handle.valueChanged.connect(self._handle_changed)
        opts.addWidget(self.handle)
        opts.addSpacing(10)
        opts.addWidget(label("Gap between takes", "muted"))
        self.gap = QSpinBox()
        self.gap.setRange(0, 5000)
        self.gap.setSingleStep(50)
        self.gap.setSuffix(" ms")
        self.gap.setMinimumWidth(110)
        self.gap.setValue(0)
        self.gap.valueChanged.connect(self._changed)
        opts.addWidget(self.gap)
        opts.addSpacing(10)
        self.level = QCheckBox("Even out loudness between takes")
        self.level.setChecked(True)
        self.level.toggled.connect(self._changed)
        opts.addWidget(self.level)
        opts.addStretch(1)
        self.summary = label("", "mono")
        pause_row = QHBoxLayout()
        pause_row.setSpacing(14)
        self.pause_box = QCheckBox("Shorten long pauses")
        self.pause_box.setChecked(False)  # off by default: always the user's choice
        self.pause_box.setToolTip("Shortens long silences inside each take (not the ends) to a natural pause.\n"
                                  "The original files are never changed.")
        self.pause_box.toggled.connect(self._pauses_changed)
        pause_row.addWidget(self.pause_box)
        pause_row.addWidget(label("longer than", "muted"))
        self.pause_min = QDoubleSpinBox()
        self.pause_min.setRange(0.3, 5.0)
        self.pause_min.setSingleStep(0.1)
        self.pause_min.setDecimals(1)
        self.pause_min.setSuffix(" s")
        self.pause_min.setValue(DEFAULT_MIN_PAUSE_S)
        self.pause_min.setMinimumWidth(100)
        self.pause_min.valueChanged.connect(self._pauses_changed)
        pause_row.addWidget(self.pause_min)
        pause_row.addWidget(label("shorten to", "muted"))
        self.pause_keep = QSpinBox()
        self.pause_keep.setRange(0, 2000)
        self.pause_keep.setSingleStep(50)
        self.pause_keep.setSuffix(" ms")
        self.pause_keep.setSpecialValueText("Remove completely")
        self.pause_keep.setValue(int(DEFAULT_KEEP_MS))
        self.pause_keep.setMinimumWidth(170)
        self.pause_keep.setToolTip("Length each long pause is shortened to. Set to 0 to remove long pauses completely.")
        self.pause_keep.valueChanged.connect(self._pauses_changed)
        pause_row.addWidget(self.pause_keep)
        pause_row.addStretch(1)
        self._sync_pause_controls()
        opts.addWidget(self.summary)
        outer.addLayout(opts)
        outer.addLayout(pause_row)

        outer.addWidget(self._build_preview())

    def _build_preview(self) -> QWidget:
        c = card()
        v = QVBoxLayout(c)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        t = label("COMPILATION PREVIEW", "section")
        t.setProperty("tone", "accent")
        top.addWidget(t)
        top.addStretch(1)
        self.preview_state = label("", "faint")
        top.addWidget(self.preview_state)
        v.addLayout(top)
        self.wave = WaveformView("enhanced", "Press Play to build and hear the compilation")
        self.wave.setMinimumHeight(90)
        self.wave.setMaximumHeight(130)
        self.wave.seekRequested.connect(self.player.seek)
        v.addWidget(self.wave)
        row = QHBoxLayout()
        self.play_btn = QPushButton("▶")
        self.play_btn.setProperty("role", "round")
        self.play_btn.setToolTip("Build (if needed) and play the compilation (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        stop = QPushButton("■")
        stop.setProperty("role", "icon")
        stop.clicked.connect(self.player.stop)
        row.addWidget(stop)
        self.time_label = label("0:00.0 / 0:00.0", "mono")
        row.addWidget(self.time_label)
        row.addStretch(1)
        self.export_btn = QPushButton("Export Compilation…")
        self.export_btn.setProperty("role", "primary")
        self.export_btn.clicked.connect(self.export)
        row.addWidget(self.export_btn)
        v.addLayout(row)
        return c

    # --- takes --------------------------------------------------------------------------
    def set_session(self, session):
        self.session = session

    def ensure_populated(self):
        """First time the tab is shown for a session: start with its enhanced exports."""
        if self.session is not None and self._populated_for != self.session.path and self.list.count() == 0:
            self._populated_for = self.session.path
            self.add_session_exports(quiet=True)

    def add_session_exports(self, quiet: bool = False):
        if self.session is None:
            return
        files = sorted(self.session.exports_dir.glob("*.wav"), key=lambda p: (p.stat().st_mtime, p.name))
        if not files and not quiet:
            self.preview_state.setText("This session has no exported takes yet.")
        self.add_files(files)

    def _choose_files(self):
        start = str(self.session.exports_dir) if self.session else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Add takes", start, "WAV audio (*.wav *.wave)")
        self.add_files([Path(f) for f in files])

    def paths(self) -> list[str]:
        return [self.list.item(i).data(PATH_ROLE) for i in range(self.list.count())]

    def add_files(self, files: list):
        existing = set(self.paths())
        new = [str(Path(f)) for f in files if str(Path(f)) not in existing]
        if not new:
            return
        for p in new:
            it = QListWidgetItem()
            it.setData(PATH_ROLE, p)
            it.setToolTip(p)
            it.setFlags(it.flags() | Qt.ItemIsDragEnabled)
            self.list.addItem(it)
        handle = self.handle.value()
        pauses = self.pause_settings()

        def work(ctx):
            loaded, failed = {}, []
            for i, p in enumerate(new):
                ctx.check()
                try:
                    loaded[p] = load_take(Path(p), handle, pauses)
                except Exception as exc:  # unreadable file: reported, removed from the list
                    failed.append((p, getattr(exc, "user_message", str(exc))))
                ctx.progress((i + 1) / len(new), f"Loading takes {i + 1}/{len(new)}...")
            return loaded, failed

        def done(result):
            loaded, failed = result
            self.takes.update(loaded)
            for p, msg in failed:
                self._remove_path(p)
                log.warning("Could not add %s: %s", p, msg)
            if failed:
                self.preview_state.setText(f"{len(failed)} file(s) could not be read and were skipped.")
            self._changed()

        self._load_task = run_task(work, self, on_success=done,
                                   on_progress=lambda f, m: self.preview_state.setText(m))
        self._changed()

    def _remove_path(self, p: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(PATH_ROLE) == p:
                self.list.takeItem(i)
                break

    def remove_selected(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._changed()

    def clear(self):
        self.list.clear()
        self._changed()

    def move_selected(self, delta: int):
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        if not rows:
            return
        if (delta < 0 and rows[0] == 0) or (delta > 0 and rows[-1] == self.list.count() - 1):
            return
        order = rows if delta < 0 else rows[::-1]
        for r in order:
            it = self.list.takeItem(r)
            self.list.insertItem(r + delta, it)
            it.setSelected(True)
        self._changed()

    def options(self) -> dict:
        """Current choices, saved in the preferences between sessions."""
        return {"handle_ms": self.handle.value(), "gap_ms": self.gap.value(), "level": self.level.isChecked(),
                "pause_on": self.pause_box.isChecked(), "pause_min_s": self.pause_min.value(),
                "pause_keep_ms": self.pause_keep.value()}

    def restore_options(self, o: dict):
        try:
            self.handle.setValue(int(o.get("handle_ms", self.handle.value())))
            self.gap.setValue(int(o.get("gap_ms", self.gap.value())))
            self.level.setChecked(bool(o.get("level", self.level.isChecked())))
            self.pause_min.setValue(float(o.get("pause_min_s", self.pause_min.value())))
            self.pause_keep.setValue(int(o.get("pause_keep_ms", self.pause_keep.value())))
            self.pause_box.setChecked(bool(o.get("pause_on", False)))
        except (TypeError, ValueError):
            pass  # damaged preferences: keep the defaults
        self._sync_pause_controls()

    def pause_settings(self) -> PauseSettings:
        return PauseSettings(enabled=self.pause_box.isChecked(), min_pause_s=self.pause_min.value(),
                             keep_ms=float(self.pause_keep.value()))

    def _sync_pause_controls(self):
        on = self.pause_box.isChecked()
        self.pause_min.setEnabled(on)
        self.pause_keep.setEnabled(on)

    def _pauses_changed(self, *_):
        self._sync_pause_controls()
        self._handle_changed(self.handle.value())

    def _handle_changed(self, ms: int):
        pauses = self.pause_settings()
        for t in self.takes.values():
            retrim(t, ms, pauses)
        self._changed()

    def _ordered_takes(self) -> list[Take]:
        return [self.takes[p] for p in self.paths() if p in self.takes]

    def _changed(self, *_):
        """Order, takes or options changed: the preview no longer matches."""
        self.list.viewport().update()
        self.player.stop()
        self.compilation = None
        self.wave.set_overview(None)
        takes = self._ordered_takes()
        n = self.list.count()
        self.empty_hint.setVisible(n == 0)
        self.export_btn.setEnabled(bool(takes))
        if takes:
            before = sum(t.duration for t in takes)
            after = sum(t.final_duration for t in takes if not t.silent) + \
                self.gap.value() / 1000 * max(0, sum(not t.silent for t in takes) - 1)
            self.summary.setText(f"{n} take{'s' if n != 1 else ''} · {format_duration(before)} → "
                                 f"{format_duration(after)}")
        else:
            self.summary.setText("")
        self.preview_state.setText("")

    # --- preview & export -------------------------------------------------------------------
    def build(self, then=None):
        takes = self._ordered_takes()
        if not takes:
            return
        gap, level = self.gap.value(), self.level.isChecked()

        def work(ctx):
            comp = assemble(takes, gap_ms=gap, match_loudness=level,
                            progress=lambda f: ctx.progress(f, "Building compilation..."))
            return comp, WaveformOverview.compute(comp.samples, comp.sample_rate)

        def done(payload):
            comp, ov = payload
            self.compilation = comp
            self.wave.set_overview(ov)
            self.wave.highlight_regions = [(a, b) for i, (a, b, _n) in enumerate(comp.segments) if i % 2 == 0]
            self.player.set_source("original", comp.samples, comp.sample_rate)
            self.player.set_active("original")
            msg = (f"{len(comp.segments)} takes • {format_duration(comp.duration)} • "
                   f"{comp.removed_seconds:.1f} s of silence removed")
            if comp.pause_seconds:
                msg += f" ({comp.pause_seconds:.1f} s from long pauses)"
            self.preview_state.setText(msg)
            self.time_label.setText(f"0:00.0 / {format_duration(comp.duration)}")
            if then:
                then()

        self._preview_task = run_task(work, self, on_success=done,
                                      on_error=lambda t, m, c: self.preview_state.setText("" if c else m),
                                      on_progress=lambda f, m: self.preview_state.setText(m))

    def toggle_play(self):
        if self.player.state == "playing":
            self.player.pause()
            return
        if self.compilation is None:
            self.build(then=self._play)
        else:
            self._play()

    def _play(self):
        self.playStarted.emit()
        self.player.play()

    def pause(self):
        if self.player.state == "playing":
            self.player.pause()

    def preview_take(self, path: str):
        t = self.takes.get(path)
        if t is None or t.silent:
            return
        self.compilation = None
        self.player.set_source("original", t.samples[t.start:t.end], t.sample_rate)
        self.player.set_active("original")
        self.player.seek(0)
        self.preview_state.setText(f"Playing trimmed take: {Path(path).name}")
        self._play()

    def _on_position(self, s: float):
        self.wave.set_playhead(s)
        total = self.compilation.duration if self.compilation else 0.0
        self.time_label.setText(f"{format_duration(s)} / {format_duration(total)}")

    def export(self):
        name = f"{self.session.name} - Compilation" if self.session else "Compilation"
        if self.compilation is None:
            self.build(then=lambda: self.exportRequested.emit(self.compilation, name))
        else:
            self.exportRequested.emit(self.compilation, name)

    def shutdown(self):
        self.player.shutdown()

