"""Compile tab: join enhanced takes into one WAV.

Starts with the current session's enhanced exports. Takes can be added from
the session, from files (button or drag-and-drop from Explorer) or from the
Playback tab, re-ordered by dragging, previewed and exported. Leading and
trailing silence of every take is trimmed, keeping up to 100 ms (adjustable)
at each end.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from ..audio.compile import DEFAULT_HANDLE_MS, Take, assemble, load_take, retrim
from ..audio.pauses import DEFAULT_KEEP_MS, DEFAULT_MIN_PAUSE_S, PauseSettings
from ..audio.loader import format_duration
from ..utils.logging import get_logger
from ..utils.shell import shortcut_text
from ..workers.processing_worker import run_task
from . import theme
from .audio_player import AudioPlayer
from .waveform import WaveformOverview, WaveformView
from .widgets import card, label

log = get_logger("compile")
PATH_ROLE = Qt.UserRole
TITLE_ROLE = Qt.UserRole + 1  # teleprompter rows: "03 · Section · Take 2 of 3" instead of the file name
UNIT_ROLE = Qt.UserRole + 2  # teleprompter rows: the script unit id
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
        name = index.data(TITLE_ROLE) or Path(path).name
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
    prompterRequested = Signal()
    playStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.takes: dict[str, Take] = {}
        self.compilation = None
        self._stale = False  # the preview no longer matches the list (a rebuild is on its way)
        self._generation = 0  # newest build wins; results of older builds are dropped
        self._take_preview = False  # the player holds a single take, not the compilation
        self._populated_for = None
        self._preview_task = None
        self._load_task = None
        self.activity = None  # the window's ActivityTracker, set by MainWindow
        self.script_mode = False  # the list shows the teleprompter's takes in script order
        self._script_refresh = QTimer(self)
        self._script_refresh.setSingleShot(True)
        self._script_refresh.setInterval(300)
        self._script_refresh.timeout.connect(self.refresh_script)
        # rebuild the preview automatically once the user pauses: a burst of drags or
        # option changes leads to one build, so Play is instant
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(lambda: self.build())
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
        up.setToolTip(f"Move up ({shortcut_text('Ctrl+Up')})")
        up.clicked.connect(lambda: self.move_selected(-1))
        down = QPushButton("▼")
        down.setProperty("role", "icon")
        down.setToolTip(f"Move down ({shortcut_text('Ctrl+Down')})")
        down.clicked.connect(lambda: self.move_selected(1))
        clear = QPushButton("Clear")
        clear.setProperty("role", "ghost")
        clear.clicked.connect(self.clear)
        # where the list comes from: the session's exports, or the teleprompter's takes in script order
        self.src_exports = QPushButton("SESSION EXPORTS")
        self.src_script = QPushButton("TELEPROMPTER TAKES")
        for b, side in ((self.src_exports, "left"), (self.src_script, "right")):
            b.setProperty("role", "ab")
            b.setProperty("side", side)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
        self.src_exports.setChecked(True)
        self.src_script.setToolTip("The teleprompter's takes in script order: the selected take of each section.\n"
                                   "Right-click a row to use another take.")
        self.src_exports.clicked.connect(lambda: self.set_script_mode(False))
        self.src_script.clicked.connect(lambda: self.set_script_mode(True))
        src = QHBoxLayout()
        src.setSpacing(0)
        src.addWidget(self.src_exports)
        src.addWidget(self.src_script)
        self.src_switch = QWidget()
        self.src_switch.setLayout(src)
        self.src_switch.setVisible(False)  # until the session has teleprompter takes
        tools.addWidget(self.src_switch)
        tools.addSpacing(10)
        self._tool_buttons = (self.add_session_btn, add_files, self.remove_btn, up, down)
        for w in (self.add_session_btn, add_files, self.remove_btn, up, down):
            tools.addWidget(w)
        tools.addStretch(1)
        prompter = QPushButton("\u25a4 Teleprompter")
        prompter.setToolTip("Record a script with the teleprompter: its takes appear here in script order (Ctrl+T)")
        prompter.clicked.connect(self.prompterRequested)
        tools.addWidget(prompter)
        tools.addWidget(clear)
        outer.addLayout(tools)

        self.list = TakeList(self)
        self.list.setItemDelegate(TakeDelegate(self))
        self.list.filesDropped.connect(self.add_files)
        self.list.orderChanged.connect(self._changed)
        self.list.moveRequested.connect(self.move_selected)
        self.list.itemDoubleClicked.connect(lambda it: self.preview_take(it.data(PATH_ROLE)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._row_menu)
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
        """First time the tab is shown for a session: its teleprompter takes in script order
        if it has any, else its enhanced exports."""
        if self.session is None:
            return
        has_script = bool(self._book().takes)
        self.src_switch.setVisible(has_script)
        if self._populated_for != self.session.path and (self.list.count() == 0 or self.script_mode):
            self._populated_for = self.session.path
            if has_script:
                self.set_script_mode(True)
            else:
                self.add_session_exports(quiet=True)

    # --- teleprompter takes ------------------------------------------------------------------
    def _book(self):
        from ..teleprompter.takes import TakeBook

        return TakeBook.shared(self.session.path)

    def set_script_mode(self, on: bool):
        self.script_mode = on
        self.src_exports.setChecked(not on)
        self.src_script.setChecked(on)
        for b in self._tool_buttons:
            b.setEnabled(not on)
        self.list.setDragDropMode(QAbstractItemView.NoDragDrop if on else QAbstractItemView.InternalMove)
        self.list.clear()
        if on:
            self.refresh_script()
        else:
            self.add_session_exports(quiet=True)

    def takes_changed(self):
        """The teleprompter recorded or enhanced a take: update the list soon."""
        if self.session is None:
            return
        self.src_switch.setVisible(True)
        if self.script_mode or (self.list.count() == 0 and self.isVisible()):
            if not self.script_mode:
                self.set_script_mode(True)
            self._script_refresh.start()

    def refresh_script(self):
        if self.session is None or not self.script_mode:
            return
        book = self._book()
        rows = book.compilation(self.session.recordings_dir, self.session.exports_dir)
        wanted = [str(p) for _u, _t, p in rows]
        if wanted == self.paths():
            for i, (u, t, p) in enumerate(rows):  # same files: only the labels may change
                self.list.item(i).setData(TITLE_ROLE, self._title(book, u, t, p))
            self.list.viewport().update()
            return
        self.list.clear()
        for u, t, p in rows:
            it = QListWidgetItem()
            it.setData(PATH_ROLE, str(p))
            it.setData(TITLE_ROLE, self._title(book, u, t, p))
            it.setData(UNIT_ROLE, u["id"])
            it.setToolTip(f"{p}\nRight-click to use another take.")
            self.list.addItem(it)
        self._load_paths([w for w in wanted if w not in self.takes])
        self._changed()

    def _title(self, book, unit: dict, take, path: Path) -> str:
        n = len(book.takes_of(unit["id"]))
        star = " \u2605" if take.keeper else ""
        state = "" if take.enhanced and path.parent == self.session.exports_dir else " \u00b7 enhancing\u2026"
        return f"{unit['id']} \u00b7 {unit.get('title', '')} \u00b7 Take {take.number} of {n}{star}{state}"

    def _row_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None or not self.script_mode or self.session is None:
            return
        unit = it.data(UNIT_ROLE)
        book = self._book()
        current = book.selected(unit)
        m = QMenu(self)
        for t in sorted(book.takes_of(unit), key=lambda t: t.number):
            star = "  \u2605" if t.keeper else ""
            used = "  \u2190 used" if current and t.number == current.number else ""
            a = m.addAction(f"Take {t.number}  ({t.duration:.1f} s){star}{used}")
            a.triggered.connect(lambda _=False, n=t.number: self._use_take(unit, n))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _use_take(self, unit: str, number: int):
        book = self._book()
        book.select(unit, number)
        self.refresh_script()

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
        self._load_paths(new)

    def _load_paths(self, new: list):
        """Read and trim the takes of ``new`` (paths already in the list) in the background."""
        if not new:
            return
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
        """Order, takes or options changed: the preview no longer matches. It is rebuilt
        automatically after a short pause; playback carries on with the old version and
        switches to the new one at the same take when it is ready."""
        self.list.viewport().update()
        takes = self._ordered_takes()
        playing = self.player.state == "playing"
        if not playing:
            self.player.stop()
        if not takes:
            self.player.stop()
            self.compilation = None
            self.wave.set_overview(None)
        self._stale = True
        if takes:
            self._rebuild_timer.start(self._rebuild_delay_ms(takes))
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
        self.preview_state.setText("Updating preview\u2026" if takes else "")

    @staticmethod
    def _rebuild_delay_ms(takes: list[Take]) -> int:
        """Wait a little longer for very long compilations, so dragging stays smooth."""
        total = sum(t.final_duration for t in takes)
        return 750 if total < 30 * 60 else 1500

    # --- preview & export -------------------------------------------------------------------
    def build(self, then=None):
        """Build the preview now (in the background). ``then`` runs once it is ready."""
        self._rebuild_timer.stop()
        takes = self._ordered_takes()
        if not takes:
            return
        if self._preview_task is not None and self._preview_task.running:
            self._preview_task.cancel()  # its result would be outdated anyway
        self._generation += 1
        gen = self._generation
        gap, level = self.gap.value(), self.level.isChecked()
        key = "compile-preview"
        if self.activity is not None:
            self.activity.start(key, "Updating preview", self)

        def work(ctx):
            comp = assemble(takes, gap_ms=gap, match_loudness=level,
                            progress=lambda f: (ctx.check(), ctx.progress(f, "Updating preview...")))
            return comp, WaveformOverview.compute(comp.samples, comp.sample_rate)

        def done(payload):
            if gen != self._generation:
                return  # a newer build is on its way
            if self.activity is not None:
                self.activity.finish(key)
            self._show_compilation(*payload)
            if then:
                then()

        def failed(_title, message, cancelled):
            if gen == self._generation:
                if self.activity is not None:
                    self.activity.finish(key, ok=False)
                self.preview_state.setText("" if cancelled else message)

        def progress(f, m):
            if gen == self._generation:
                self.preview_state.setText(m)
                if self.activity is not None:
                    self.activity.update(key, f, m)

        self._preview_task = run_task(work, self, on_success=done, on_error=failed, on_progress=progress)

    @property
    def preview_ready(self) -> bool:
        """The preview is built and matches the current list and options."""
        return self.compilation is not None and not self._stale

    def _show_compilation(self, comp, ov):
        old, pos = self.compilation, self.player.position
        playing = self.player.state == "playing" and not self._take_preview
        self.compilation, self._stale = comp, False
        self.wave.set_overview(ov)
        self.wave.highlight_regions = [(a, b) for i, (a, b, _n) in enumerate(comp.segments) if i % 2 == 0]
        msg = (f"{len(comp.segments)} takes • {format_duration(comp.duration)} • "
               f"{comp.removed_seconds:.1f} s of silence removed")
        if comp.pause_seconds:
            msg += f" ({comp.pause_seconds:.1f} s from long pauses)"
        self.preview_state.setText(msg)
        if self._take_preview and self.player.state == "playing":
            return  # a single take is playing: the new preview is used at the next Play
        self._take_preview = False
        self.player.set_source("original", comp.samples, comp.sample_rate)
        self.player.set_active("original")
        new_pos = map_position(old, comp, pos) if playing else 0.0
        self.player.seek(new_pos)
        self._on_position(new_pos)

    def toggle_play(self):
        if self.player.state == "playing":
            self.player.pause()
            return
        if self.compilation is None or self._stale:
            self.build(then=self._play)  # do not wait for the timer
        else:
            if self._take_preview:  # back from a single take to the compilation
                self._take_preview = False
                self.player.set_source("original", self.compilation.samples, self.compilation.sample_rate)
                self.player.set_active("original")
                self.player.seek(0)
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
        self._take_preview = True
        self.player.set_source("original", t.samples[t.start:t.end], t.sample_rate)
        self.player.set_active("original")
        self.player.seek(0)
        self.preview_state.setText(f"Playing trimmed take: {Path(path).name}")
        self._play()

    def _on_position(self, s: float):
        self.wave.set_playhead(s)
        total = self.player.duration if self._take_preview else (self.compilation.duration if self.compilation else 0.0)
        self.time_label.setText(f"{format_duration(s)} / {format_duration(total)}")

    def export(self):
        name = f"{self.session.name} - Compilation" if self.session else "Compilation"
        if self.compilation is None or self._stale:
            self.build(then=lambda: self.exportRequested.emit(self.compilation, name))
        else:
            self.exportRequested.emit(self.compilation, name)

    def shutdown(self):
        self._rebuild_timer.stop()
        self.player.shutdown()


def map_position(old, new, pos: float) -> float:
    """Where ``pos`` (seconds in compilation ``old``) lands in ``new``: the same point of
    the same take if it is still there, otherwise the same time, clamped."""
    if new is None:
        return 0.0
    if old is not None:
        for a, b, name in old.segments:
            if a <= pos < b:
                for a2, b2, name2 in new.segments:
                    if name2 == name:
                        return min(a2 + (pos - a), max(a2, b2 - 0.01))
                break
    return max(0.0, min(pos, new.duration - 0.01))

