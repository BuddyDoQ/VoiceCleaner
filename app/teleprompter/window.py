"""The pop-out teleprompter window.

A native port of the HTML prompter (scrolling at a words-per-minute pace, text size,
section list, stage directions, mirror, 3-2-1 countdown, fullscreen, script editor, help,
controls that hide while reading), plus recording: starting the prompter records, every
section (or paragraph, in line-by-line mode) becomes its own take, Retake records the
current one again, and the takes go to the session and, in script order, to Compile.

Keys: Space play/pause · Backspace or PageUp retake · Right or PageDown next ·
Left previous · Up/Down speed · +/- size · G keep this take · P play the last take ·
M mirror · D directions · F fullscreen · E edit · Home back to the top · H help · Esc stop.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
                           QPolygonF, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ..audio.recorder import InputSource, RecordingError, list_sources
from ..utils.logging import get_logger
from ..utils.shell import BACKSPACE_NAME
from .run import PrompterRun
from .script import SAMPLE, Script, Unit, parse
from .takes import TakeBook, TakeInfo, TakeRecorder

log = get_logger("teleprompter")

# Colours come from the app's theme (day or night); by default the prompter follows the app.
C: dict = {}


def palette(mode: str) -> dict:
    """The prompter's colours for "night" or "day", from the app theme's tokens."""
    from ..ui import theme

    t = theme.NIGHT if mode == "night" else theme.DAY
    night = mode == "night"
    return {
        "mode": mode, "bg": t["BG"], "text": t["TEXT"], "muted": t["MUTED"], "faint": t["FAINT"],
        "cue": t["WARNING"], "accent": t["ACCENT"], "accent_text": t["ACCENT_TEXT"], "rec": t["RECORD"],
        "panel": QColor(t["SURFACE"]).name(), "panel_alpha": 235, "button": t["SURFACE_2"], "hover": t["SURFACE_3"],
        "border": t["BORDER_STRONG"], "inset": t["SURFACE_2"], "track": t["SURFACE_3"],
        "shade": "rgba(0,0,0,190)" if night else "rgba(20,20,30,110)",
        "count_bg": "rgba(0,0,0,140)" if night else "rgba(255,255,255,170)",
    }


def _rgba(hex_color: str, alpha: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


READ_FRAC = 0.40


def build_style() -> str:
    from ..ui import theme  # point sizes scaled like the app's (macOS draws them a quarter smaller)

    panel = _rgba(C["panel"], C["panel_alpha"])
    mono = f"'{theme.FONT_MONO}', Consolas, monospace"
    return theme._scale_pt(f"""
QWidget#prompter {{ background: {C['bg']}; }}
QFrame#bar, QFrame#help, QFrame#editor_box {{ background: {panel}; border: 1px solid {C['border']}; border-radius: 12px; }}
QFrame#bar QLabel, QFrame#help QLabel {{ color: {C['muted']}; font-size: 10pt; background: transparent; }}
QFrame#bar QPushButton, QFrame#editor_box QPushButton, QFrame#bar QComboBox {{
    background: {C['button']}; color: {C['text']}; border: 1px solid {C['border']}; border-radius: 7px;
    padding: 5px 10px; font-size: 10pt; }}
QFrame#bar QPushButton:hover, QFrame#editor_box QPushButton:hover {{ background: {C['hover']}; }}
QFrame#bar QPushButton:checked {{ background: {C['accent']}; border-color: {C['accent']}; color: {C['accent_text']}; }}
QFrame#bar QPushButton:disabled {{ color: {C['faint']}; }}
QPushButton#play {{ font-weight: 700; min-width: 86px; }}
QFrame#bar QPushButton#retake {{ color: {C['accent']}; font-weight: 700; }}
QFrame#editor_box QPushButton#load {{ background: {C['accent']}; border-color: {C['accent']}; color: {C['accent_text']}; font-weight: 700; }}
QLabel#rec {{ color: {C['rec']}; font-weight: 700; font-size: 10.5pt; }}
QLabel#status {{ color: {C['text']}; background: {panel}; border: 1px solid {C['border']}; border-radius: 10px;
    padding: 8px 16px; font-size: 12pt; }}
QLabel#countdown {{ color: {C['accent']}; background: {C['count_bg']}; font-weight: 800; }}
QPlainTextEdit {{ background: {C['inset']}; color: {C['text']}; border: 1px solid {C['border']}; border-radius: 8px;
    font-family: {mono}; font-size: 11pt; padding: 8px; }}
