"""Check for Updates: background check against GitHub, and the "update available" dialog
that downloads, verifies and (for installed copies on Windows) installs the update."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout)

from ..utils import update_install, updates
from ..utils.config import APP_VERSION
from ..utils.logging import get_logger
from ..utils.shell import reveal
from .brand import logo_pixmap

log = get_logger("updates")


class UpdateChecker(QObject):
    """Runs ``updates.fetch_latest`` on a background thread; results arrive on the UI thread."""

    found = Signal(object, bool)  # ReleaseInfo, manual
    upToDate = Signal(object, bool)  # ReleaseInfo, manual
    failed = Signal(str, bool)  # message, manual

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def check(self, manual: bool) -> bool:
        """Start a check; False if one is already running."""
        if self._busy:
            return False
        self._busy = True
        threading.Thread(target=self._run, args=(manual,), name="update-check", daemon=True).start()
        return True

    def _run(self, manual: bool):
        try:
            release = updates.fetch_latest()
        except updates.UpdateError as exc:
            log.info("Update check failed: %s", exc.__cause__ or exc)
            self._busy = False
            self.failed.emit(str(exc), manual)
            return
        except Exception as exc:  # never let a check take the app down
            log.exception("Update check failed")
            self._busy = False
            self.failed.emit(f"The update check failed ({exc.__class__.__name__}).", manual)
            return
        self._busy = False
        newer = updates.is_newer(release.version)
        log.info("Update check: latest %s, running %s%s", release.version, APP_VERSION, " (newer)" if newer else "")
        (self.found if newer else self.upToDate).emit(release, manual)


class _Downloader(QObject):
    """Downloads and verifies the update on a background thread."""

    progress = Signal(int, int)  # bytes done, total
    finished = Signal(object)  # Path of the verified file
    failed = Signal(str)

    def __init__(self, release, asset, parent=None):
        super().__init__(parent)
        self.release, self.asset = release, asset
        self._cancel = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="update-download", daemon=True).start()

    def cancel(self):
        self._cancel.set()

    def _run(self):
        try:
            path = update_install.download(self.release, self.asset, progress=self.progress.emit,
                                           cancelled=self._cancel.is_set)
        except updates.UpdateError as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never take the app down
            log.exception("Update download failed")
            self.failed.emit(f"The download failed ({exc.__class__.__name__}).")
            return
        self.finished.emit(path)


class UpdateDialog(QDialog):
    """Shows what is new, then downloads, verifies and installs the update (installed copies
    on Windows) or downloads and shows it (portable copies, macOS)."""

    SKIP, LATER, DOWNLOAD, INSTALL_NOW, INSTALL_ON_QUIT = 2, 0, 1, 3, 4

    def __init__(self, release: updates.ReleaseInfo, auto_check: bool, parent=None, plan=None):
        super().__init__(parent)
        self.release = release
        self.plan = plan or update_install.plan(release)
        self.installer: Path | None = None  # the verified download, once ready
        self._downloader = None
        self.setWindowTitle("Update available")
        self.setMinimumSize(640, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(8)

        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(56))
        logo.setFixedSize(56, 56)
        top.addWidget(logo, 0, Qt.AlignTop)
        top.addSpacing(8)
        words = QVBoxLayout()
        words.setSpacing(2)
        title = QLabel(f"VoiceCleaner {release.version} is available")
        title.setProperty("role", "section")
        words.addWidget(title)
        when = f"Released {release.published}. " if release.published else ""
        sub = QLabel(f"{when}You have version {APP_VERSION}.")
        sub.setProperty("role", "muted")
        words.addWidget(sub)
        top.addLayout(words, 1)
        v.addLayout(top)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(updates.whats_new(release.notes) or "See the release page for details.")
        v.addWidget(notes, 1)

        self.asset = self.plan.asset
        mb = f"{self.asset.size / 1e6:.0f} MB" if self.asset and self.asset.size else ""
        if self.plan.mode == "msi":
            hint = (f"Install Update downloads the installer ({mb}), checks it, then closes VoiceCleaner, "
                    "installs the update and opens it again. Windows asks for permission.")
        elif self.plan.mode == "bundle":
            hint = ("Install Update downloads the GPU edition's installer, checks it, then closes VoiceCleaner. "
                    "The installer downloads the update (about 2 GB) with its own progress window, and "
                    "VoiceCleaner opens again when it is done. Windows asks for permission.")
        elif self.plan.mode == "show":
            hint = f"Download gets {self.asset.name} ({mb}), checks it and shows it, ready to install."
        elif self.asset:
            hint = (f"Download: {self.asset.name} ({mb}). It opens in your browser; install it like the "
                    "version you have now.")
        else:
            hint = "Choose the download for your computer on the release page."
        self.hint = QLabel(hint)
        self.hint.setProperty("role", "faint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

        # progress of the download (hidden until it starts)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.state = QLabel("")
        self.state.setProperty("role", "muted")
        self.state.setWordWrap(True)
        self.state.setVisible(False)
        v.addWidget(self.state)

        self.auto_box = QCheckBox("Check for updates when VoiceCleaner starts")
        self.auto_box.setChecked(auto_check)
        v.addWidget(self.auto_box)

        row = QHBoxLayout()
        skip = QPushButton("Skip Version")
        skip.setProperty("role", "outline")
        skip.clicked.connect(lambda: self.done(self.SKIP))
        page = QPushButton("Release Page")
        page.setProperty("role", "outline")
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
        later = QPushButton("Later")
        later.setProperty("role", "outline")
        later.clicked.connect(lambda: self.done(self.LATER))
        self.later_btn = later
        download = QPushButton("Install Update" if self.plan.installs else "Download")
        download.setProperty("role", "primary")
        download.setDefault(True)
        download.clicked.connect(self._download)
        self.action_btn = download
        # after the download: install now or when VoiceCleaner quits
        self.on_quit_btn = QPushButton("Install When I Quit")
        self.on_quit_btn.setProperty("role", "outline")
        self.on_quit_btn.clicked.connect(lambda: self.done(self.INSTALL_ON_QUIT))
        self.now_btn = QPushButton("Install and Restart")
        self.now_btn.setProperty("role", "primary")
        self.now_btn.clicked.connect(lambda: self.done(self.INSTALL_NOW))
        self.cancel_btn = QPushButton("Cancel Download")
        self.cancel_btn.setProperty("role", "outline")
        self.cancel_btn.clicked.connect(self._cancel_download)
        row.addWidget(skip)
        row.addStretch(1)
        for b in (page, later, download, self.cancel_btn, self.on_quit_btn, self.now_btn):
            row.addWidget(b)
        for b in (skip, page, later, download, self.cancel_btn, self.on_quit_btn, self.now_btn):
            b.setMinimumWidth(b.sizeHint().width())  # never clip a label
        for b in (self.cancel_btn, self.on_quit_btn, self.now_btn):
            b.setVisible(False)
        self._buttons = (skip, page, later, download)
        v.addLayout(row)

    # --- download and install -----------------------------------------------------------
    def _download(self):
        if self.plan.mode == "browser" or self.asset is None:
            QDesktopServices.openUrl(QUrl(self.asset.url if self.asset else self.release.page_url))
            self.done(self.DOWNLOAD)
            return
        for b in self._buttons:
            b.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.bar.setVisible(True)
        self.state.setVisible(True)
        self.state.setText("Checking the download\u2026")
        self._t0 = time.monotonic()
        self._downloader = _Downloader(self.release, self.asset, self)
        self._downloader.progress.connect(self._progress)
        self._downloader.finished.connect(self._downloaded)
        self._downloader.failed.connect(self._failed)
        self._downloader.start()

    def _progress(self, done: int, total: int):
        frac = done / total if total else 0.0
        self.bar.setValue(int(frac * 1000))
        text = f"Downloading\u2026 {done / 1e6:.0f} of {total / 1e6:.0f} MB"
        elapsed = time.monotonic() - self._t0
        if 0.03 < frac < 1 and elapsed > 2:
            left = elapsed * (1 - frac) / frac
            text += f" \u2022 about {left / 60:.0f} min left" if left >= 90 else f" \u2022 about {left:.0f} s left"
        self.state.setText(text)

    def _downloaded(self, path: Path):
        self.installer = path
        self.bar.setValue(1000)
        self.cancel_btn.setVisible(False)
        size = path.stat().st_size / 1e6
        if self.plan.installs:
            self.state.setText(f"<b>Downloaded and verified</b> ({size:.0f} MB). VoiceCleaner will close, install "
                               f"version {self.release.version} and open again. Windows will ask for permission.")
            self.on_quit_btn.setVisible(True)
            self.now_btn.setVisible(True)
            self.now_btn.setDefault(True)
            self.now_btn.setFocus()
        else:  # portable copy or macOS: show the verified file
            self.state.setText(f"<b>Downloaded and verified:</b> {path.name}. Quit VoiceCleaner, then install it "
                               "(on a Mac: open it and drag VoiceCleaner to Applications, replacing the old one).")
            reveal(path)
            self.later_btn.setText("Close")
            self.later_btn.setVisible(True)

    def _failed(self, message: str):
        self.cancel_btn.setVisible(False)
        self.bar.setVisible(False)
        self.state.setText(f"<b>The update was not downloaded.</b> {message}")
        for b in self._buttons:
            b.setVisible(True)
        self.action_btn.setText("Try Again")

    def _cancel_download(self):
        if self._downloader is not None:
            self._downloader.cancel()
        self._failed("The download was cancelled; what was downloaded is kept and the next try continues it.")

    def reject(self):
        if self._downloader is not None:
            self._downloader.cancel()
        super().reject()
