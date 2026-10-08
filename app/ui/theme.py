"""Visual theme: palette and Qt stylesheet."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette

BG = "#101217"
SURFACE = "#171a21"
SURFACE_2 = "#1e222b"
SURFACE_3 = "#262b36"
BORDER = "#2b303b"
TEXT = "#e8eaef"
MUTED = "#9aa3b2"
FAINT = "#6b7383"
ACCENT = "#36d6b0"  # enhanced / primary action
ACCENT_DARK = "#1f9e82"
ORIGINAL = "#8ea2d0"  # original audio
WARNING = "#f2b65a"
ERROR = "#ef6b6b"
SUCCESS = "#59d48f"


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    c = QColor(hex_value)
    c.setAlpha(alpha)
    return c


def app_font() -> QFont:
    font = QFont("Segoe UI Variable Text")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSizeF(10)
    return font


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
    p.setColor(QPalette.ToolTipBase, QColor(SURFACE_3))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.PlaceholderText, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    return p


STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}

QLabel[role="title"] {{ font-size: 17pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 12pt; font-weight: 600; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 9pt; }}
QLabel[role="chip"] {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 11px; padding: 3px 10px; color: {MUTED}; font-size: 9pt; }}
QLabel[role="value"] {{ font-size: 11pt; font-weight: 600; }}

QFrame[role="card"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame[role="sidebar"] {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
QFrame[role="divider"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; }}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: #06221b; border: none; font-weight: 600; font-size: 11pt; padding: 11px 18px; border-radius: 10px; }}
QPushButton[role="primary"]:hover {{ background: #4fe3c0; }}
QPushButton[role="primary"]:pressed {{ background: {ACCENT_DARK}; }}
QPushButton[role="primary"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="ghost"] {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton[role="ghost"]:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton[role="round"] {{ border-radius: 22px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; padding: 0; background: {TEXT}; color: {BG}; border: none; font-size: 14pt; }}
QPushButton[role="round"]:hover {{ background: #ffffff; }}
QPushButton[role="round"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[role="icon"] {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; font-size: 11pt; }}

QPushButton[role="ab"] {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 9px 18px; font-weight: 600; letter-spacing: 1px; font-size: 9.5pt; color: {MUTED}; }}
QPushButton[role="ab"][side="left"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }}
QPushButton[role="ab"][side="right"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; }}
QPushButton[role="ab"][side="left"]:checked {{ background: {ORIGINAL}; color: #0b1020; border-color: {ORIGINAL}; }}
QPushButton[role="ab"][side="right"]:checked {{ background: {ACCENT}; color: #06221b; border-color: {ACCENT}; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; selection-background-color: {ACCENT_DARK}; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: #3a4150; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QSlider::groove:horizontal {{ height: 6px; background: {SURFACE_3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::sub-page:horizontal:disabled {{ background: {BORDER}; }}
QSlider[role="small"]::groove:horizontal {{ height: 4px; }}
QSlider[role="small"]::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider[role="original"]::sub-page:horizontal {{ background: {ORIGINAL}; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a4150; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}

QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border: none; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabWidget::pane {{ border: none; }}

QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: transparent; selection-background-color: {SURFACE_3}; }}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMessageBox {{ background: {SURFACE}; }}
"""
