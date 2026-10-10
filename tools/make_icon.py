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


def write_icns(path: Path):
    """iconutil builds an .icns from an .iconset folder of fixed-name PNGs."""
    iconset = Path(tempfile.mkdtemp()) / "voicecleaner.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        render_icon_pixmap(size).save(str(iconset / f"icon_{size}x{size}.png"), "PNG")
        render_icon_pixmap(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"), "PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    shutil.rmtree(iconset.parent)
    print("wrote", path)


if __name__ == "__main__":
    main()
