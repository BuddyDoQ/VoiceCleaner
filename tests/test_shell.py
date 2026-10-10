"""Platform helpers: shortcut notation."""
import pytest

from app.utils import shell


@pytest.mark.parametrize("keys, mac", [
    ("Ctrl+E", "⌘E"), ("Ctrl+Shift+N", "⇧⌘N"), ("Shift+Ctrl+N", "⇧⌘N"), ("Ctrl+Enter", "⌘↩"),
    ("Ctrl+/", "⌘/"), ("Ctrl+Up", "⌘↑"), ("Ctrl+mouse wheel", "⌘ + scroll"), ("Space", "Space"), ("A", "A"),
])
def test_shortcut_text(monkeypatch, keys, mac):
    monkeypatch.setattr(shell.sys, "platform", "darwin")
    assert shell.shortcut_text(keys) == mac
    monkeypatch.setattr(shell.sys, "platform", "win32")
    assert shell.shortcut_text(keys) == keys  # Windows text is unchanged
