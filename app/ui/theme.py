"""Steamburger Studios visual theme: day and night palettes, brand fonts, stylesheet.

Colours and type follow https://www.steamburgerstudios.com/ (its CSS design
tokens): near-black blue "night" / soft grey "day" backgrounds, blue and purple
accents, Bebas Neue for display type, Syne for the interface and DM Mono for
labels and readouts.

Colours are module attributes that :func:`apply` swaps at runtime. Widgets
never bake colours in: custom painting reads ``theme.X`` at paint time, and
styled widgets use roles (``setProperty("tone", "accent")`` etc.) matched by
the global stylesheet, so switching day/night restyles everything at once.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

ASSETS = Path(__file__).resolve().parent / "assets"

NIGHT = {
    # panels a step lighter than the page, with clearer borders, so sections stand out
    "BG": "#07070e",  # --bg
    "BG_ALT": "#10101e",  # --bg-alt (sidebar, header)
    "SURFACE": "#171729",  # --bg-card
    "SURFACE_2": "#20203a",  # --bg-inset (inputs, inset panels)
    "SURFACE_3": "#2c2c4a",  # hover / tracks
    "BORDER": "#2a2a3e",  # --border
    "BORDER_STRONG": "#3b3b52",  # --border-strong
    "TEXT": "#eeeef8",  # --text-primary
    "MUTED": "#b8b8d0",  # --text-secondary
    "FAINT": "#8a8aa6",  # --text-muted, lifted to 4.7:1 on inset panels (was #6e6e8a, 3.6:1)
    "CONTROL_BORDER": "#8a8aa6",
    "TRACK": "#6a6a88",  # slider grooves: 3:1 or more on every panel (was SURFACE_3, 1.4:1)  # check box outlines: 3:1 or more against what is around them
    "ACCENT": "#60a5fa",  # --accent-blue  (primary, Enhanced)
    "ACCENT_DARK": "#3b82f6",
    "ACCENT_INK": "#60a5fa",  # accent as text on the page
    "ACCENT_TEXT": "#07070e",  # text on an ACCENT fill: dark, 7.9:1 (white was 2.5:1)
    "ORIGINAL": "#a78bfa",  # --accent-purple (Original)
    "ORIGINAL_TEXT": "#07070e",  # text on an ORIGINAL fill, 7.4:1
    "WARNING": "#fbbf24",
    "ERROR": "#f87171",
    "SUCCESS": "#4ade80",
    "RECORD": "#ff5a5f",
    "RECORD_TEXT": "#07070e",  # text on a RECORD fill, 6.6:1
    "RECORD_HOVER": "#ff7377",
    "WAVE_ORIGINAL": "#a78bfa",
    "WAVE_ENHANCED": "#60a5fa",
    "PLAYHEAD": "#ffffff",  # playhead on the waveform being heard
}
DAY = {
    "BG": "#f2f2f5",
    "BG_ALT": "#e8e8ef",
    "SURFACE": "#ffffff",
    "SURFACE_2": "#e8e8ef",
    "SURFACE_3": "#dddde8",
    "BORDER": "#dcdce3",
    "BORDER_STRONG": "#c3c3cc",
    "TEXT": "#0a0a12",
    "MUTED": "#363645",
    "FAINT": "#5f5f7a",  # 5.1:1 on inset panels (was #6e6e8a, 4.0:1)
    "CONTROL_BORDER": "#6e6e8a",
    "TRACK": "#7c7c8c",  # was SURFACE_3, 1.1:1
    "ACCENT": "#2563eb",  # white text on it 5.2:1 (was #3b82f6, 3.7:1)
    "ACCENT_DARK": "#1d4ed8",
    "ACCENT_INK": "#1d4ed8",  # accent as text on the page: 5.5:1 on the sidebar
    "ACCENT_TEXT": "#ffffff",
    "ORIGINAL": "#7c3aed",  # white text on it 5.7:1 (was #8b5cf6, 4.2:1)
    "ORIGINAL_TEXT": "#ffffff",
    "WARNING": "#b45309",
    "ERROR": "#dc2626",
    "SUCCESS": "#15803d",
    "RECORD": "#c81e1e",  # white text on it 5.7:1 (was #e5383b, 4.2:1)
    "RECORD_TEXT": "#ffffff",
    "RECORD_HOVER": "#a51b1b",
    "WAVE_ORIGINAL": "#8b5cf6",
    "WAVE_ENHANCED": "#3b82f6",
    "PLAYHEAD": "#0a0a12",  # dark on the light background (white was invisible)
}

# Increased contrast (the system's contrast setting: Windows contrast themes, macOS Increase
# Contrast; or Settings > Increase contrast): secondary text, borders and tracks move a step
# closer to the main text colour, so nothing depends on a subtle shade.
HIGH_CONTRAST = {
    "night": {"MUTED": "#dcdcec", "FAINT": "#b8b8d0", "BORDER": "#8a8aa6", "BORDER_STRONG": "#b8b8d0",
              "CONTROL_BORDER": "#b8b8d0", "TRACK": "#9a9ab4"},
    "day": {"MUTED": "#14141c", "FAINT": "#363645", "BORDER": "#6e6e8a", "BORDER_STRONG": "#363645",
            "CONTROL_BORDER": "#363645", "TRACK": "#5f5f7a"},
}
CONTRAST = "auto"  # "auto" follows the system | "high" always
HIGH = False  # increased contrast in use now

# current values (night by default); replaced by apply()
BG = BG_ALT = SURFACE = SURFACE_2 = SURFACE_3 = BORDER = BORDER_STRONG = ""
TEXT = MUTED = FAINT = CONTROL_BORDER = TRACK = ACCENT = ACCENT_DARK = ACCENT_INK = ACCENT_TEXT = ORIGINAL = ORIGINAL_TEXT = ""
WARNING = ERROR = SUCCESS = RECORD = RECORD_TEXT = RECORD_HOVER = WAVE_ORIGINAL = WAVE_ENHANCED = PLAYHEAD = ""
MODE = "night"
STYLESHEET = ""

# The fonts in use: the system's (default, most readable in an app) or the Steamburger
# Studios brand fonts. The logo wordmark and the splash always use the brand fonts.
FONT_STYLE = "system"  # "system" | "brand"
FONT_UI = "Syne"
FONT_DISPLAY = "Bebas Neue"
FONT_MONO = "DM Mono"
BRAND_UI, BRAND_DISPLAY, BRAND_MONO = "Syne", "Bebas Neue", "DM Mono"
_fonts_loaded = False


def load_fonts() -> None:
    """Register the bundled brand fonts (SIL Open Font License)."""
    global _fonts_loaded, BRAND_UI, BRAND_DISPLAY, BRAND_MONO
    if _fonts_loaded:
        return
    families = {}
    for ttf in sorted((ASSETS / "fonts").glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(ttf))
        if fid >= 0:
            for fam in QFontDatabase.applicationFontFamilies(fid):
                families[fam.lower()] = fam
    BRAND_UI = families.get("syne", "Segoe UI")
    BRAND_DISPLAY = families.get("bebas neue", "Impact")
    BRAND_MONO = families.get("dm mono", "Consolas")
    _fonts_loaded = True
    set_font_style(FONT_STYLE)
    import logging

    logging.getLogger("VoiceCleaner.theme").info("Brand fonts: %s / %s / %s", BRAND_DISPLAY, BRAND_UI, BRAND_MONO)


def _system_fonts() -> tuple[str, str]:
    """(text font, monospace font) of this system."""
    ui = QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
    available = set(QFontDatabase.families())
    wanted = ("SF Mono", "Menlo") if sys.platform == "darwin" else ("Cascadia Mono", "Consolas")
    mono = next((f for f in wanted if f in available), QFontDatabase.systemFont(QFontDatabase.FixedFont).family())
    return ui, mono


def set_font_style(style: str) -> None:
    """Use the system fonts or the brand fonts (apply() restyles the app)."""
    global FONT_STYLE, FONT_UI, FONT_DISPLAY, FONT_MONO
    FONT_STYLE = "brand" if style == "brand" else "system"
    if not _fonts_loaded:
        return  # load_fonts() calls back once the brand fonts are registered
    if FONT_STYLE == "brand":
        FONT_UI, FONT_DISPLAY, FONT_MONO = BRAND_UI, BRAND_DISPLAY, BRAND_MONO
    else:
        ui, mono = _system_fonts()
        FONT_UI, FONT_DISPLAY, FONT_MONO = ui, ui, mono


def _set(values: dict) -> None:
    globals().update(values)


_set(NIGHT)


# macOS draws a point as 1 px (72 dpi), Windows as 1.33 px (96 dpi), so the same point sizes
# came out a quarter smaller on a Mac. Scale them there to match the Windows look.
MAC_FONT_FIX = sys.platform == "darwin"
PT_SCALE = 96 / 72 if MAC_FONT_FIX else 1.0


def pt(size: float) -> float:
    """A font size in points, as it looks on Windows, for this platform."""
    return round(size * PT_SCALE, 2)


def _scale_pt(css: str) -> str:
    if PT_SCALE == 1.0:
        return css
    return re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)pt\b", lambda m: f"{pt(float(m.group(1)))}pt", css)


# Widget classes macOS gives their own system font (13 pt San Francisco), which override
# the application font: without this, labels, buttons and menus ignored the brand font.
_PLATFORM_FONT_CLASSES = ("QLabel", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton", "QMenu",
                          "QMenuBar", "QMessageBox", "QMessageBoxLabel", "QTipLabel", "QHeaderView",
                          "QListView", "QListWidget", "QTreeView", "QTableView", "QTabBar", "QGroupBox",
                          "QComboBoxMenuItem", "QAbstractItemView", "QSpinBox", "QDoubleSpinBox")


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    c = QColor(hex_value)
    c.setAlpha(alpha)
    return c


def app_font() -> QFont:
    load_fonts()
    font = QFont(FONT_UI)
    font.setPointSizeF(pt(9.5))
    return font


def mono_font(size: float = 9.0) -> QFont:
    load_fonts()
    f = QFont(FONT_MONO)
    f.setPointSizeF(pt(size))
    return f


def display_font(size: float = 20.0) -> QFont:
    load_fonts()
    f = QFont(FONT_DISPLAY)
    f.setPointSizeF(pt(size))
    return f


def palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(SURFACE_2))
    p.setColor(QPalette.AlternateBase, QColor(SURFACE))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(SURFACE_2))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.Highlight, QColor(ACCENT_DARK))
    p.setColor(QPalette.HighlightedText, QColor(ACCENT_TEXT))
    p.setColor(QPalette.ToolTipBase, QColor(SURFACE))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.PlaceholderText, QColor(FAINT))
    p.setColor(QPalette.Link, QColor(ACCENT))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    return p


def _check_image(color: str) -> str:
    """A tick for ticked check boxes, drawn in ``color`` and cached as a PNG (no SVG plugin needed).
    Drawn at 32 px and scaled into the 16 px box, so it stays sharp on high-DPI screens."""
    import tempfile

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen

    folder = Path(tempfile.gettempdir()) / "VoiceCleaner-ui"
    path = folder / f"check-{color.lstrip('#').lower()}.png"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        img = QImage(32, 32, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color), 4.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        tick = QPainterPath(QPointF(7, 16.5))
        tick.lineTo(13.5, 23)
        tick.lineTo(25, 9.5)
        p.drawPath(tick)
        p.end()
        tmp = path.with_suffix(f".{os.getpid()}.png")
        img.save(str(tmp), "PNG")
        os.replace(tmp, path)
    return path.as_posix()


def build_stylesheet() -> str:
    try:
        check, check_disabled = _check_image(ACCENT_TEXT), _check_image(FAINT)
    except Exception:  # no tick image (unwritable temp folder): the box still fills with the accent
        check = check_disabled = ""
    mono = f"'{FONT_MONO}', Consolas, monospace"
    brand_disp = f"'{BRAND_DISPLAY}', Impact, sans-serif"
    if FONT_STYLE == "brand":
        def heading(size):
            return f"font-family: {brand_disp}; font-size: {size}pt; letter-spacing: 1px;"
    else:  # the brand face is condensed: a wide system font needs less size, more weight
        def heading(size):
            return f"font-family: '{FONT_UI}'; font-size: {round(size * 0.7, 1)}pt; font-weight: 600;"
    return _scale_pt(f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px; border-radius: 4px; }}

QLabel[role="title"] {{ {heading(24)} }}
QLabel[role="display"] {{ {heading(30)} }}
QLabel[role="h2"] {{ font-size: 12pt; font-weight: 700; }}
QLabel[role="section"] {{ color: {FAINT}; font-family: {mono}; font-size: 8pt; font-weight: 500; letter-spacing: 2px; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 8.5pt; }}
QLabel[role="chip"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 3px 9px; color: {MUTED}; font-family: {mono}; font-size: 8pt; }}
QLabel[role="value"] {{ font-size: 11pt; font-weight: 700; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: 10pt; }}
QLabel[role="status"] {{ color: {MUTED}; font-family: {mono}; font-size: 8.5pt; background: transparent; padding: 0 2px; }}
QLabel[role="meta"] {{ color: {FAINT}; font-family: {mono}; font-size: 8pt; background: transparent; }}
QLabel[tone="accent"] {{ color: {ACCENT_INK}; }}
QLabel[tone="original"] {{ color: {ORIGINAL}; }}
QLabel[tone="record"] {{ color: {RECORD}; }}
QLabel[tone="warning"] {{ color: {WARNING}; }}
QLabel[tone="success"] {{ color: {SUCCESS}; }}
QLabel[tone="faint"] {{ color: {FAINT}; }}
QLabel[tone="muted"] {{ color: {MUTED}; }}
QLabel[role="score"] {{ {heading(34)} }}
QLabel[role="brand"] {{ font-family: {brand_disp}; font-size: 19pt; letter-spacing: 1px; }}
QLabel[role="wordmark"] {{ font-family: {brand_disp}; font-size: 30pt; letter-spacing: 1px; }}
QLabel[role="brandsub"] {{ font-family: {mono}; font-size: 7.5pt; color: {FAINT}; letter-spacing: 2px; }}
QLabel[role="eqvalue"] {{ color: {FAINT}; font-family: {mono}; font-size: 7.5pt; }}
QLabel[role="eqvalue"][active="true"] {{ color: {ACCENT_INK}; }}
QLabel[role="tag"] {{ font-family: {mono}; font-size: 7.5pt; letter-spacing: 1px; color: {WARNING}; }}
QFrame[role="smartnote"] {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-left: 3px solid {ACCENT}; border-radius: 4px; }}
QLabel[role="smartnotetext"] {{ background: transparent; border: none; color: {MUTED}; font-size: 8.5pt; }}
QLabel[auto="true"] {{ color: {ACCENT_INK}; font-family: {mono}; font-size: 8pt; }}

QFrame[role="card"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame[role="inset"] {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame[role="sidebar"] {{ background: {BG_ALT}; border-left: 1px solid {BORDER}; }}
QFrame[role="header"] {{ background: {BG_ALT}; border-bottom: 1px solid {BORDER}; }}
QFrame[role="actions"] {{ border: none; border-top: 1px solid {BORDER}; }}
QFrame[role="divider"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}
QFrame[role="vdivider"] {{ background: {BORDER}; max-width: 1px; min-width: 1px; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {SURFACE_3}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
/* buttons: Title Case in the interface font. Monospace capitals are kept for labels, tabs and readouts */
QPushButton[role="primary"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; font-size: 10pt; padding: 10px 18px; border-radius: 4px; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[role="primary"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="activity"] {{ background: {SURFACE_2}; border: 1px solid {ACCENT}; border-radius: 4px; padding: 5px 12px; color: {ACCENT_INK}; font-family: {mono}; font-size: 8pt; }}
QPushButton[role="activity"]:hover {{ background: {SURFACE_3}; }}
QPushButton[role="toggle"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 7px 12px; color: {MUTED}; }}
QPushButton[role="toggle"]:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_TEXT}; }}
QPushButton[role="ghost"] {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton[role="ghost"]:hover {{ color: {ACCENT_INK}; }}
QPushButton[role="outline"] {{ background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton[role="outline"]:hover {{ border-color: {ACCENT}; color: {ACCENT_INK}; }}
QPushButton[role="round"] {{ border-radius: 22px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; padding: 0; background: {TEXT}; color: {BG}; border: none; font-size: 14pt; }}
QPushButton[role="round"]:hover {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}
QPushButton[role="round"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="icon"] {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 11pt; }}
QPushButton[role="themetoggle"] {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 12pt; background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton[role="themetoggle"]:hover {{ border-color: {ACCENT}; }}

QPushButton[role="ab"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 8px 18px; font-weight: 600; color: {MUTED}; }}
QPushButton[role="ab"][side="left"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }}
QPushButton[role="ab"][side="right"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; }}
QPushButton[role="ab"][side="left"]:checked {{ background: {ORIGINAL}; color: {ORIGINAL_TEXT}; border-color: {ORIGINAL}; }}
QPushButton[role="ab"][side="right"]:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; border-color: {ACCENT}; }}
QPushButton[role="seg"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 0; padding: 8px 16px; font-weight: 600; color: {MUTED}; }}
QPushButton[role="seg"][side="left"] {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
QPushButton[role="seg"][side="right"] {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
QPushButton[role="seg"]:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; border-color: {ACCENT}; }}
QPushButton[role="seg"]:hover:!checked {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton[role="record"] {{ background: transparent; border: 1px solid {BORDER_STRONG}; color: {TEXT}; padding: 6px 12px; }}
QPushButton[role="record"]:hover {{ border-color: {RECORD}; }}
QPushButton[role="record"]:checked {{ background: {RECORD}; border-color: {RECORD}; color: {RECORD_TEXT}; font-weight: 600; }}
QPushButton[role="recbig"] {{ background: {RECORD}; color: {RECORD_TEXT}; border: none; padding: 10px 18px; font-weight: 600; font-size: 10pt; }}
QPushButton[role="recbig"]:hover {{ background: {RECORD_HOVER}; }}
QPushButton[role="recbig"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="session"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 5px 12px; }}
QPushButton[role="session"]::menu-indicator {{ image: none; width: 0; }}
QPushButton[role="session"][state="open"] {{ border-color: {ACCENT}; color: {ACCENT_INK}; }}
QPushButton[role="session"][state="rec"] {{ border-color: {RECORD}; color: {RECORD}; font-weight: 700; }}

QToolButton[role="link"] {{ border: none; color: {MUTED}; padding: 4px 0; }}
QToolButton[role="link"]:hover {{ color: {ACCENT_INK}; }}
QToolButton[role="disclosure"] {{ border: none; color: {TEXT}; font-weight: 700; font-size: 10pt; padding: 4px 0; }}
QToolButton[role="disclosure-muted"] {{ border: none; color: {MUTED}; font-weight: 700; padding: 4px 0; }}
QToolButton[role="disclosure-muted"]:hover {{ color: {TEXT}; }}
QToolButton[role="menu"] {{ border: none; font-size: 16pt; padding: 0 8px; color: {MUTED}; }}
QToolButton[role="menu"]::menu-indicator {{ image: none; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 6px 10px; selection-background-color: {ACCENT_DARK}; }}
QComboBox:hover, QLineEdit:hover, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QSpinBox, QDoubleSpinBox {{ font-family: {mono}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; outline: none; padding: 4px; }}

QSlider:horizontal {{ min-height: 20px; }}
QSlider:vertical {{ min-width: 18px; }}
QSlider::groove:horizontal {{ height: 4px; background: {TRACK}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QSlider::handle:horizontal:focus {{ background: {ACCENT}; }}
QSlider::handle:vertical:focus {{ background: {ACCENT}; }}
QSlider::sub-page:horizontal:disabled {{ background: {BORDER_STRONG}; }}
QSlider::handle:horizontal:disabled {{ background: {FAINT}; }}
QSlider[role="small"]::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {TRACK}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {TEXT}; height: 12px; width: 12px; margin: 0 -4px; border-radius: 6px; }}
QSlider::handle:vertical:hover {{ background: {ACCENT}; }}
QSlider::add-page:vertical, QSlider::sub-page:vertical {{ background: transparent; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 2px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QCheckBox {{ spacing: 8px; }}
/* the outline stands out (3:1 or more) and a ticked box shows a tick, not just a colour */
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 3px; border: 1px solid {CONTROL_BORDER}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}
QCheckBox::indicator:disabled {{ border-color: {BORDER_STRONG}; }}
QCheckBox::indicator:checked:disabled {{ background: {SURFACE_3}; border-color: {BORDER_STRONG}; image: url("{check_disabled}"); }}
/* radio buttons: the same visible outline; the chosen one is a filled ring with a dot */
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {CONTROL_BORDER}; background: {SURFACE_2}; }}
QRadioButton::indicator:checked {{ border-color: {ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {ACCENT_TEXT}, stop:0.38 {ACCENT_TEXT}, stop:0.45 {ACCENT}, stop:1 {ACCENT}); }}
QRadioButton[focusring="true"]::indicator {{ border: 2px solid {TEXT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {SURFACE_2}; color: {MUTED}; padding: 7px 20px; margin: 10px 6px 0 0; border: 1px solid {BORDER}; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; font-family: {mono}; font-size: 8.5pt; letter-spacing: 2px; }}
QTabBar::tab:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
/* the selected tab is part of the page: page colour, no line under it, taller, accent edge on top */
QTabBar::tab:selected {{ background: {BG}; color: {ACCENT_INK}; border: 1px solid {BORDER_STRONG}; border-top: 3px solid {ACCENT}; border-bottom: none; margin-top: 4px; margin-bottom: -1px; padding: 9px 20px 9px 20px; }}
/* the bar measures each tab with its own font: give it the tabs' font and spacing, or a wider
   monospace face (system fonts: SF Mono / Menlo) is cut short ("ENHAN...") */
QTabBar {{ border-bottom: 1px solid {BORDER_STRONG}; font-family: {mono}; font-size: 8.5pt; letter-spacing: 2px; }}

QTableWidget, QTreeWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: transparent; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; alternate-background-color: {BG_ALT}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 4px; border-bottom: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {SURFACE}; color: {FAINT}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; font-family: {mono}; font-size: 8pt; letter-spacing: 1px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMessageBox {{ background: {BG}; }}

/* keyboard focus: a ring on the control reached with Tab (focus_ring.py sets focusring) */
QPushButton[focusring="true"], QToolButton[focusring="true"] {{ border: 2px solid {ACCENT}; }}
QPushButton[role="primary"][focusring="true"], QPushButton[role="recbig"][focusring="true"],
QPushButton[role="round"][focusring="true"], QPushButton[focusring="true"]:checked {{ border: 2px solid {TEXT}; }}
QCheckBox[focusring="true"]::indicator {{ border: 2px solid {ACCENT}; }}
QCheckBox[focusring="true"]::indicator:checked {{ border: 2px solid {TEXT}; }}
QTabBar[focusring="true"]::tab:selected {{ border: 2px solid {ACCENT}; border-top: 3px solid {ACCENT}; border-bottom: none; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QTableWidget[focusring="true"], QTreeWidget[focusring="true"], QListWidget[focusring="true"] {{ border: 2px solid {ACCENT}; }}
/* on a Mac, Tab also stops on a scrolling page (the arrow keys then scroll it) */
QScrollArea[focusring="true"] {{ border: 2px solid {ACCENT}; border-radius: 6px; }}
""")


