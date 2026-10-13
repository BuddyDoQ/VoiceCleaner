"""Getting Started: a short coach-mark tour over the real window.

The window is dimmed except for the control being explained; a callout next to it says
what it does, with Back / Next / Skip. The tour switches tabs by itself.

* A new install (no saved settings yet) gets the basics, one stop per tab.
* After an update, only what is new since the version last seen (``WHATS_NEW`` entries
  newer than it); an update without entries shows nothing.
* Both can be shown again from the menu (*Getting Started*, *What's New*).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme

# settings saved by versions before the tour existed count as this version
FIRST_TOUR_VERSION = "1.1.1"


@dataclass
class Step:
    title: str
    text: str
    tab: int | None = None  # switch to this tab first (0 Enhance, 1 Playback, 2 Compile, 3 Batch)
    target: Callable | None = None  # window -> the widget (or widgets) to point at; None: centred
    version: str = ""  # What's New entries: the version that introduced it


def _visible(w):
    return w if w is not None and w.isVisible() else None


BASICS = [
    Step("Welcome to VoiceCleaner",
         "A one-minute tour of the basics. You can see it again any time from the ⋯ menu "
         "(Getting Started)."),
    Step("Open a recording",
         "Drop a WAV file here or choose one. You can also record straight into VoiceCleaner, "
         "from a microphone or the computer's own sound.",
         tab=0, target=lambda w: _visible(w.drop_zone) or _visible(w.wave_orig)),
    Step("Pick a starting point",
         "Choose a preset. With Smart settings on, VoiceCleaner listens to each recording and sets "
         "Noise, Speech and Room for it (marked AUTO). Everything below is yours to adjust.",
         tab=0, target=lambda w: [w.settings_panel.preset, w.settings_panel.save_preset_btn,
                                  w.settings_panel.smart]),
    Step("Enhance and export",
         "Enhance processes the recording. Press Space to listen and A / B to switch between the "
         "original and the enhanced version, then export the result.",
         tab=0, target=lambda w: w.sidebar_actions),
    Step("Sessions",
         "A session is a project folder: its recordings and exports stay together. Start a new "
         "one for each episode or project.",
         target=lambda w: w.session_btn),
    Step("Teleprompter",
         "Read a script while VoiceCleaner records: one take per section, retakes with Backspace. "
         "The takes are enhanced and appear in Compile in script order.",
         target=lambda w: w.prompter_btn),
    Step("Playback",
         "Every recording and export of the session (or any folder): search, play, and send a file "
         "to the enhancer, a batch or a compilation.",
         tab=1, target=lambda w: w.playback.table),
    Step("Compile",
         "Join takes into one file. Drag to reorder; silence at the ends is trimmed. Right-click a "
         "take to edit out a pop or a pause, or to enhance it again.",
         tab=2, target=lambda w: w.compile_panel.list),
    Step("Batch",
         "Many files at once, with the same settings as the Enhance tab.",
         tab=3, target=lambda w: w.batch_panel.table),
    Step("You're ready",
         "Start by opening or recording something on the Enhance tab. Keyboard shortcuts are in the "
         "⋯ menu.",
         tab=0, target=lambda w: w.menu_btn),
]

WHATS_NEW = [
    Step("New: Getting Started",
         "A short tour of the basics, and after each update a quick look at what is new. Both are "
         "in the ⋯ menu any time.",
         target=lambda w: w.menu_btn, version="1.1.2"),
]


def _vkey(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def news_since(seen: str) -> list[Step]:
    return [s for s in WHATS_NEW if _vkey(s.version) > _vkey(seen)]


def latest_news() -> list[Step]:
    """What's New of the newest version that has entries."""
    if not WHATS_NEW:
        return []
    newest = max(_vkey(s.version) for s in WHATS_NEW)
    return [s for s in WHATS_NEW if _vkey(s.version) == newest]


def steps_on_start(fresh_install: bool, seen: str | None) -> list[Step]:
    """What to show when the app starts: the basics on a new install, else what is new
    since ``seen`` (settings from before the tour existed count as FIRST_TOUR_VERSION)."""
    if fresh_install:
        return list(BASICS)
    return news_since(seen or FIRST_TOUR_VERSION)


