"""Playback tab: browse and play recordings and exports.

Starts with the current session (its Recordings and Exports). "All Recordings"
lists every session under the default sessions directory, and "Browse Folder"
scans any folder (recursively). Every *.wav is header-checked in the
background; only files that can actually be played are listed.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFileDialog, QHBoxLayout, QHeaderView,
                               QLineEdit, QPushButton, QSlider, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..audio.loader import format_duration, format_size, load_wav, probe
from ..utils.errors import VoiceCleanerError
from ..utils.logging import get_logger
from ..utils.shell import FILE_MANAGER, open_folder, reveal
from ..workers.processing_worker import run_task
from .audio_player import AudioPlayer
from .waveform import WaveformOverview, WaveformView
from .widgets import ElidedLabel, card, label

log = get_logger("playback")

MAX_FILES = 5000
MAX_DEPTH = 8
COLUMNS = ["Name", "Type", "Duration", "Format", "Size", "Modified", "Folder"]


class _SortItem(QTableWidgetItem):
    """Table item that sorts by a hidden key (numbers, dates) instead of text."""

    def __init__(self, text: str, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        return self.key < getattr(other, "key", other.text())


def scan_folder(root: Path, ctx=None, max_files: int = MAX_FILES) -> tuple[list[dict], int]:
    """All playable WAV files under ``root`` (recursive). Returns (entries, skipped)."""
    entries, skipped = [], 0
    root = Path(root)
    base_depth = len(root.parts)
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base_depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]  # skip .parts and hidden folders
        for f in filenames:
            if f.lower().endswith((".wav", ".wave")):
                candidates.append(Path(dirpath) / f)
        if len(candidates) >= max_files:
            break
    for i, p in enumerate(candidates[:max_files]):
        if ctx is not None:
            ctx.check()
            if i % 25 == 0:
                ctx.progress(i / max(1, len(candidates)), f"Scanning {i} of {len(candidates)} files...")
        try:
            info = probe(p)
            st = p.stat()
        except (VoiceCleanerError, OSError):
            skipped += 1
            continue
        entries.append({"path": p, "info": info, "mtime": st.st_mtime})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries, skipped


class PlaybackPanel(QWidget):
    openInEnhancer = Signal(object)  # Path
    addToBatch = Signal(list)
    addToCompile = Signal(list)
    playStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.mode = "session"  # session | all | folder
        self.folder: Path | None = None
        self.default_root: Path | None = None
        self.entries: list[dict] = []
        self.current: Path | None = None
        self._scan_task = None
        self._load_task = None
        self.player = AudioPlayer(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.stateChanged.connect(self._on_state)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(label("Playback", "title"))
        head.addSpacing(16)
        self.src_buttons = {}
        group = QButtonGroup(self)
        for i, (key, text, tip) in enumerate([
            ("session", "THIS SESSION", "Recordings and exports of the current session"),
            ("all", "ALL RECORDINGS", "Every session in the default recordings folder"),
            ("folder", "BROWSE FOLDER…", "Choose any folder; all playable WAV files in it are listed"),
        ]):
            b = QPushButton(text)
            b.setProperty("role", "seg")
            b.setProperty("side", "left" if i == 0 else ("right" if i == 2 else "middle"))
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self._source_clicked(k))
            group.addButton(b)
            self.src_buttons[key] = b
            head.addWidget(b)
        self.src_buttons["session"].setChecked(True)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._apply_filter)
        head.addWidget(self.search)
        refresh = QPushButton("↻")
        refresh.setProperty("role", "icon")
        refresh.setToolTip("Rescan")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        outer.addLayout(head)

        loc = QHBoxLayout()
        self.location = ElidedLabel("")
        self.location.setProperty("role", "faint")
        loc.addWidget(self.location, 1)
        self.open_dir_btn = QPushButton("Open Recording Folder")
        self.open_dir_btn.setProperty("role", "outline")
        self.open_dir_btn.setToolTip(f"Show this location in {FILE_MANAGER}")
        self.open_dir_btn.clicked.connect(self._open_location)
        loc.addWidget(self.open_dir_btn)
        outer.addLayout(loc)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(COLUMNS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _i: self.play_selected())
        split.addWidget(self.table)

        split.addWidget(self._build_player())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)
        self.status = label("", "faint")
        outer.addWidget(self.status)

    def _build_player(self) -> QWidget:
        c = card()
        v = QVBoxLayout(c)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)
        top = QHBoxLayout()
        self.now_name = ElidedLabel("Select a recording to play it")
        self.now_name.setProperty("role", "h2")
        top.addWidget(self.now_name, 1)
        self.now_meta = label("", "faint")
        top.addWidget(self.now_meta)
        v.addLayout(top)
        self.wave = WaveformView("enhanced", "Select a recording above")
        self.wave.setMinimumHeight(120)
        self.wave.seekRequested.connect(self.player.seek)
        v.addWidget(self.wave, 1)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_btn = QPushButton("▶")
        self.play_btn.setProperty("role", "round")
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        stop = QPushButton("■")
        stop.setProperty("role", "icon")
        stop.clicked.connect(self.player.stop)
        row.addWidget(stop)
        self.time_label = label("0:00.0 / 0:00.0", "mono")
        row.addWidget(self.time_label)
        row.addSpacing(10)
        row.addWidget(label("\U0001F50A", "faint"))
        vol = QSlider(Qt.Horizontal)
        vol.setProperty("role", "small")
        vol.setRange(0, 100)
        vol.setValue(90)
        vol.setFixedWidth(110)
        vol.valueChanged.connect(lambda val: self.player.set_volume("original", val / 100))
        row.addWidget(vol)
        row.addStretch(1)
        self.enhance_btn = QPushButton("Open in Enhancer")
        self.enhance_btn.setProperty("role", "primary")
        self.enhance_btn.clicked.connect(lambda: self.current and self.openInEnhancer.emit(self.current))
        self.batch_btn = QPushButton("Add to Batch")
        self.batch_btn.setProperty("role", "outline")
        self.batch_btn.clicked.connect(self._add_selected_to_batch)
        self.show_btn = QPushButton("Show in Folder")
        self.show_btn.setProperty("role", "outline")
        self.show_btn.clicked.connect(lambda: self.current and reveal(self.current))
        for b in (self.show_btn, self.batch_btn, self.enhance_btn):
            b.setEnabled(False)
            row.addWidget(b)
        self.compile_btn = QPushButton("Add to Compilation")
        self.compile_btn.setProperty("role", "outline")
        self.compile_btn.setToolTip("Add the selected recordings to the Compile tab")
        self.compile_btn.setEnabled(False)
        self.compile_btn.clicked.connect(
            lambda: self.addToCompile.emit(self._selected_paths() or ([self.current] if self.current else [])))
        row.insertWidget(row.indexOf(self.batch_btn), self.compile_btn)
        v.addLayout(row)
        return c

    # --- sources ------------------------------------------------------------------------
    def set_session(self, session, default_root: Path):
        self.session = session
        self.default_root = default_root
        if self.mode == "session":
            self.refresh()

    def _source_clicked(self, key: str):
        if key == "folder":
            start = str(self.folder or self.default_root or Path.home())
            d = QFileDialog.getExistingDirectory(self, "Choose a folder with recordings", start)
            if not d:
                self.src_buttons[self.mode].setChecked(True)
                return
            self.folder = Path(d)
            self.src_buttons["folder"].setText(f"{Path(d).name[:18].upper() or d}…")
        self.mode = key
        self.refresh()

    def location_path(self) -> Path | None:
        if self.mode == "session":
            return self.session.path if self.session else None
        if self.mode == "all":
            return self.default_root
        return self.folder

    def refresh(self):
        root = self.location_path()
        if root is None:
            return
        if self._scan_task is not None and self._scan_task.running:
            self._scan_task.cancel()
        self.location.setText(str(root))
        self.location.setToolTip(str(root))
        self.open_dir_btn.setText("Open Session Folder" if self.mode == "session" else f"Open in {FILE_MANAGER}")
        self.status.setText("Scanning…")

        def work(ctx):
            return scan_folder(root, ctx)

        def done(result):
            entries, skipped = result
            self._populate(entries, root)
            msg = f"{len(entries)} playable recording{'s' if len(entries) != 1 else ''}"
            if skipped:
                msg += f" • {skipped} file{'s' if skipped != 1 else ''} skipped (damaged or not audio)"
            if len(entries) >= MAX_FILES:
                msg += f" • showing the first {MAX_FILES}"
            self.status.setText(msg)

        self._scan_task = run_task(work, self, on_success=done,
                                   on_error=lambda t, m, c: self.status.setText("" if c else m),
                                   on_progress=lambda f, m: self.status.setText(m))

    def _populate(self, entries: list[dict], root: Path):
        self.entries = entries
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for e in entries:
            p, info = e["path"], e["info"]
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = _SortItem(p.name, p.name.lower())
            name.setData(Qt.UserRole, str(p))
            name.setToolTip(str(p))
            kind = "Export" if p.parent.name == "Exports" else ("Recording" if p.parent.name == "Recordings" else "")
            try:
                rel = str(p.parent.relative_to(root)) if p.parent != root else "."
            except ValueError:
                rel = str(p.parent)
            cells = [
                name,
                _SortItem(kind, kind),
                _SortItem(format_duration(info.duration), info.duration),
                _SortItem(f"{info.sample_rate / 1000:g} kHz • {info.channel_label} • {info.subtype_label}",
                          (info.sample_rate, info.channels)),
                _SortItem(format_size(info.file_size), info.file_size),
                _SortItem(datetime.fromtimestamp(e["mtime"]).strftime("%Y-%m-%d %H:%M"), e["mtime"]),
                _SortItem(rel, rel.lower()),
            ]
            for c, item in enumerate(cells):
                self.table.setItem(row, c, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(5, Qt.DescendingOrder)
        self._apply_filter(self.search.text())
        if self.current:  # keep the selection on rescans
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.UserRole) == str(self.current):
                    self.table.selectRow(r)
                    break

    def _apply_filter(self, text: str):
        t = text.strip().lower()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            hit = not t or t in item.text().lower() or t in (self.table.item(r, 6).text().lower())
            self.table.setRowHidden(r, not hit)

    def _open_location(self):
        root = self.location_path()
        if root and root.exists():
            open_folder(root)

    # --- playback -----------------------------------------------------------------------
    def _selected_paths(self) -> list[Path]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [Path(self.table.item(r, 0).data(Qt.UserRole)) for r in rows]

    def _selection_changed(self):
        paths = self._selected_paths()
        if len(paths) == 1 and paths[0] != self.current:
            self.load(paths[0], autoplay=False)
        self.batch_btn.setEnabled(bool(paths))
        self.compile_btn.setEnabled(bool(paths))

    def _add_selected_to_batch(self):
        paths = self._selected_paths() or ([self.current] if self.current else [])
        if paths:
            self.addToBatch.emit(paths)

    def load(self, path: Path, autoplay: bool = False):
        self.player.stop()
        self.current = path
        self.now_name.setText(path.name)
        self.now_name.setToolTip(str(path))
        self.wave.set_overview(None)
        self.wave.set_busy("Loading…")

        def work(ctx):
            audio = load_wav(path)
            return audio, WaveformOverview.compute(audio.samples, audio.sample_rate)

        def done(payload):
            audio, ov = payload
            if self.current != path:
                return
            self.wave.set_busy("")
            self.wave.set_overview(ov)
            self.player.set_source("original", audio.samples, audio.sample_rate)
            self.player.set_active("original")
            self.player.seek(0)
            info = audio.info
            self.now_meta.setText(f"{format_duration(info.duration)} • {info.sample_rate / 1000:g} kHz • "
                                  f"{info.channel_label} • {info.subtype_label}")
            self._duration = audio.duration
            for b in (self.show_btn, self.batch_btn, self.enhance_btn):
                b.setEnabled(True)
            if autoplay:
                self.play()

        def fail(title, message, _c):
            self.wave.set_busy("")
            self.wave.placeholder = message
            self.wave.update()

        self._duration = 0.0
        self._load_task = run_task(work, self, on_success=done, on_error=fail)

    def play_selected(self):
        paths = self._selected_paths()
        if paths:
            if paths[0] == self.current and self.player.has_source("original"):
                self.play()
            else:
                self.load(paths[0], autoplay=True)

    def play(self):
        if self.player.has_source("original"):
            self.playStarted.emit()
            self.player.play()

    def toggle_play(self):
        if self.player.state == "playing":
            self.player.pause()
        else:
            self.play()

    def pause(self):
        if self.player.state == "playing":
            self.player.pause()

    def _on_position(self, seconds: float):
        self.wave.set_playhead(seconds)
        self.time_label.setText(f"{format_duration(seconds)} / {format_duration(getattr(self, '_duration', 0.0))}")

    def _on_state(self, state: str):
        self.play_btn.setText("❚❚" if state == "playing" else "▶")

    def shutdown(self):
        self.player.shutdown()


