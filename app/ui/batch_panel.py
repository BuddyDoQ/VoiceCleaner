"""Batch processing tab: queue of files, status per file, Enhance All."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..audio.loader import format_duration, probe
from ..utils.errors import VoiceCleanerError
from . import theme
from .widgets import label

COL_NAME, COL_STATUS, COL_DURATION, COL_PROGRESS = range(4)


class BatchPanel(QWidget):
    startRequested = Signal(list, object)  # files, output dir (Path | None)
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[Path] = []
        self.running = False
        self._done = self._failed = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        t = label("Batch processing", "title")
        head.addWidget(t)
        head.addStretch(1)
        self.add_btn = QPushButton("Add WAV Files…")
        self.add_btn.clicked.connect(self._choose)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        for b in (self.add_btn, self.remove_btn, self.clear_btn):
            head.addWidget(b)
        lay.addLayout(head)
        lay.addWidget(label("Drop several WAV files anywhere in this window. Each file is analyzed and enhanced with "
                            "the preset and settings from the Enhance tab, then saved as a new file.", "muted", wrap=True))

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
        lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Save to"))
        self.dest = QComboBox()
        self.dest.addItem("Same folder as each original", None)
        self.dest.addItem("Choose folder…", "choose")
        self.dest.activated.connect(self._dest_changed)
        self.dest.setMinimumWidth(280)
        bottom.addWidget(self.dest)
        bottom.addStretch(1)
        self.summary = label("", "muted")
        bottom.addWidget(self.summary)
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

    def clear(self):
        if self.running:
            return
        self.files.clear()
        self.table.setRowCount(0)
        self._update_buttons()

    def _remove_selected(self):
        if self.running:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.files[r]
        self._update_buttons()

    def _choose(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add WAV files", "", "WAV audio (*.wav *.wave)")
        self.add_files([Path(f) for f in files])

    def _dest_changed(self, index: int):
        if self.dest.itemData(index) == "choose":
            d = QFileDialog.getExistingDirectory(self, "Choose output folder")
            if d:
                existing = self.dest.findData(d)
                if existing < 0:
                    self.dest.insertItem(1, d, d)
                    existing = 1
                self.dest.setCurrentIndex(existing)
            else:
                self.dest.setCurrentIndex(0)

    def set_output_dir(self, d: str):
        if d and Path(d).is_dir():
            self.dest.insertItem(1, d, d)
            self.dest.setCurrentIndex(1)

    @property
    def output_dir(self) -> Path | None:
        d = self.dest.currentData()
        return Path(d) if d and d != "choose" else None

    # --- run ----------------------------------------------------------------------------
    def _start(self):
        if not self.files:
            return
        for r in range(len(self.files)):
            self._set_status(r, "Waiting")
            self._bar(r).setValue(0)
        self.startRequested.emit(list(self.files), self.output_dir)

    def set_running(self, running: bool):
        self.running = running
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

    def _update_buttons(self):
        has = bool(self.files)
        self.start_btn.setEnabled(has and not self.running)
        self.start_btn.setText("Processing…" if self.running else "Enhance All")
        self.cancel_btn.setVisible(self.running)
        for b in (self.add_btn, self.remove_btn, self.clear_btn):
            b.setEnabled(not self.running)
        if not self.running and not has:
            self.summary.setText("")