class TourOverlay(QWidget):
    """Dims the window around the current step's control and shows the callout."""

    finished = Signal()

    PAD = 6

    def __init__(self, window: QWidget, steps: list[Step], heading: str = ""):
        super().__init__(window)
        self.win, self.steps, self.heading = window, steps, heading
        self.i = 0
        self.hole: QRect | None = None
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)

        self.card = QFrame(self)
        self.card.setObjectName("tour_card")
        v = QVBoxLayout(self.card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        self.count = QLabel()
        self.count.setObjectName("tour_count")
        self.title = QLabel()
        self.title.setObjectName("tour_title")
        self.title.setWordWrap(True)
        self.text = QLabel()
        self.text.setObjectName("tour_text")
        self.text.setWordWrap(True)
        v.addWidget(self.count)
        v.addWidget(self.title)
        v.addWidget(self.text)
        row = QHBoxLayout()
        self.skip_btn = QPushButton("Skip tour")
        self.skip_btn.setProperty("role", "ghost")
        self.back_btn = QPushButton("Back")
        self.next_btn = QPushButton("Next")
        self.next_btn.setProperty("role", "primary")
        for b in (self.skip_btn, self.back_btn, self.next_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        self.skip_btn.clicked.connect(self.close_tour)
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.back_btn)
        row.addWidget(self.next_btn)
        v.addLayout(row)
        self.card.setFixedWidth(380)
        self._style()
        window.installEventFilter(self)

    def _style(self):
        self.card.setStyleSheet(
            f"QFrame#tour_card {{ background: {theme.SURFACE}; border: 1px solid {theme.ACCENT}; border-radius: 12px; }}"
            f"QLabel#tour_count {{ color: {theme.ACCENT}; font-family: '{theme.FONT_MONO}'; "
            f"font-size: {theme.pt(8.5)}pt; background: transparent; }}"
            f"QLabel#tour_title {{ color: {theme.TEXT}; font-family: '{theme.FONT_UI}'; font-weight: 700; "
            f"font-size: {theme.pt(12.5)}pt; background: transparent; }}"
            f"QLabel#tour_text {{ color: {theme.MUTED}; font-family: '{theme.FONT_UI}'; "
            f"font-size: {theme.pt(10)}pt; background: transparent; }}")

    # --- flow -------------------------------------------------------------------------------
    def start(self):
        self.setGeometry(self.win.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._show_step()

    def next(self):
        if self.i >= len(self.steps) - 1:
            self.close_tour()
            return
        self.i += 1
        self._show_step()

    def back(self):
        if self.i > 0:
            self.i -= 1
            self._show_step()

    def close_tour(self):
        self.win.removeEventFilter(self)
        self.hide()
        self.finished.emit()
        self.deleteLater()

    def _show_step(self):
        s = self.steps[self.i]
        if s.tab is not None and hasattr(self.win, "tabs"):
            self.win.tabs.setCurrentIndex(s.tab)
        n = len(self.steps)
        prefix = f"{self.heading}  ·  " if self.heading else ""
        self.count.setText(f"{prefix}{self.i + 1} / {n}" if n > 1 else self.heading)
        self.title.setText(s.title)
        self.text.setText(s.text)
        self.back_btn.setVisible(self.i > 0)
        self.skip_btn.setVisible(self.i < n - 1)
        self.next_btn.setText("Done" if self.i == n - 1 else "Next")
        self._style()  # the theme may have changed
        self._place()
        QTimer.singleShot(60, self._place)  # once a newly shown tab has its final layout

    def _target_rect(self) -> QRect | None:
        s = self.steps[self.i]
        try:
            found = s.target(self.win) if s.target else None
        except AttributeError:
            found = None
        widgets = found if isinstance(found, list) else [found]
        r = QRect()
        for w in widgets:
            if w is not None and w.isVisible():
                r = r.united(QRect(w.mapTo(self.win, QPoint(0, 0)), w.size()))
        if r.isNull():
            return None
        r = r.adjusted(-self.PAD, -self.PAD, self.PAD, self.PAD)
        return r.intersected(self.rect().adjusted(4, 4, -4, -4))

    def _place(self):
        if not self.isVisible():
            return
        self.setGeometry(self.win.rect())
        self.hole = self._target_rect()
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.sizeHint().height()
        W, H, gap = self.width(), self.height(), 14
        if self.hole is None:
            x, y = (W - cw) // 2, (H - ch) // 2
        else:
            h = self.hole
            spots = [(h.left(), h.bottom() + gap), (h.left(), h.top() - gap - ch),
                     (h.right() + gap, h.top()), (h.left() - gap - cw, h.top())]
            fits = [(x, y) for x, y in spots if 8 <= y <= H - ch - 8 and -cw < x < W]
            if not fits:  # a big target (a whole list): inside it, near the top
                fits = [(h.left() + 24, h.top() + 24)]
            x, y = fits[0]
        x = max(8, min(x, W - cw - 8))
        y = max(8, min(y, H - ch - 8))
        self.card.setGeometry(x, y, cw, ch)
        self.update()

    # --- painting and input -------------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shade = QPainterPath()
        shade.setFillRule(Qt.OddEvenFill)
        shade.addRect(QRectF(self.rect()))
        if self.hole is not None:
            shade.addRoundedRect(QRectF(self.hole), 10, 10)
        p.fillPath(shade, theme.qcolor("#000000", 150 if theme.MODE == "night" else 110))
        if self.hole is not None:
            p.setPen(QPen(theme.qcolor(theme.ACCENT), 2))
            p.drawRoundedRect(QRectF(self.hole), 10, 10)

    def mousePressEvent(self, e):
        e.accept()  # the window behind is not clickable during the tour

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter):
            self.next()
        elif k == Qt.Key_Left:
            self.back()
        elif k == Qt.Key_Escape:
            self.close_tour()
        e.accept()

    def eventFilter(self, obj, e):
        if obj is self.win and e.type() == QEvent.Resize:
            self._place()
        return False
