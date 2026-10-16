"""Render the application icon to assets/voicecleaner.ico (Windows build) and, on macOS,
assets/voicecleaner.icns (Mac build)."""
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.ui.brand import logo_pixmap  # noqa: E402


def render_icon_pixmap(size: int):
    return logo_pixmap(size, dpr=1)  # exact pixel sizes for icon files


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 - needed for QPixmap
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    write_ico(out / "voicecleaner.ico")
    render_icon_pixmap(512).save(str(out / "voicecleaner.png"), "PNG")
    print("wrote", out / "voicecleaner.ico")
    if sys.platform == "darwin":
        write_icns(out / "voicecleaner.icns")


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _png_bytes(size: int) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    render_icon_pixmap(size).save(buf, "PNG")
    return bytes(ba.data())


def write_ico(path: Path):
    """A multi-size .ico with every size rendered from the vector logo, so Windows never
    has to shrink one large image itself (that is what made small icons look ragged).
    Each entry is a PNG, which Windows Vista and later read directly."""
    images = [(s, _png_bytes(s)) for s in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


# macOS app icons sit on a rounded-square tile (Apple's grid: an 824 px tile inset 100 px on a
# 1024 canvas, corner radius ~185 px). A bare shape is put in a grey tile by macOS 26 and later,
# and looks too big next to other apps. The Windows icon keeps the bare logo.
MAC_TILE = (100 / 1024, 824 / 1024, 185 / 1024)  # inset, size, corner radius (fractions)
MAC_LOGO = 0.62  # logo size as a fraction of the canvas, centred on the tile
MAC_LOGO_SMALL = 0.74  # at 16 and 32 px the logo fills more of the tile to stay legible
MAC_TILE_TOP, MAC_TILE_BOTTOM = "#22222f", "#07070e"  # the app's dark theme, lifted at the top


def mac_icon_pixmap(size: int):
    """The logo on a macOS-style dark tile, ``size`` px square."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    inset, side, radius = (f * size for f in MAC_TILE)
    tile = QRectF(inset, inset, side, side)
    for i in range(6):  # soft drop shadow below the tile
        sh = QPainterPath()
        grow = size * 0.004 * (i + 1)
        sh.addRoundedRect(tile.adjusted(-grow, -grow + size * 0.012, grow, grow + size * 0.012), radius + grow, radius + grow)
        p.fillPath(sh, QColor(0, 0, 0, 14))
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)
    grad = QLinearGradient(QPointF(0, tile.top()), QPointF(0, tile.bottom()))
    grad.setColorAt(0.0, QColor(MAC_TILE_TOP))
    grad.setColorAt(1.0, QColor(MAC_TILE_BOTTOM))
    p.fillPath(path, grad)
    p.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, size / 512)))  # faint edge, reads on dark docks
    p.drawPath(path)
    logo = max(1, round(size * (MAC_LOGO_SMALL if size <= 32 else MAC_LOGO)))  # list views: larger logo
    p.drawPixmap(round((size - logo) / 2), round((size - logo) / 2 + size * 0.01), render_icon_pixmap(logo))
    p.end()
    return pm


def write_icns(path: Path):
    """iconutil builds an .icns from an .iconset folder of fixed-name PNGs (macOS-style tile)."""
    iconset = Path(tempfile.mkdtemp()) / "voicecleaner.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        mac_icon_pixmap(size).save(str(iconset / f"icon_{size}x{size}.png"), "PNG")
        mac_icon_pixmap(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"), "PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    shutil.rmtree(iconset.parent)
    print("wrote", path)


if __name__ == "__main__":
    main()