QFrame#editor_shade {{ background: {C['shade']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {C['track']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {C['text']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider:horizontal {{ min-height: 18px; }}
""")


class _MicCombo(QComboBox):
    """Lists the microphones again each time it opens, so one plugged in later appears."""

    def __init__(self, refresh):
        super().__init__()
        self._refresh = refresh

    def showPopup(self):
        self._refresh()
        super().showPopup()


SILENT_AFTER_S = 2.0  # this long with nothing but digital silence: warn


def _reading_font() -> str:
    """A plain, very readable sans serif (the brand display fonts are for headings)."""
    families = set(QFontDatabase.families())
    for name in ("Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue", "Arial"):
        if name in families:
            return name
    return QFont().defaultFamily()


class Stage(QWidget):
    """The script, scrolled past the reading line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = QTextDocument()
        self.doc.setDocumentMargin(0)
        self.pos = 0.0  # document y at the reading line
        self.size_px = 54
        self.mirror = False
        self.show_cues = True
        self.recording = False
        self.script: Script | None = None
        self.units: list[Unit] = []
        self.unit_y: list[float] = []
        self.family = _reading_font()
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    # --- content ------------------------------------------------------------------
    def set_script(self, script: Script, units: list[Unit]):
        self.script, self.units = script, units
        self.rebuild()

    def text_width(self) -> float:
        return max(200.0, min(1100.0, self.width()) - 2 * max(24.0, self.width() * 0.07))

    def rebuild(self):
        """Lay the script out again (new script, size, width or directions)."""
        frac = self.pos / self.max_pos if self.max_pos else 0.0
        doc = self.doc
        doc.clear()
        s = float(self.size_px)
        base = QFont(self.family)
        base.setPixelSize(int(s))
        base.setWeight(QFont.DemiBold)
        doc.setDefaultFont(base)
        cur = QTextCursor(doc)
        first = [True]

        def block(top: float, bottom: float, runs, line=145):
            bf = QTextBlockFormat()
            bf.setTopMargin(top)
            bf.setBottomMargin(bottom)
            bf.setLineHeight(line, 1)  # proportional
            if first[0]:
                cur.setBlockFormat(bf)
                first[0] = False
            else:
                cur.insertBlock(bf)
            for text, cf in runs:
                cur.insertText(text, cf)
            return cur.block().blockNumber()

        def fmt(color, scale=1.0, italic=False, weight=QFont.DemiBold, spacing=0.0):
            cf = QTextCharFormat()
            f = QFont(self.family)
            f.setPixelSize(max(8, int(s * scale)))
            f.setItalic(italic)
            f.setWeight(weight)
            if spacing:
                f.setLetterSpacing(QFont.PercentageSpacing, 100 + spacing)
            cf.setFont(f)
            cf.setForeground(QColor(color))
            return cf

        text_f, cue_f = fmt(C["text"]), fmt(C["cue"], 0.5, italic=True, weight=QFont.Medium)
        head_f = fmt(C["muted"], 0.42, weight=QFont.Bold, spacing=14)
        clip_f = fmt(C["accent"], 0.36, weight=QFont.Bold, spacing=6)
        para_units = {(u.section, u.paragraph) for u in self.units}
        starts: dict[tuple, int] = {}
        if self.script is not None:
            for sec in self.script.sections:
                runs = [(sec.title.upper(), head_f)]
                if sec.clip:
                    runs.append(("   ✂ " + sec.clip, clip_f))
                n = block(0 if sec.index == 0 else s * 0.7, s * 0.4, runs, 120)
                starts[(sec.index, None)] = n
                p = 0
                for b in sec.blocks:
                    if b.kind == "cue":
                        if self.show_cues:
                            block(0, s * 0.5, [(b.text, cue_f)], 135)
                        continue
                    runs = []
                    pos = 0
                    for m in re.finditer(r"\[[^\]]+\]", b.text):
                        if m.start() > pos:
                            runs.append((b.text[pos:m.start()], text_f))
                        if self.show_cues:
                            runs.append((m.group(0), cue_f))
                        pos = m.end()
                    runs.append((b.text[pos:], text_f))
                    n = block(0, s * 0.9, runs)
                    if (sec.index, p) in para_units:
                        starts[(sec.index, p)] = n
                    p += 1
        block(s * 0.8, 0, [("— END —", fmt(C["faint"], 0.5, weight=QFont.Medium, spacing=30))])
        bf = cur.blockFormat()
        bf.setAlignment(Qt.AlignHCenter)
        cur.setBlockFormat(bf)
        doc.setTextWidth(self.text_width())
        lay = doc.documentLayout()
        self.unit_y = []
        for u in self.units:
            b = doc.findBlockByNumber(starts.get((u.section, u.paragraph), 0))
            self.unit_y.append(lay.blockBoundingRect(b).top())
        self.pos = frac * self.max_pos
        self.update()

    # --- geometry ------------------------------------------------------------------------
    @property
    def max_pos(self) -> float:
        return max(1.0, self.doc.size().height() - self.size_px)

    @property
    def px_per_word(self) -> float:
        words = max(1, self.script.words if self.script else 1)
        return self.max_pos / words

    def unit_index(self) -> int:
        idx = 0
        for i, y in enumerate(self.unit_y):
            if y <= self.pos + 4:
                idx = i
        return idx

    def unit_start(self, i: int) -> float:
        return self.unit_y[i] if 0 <= i < len(self.unit_y) else 0.0

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.rebuild()

    # --- painting ----------------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(C["bg"]))
        tw = self.text_width()
        read_y = h * READ_FRAC
        p.save()
        if self.mirror:
            p.translate(w, 0)
            p.scale(-1, 1)
        p.translate((w - tw) / 2, read_y - self.pos)
        self.doc.drawContents(p, QRectF(0, self.pos - read_y, tw, h))
        p.restore()
        # fade towards the edges, so the eye stays on the reading line
        bg = QColor(C["bg"])
        clear = QColor(C["bg"])
        clear.setAlpha(0)
        top = QLinearGradient(0, 0, 0, read_y - h * 0.18)
        top.setColorAt(0, bg)
        top.setColorAt(1, clear)
        p.fillRect(QRectF(0, 0, w, read_y - h * 0.18), top)
        y0 = read_y + h * 0.22
        bottom = QLinearGradient(0, y0, 0, h * 0.97)
        bottom.setColorAt(0, clear)
        bottom.setColorAt(1, bg)
        p.fillRect(QRectF(0, y0, w, h - y0), bottom)
        # reading-line markers (red while recording)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["rec"] if self.recording else C["accent"]))
        for x, d in ((14, 1), (w - 14, -1)):
            p.drawPolygon(QPolygonF([QPointF(x, read_y - 14), QPointF(x + 20 * d, read_y), QPointF(x, read_y + 14)]))
        # progress along the top
        p.setBrush(QColor(C["accent"]))
        p.drawRect(QRectF(0, 0, w * min(1.0, self.pos / self.max_pos), 3))


