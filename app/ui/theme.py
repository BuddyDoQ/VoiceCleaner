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

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

ASSETS = Path(__file__).resolve().parent / "assets"

NIGHT = {
    "BG": "#080810",  # --bg
    "BG_ALT": "#0e0e1a",  # --bg-alt (sidebar, header)
    "SURFACE": "#121220",  # --bg-card
    "SURFACE_2": "#1a1a2e",  # --bg-inset (inputs, inset panels)
    "SURFACE_3": "#262640",  # hover / tracks
    "BORDER": "#1f1f2d",  # --border (7 % white on bg)
    "BORDER_STRONG": "#30303f",  # --border-strong
    "TEXT": "#eeeef8",  # --text-primary
    "MUTED": "#b8b8d0",  # --text-secondary
    "FAINT": "#6e6e8a",  # --text-muted
    "ACCENT": "#60a5fa",  # --accent-blue  (primary, Enhanced)
    "ACCENT_DARK": "#3b82f6",
    "ACCENT_TEXT": "#ffffff",
    "ORIGINAL": "#a78bfa",  # --accent-purple (Original)
    "WARNING": "#fbbf24",
    "ERROR": "#f87171",
    "SUCCESS": "#4ade80",
    "RECORD": "#ff5a5f",
    "WAVE_ORIGINAL": "#a78bfa",
    "WAVE_ENHANCED": "#60a5fa",
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
    "FAINT": "#6e6e8a",
    "ACCENT": "#3b82f6",
    "ACCENT_DARK": "#2563eb",
    "ACCENT_TEXT": "#ffffff",
    "ORIGINAL": "#8b5cf6",
    "WARNING": "#b45309",
    "ERROR": "#dc2626",
    "SUCCESS": "#15803d",
    "RECORD": "#e5383b",
    "WAVE_ORIGINAL": "#8b5cf6",
    "WAVE_ENHANCED": "#3b82f6",
}

# current values (night by default); replaced by apply()
BG = BG_ALT = SURFACE = SURFACE_2 = SURFACE_3 = BORDER = BORDER_STRONG = ""
TEXT = MUTED = FAINT = ACCENT = ACCENT_DARK = ACCENT_TEXT = ORIGINAL = ""
WARNING = ERROR = SUCCESS = RECORD = WAVE_ORIGINAL = WAVE_ENHANCED = ""
MODE = "night"
STYLESHEET = ""

FONT_UI = "Syne"
FONT_DISPLAY = "Bebas Neue"
FONT_MONO = "DM Mono"
_fonts_loaded = False


def load_fonts() -> None:
    """Register the bundled brand fonts (SIL Open Font License)."""
    global _fonts_loaded, FONT_UI, FONT_DISPLAY, FONT_MONO
    if _fonts_loaded:
        return
    families = {}
    for ttf in sorted((ASSETS / "fonts").glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(ttf))
        if fid >= 0:
            for fam in QFontDatabase.applicationFontFamilies(fid):
                families[fam.lower()] = fam
    FONT_UI = families.get("syne", "Segoe UI")
    FONT_DISPLAY = families.get("bebas neue", "Impact")
    FONT_MONO = families.get("dm mono", "Consolas")
    _fonts_loaded = True
    import logging

    logging.getLogger("VoiceCleaner.theme").info("Brand fonts: %s / %s / %s", FONT_DISPLAY, FONT_UI, FONT_MONO)


def _set(values: dict) -> None:
    globals().update(values)


_set(NIGHT)


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    c = QColor(hex_value)
    c.setAlpha(alpha)
    return c


def app_font() -> QFont:
    load_fonts()
    font = QFont(FONT_UI)
    font.setPointSizeF(9.5)
    return font


def mono_font(size: float = 9.0) -> QFont:
    load_fonts()
    f = QFont(FONT_MONO)
    f.setPointSizeF(size)
    return f


def display_font(size: float = 20.0) -> QFont:
    load_fonts()
    f = QFont(FONT_DISPLAY)
    f.setPointSizeF(size)
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
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor(SURFACE))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.PlaceholderText, QColor(FAINT))
    p.setColor(QPalette.Link, QColor(ACCENT))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    return p


def build_stylesheet() -> str:
    mono = f"'{FONT_MONO}', Consolas, monospace"
    disp = f"'{FONT_DISPLAY}', Impact, sans-serif"
    return f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px; border-radius: 4px; }}

