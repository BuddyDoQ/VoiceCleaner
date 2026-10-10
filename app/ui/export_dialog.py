"""Export dialog: file name, folder, format, bit depth, sample rate."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..export.mp3_exporter import mp3_supported
from ..export.wav_exporter import BIT_DEPTH_LABELS, same_file
from ..utils.shell import FILE_MANAGER


class ExportDialog(QDialog):
    def __init__(self, source: Path, sample_rate: int, directory: str, fmt: str, bit_depth: str, out_rate: int,
                 parent=None, suggested_name: str | None = None, reveal_after: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Export Enhanced Audio")
        self.setMinimumWidth(520)
        self.source = source
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        title = QLabel("Export enhanced audio")
        title.setProperty("role", "h2")
        lay.addWidget(title)
        note = QLabel("The original recording is never modified.")
        note.setProperty("role", "faint")
        lay.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(suggested_name or f"{source.stem}_enhanced")
        form.addRow("File name", self.name)

        folder_row = QHBoxLayout()
        self.folder = QLineEdit(directory or str(source.parent))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        fw = QWidget()
        fw.setLayout(folder_row)
        folder_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Folder", fw)

        self.format = QComboBox()
        self.format.addItem("WAV (recommended)", "wav")
        if mp3_supported():
            self.format.addItem("MP3", "mp3")
        self.format.setCurrentIndex(max(0, self.format.findData(fmt)))
        self.format.currentIndexChanged.connect(self._format_changed)
        form.addRow("Format", self.format)

        self.depth = QComboBox()
        for key, text in BIT_DEPTH_LABELS.items():
            self.depth.addItem(text, key)
        self.depth.setCurrentIndex(max(0, self.depth.findData(bit_depth)))
        form.addRow("Bit depth", self.depth)

        self.quality = QComboBox()
        self.quality.addItem("High quality (VBR ~190 kbps)", "high")
        self.quality.addItem("Standard (VBR ~130 kbps)", "standard")
        self.quality.addItem("Small file (VBR ~100 kbps)", "small")
        form.addRow("MP3 quality", self.quality)

        self.rate = QComboBox()
        self.rate.addItem(f"Same as original ({sample_rate / 1000:g} kHz)", 0)
        for r in (44100, 48000, 96000):
            if r != sample_rate:
                self.rate.addItem(f"{r / 1000:g} kHz", r)
        self.rate.setCurrentIndex(max(0, self.rate.findData(out_rate)))
        form.addRow("Sample rate", self.rate)
        lay.addLayout(form)

        self.reveal_box = QCheckBox(f"Show the file in {FILE_MANAGER} when done")
        self.reveal_box.setChecked(reveal_after)
        lay.addWidget(self.reveal_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton("Export", QDialogButtonBox.AcceptRole)
        self.ok.setProperty("role", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._format_changed()

    def _format_changed(self):
        is_wav = self.format.currentData() == "wav"
        self.depth.setEnabled(is_wav)
        self.quality.setEnabled(not is_wav)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose export folder", self.folder.text())
        if d:
            self.folder.setText(d)

    @property
    def output_path(self) -> Path:
        ext = ".wav" if self.format.currentData() == "wav" else ".mp3"
        name = self.name.text().strip() or f"{self.source.stem}_enhanced"
        if name.lower().endswith((".wav", ".mp3")):
            name = name[:-4]
        return Path(self.folder.text().strip()) / f"{name}{ext}"

    def _accept(self):
        path = self.output_path
        if not path.parent.is_dir():
            QMessageBox.warning(self, "Folder not found", f"The folder “{path.parent}” does not exist.")
            return
        if same_file(path, self.source) or path.resolve() == self.source.resolve():
            QMessageBox.warning(self, "Choose another name",
                                "That is the original recording. VoiceCleaner never overwrites the original.")
            return
        if path.exists():
            r = QMessageBox.question(self, "Replace file?", f"“{path.name}” already exists. Replace it?")
            if r != QMessageBox.Yes:
                return
        self.accept()

    def values(self) -> dict:
        return {
            "path": self.output_path, "format": self.format.currentData(), "bit_depth": self.depth.currentData(),
            "sample_rate": self.rate.currentData(), "mp3_quality": self.quality.currentData(),
            "reveal": self.reveal_box.isChecked(),
        }