class TeleprompterWindow(QWidget):
    takeSaved = Signal(object, object)  # TakeInfo, Path of the take file
    recordingChanged = Signal(bool)
    scriptChanged = Signal()
    closed = Signal()

    def __init__(self, session, prefs: dict, mic: InputSource | None = None, gain: float = 1.0, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("prompter")
        self.setWindowTitle("VoiceCleaner Teleprompter")
        self.setMinimumSize(640, 420)
        self.prefs = prefs
        C.update(palette(self._theme_mode()))
        self.setStyleSheet(build_style())
        self.session = session
        self.book = TakeBook.shared(session.path)
        self.mic, self.gain = mic, gain
        self._closing = False
        self._silence_warned = False
        self.recorder: TakeRecorder | None = None
        self.run: PrompterRun | None = None
        self.playing = False
        self.counting = False
        self.wpm = int(prefs.get("wpm", 150))
        self.advance = prefs.get("advance", "scroll")  # "scroll" | "step"
        self.elapsed = 0.0
        self._last_tick: float | None = None
        self._target: float | None = None  # animated jump target (step mode, retake, jumps)
        self._hold_until = 0.0  # short pause after a retake
        self._last_take: tuple[TakeInfo, Path] | None = None
        self._unit = 0
        self._player = None

        self.stage = Stage(self)
        self.stage.size_px = int(prefs.get("size", 54))
        self.stage.mirror = bool(prefs.get("mirror", False))
        self.stage.show_cues = bool(prefs.get("cues", True))
        self._build_overlays()
        text = self.book.script or prefs.get("last_script") or SAMPLE
        self.load_script(text, self.book.mode if self.book.script else prefs.get("units", "section"), save=False)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(2500)
        self._idle.timeout.connect(self._hide_controls)
        self.setMouseTracking(True)
        self.stage.setMouseTracking(True)
        geo = prefs.get("geometry")
        if geo:
            from PySide6.QtCore import QByteArray

            self.restoreGeometry(QByteArray.fromHex(str(geo).encode()))
        else:
            self.resize(1100, 720)

    # ================================================================================ UI
    def _build_overlays(self):
        # control bar
        self.bar = QFrame(self)
        self.bar.setObjectName("bar")
        v = QVBoxLayout(self.bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        row1.setSpacing(8)
        row2.setSpacing(10)
        v.addLayout(row1)
        v.addLayout(row2)

        self.rec_label = QLabel("")
        self.rec_label.setObjectName("rec")
        self.rec_label.setMinimumWidth(170)
        from ..ui.record_panel import LevelMeter

        self.meter = LevelMeter()
        self.meter.setFixedWidth(110)
        self.play_btn = self._btn("▶ Start", self.toggle_play, "Start recording and scrolling (Space)", "play")
        self.stop_btn = self._btn("■ Stop", self.stop, "Stop and save the take (Esc)")
        self.retake_btn = self._btn("⟲ Retake", self.retake, f"Record this section again ({BACKSPACE_NAME} or PageUp)",
                                    "retake")
        self.keep_btn = self._btn("★ Keep", self.keep, "Keep this take: later retakes don't replace it (G)")
        self.last_btn = self._btn("▶ Last take", self.play_last, "Listen to the last take (P)")
        for wdg in (self.rec_label, self.meter, self.play_btn, self.stop_btn, self.retake_btn, self.keep_btn,
                    self.last_btn):
            row1.addWidget(wdg)
        row1.addStretch(1)
        self.time_label = QLabel("")

        self.speed = self._slider(80, 260, self.wpm, lambda v: self.set_speed(v))
        self.speed_label = QLabel()
        self.size = self._slider(28, 110, self.stage.size_px, lambda v: self.set_size(v))
        self.size_label = QLabel()
        self.section = QComboBox()
        self.section.setFixedWidth(170)
        self.section.activated.connect(lambda i: self.jump_to(i))
        self.mode_btn = self._btn("Step", self._toggle_advance,
                                  "Step: the prompter waits on each line until you press → or PageDown")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setChecked(self.advance == "step")
        self.units_btn = self._btn("Lines", self._toggle_units, "One take per paragraph instead of per section")
        self.units_btn.setCheckable(True)
        self.cues_btn = self._btn("Directions", self.toggle_cues, "Show stage directions (D)")
        self.cues_btn.setCheckable(True)
        self.cues_btn.setChecked(self.stage.show_cues)
        self.mirror_btn = self._btn("Mirror", self.toggle_mirror, "Mirror for teleprompter glass (M)")
        self.mirror_btn.setCheckable(True)
        self.mirror_btn.setChecked(self.stage.mirror)
        self.cd_btn = self._btn("Countdown", lambda: None, "3-2-1 countdown before recording starts")
        self.cd_btn.setCheckable(True)
        self.cd_btn.setChecked(bool(self.prefs.get("countdown", True)))
        self.mic_combo = _MicCombo(self._fill_mics)
        self.mic_combo.setMinimumWidth(120)  # shrinks when the window (or larger text, macOS) needs room
        self.mic_combo.setMaximumWidth(220)
        self.mic_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.mic_combo.setToolTip("Microphone the takes are recorded from")
        self._fill_mics()
        self.speed.setToolTip("Scrolling speed (↑ ↓)")
        self.size.setToolTip("Text size (+ −)")
        for wdg in (self.speed, self.speed_label, self.size, self.size_label,
                    self.section, self.mode_btn, self.units_btn, self.cues_btn, self.mirror_btn, self.cd_btn):
            row2.addWidget(wdg)
        row1.addWidget(self.time_label)
        row1.addSpacing(8)
        row1.addWidget(self.mic_combo)
        row2.addStretch(1)
        self.theme_btn = self._btn(self.THEME_LABELS[self.prefs.get("theme", "auto")], self._cycle_theme,
                                   "Colours: Auto follows the app's day/night setting; or always Dark or Light")
        row2.addWidget(self.theme_btn)
        for b, fn, tip in (("⛶", self.toggle_fullscreen, "Fullscreen (F)"), ("Edit", self.open_editor,
                           "Edit the script (E)"), ("?", self.toggle_help, "Keys (H)")):
            row2.addWidget(self._btn(b, fn, tip))
        self.set_speed(self.wpm)
        self.set_size(self.stage.size_px, rebuild=False)

        # help
        self.help = QFrame(self)
        self.help.setObjectName("help")
        hl = QVBoxLayout(self.help)
        hl.setContentsMargins(14, 10, 14, 10)
        self.help_text = QLabel()
        hl.addWidget(self.help_text)
        self.help.setVisible(not self.prefs.get("help_hidden", False))

        self.status = QLabel("", self)
        self.status.setObjectName("status")
        self.status.setVisible(False)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status.setVisible(False))

        self.countdown = QLabel("", self)
        self.countdown.setObjectName("countdown")
        self.countdown.setAlignment(Qt.AlignCenter)
        self.countdown.setVisible(False)

        # script editor
        self.editor = QFrame(self)
        self.editor.setObjectName("editor_shade")
        ev = QVBoxLayout(self.editor)
        ev.setContentsMargins(40, 30, 40, 30)
        box = QFrame()
        box.setObjectName("editor_box")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(16, 14, 16, 14)
        self.editor_hint = QLabel()
        self.editor_hint.setWordWrap(True)
        bv.addWidget(self.editor_hint)
        self.editor_text = QPlainTextEdit()
        bv.addWidget(self.editor_text, 1)
        er = QHBoxLayout()
        er.addWidget(self._btn("Open File…", self._open_file, "Load a .txt or .md script"))
        er.addStretch(1)
        er.addWidget(self._btn("Cancel", self.close_editor, "Esc"))
        load = self._btn("Load Script", self._apply_editor, "", "load")
        er.addWidget(load)
        bv.addLayout(er)
        ev.addWidget(box)
        self.editor.setVisible(False)
        self._fill_rich_text()
        self._update_controls()

    def _fill_rich_text(self):
        """The help card and editor hint, in the current colours."""
        def k(key):
            return f"<b style='color:{C['text']}'>{key}</b>"

        self.help_text.setText(
            f"{k('Space')} start / pause &nbsp; {k('Esc')} stop<br>"
            f"{k(BACKSPACE_NAME)} or {k('PgUp')} retake<br>"
            f"{k(chr(0x2192))} or {k('PgDn')} next &nbsp; {k(chr(0x2190))} previous<br>"
            f"{k(chr(0x2191) + ' ' + chr(0x2193))} speed &nbsp; {k('+ ' + chr(0x2212))} size<br>"
            f"{k('G')} keep take &nbsp; {k('P')} play last take<br>"
            f"{k('M')} mirror &nbsp; {k('D')} directions &nbsp; {k('F')} fullscreen<br>"
            f"{k('E')} edit &nbsp; {k('Home')} top &nbsp; {k('H')} hide this<br>Mouse wheel nudges")
        self.editor_hint.setText(
            f"<span style='color:{C['muted']}'>Paste or edit your script. "
            f"<span style='color:{C['cue']}'>## Heading | Clip name</span> starts a section (the clip name "
            f"names its takes) {chr(0xB7)} <span style='color:{C['cue']}'>[brackets]</span> are stage directions "
            f"{chr(0xB7)} a blank line starts a new paragraph.</span>")

    # --- colours: follow the app by default -----------------------------------------------
    THEMES = ("auto", "night", "day")
    THEME_LABELS = {"auto": chr(0x25D0) + " Auto", "night": chr(0x263E) + " Dark", "day": chr(0x2600) + " Light"}

    def _theme_mode(self) -> str:
        from ..ui import theme

        pref = self.prefs.get("theme", "auto")
        return pref if pref in ("night", "day") else theme.MODE

    def apply_theme(self):
        """Re-colour everything (the app's theme or the prompter's own choice changed)."""
        C.update(palette(self._theme_mode()))
        self.setStyleSheet(build_style())
        self._fill_rich_text()
        self.theme_btn.setText(self.THEME_LABELS.get(self.prefs.get("theme", "auto"), self.THEME_LABELS["auto"]))
        self.stage.rebuild()
        self.update()

    def _cycle_theme(self):
        cur = self.prefs.get("theme", "auto")
        self.prefs["theme"] = self.THEMES[(self.THEMES.index(cur) + 1) % 3] if cur in self.THEMES else "auto"
        self.apply_theme()

    def _btn(self, text, fn, tip="", name="") -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)  # keys always reach the prompter
        if name:
            b.setObjectName(name)
        b.clicked.connect(lambda _=False: fn())
        return b

    def _slider(self, lo, hi, value, fn) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        s.setFixedWidth(92)
        s.setFocusPolicy(Qt.NoFocus)
        s.valueChanged.connect(fn)
        return s

    def _fill_mics(self):
        keep = self.mic_combo.currentData()
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        try:
            mics, _desk = list_sources()
        except RecordingError as exc:
            self.mic_combo.addItem("No microphone", None)
            self.mic_combo.setToolTip(exc.user_message)
            return
        for m in mics:
            self.mic_combo.addItem(m.name + ("  (default)" if m.is_default else ""), m)
        want = keep.id if keep is not None else (self.mic.id if self.mic else self.prefs.get("mic"))
        for i in range(self.mic_combo.count()):
            src = self.mic_combo.itemData(i)
            if src is not None and src.id == want:
                self.mic_combo.setCurrentIndex(i)
        self.mic_combo.blockSignals(False)

    def _fit_min_width(self):
        """Never narrower than the control bar needs: otherwise its right end (the microphone)
        is pushed out of sight. Measured once styled, as text size depends on the platform."""
        need = self.bar.minimumSizeHint().width() + 24
        if self.minimumWidth() != need:
            self.setMinimumWidth(need)

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_min_width()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.StyleChange, e.Type.FontChange) and hasattr(self, "bar"):
            self._fit_min_width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        self.stage.setGeometry(0, 0, w, h)
        bw = min(max(1180, self.bar.sizeHint().width()), w - 24)
        self.bar.setFixedWidth(bw)
        self.bar.adjustSize()
        self.bar.move((w - bw) // 2, h - self.bar.height() - 16)
        self.help.adjustSize()
        self.help.move(w - self.help.width() - 14, 14)
        self.countdown.setGeometry(0, 0, w, h)
        f = self.countdown.font()
        f.setPixelSize(int(h * 0.3))
        self.countdown.setFont(f)
        self.editor.setGeometry(0, 0, w, h)
        self._place_status()

    def _place_status(self):
        self.status.setWordWrap(True)
        wide = len(self.status.text()) > 70  # long messages get a wide box, not a narrow column
        self.status.setMinimumWidth(min(720, self.width() - 80) if wide else 0)
        self.status.setMaximumWidth(max(300, min(900, self.width() - 80)))
        self.status.adjustSize()
        self.status.move((self.width() - self.status.width()) // 2, 18)

    def flash(self, text: str, ms: int = 1800):
        self.status.setText(text)
        self.status.setVisible(True)
        self.status.raise_()
        self._place_status()
        self._status_timer.start(ms)

    # --- controls hide while reading ----------------------------------------------------
    def _poke(self):
        self.bar.setVisible(True)
        self.help.setVisible(not self.prefs.get("help_hidden", False))
        self.unsetCursor()
        if self.playing:
            self._idle.start()

    def _hide_controls(self):
        if self.playing and not self.editor.isVisible():
            self.bar.setVisible(False)
            self.help.setVisible(False)
            self.setCursor(Qt.BlankCursor)

    def mouseMoveEvent(self, e):
        self._poke()
        super().mouseMoveEvent(e)

    def wheelEvent(self, e):
        self._move_to(self.stage.pos - e.angleDelta().y() * 0.6, animate=False)
        self._poke()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.editor.isVisible():
            self.toggle_play()

    # ================================================================================ script
    def load_script(self, text: str, mode: str = "section", save: bool = True):
        if self.recording:
            self.stop()
        script = parse(text)
        units = script.units(mode)
        self.mode = mode
        self.stage.set_script(script, units)
        self.stage.pos = 0.0
        self.units_btn.setChecked(mode == "paragraph")
        self.section.clear()
        for u in units:
            done = len(self.book.takes_of(u.id))
            self.section.addItem(f"{u.id}  {u.title}" + (f"  ✓{done}" if done else ""))
        if save:
            self.book.set_script(text, mode, units)
            self.book.save()
            self.prefs["last_script"] = text
            self.scriptChanged.emit()
        self._unit = 0
        self._update_controls()

    def _toggle_units(self):
        self.load_script(self.stage.script.text, "paragraph" if self.units_btn.isChecked() else "section")
        self.prefs["units"] = self.mode

    def open_editor(self):
        if self.playing:
            self.toggle_play()
        self.editor_text.setPlainText(self.stage.script.text if self.stage.script else "")
        self.editor.setVisible(True)
        self.editor.raise_()
        self.editor_text.setFocus()

    def close_editor(self):
        self.editor.setVisible(False)
        self.setFocus()

    def _apply_editor(self):
        self.load_script(self.editor_text.toPlainText(), self.mode)
        self.close_editor()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open script", str(Path.home()),
                                              "Scripts (*.txt *.md *.markdown);;All files (*)")
        if path:
            try:
                self.editor_text.setPlainText(Path(path).read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                QMessageBox.warning(self, "Open script", f"The file could not be read: {exc}")

    # ================================================================================ display
    def set_speed(self, v: int):
        self.wpm = max(80, min(260, int(v)))
        self.speed.blockSignals(True)
        self.speed.setValue(self.wpm)
        self.speed.blockSignals(False)
        self.speed_label.setText(f"{self.wpm} wpm")
        self.prefs["wpm"] = self.wpm

    def set_size(self, v: int, rebuild: bool = True):
        self.stage.size_px = max(28, min(110, int(v)))
        self.size.blockSignals(True)
        self.size.setValue(self.stage.size_px)
        self.size.blockSignals(False)
        self.size_label.setText(f"{self.stage.size_px} px")
        self.prefs["size"] = self.stage.size_px
        if rebuild:
            self.stage.rebuild()

    def toggle_cues(self):
        self.stage.show_cues = not self.stage.show_cues
        self.cues_btn.setChecked(self.stage.show_cues)
        self.prefs["cues"] = self.stage.show_cues
        self.stage.rebuild()

    def toggle_mirror(self):
        self.stage.mirror = not self.stage.mirror
        self.mirror_btn.setChecked(self.stage.mirror)
        self.prefs["mirror"] = self.stage.mirror
        self.stage.update()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def toggle_help(self):
        self.prefs["help_hidden"] = not self.prefs.get("help_hidden", False)
        self.help.setVisible(not self.prefs["help_hidden"])

    def _toggle_advance(self):
        self.advance = "step" if self.mode_btn.isChecked() else "scroll"
        self.prefs["advance"] = self.advance

    # ================================================================================ moving
    def _move_to(self, y: float, animate: bool = True):
        y = max(0.0, min(self.stage.max_pos, y))
        if animate:
            self._target = y
        else:
            self._target = None
            self.stage.pos = y
            self.stage.update()
            self._unit_changed_check()

    def jump_to(self, i: int):
        """Go to unit ``i``; while recording, that starts a new take of it."""
        i = max(0, min(len(self.stage.units) - 1, i))
        self._move_to(self.stage.unit_start(i))
        if self.recording and self.run is not None and i != self._unit:
            self._unit = i
            self.run.next_unit(self.stage.units[i])
            self._update_controls()

    def next_unit(self):
        self.jump_to(self._unit + 1)

    def prev_unit(self):
        # well into this unit: back to its start (a new take of it); else the one before
        into = self.stage.pos - self.stage.unit_start(self._unit)
        if into > self.stage.size_px * 0.8:
            self.retake()
        else:
            self.jump_to(self._unit - 1)

    def _unit_changed_check(self):
        """Scrolling reached another unit: the take changes with it."""
        i = self.stage.unit_index()
        if i != self._unit and self._target is None:
            self._unit = i
            if self.recording and self.run is not None:
                self.run.next_unit(self.stage.units[i])
            self._update_controls()

    def _tick(self):
        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else min(0.1, now - self._last_tick)
        self._last_tick = now
        if self._target is not None:
            d = self._target - self.stage.pos
            if abs(d) < 1.0:
                self.stage.pos = self._target
                self._target = None
            else:
                self.stage.pos += d * min(1.0, dt * 9.0)
            self.stage.update()
        elif self.playing and self.advance == "scroll" and now >= self._hold_until:
            self.stage.pos += (self.wpm / 60.0) * self.stage.px_per_word * dt
            if self.stage.pos >= self.stage.max_pos:
                self.stage.pos = self.stage.max_pos
                self.stage.update()
                self.stop()  # the end of the script
                return
            self.stage.update()
            self._unit_changed_check()
        if self.playing:
            self.elapsed += dt
        if self.recorder is not None:
            if self.run is not None:
                self.run.poll()
            rms, peak = self.recorder.levels()
            self.meter.set_level(rms, peak)
            r = self.recorder
            if (not self._silence_warned and r.frames > SILENT_AFTER_S * r.sr and r.max_peak < 1e-6):
                self._silence_warned = True
                name = r.source.name if r.source else "the microphone"
                self.rec_label.setText("\u25cf NO SOUND")
                self.flash(f"No sound from \u201c{name}\u201d. It is sending pure silence: choose your microphone "
                           "in the list at the top right (Stop first), and check that it is plugged in and not muted.",
                           12000)
            if self.recorder.error:
                err, self.recorder.error = self.recorder.error, ""
                self.stop()
                QMessageBox.warning(self, "Recording problem", err)
        self._update_time()

    # ================================================================================ recording
    @property
    def recording(self) -> bool:
        return self.recorder is not None

    def toggle_play(self):
        if self.counting or self.editor.isVisible():
            return
        if self.playing:
            self.playing = False
            if self.run is not None:
                self.run.pause()
            self.flash("Paused — the take is paused too")
        elif self.recording:
            self.playing = True
            if self.run is not None:
                self.run.resume()
        else:
            self._start_recording()
            return
        self._poke()
        self._update_controls()

    def _start_recording(self):
        src = self.mic_combo.currentData()
        if src is None:
            QMessageBox.warning(self, "No microphone", "Connect a microphone, then choose it in the prompter's "
                                "microphone list.")
            return
        self.prefs["mic"] = src.id
        self._silence_warned = False
        if self.book.script != self.stage.script.text or self.book.mode != self.mode or not self.book.units:
            self.book.set_script(self.stage.script.text, self.mode, self.stage.units)  # what the takes belong to
            self.book.save()
            self.scriptChanged.emit()
        raw = self.session.path / ".parts" / f"teleprompter {time.strftime('%Y%m%d-%H%M%S')}.f32"
        self.recorder = self._make_recorder(raw, src)
        self.run = PrompterRun(self.recorder, self.book, self.session.name, self.session.recordings_dir,
                               on_take=self._take_written)
        try:
            self.recorder.start()
        except RecordingError as exc:
            self.recorder = self.run = None
            QMessageBox.warning(self, exc.title, exc.user_message)
            return
        self.stage.recording = True
        self.recordingChanged.emit(True)
        self._countdown(3 if self.cd_btn.isChecked() else 0)
        self._update_controls()

    def _make_recorder(self, raw: Path, src: InputSource) -> TakeRecorder:
        return TakeRecorder(raw, src, self.gain if (self.mic and src.id == self.mic.id) else 1.0)

    def _countdown(self, n: int):
        """3-2-1 while the microphone opens; reading starts when audio arrives."""
        self.counting = True
        self.countdown.setVisible(n > 0)
        self.countdown.raise_()

        def step(k):
            if self._closing:
                return
            if self.recorder is None:
                self.counting = False
                self.countdown.setVisible(False)
                return
            if k > 0:
                self.countdown.setText(str(k))
                QTimer.singleShot(800, lambda: step(k - 1))
                return
            if not self.recorder.capturing:
                self.countdown.setVisible(True)
                self.countdown.setText("…")
                QTimer.singleShot(100, lambda: step(0))  # wait for the microphone
                return
            self.countdown.setVisible(False)
            self.counting = False
            self._unit = self.stage.unit_index()
            self.run.begin(self.stage.units[self._unit])
            self.playing = True
            self._poke()
            self._update_controls()

        step(n)

    def retake(self):
        """Record the current unit again from its start; the attempt so far is kept."""
        if not self.stage.units:
            return
        self._move_to(self.stage.unit_start(self._unit))
        if self.recording and self.run is not None and not self.counting:
            self.run.retake()
            self._hold_until = time.monotonic() + 1.0  # a moment to breathe before it scrolls on
            if not self.playing:
                self.playing = True
                self.run.resume()
            self.flash(f"⟲ Retake — {self.stage.units[self._unit].title}")
        self._update_controls()

    def keep(self):
        """Keep the take of the current unit (the one being recorded, or the last one)."""
        u = self.stage.units[self._unit] if self.stage.units else None
        if u is None:
            return
        if self.recording and self.run is not None and self.run.current is not None:
            self.run.current.keeper = True
            self.flash("★ This take will be kept")
        elif self.book.takes_of(u.id):
            self.book.mark_keeper(u.id)
            self.scriptChanged.emit()
            self.flash(f"★ Take {self.book.selected(u.id).number} kept")

    def stop(self, now: bool = False):
        """Stop recording: the current take ends (with a short tail) and is saved.
        ``now``: save it before returning (the window is closing)."""
        self.playing = False
        self._target = None
        if self.recorder is None:
            return
        rec, run = self.recorder, self.run
        self.recorder = self.run = None
        self.counting = False
        self.countdown.setVisible(False)

        def finish():
            try:
                if run is not None and rec.capturing:
                    run.stop()
            finally:
                rec.stop()
                try:
                    rec.raw_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if now:
            time.sleep(0.4)  # the last word's tail
            finish()
        else:
            QTimer.singleShot(400, finish)  # let the last word's tail arrive
        self.stage.recording = False
        self.stage.update()
        self.recordingChanged.emit(False)
        self.meter.reset()
        self._poke()
        self._update_controls()

    def _take_written(self, info: TakeInfo, path: Path):
        if getattr(info, "keeper", False):
            self.book.mark_keeper(info.unit, info.number)
        self._last_take = (info, path)
        if self._closing:
            self.takeSaved.emit(info, path)  # still enhanced and listed; no window to update
            return
        u = next((u for u in self.stage.units if u.id == info.unit), None)
        self.flash(f"Take {info.number} saved — {u.title if u else info.unit} ({info.duration:.1f} s)", 2200)
        for i, unit in enumerate(self.stage.units):
            done = len(self.book.takes_of(unit.id))
            self.section.setItemText(i, f"{unit.id}  {unit.title}" + (f"  ✓{done}" if done else ""))
        self.takeSaved.emit(info, path)
        self._update_controls()

    def play_last(self):
        if self._last_take is None:
            self.flash("No take recorded yet")
            return
        if self.playing:
            self.toggle_play()  # don't record the playback
        from ..audio.loader import load_wav
        from ..ui.audio_player import AudioPlayer

        if self._player is None:
            self._player = AudioPlayer(self)
        info, path = self._last_take
        audio = load_wav(path)
        self._player.set_source("original", audio.samples, audio.sample_rate)
        self._player.set_active("original")
        self._player.seek(0)
        self._player.play()
        self.flash(f"▶ Take {info.number} ({info.duration:.1f} s)")

    # ================================================================================ status
    def _update_controls(self):
        rec = self.recording
        self.play_btn.setText("❚❚ Pause" if self.playing else ("▶ Resume" if rec else "▶ Start"))
        self.stop_btn.setEnabled(rec)
        self.retake_btn.setEnabled(bool(self.stage.units))
        self.units_btn.setEnabled(not rec)
        self.mic_combo.setEnabled(not rec)
        self.last_btn.setEnabled(self._last_take is not None)
        if self.stage.units and self.section.count():
            self.section.blockSignals(True)
            self.section.setCurrentIndex(min(self._unit, self.section.count() - 1))
            self.section.blockSignals(False)
        if rec:
            u = self.stage.units[self._unit] if self.stage.units else None
            n = self.book.next_number(u.id) if u else 1
            self.rec_label.setText(f"● REC · {u.id if u else ''} · Take {n}")
        else:
            self.rec_label.setText(f"{len(self.book.takes)} takes in this session" if self.book.takes else "Not recording")

    def _update_time(self):
        frac = self.stage.pos / self.stage.max_pos
        words = self.stage.script.words if self.stage.script else 0
        left = (1 - frac) * words / max(1, self.wpm) * 60
        fmt = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"  # noqa: E731
        rec = f" · recorded {fmt(self.recorder.frames / self.recorder.sr)}" if self.recorder else ""
        self.time_label.setText(f"{fmt(self.elapsed)} · {fmt(left)} left{rec}")

    # ================================================================================ keys
    def keyPressEvent(self, e):
        if self.editor.isVisible():
            if e.key() == Qt.Key_Escape:
                self.close_editor()
            else:
                super().keyPressEvent(e)
            return
        k, text = e.key(), e.text().lower()
        actions = {
            Qt.Key_Space: self.toggle_play, Qt.Key_Backspace: self.retake, Qt.Key_PageUp: self.retake,
            Qt.Key_Right: self.next_unit, Qt.Key_PageDown: self.next_unit, Qt.Key_Left: self.prev_unit,
            Qt.Key_Up: lambda: self.set_speed(self.wpm + 10), Qt.Key_Down: lambda: self.set_speed(self.wpm - 10),
            Qt.Key_Home: lambda: self._move_to(0.0),
            Qt.Key_Escape: lambda: self.stop() if self.recording else (self.showNormal() if self.isFullScreen() else None),
        }
        by_text = {"+": lambda: self.set_size(self.stage.size_px + 4), "=": lambda: self.set_size(self.stage.size_px + 4),
                   "-": lambda: self.set_size(self.stage.size_px - 4), "_": lambda: self.set_size(self.stage.size_px - 4),
                   "m": self.toggle_mirror, "d": self.toggle_cues, "f": self.toggle_fullscreen, "e": self.open_editor,
                   "h": self.toggle_help, "g": self.keep, "p": self.play_last}
        fn = actions.get(k) or by_text.get(text)
        if fn is not None:
            fn()
            self._poke()
            e.accept()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self._closing = True
        if self.recording:
            self.stop(now=True)  # the last take is saved before the window goes
        self.prefs["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        if self._player is not None:
            self._player.shutdown()
        self.closed.emit()
        super().closeEvent(e)

    def sizeHint(self):
        return QSize(1100, 720)
