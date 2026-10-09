"""Render the application icon to assets/voicecleaner.ico (used by the Windows build)."""
import sys
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


if __name__ == "__main__":
    main()
