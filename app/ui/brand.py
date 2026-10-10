"""Steamburger Studios brand: logo rendering, About dialog, session dialogs."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from .. import sessions
from ..utils.config import APP_NAME, APP_VERSION
from . import theme

STUDIO_NAME = "Steamburger Studios"
STUDIO_URL = "https://www.steamburgerstudios.com/"
LOGO = theme.ASSETS / "SteamburgerLogoIcon.svg"
ELLIPSIS = "…"
_renderer: QSvgRenderer | None = None


def logo_pixmap(size: int, dpr: float | None = None) -> QPixmap:
    """The Steamburger burger mark (black with a white outline: reads on day and night),
    drawn from the vector logo. ``size`` is in logical pixels; by default it is rendered
    at the screen's scale (e.g. 150 %) so it stays sharp. Pass ``dpr=1`` for exact pixel
    sizes (icon files, installer art)."""
    global _renderer
    if _renderer is None:
        _renderer = QSvgRenderer(str(LOGO))
    if dpr is None:
        app = QGuiApplication.instance()
        dpr = max((s.devicePixelRatio() for s in app.screens()), default=1.0) if app else 1.0
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    _renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(px / size)
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(s, dpr=1))
    return icon


def _label(text: str, role: str | None = None, tone: str | None = None) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if tone:
        w.setProperty("tone", tone)
    return w


class AboutDialog(QDialog):
    def __init__(self, model_lines: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 20)
        v.setSpacing(6)
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(96))
        logo.setFixedSize(96, 96)
        top.addWidget(logo, 0, Qt.AlignTop)
        top.addSpacing(10)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(_label("STEAMBURGER STUDIOS", "section", "accent"))
        words.addWidget(_label("VOICECLEANER", "display"))
        from ..utils.config import edition

        ed = {"gpu": " · NVIDIA GPU EDITION", "standard": " · STANDARD EDITION"}.get(edition(), "")
        words.addWidget(_label(f"VERSION {APP_VERSION}{ed}", "brandsub"))
        top.addLayout(words, 1)
        v.addLayout(top)
        v.addSpacing(10)
        body = _label("Speech cleanup and enhancement for WAV recordings: noise and room reduction, neural "
                      "voice re-synthesis, EQ, leveling and loudness, plus recording, sessions and playback.\n"
                      "Works fully offline. Your audio never leaves this computer.", "muted")
        body.setWordWrap(True)
        v.addWidget(body)
        v.addSpacing(8)
        link = QLabel(f'Made by <b>{STUDIO_NAME}</b> — <a href="{STUDIO_URL}" '
                      f'style="color:{theme.ACCENT}; text-decoration:none;">www.steamburgerstudios.com</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        v.addWidget(link)
        v.addSpacing(8)
        credits = _label("\n".join(model_lines), "faint")
        credits.setWordWrap(True)
        v.addWidget(credits)
        buttons = QDialogButtonBox()
        visit = buttons.addButton("Visit Website", QDialogButtonBox.ActionRole)
        visit.setProperty("role", "outline")
        visit.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(STUDIO_URL)))
        ok = buttons.addButton(QDialogButtonBox.Ok)
        ok.setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        v.addSpacing(6)
        v.addWidget(buttons)


class SessionNameDialog(QDialog):
    """Name entry with a live character counter and file-name preview."""

    def __init__(self, title: str, initial: str = "", action: str = "Create", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(8)
        v.addWidget(_label(title.upper(), "section", "accent"))
        info = _label("A session keeps related recordings and exports together in their own folder. "
                      "Its name is added to every file it creates.", "muted")
        info.setWordWrap(True)
        v.addWidget(info)
        row = QHBoxLayout()
        self.edit = QLineEdit(initial)
        self.edit.setMaxLength(sessions.MAX_NAME_LENGTH)
        self.edit.setPlaceholderText("e.g. Episode 12 or Client Interview")
        self.edit.textChanged.connect(self._update)
        row.addWidget(self.edit, 1)
        self.counter = _label("", "faint")
        row.addWidget(self.counter)
        v.addLayout(row)
        self.preview = _label("", "faint")
        self.preview.setWordWrap(True)
        v.addWidget(self.preview)
        self.problem = _label("", tone="warning")
        v.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton(action, QDialogButtonBox.AcceptRole)
        self.ok.setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.edit.selectAll()
        self._update()

    def _update(self):
        text = self.edit.text()
        self.counter.setText(f"{len(text)}/{sessions.MAX_NAME_LENGTH}")
        clean = sessions.sanitize_name(text)
        problem = sessions.validate_name(text) if text else None
        self.problem.setText(problem or "")
        self.ok.setEnabled(bool(clean) and problem is None)
        shown = clean or ELLIPSIS
        self.preview.setText(f"Folder: {sessions.sessions_root() / shown}\n"
                             f"Files: {shown} - Rec 001.wav, {shown} - Rec 001 enhanced.wav")

    @property
    def name(self) -> str:
        return sessions.sanitize_name(self.edit.text())


class OpenSessionDialog(QDialog):
    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open Session")
        self.setMinimumSize(540, 420)
        self.chosen: str | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 14)
        v.addWidget(_label("OPEN SESSION", "section", "accent"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(0, 0))
        for s in sessions.list_sessions():
            n_files = len(s.audio_files())
            marker = "   (current)" if s.name == current else ""
            item = QListWidgetItem(f"{s.name}{marker}\n    {n_files} file{'s' if n_files != 1 else ''}  •  {s.path}")
            item.setData(Qt.UserRole, str(s.path))
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        v.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        browse = QPushButton(f"Browse{ELLIPSIS}")
        browse.setToolTip("Open any folder as a session")
        browse.clicked.connect(self._browse)
        buttons.addButton(browse, QDialogButtonBox.ActionRole)
        ok = buttons.addButton("Open", QDialogButtonBox.AcceptRole)
        ok.setProperty("role", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept(self):
        item = self.list.currentItem()
        if item:
            self.chosen = item.data(Qt.UserRole)
            self.accept()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a session folder", str(sessions.sessions_root()))
        if d:
            self.chosen = d
            self.accept()