def resolve_mode(preference: str) -> str:
    """``auto`` follows the Windows app theme (Settings > Personalization > Colors)."""
    if preference in ("day", "night"):
        return preference
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "day"
    except Exception:
        pass
    return "night"


def system_high_contrast() -> bool:
    """The system asks for more contrast (a Windows contrast theme, macOS Increase Contrast)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()  # kept alive while its accessibility hints are read
        return hints.accessibility().contrastPreference() == Qt.ContrastPreference.HighContrast
    except Exception:
        return False


def _set_window_scheme(app, preference: str) -> None:
    """Title bars and other system-drawn parts follow Day / Night too (macOS: the app's
    appearance). "auto" clears the override, so the system's own setting reads through again."""
    try:
        from PySide6.QtCore import Qt

        scheme = {"day": Qt.ColorScheme.Light, "night": Qt.ColorScheme.Dark}.get(preference, Qt.ColorScheme.Unknown)
        app.styleHints().setColorScheme(scheme)
    except Exception:  # Qt before 6.8
        pass


def apply(app, preference: str = "auto", fonts: str | None = None, contrast: str | None = None) -> str:
    """Switch every colour (and the fonts, when ``fonts`` is "system" or "brand"; the contrast,
    when ``contrast`` is "auto" or "high") and restyle the running application. Returns the
    mode used."""
    global MODE, STYLESHEET, CONTRAST, HIGH
    if fonts is not None:
        set_font_style(fonts)
    if contrast is not None:
        CONTRAST = "high" if contrast == "high" else "auto"
    load_fonts()
    if app is not None:
        _set_window_scheme(app, preference)  # before resolve_mode: "auto" reads the system's
    MODE = resolve_mode(preference)
    HIGH = CONTRAST == "high" or system_high_contrast()
    _set(DAY if MODE == "day" else NIGHT)
    if HIGH:
        _set(HIGH_CONTRAST[MODE])
    STYLESHEET = build_stylesheet()
    if app is not None:
        app.setPalette(palette())
        app.setFont(app_font())
        app.setStyleSheet(STYLESHEET)
        if MAC_FONT_FIX:  # after the stylesheet: setting it restores the macOS per-class fonts
            for cls in _PLATFORM_FONT_CLASSES:
                app.setFont(app_font(), cls)
        for w in app.allWidgets():
            w.update()  # custom-painted widgets pick up the new colours
    return MODE


def restyle(widget) -> None:
    """Re-evaluate stylesheet rules after changing a widget's role/tone property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
