"""Check for Updates: background check against GitHub and the "update available" dialog."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from ..utils import updates
from ..utils.config import APP_VERSION
from ..utils.logging import get_logger
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


class UpdateDialog(QDialog):
    """Shows what is new and offers the download that fits this computer."""

    SKIP, LATER, DOWNLOAD = 2, 0, 1

    def __init__(self, release: updates.ReleaseInfo, auto_check: bool, parent=None):
        super().__init__(parent)
        self.release = release
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

        self.asset = updates.pick_asset(release)
        hint = (f"Download: {self.asset.name} ({self.asset.size / 1e6:.0f} MB). It opens in your browser; "
                "install it like the version you have now." if self.asset else
                "Choose the download for your computer on the release page.")
        h = QLabel(hint)
        h.setProperty("role", "faint")
        h.setWordWrap(True)
        v.addWidget(h)

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
        download = QPushButton("Download")
        download.setProperty("role", "primary")
        download.setDefault(True)
        download.clicked.connect(self._download)
        row.addWidget(skip)
        row.addStretch(1)
        for b in (page, later, download):
            row.addWidget(b)
        for b in (skip, page, later, download):
            b.setMinimumWidth(b.sizeHint().width())  # never clip a label
        v.addLayout(row)

    def _download(self):
        QDesktopServices.openUrl(QUrl(self.asset.url if self.asset else self.release.page_url))
        self.done(self.DOWNLOAD)
