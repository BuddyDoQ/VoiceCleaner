"""Render the installer artwork (WiX dialog + banner bitmaps) from the Steamburger brand.

WixUIDialogBmp 493x312: welcome/finish pages; the left 164 px are artwork, the dialog
text is drawn on the right, so that side stays light.
WixUIBannerBmp 493x58: top banner of the inner pages; title text is drawn on the left.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.brand import logo_pixmap  # noqa: E402

OUT = ROOT / "packaging" / "wix"


def dialog_bmp(path: Path):
    img = QImage(493, 312, QImage.Format_RGB888)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.fillRect(QRectF(0, 0, 164, 312), QColor(theme.NIGHT["BG"]))
    p.fillRect(QRectF(162, 0, 2, 312), QColor(theme.NIGHT["ACCENT"]))
    p.drawPixmap(22, 54, logo_pixmap(120))
    f = QFont(theme.FONT_DISPLAY)
    f.setPixelSize(30)
    p.setFont(f)
    p.setPen(QColor(theme.NIGHT["TEXT"]))
    p.drawText(QRectF(0, 186, 164, 34), Qt.AlignHCenter, "VOICECLEANER")
    f = QFont(theme.FONT_MONO)
    f.setPixelSize(9)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    p.setFont(f)
    p.setPen(QColor(theme.NIGHT["ACCENT"]))
    p.drawText(QRectF(0, 222, 164, 16), Qt.AlignHCenter, "STEAMBURGER STUDIOS")
    p.end()
    img.save(str(path), "BMP")


def banner_bmp(path: Path):
    img = QImage(493, 58, QImage.Format_RGB888)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(QRectF(493 - 120, 0, 120, 58), QColor(theme.NIGHT["BG"]))
    p.fillRect(QRectF(493 - 122, 0, 2, 58), QColor(theme.NIGHT["ACCENT"]))
    p.drawPixmap(493 - 104, 6, logo_pixmap(46))
    f = QFont(theme.FONT_DISPLAY)
    f.setPixelSize(15)
    p.setFont(f)
    p.setPen(QColor(theme.NIGHT["TEXT"]))
    p.drawText(QRectF(493 - 56, 0, 52, 58), Qt.AlignVCenter | Qt.AlignLeft, "VOICE\nCLEANER")
    p.end()
    img.save(str(path), "BMP")


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841
    theme.load_fonts()
    OUT.mkdir(parents=True, exist_ok=True)
    dialog_bmp(OUT / "dialog.bmp")
    banner_bmp(OUT / "banner.bmp")
    print("wrote", OUT / "dialog.bmp", "and", OUT / "banner.bmp")


if __name__ == "__main__":
    main()
