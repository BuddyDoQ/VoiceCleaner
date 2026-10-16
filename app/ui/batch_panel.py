"""Batch processing tab: queue of files, status per file, Enhance All.

*Preview* is a switch: while it is on, the selected file is rendered as a 10-second sample
(the stretch with the most speech) with the current settings, to hear what the batch will
produce before running it. Selecting another file previews that one; changing a setting
renders the sample again. Original and enhanced are compared at equal loudness."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..audio.loader import format_duration, probe
from ..audio.decode import FILE_FILTER
from ..utils.errors import VoiceCleanerError
from ..utils.shell import FILE_MANAGER, open_folder
from . import theme
from .audio_player import AudioPlayer
from .widgets import card, label

COL_NAME, COL_STATUS, COL_DURATION, COL_PROGRESS = range(4)


class BatchPanel(QWidget):
    startRequested = Signal(list, object)  # files, output dir (Path | None)
    cancelRequested = Signal()
    openRequested = Signal(object)  # Path of a finished file, to compare in the Enhance tab
    previewRequested = Signal(object)  # Path: render a sample of it with the current settings
    previewStopped = Signal()  # the Preview switch was turned off

    def __init__(self, parent=None, *, routing=None):
        super().__init__(parent)
        self._audio_routing = routing
        self.files: list[Path] = []
        self.outputs: dict[int, Path] = {}  # row -> enhanced file
        self.running = False
        self._done = self._failed = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        t = label("Batch processing", "title")
        head.addWidget(t)
        head.addStretch(1)
        self.add_btn = QPushButton("Add Files…")
        self.add_btn.clicked.connect(self._choose)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.preview_btn = QPushButton("\u25c9 PREVIEW")
        self.preview_btn.setProperty("role", "toggle")
        self.preview_btn.setCheckable(True)
        self.preview_btn.setCursor(Qt.PointingHandCursor)
        self.preview_btn.setToolTip("Preview on: hear a 10-second sample of the selected file with the current "
                                    "settings before enhancing everything. Click another file to hear it (P)")
        self.preview_btn.toggled.connect(self._preview_toggled)
        head.addWidget(self.preview_btn)
        for b in (self.add_btn, self.remove_btn, self.clear_btn):
            head.addWidget(b)
        lay.addLayout(head)
        lay.addWidget(label("Drop several recordings (or videos) anywhere in this window. Each file is analyzed and enhanced with "
                            "the settings on the right (the same as in the Enhance tab), then saved as a new file. "
                            "Double-click a finished file to compare it in the Enhance tab.", "muted", wrap=True))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Duration", "Progress"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_STATUS, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_DURATION, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_PROGRESS, 180)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        lay.addWidget(self.table, 1)
        lay.addWidget(self._build_preview())

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Save to"))
        self.dest = QComboBox()
        self.dest.addItem("This session's Exports folder", "session")
        self.dest.addItem("Same folder as each original", None)
        self.dest.addItem("Choose folder…", "choose")
        self.dest.activated.connect(self._dest_changed)
        self.dest.setMinimumWidth(280)
        bottom.addWidget(self.dest)
        bottom.addStretch(1)
        self.summary = label("", "muted")
        bottom.addWidget(self.summary)
        self.open_out_btn = QPushButton("Open Output Folder")
        self.open_out_btn.setToolTip(f"Show the enhanced files in {FILE_MANAGER}")
        self.open_out_btn.clicked.connect(self._open_output_folder)
        self.open_out_btn.setVisible(False)
        bottom.addWidget(self.open_out_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancelRequested)
        self.cancel_btn.setVisible(False)
        bottom.addWidget(self.cancel_btn)
        self.start_btn = QPushButton("Enhance All")
        self.start_btn.setProperty("role", "primary")
        self.start_btn.clicked.connect(self._start)
        bottom.addWidget(self.start_btn)
        lay.addLayout(bottom)
        self._update_buttons()

    # --- queue ---------------------------------------------------------------------
    def add_files(self, paths: list[Path]):
        if self.running:
            return
        for p in paths:
            p = Path(p)
            if p in self.files:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.files.append(p)
            self.table.setItem(row, COL_NAME, QTableWidgetItem(p.name))
            self.table.item(row, COL_NAME).setToolTip(str(p))
            try:
                info = probe(p)
                dur, status, ok = format_duration(info.duration), "Waiting", True
            except VoiceCleanerError as exc:
                dur, status, ok = "—", exc.user_message, False
            self.table.setItem(row, COL_DURATION, QTableWidgetItem(dur))
            self._set_status(row, status, None if ok else "error")
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(0)
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(8, 0, 8, 0)
            wl.addWidget(bar)
            self.table.setCellWidget(row, COL_PROGRESS, wrap)
        self._update_buttons()

    def _bar(self, row: int) -> QProgressBar:
        return self.table.cellWidget(row, COL_PROGRESS).findChild(QProgressBar)

    def _set_status(self, row: int, text: str, kind: str | None = None):
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        colors = {"done": theme.SUCCESS, "error": theme.ERROR, "active": theme.ACCENT}
        if kind in colors:
            item.setForeground(QColor(colors[kind]))
        self.table.setItem(row, COL_STATUS, item)

    def _forget_preview(self):
        if self.preview_path is not None and self.preview_path not in self.files:
            self.player.stop()
            self.preview_path = None
            if self.preview_on and self.files:
                self.request_preview()  # the selection moved on: that file, or "select a file"
            else:
                self.clear_preview()

    def clear(self):
        if self.running:
            return
        self.files.clear()
        self.outputs.clear()
        self.table.setRowCount(0)
        self._forget_preview()
        self._update_buttons()

    def _remove_selected(self):
        if self.running:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.files[r]
        self.outputs = {}  # row numbers changed; results stay on disk
        self._forget_preview()
        self._update_buttons()

    def _choose(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add recordings", "", FILE_FILTER)
        self.add_files([Path(f) for f in files])

    def _dest_changed(self, index: int):
        if self.dest.itemData(index) == "choose":
            d = QFileDialog.getExistingDirectory(self, "Choose output folder")
            if d:
                existing = self.dest.findData(d)
                if existing < 0:
                    self.dest.insertItem(2, d, d)
                    existing = 2
                self.dest.setCurrentIndex(existing)
            else:
                self.dest.setCurrentIndex(0)

    def set_output_dir(self, d: str):
        if d and Path(d).is_dir() and d != "session":
            self.dest.insertItem(2, d, d)
            self.dest.setCurrentIndex(2)

    @property
    def output_dir(self):
        """Path, None (next to each original) or "session"."""
        d = self.dest.currentData()
        if d == "session":
            return "session"
        return Path(d) if d and d != "choose" else None

    # --- run ----------------------------------------------------------------------------
    def _start(self):
        if not self.files:
            return
        for r in range(len(self.files)):
            self._set_status(r, "Waiting")
            self._bar(r).setValue(0)
        self.outputs.clear()
        self.startRequested.emit(list(self.files), self.output_dir)

    def set_running(self, running: bool):
        self.running = running
        if running and self.preview_on:
            self.preview_btn.setChecked(False)  # the batch gets the processor to itself
        self._done = self._failed = 0
        self._update_buttons()

    def file_started(self, i: int):
        self._set_status(i, "Processing…", "active")
        self.table.scrollToItem(self.table.item(i, COL_NAME))

    def file_progress(self, i: int, frac: float, msg: str):
        self._bar(i).setValue(int(frac * 1000))
        if msg:
            self._set_status(i, msg, "active")

    def file_finished(self, i: int, ok: bool, msg: str):
        self._set_status(i, ("✓ " if ok else "✕ ") + msg, "done" if ok else "error")
        if ok:
            self._bar(i).setValue(1000)
            self._done += 1
        else:
            self._failed += 1
        self.summary.setText(f"{self._done} done" + (f", {self._failed} failed" if self._failed else ""))

    def file_output(self, i: int, path: str):
        self.outputs[i] = Path(path)
        item = self.table.item(i, COL_NAME)
        if item is not None:
            item.setToolTip(f"{self.files[i]}\n\u2192 {path}\nDouble-click to compare it in the Enhance tab.")

    def _open_row(self, row: int, _col: int):
        out = self.outputs.get(row)
        if out is not None and out.exists():
            self.openRequested.emit(out)

    def _open_output_folder(self):
        outs = [p for p in self.outputs.values() if p.exists()]
        if outs:
            open_folder(outs[0].parent)

    # --- preview ---------------------------------------------------------------------------
    def _build_preview(self) -> QWidget:
        self.preview_path: Path | None = None
        self.player = AudioPlayer(self, routing=self._audio_routing)
        self.player.stateChanged.connect(lambda s: self.play_btn.setText("\u275a\u275a" if s == "playing" else "\u25b6"))
        c = card()
        self.preview_card = c
        v = QVBoxLayout(c)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        t = label("PREVIEW", "section")
        t.setProperty("tone", "accent")
        top.addWidget(t)
        self.preview_title = label("", "muted")
        top.addWidget(self.preview_title, 1)
        self.preview_state = label("", "faint")
        top.addWidget(self.preview_state)
        v.addLayout(top)
        row = QHBoxLayout()
        self.ab_orig = QPushButton("ORIGINAL")
        self.ab_enh = QPushButton("ENHANCED")
        group = QButtonGroup(self)
        for b, side in ((self.ab_orig, "left"), (self.ab_enh, "right")):
            b.setProperty("role", "ab")
            b.setProperty("side", side)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            group.addButton(b)
        self.ab_enh.setChecked(True)
        self.ab_orig.setToolTip("Listen to the original (A)")
        self.ab_enh.setToolTip("Listen to the enhanced sample (B); switch any time while it plays")
        self.ab_orig.clicked.connect(lambda: self.player.set_active("original"))
        self.ab_enh.clicked.connect(lambda: self.player.set_active("enhanced"))
        ab = QHBoxLayout()
        ab.setSpacing(0)
        ab.addWidget(self.ab_orig)
        ab.addWidget(self.ab_enh)
        row.addLayout(ab)
        row.addSpacing(8)
        self.play_btn = QPushButton("\u25b6")
        self.play_btn.setProperty("role", "round")
        self.play_btn.setToolTip("Play / pause the sample (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        stop = QPushButton("\u25a0")
        stop.setProperty("role", "icon")
        stop.clicked.connect(self.player.stop)
        row.addWidget(stop)
        row.addStretch(1)
        row.addWidget(label("Compared at equal loudness. Change a setting and the sample follows.", "faint"))
        v.addLayout(row)
        c.setVisible(False)
        return c

    def selected_path(self) -> Path | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.files[rows.pop()] if len(rows) == 1 else None

    @property
    def preview_on(self) -> bool:
        return self.preview_btn.isChecked()

    def _target(self) -> Path | None:
        return self.selected_path() or (self.files[0] if len(self.files) == 1 else None)

    def _preview_toggled(self, on: bool):
        if on:
            self.request_preview()
        else:
            self.clear_preview()
            self.previewStopped.emit()

    def _selection_changed(self):
        self._update_buttons()
        if self.preview_on and not self.running:
            self.request_preview()  # a newly selected file is previewed right away

    def toggle_preview(self):
        if self.preview_btn.isEnabled():
            self.preview_btn.toggle()

    def request_preview(self):
        """Preview is on: render the selected file (unless it is already the one shown)."""
        if not self.preview_on or self.running:
            return
        p = self._target()
        if p is None:
            self.player.stop()
            self.preview_path = None
            self.preview_card.setVisible(True)
            self.preview_title.setText("Select a file to hear a sample of it.")
            self.preview_state.setText("")
            return
        if p == self.preview_path and self.player.has_source("enhanced"):
            return
        self.previewRequested.emit(p)

    def preview_busy(self, path: Path):
        self.preview_path = Path(path)
        self.preview_card.setVisible(True)
        self.preview_title.setText(Path(path).name)
        self.preview_state.setText("Rendering a sample\u2026")

    def show_preview(self, path: Path, original, enhanced, sr: int, window: tuple[float, float],
                     match_gain: float, seconds: float):
        playing = self.player.state == "playing"
        pos = self.player.position
        self.player.set_source("original", original, sr)
        self.player.set_source("enhanced", enhanced, sr)
        self.player.set_match_gain("original", match_gain)
        self.player.set_active("enhanced" if self.ab_enh.isChecked() else "original")
        self.preview_path = Path(path)
        self.preview_card.setVisible(True)
        a, b = window
        self.preview_title.setText(f"{Path(path).name}  \u00b7  {format_duration(a)}\u2013{format_duration(b)}"
                                   "  (the part with the most speech)")
        self.preview_state.setText(f"Rendered in {seconds:.1f} s")
        self.player.seek(min(pos, max(0.0, (b - a) - 0.05)) if playing else 0.0)
        if playing:
            self.player.play()

    def preview_failed(self, message: str):
        self.preview_state.setText(message)

    def clear_preview(self):
        self.player.stop()
        self.player.set_source("original", None)
        self.player.set_source("enhanced", None)
        self.preview_path = None
        self.preview_card.setVisible(False)

    def toggle_play(self):
        if self.preview_path is not None and self.player.has_source("enhanced"):
            self.player.toggle_play()

    def switch(self, source: str):
        (self.ab_orig if source == "original" else self.ab_enh).click()

    def shutdown(self):
        self.player.shutdown()

    def _update_buttons(self):
        has = bool(self.files)
        self.preview_btn.setEnabled(not self.running and has)
        if not has and self.preview_on:
            self.preview_btn.setChecked(False)
        self.open_out_btn.setVisible(not self.running and bool(self.outputs))
        self.start_btn.setEnabled(has and not self.running)
        self.start_btn.setText("Processing…" if self.running else "Enhance All")
        self.cancel_btn.setVisible(self.running)
        for b in (self.add_btn, self.remove_btn, self.clear_btn):
            b.setEnabled(not self.running)
        if not self.running and not has:
            self.summary.setText("")