QLabel[role="title"] {{ font-family: {disp}; font-size: 24pt; letter-spacing: 1px; }}
QLabel[role="display"] {{ font-family: {disp}; font-size: 30pt; letter-spacing: 1px; }}
QLabel[role="h2"] {{ font-size: 12pt; font-weight: 700; }}
QLabel[role="section"] {{ color: {FAINT}; font-family: {mono}; font-size: 8pt; font-weight: 500; letter-spacing: 2px; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 8.5pt; }}
QLabel[role="chip"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 3px 9px; color: {MUTED}; font-family: {mono}; font-size: 8pt; }}
QLabel[role="value"] {{ font-size: 11pt; font-weight: 700; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: 10pt; }}
QLabel[tone="accent"] {{ color: {ACCENT}; }}
QLabel[tone="original"] {{ color: {ORIGINAL}; }}
QLabel[tone="record"] {{ color: {RECORD}; }}
QLabel[tone="warning"] {{ color: {WARNING}; }}
QLabel[tone="success"] {{ color: {SUCCESS}; }}
QLabel[tone="faint"] {{ color: {FAINT}; }}
QLabel[tone="muted"] {{ color: {MUTED}; }}
QLabel[role="score"] {{ font-family: {disp}; font-size: 34pt; }}
QLabel[role="brand"] {{ font-family: {disp}; font-size: 19pt; letter-spacing: 1px; }}
QLabel[role="brandsub"] {{ font-family: {mono}; font-size: 7.5pt; color: {FAINT}; letter-spacing: 2px; }}
QLabel[role="eqvalue"] {{ color: {FAINT}; font-family: {mono}; font-size: 7.5pt; }}
QLabel[role="eqvalue"][active="true"] {{ color: {ACCENT}; }}
QLabel[role="eqfreq"] {{ color: {MUTED}; font-family: {mono}; font-size: 7.5pt; }}
QLabel[role="eqname"] {{ color: {FAINT}; font-size: 7pt; }}

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
QPushButton[role="primary"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-family: {mono}; font-weight: 500; font-size: 9.5pt; letter-spacing: 2px; padding: 11px 18px; border-radius: 4px; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[role="primary"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="ghost"] {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton[role="ghost"]:hover {{ color: {ACCENT}; }}
QPushButton[role="outline"] {{ background: transparent; border: 1px solid {BORDER_STRONG}; font-family: {mono}; letter-spacing: 1px; }}
QPushButton[role="outline"]:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton[role="round"] {{ border-radius: 22px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; padding: 0; background: {TEXT}; color: {BG}; border: none; font-size: 14pt; }}
QPushButton[role="round"]:hover {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}
QPushButton[role="round"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="icon"] {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 11pt; }}
QPushButton[role="themetoggle"] {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 12pt; background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton[role="themetoggle"]:hover {{ border-color: {ACCENT}; }}

QPushButton[role="ab"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 9px 18px; font-family: {mono}; font-weight: 500; letter-spacing: 2px; font-size: 8.5pt; color: {MUTED}; }}
QPushButton[role="ab"][side="left"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }}
QPushButton[role="ab"][side="right"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; }}
QPushButton[role="ab"][side="left"]:checked {{ background: {ORIGINAL}; color: #ffffff; border-color: {ORIGINAL}; }}
QPushButton[role="ab"][side="right"]:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; border-color: {ACCENT}; }}
QPushButton[role="seg"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 0; padding: 9px 16px; font-family: {mono}; font-weight: 500; letter-spacing: 2px; font-size: 8.5pt; color: {MUTED}; }}
QPushButton[role="seg"][side="left"] {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
QPushButton[role="seg"][side="right"] {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
QPushButton[role="seg"]:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; border-color: {ACCENT}; }}
QPushButton[role="seg"]:hover:!checked {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton[role="record"] {{ background: transparent; border: 1px solid {BORDER_STRONG}; color: {TEXT}; padding: 6px 12px; }}
QPushButton[role="record"]:hover {{ border-color: {RECORD}; }}
QPushButton[role="record"]:checked {{ background: {RECORD}; border-color: {RECORD}; color: #ffffff; font-weight: 600; }}
QPushButton[role="recbig"] {{ background: {RECORD}; color: #ffffff; border: none; padding: 10px 18px; font-family: {mono}; font-weight: 500; letter-spacing: 1px; font-size: 9.5pt; }}
QPushButton[role="recbig"]:hover {{ background: #ff7377; }}
QPushButton[role="recbig"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="session"] {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 5px 12px; font-family: {mono}; font-size: 8.5pt; }}
QPushButton[role="session"]::menu-indicator {{ image: none; width: 0; }}

QToolButton[role="link"] {{ border: none; color: {MUTED}; padding: 4px 0; }}
QToolButton[role="link"]:hover {{ color: {ACCENT}; }}
QToolButton[role="disclosure"] {{ border: none; color: {TEXT}; font-weight: 700; font-size: 10pt; padding: 4px 0; }}
QToolButton[role="disclosure-muted"] {{ border: none; color: {MUTED}; font-weight: 700; padding: 4px 0; }}
QToolButton[role="disclosure-muted"]:hover {{ color: {TEXT}; }}
QToolButton[role="menu"] {{ border: none; font-size: 16pt; padding: 0 8px; color: {MUTED}; }}
QToolButton[role="menu"]::menu-indicator {{ image: none; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 6px 10px; selection-background-color: {ACCENT_DARK}; }}
QComboBox:hover, QLineEdit:hover, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; outline: none; padding: 4px; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QSlider::sub-page:horizontal:disabled {{ background: {BORDER_STRONG}; }}
QSlider[role="small"]::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {TEXT}; height: 12px; width: 12px; margin: 0 -4px; border-radius: 6px; }}
QSlider::handle:vertical:hover {{ background: {ACCENT}; }}
QSlider::add-page:vertical, QSlider::sub-page:vertical {{ background: transparent; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 2px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 3px; border: 1px solid {BORDER_STRONG}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {FAINT}; padding: 10px 18px; border: none; border-bottom: 2px solid transparent; font-family: {mono}; font-size: 8.5pt; letter-spacing: 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QTableWidget, QTreeWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: transparent; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; alternate-background-color: {BG_ALT}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 4px; border-bottom: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {SURFACE}; color: {FAINT}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; font-family: {mono}; font-size: 8pt; letter-spacing: 1px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QMessageBox {{ background: {BG}; }}
"""


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


def apply(app, preference: str = "auto") -> str:
    """Switch every colour and restyle the running application. Returns the mode used."""
    global MODE, STYLESHEET
    load_fonts()
    MODE = resolve_mode(preference)
    _set(DAY if MODE == "day" else NIGHT)
    STYLESHEET = build_stylesheet()
    if app is not None:
        app.setPalette(palette())
        app.setFont(app_font())
        app.setStyleSheet(STYLESHEET)
        for w in app.allWidgets():
            w.update()  # custom-painted widgets pick up the new colours
    return MODE


def restyle(widget) -> None:
    """Re-evaluate stylesheet rules after changing a widget's role/tone property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
