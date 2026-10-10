"""The Steamburger logo is a true vector, and the Windows icon carries every shell size."""
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_logo_is_pure_vector():
    svg = (ROOT / "app" / "ui" / "assets" / "SteamburgerLogoIcon.svg").read_text(encoding="utf-8")
    assert "<image" not in svg and "base64" not in svg  # no embedded bitmaps
    assert svg.count("<path") >= 2


def test_logo_renders_at_any_size():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from app.ui.brand import logo_pixmap

    for size in (16, 48, 512):
        img = logo_pixmap(size, dpr=1).toImage()
        assert img.width() == size
        # opaque black burger in the middle, transparent corner
        assert img.pixelColor(size // 2, size * 3 // 8).alpha() > 200
        assert img.pixelColor(0, 0).alpha() == 0


def test_windows_icon_has_all_sizes():
    data = (ROOT / "assets" / "voicecleaner.ico").read_bytes()
    _, kind, count = struct.unpack("<HHH", data[:6])
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert kind == 1 and {16, 24, 32, 48, 256} <= sizes
