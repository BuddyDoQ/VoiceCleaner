"""Render the application icon to assets/voicecleaner.ico (Windows build) and, on macOS,
assets/voicecleaner.icns (Mac build)."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.ui.brand import logo_pixmap as render_icon_pixmap  # noqa: E402


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 - needed for QPixmap
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    # Qt writes a multi-resolution .ico from the largest image; 256 px covers all shell sizes
    render_icon_pixmap(256).save(str(out / "voicecleaner.ico"), "ICO")
    render_icon_pixmap(256).save(str(out / "voicecleaner.png"), "PNG")
    print("wrote", out / "voicecleaner.ico")
    if sys.platform == "darwin":
        write_icns(out / "voicecleaner.icns")


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
