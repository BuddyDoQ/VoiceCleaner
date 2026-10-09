"""Check for Updates: version comparison, asset choice, scheduling, notes, errors (no network)."""
import io
import json
import urllib.error

import pytest

from app.utils import updates
from app.utils.updates import Asset, ReleaseInfo, UpdateError


def release(version="1.2.0", names=(), body=""):
    return ReleaseInfo(version=version, tag=f"v{version}", page_url="https://example/releases/tag/v" + version,
                       notes=body, assets=tuple(Asset(n, f"https://dl/{n}", 1_000_000) for n in names))


ALL_ASSETS = ("SHA256SUMS.txt", "vcgpu1.cab", "VoiceCleaner-1.2.0-win64-portable.zip", "VoiceCleaner-1.2.0-win64.msi",
              "VoiceCleaner-GPU-1.2.0-web.exe", "VoiceCleaner-GPU-1.2.0-win64.msi",
              "VoiceCleaner-1.2.0-macos-arm64.dmg", "VoiceCleaner-1.2.0-macos-arm64.zip", "SHA256SUMS-macos.txt")


@pytest.mark.parametrize("text, expected", [
    ("v1.0.1", (1, 0, 1)), ("1.0.1", (1, 0, 1)), ("1.0", (1,)), ("1.0.0", (1,)), ("v2.10.3-beta", (2, 10, 3)),
    ("", ()), ("latest", ()),
])
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("candidate, current, newer", [
    ("1.0.2", "1.0.1", True), ("1.1", "1.0.9", True), ("1.0.10", "1.0.9", True), ("2.0.0", "1.9.9", True),
    ("1.0.1", "1.0.1", False), ("1.0", "1.0.0", False), ("1.0.0", "1.0.1", False), ("garbage", "1.0.0", False),
])
def test_is_newer(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


@pytest.mark.parametrize("plat, arch, ed, expected", [
    ("darwin", "arm64", "standard", "VoiceCleaner-1.2.0-macos-arm64.dmg"),
    ("darwin", "x86_64", "standard", None),  # no Intel build published
    ("win32", "AMD64", "standard", "VoiceCleaner-1.2.0-win64.msi"),
    ("win32", "AMD64", "gpu", "VoiceCleaner-GPU-1.2.0-web.exe"),
    ("linux", "x86_64", "source", None),
])
def test_pick_asset(plat, arch, ed, expected):
    a = updates.pick_asset(release(names=ALL_ASSETS), plat, arch, ed)
    assert (a.name if a else None) == expected


def test_pick_asset_mac_falls_back_to_zip():
    a = updates.pick_asset(release(names=("VoiceCleaner-1.2.0-macos-arm64.zip",)), "darwin", "arm64", "standard")
    assert a.name.endswith(".zip")


def test_due_for_auto_check():
    day = updates.AUTO_CHECK_INTERVAL_S
    assert updates.due_for_auto_check(0, now=1_000_000)  # never checked
    assert not updates.due_for_auto_check(1_000_000, now=1_000_000 + day - 1)
    assert updates.due_for_auto_check(1_000_000, now=1_000_000 + day + 1)
    assert updates.due_for_auto_check(1_000_000 + 5 * day, now=1_000_000)  # clock moved back: check again


def test_whats_new_takes_only_that_section():
    body = ("## VoiceCleaner 1.2.0\r\n\r\n### What's new in 1.2.0\r\n\r\n- Faster export.\r\n- New tab.\r\n\r\n"
            "### Which download?\r\n\r\n| Edition | File |\r\n\r\n---\r\n\r\n## macOS (arm64)\r\n")
    assert updates.whats_new(body) == "- Faster export.\n- New tab."


def test_whats_new_without_section_stops_at_rule_and_truncates():
    assert updates.whats_new("Intro text.\n\n---\n\nInstall details") == "Intro text."
    long = "\n".join(f"- line {i}" for i in range(2000))
    out = updates.whats_new(long, max_chars=200)
    assert len(out) < 220 and out.endswith("…")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_latest_parses_github_answer(monkeypatch):
    payload = {"tag_name": "v1.2.0", "html_url": "https://github.com/x/releases/tag/v1.2.0", "body": "notes",
               "published_at": "2026-11-01T10:00:00Z",
               "assets": [{"name": "VoiceCleaner-1.2.0-macos-arm64.dmg", "size": 5,
                           "browser_download_url": "https://dl/mac.dmg"}]}
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    r = updates.fetch_latest()
    assert seen["url"] == updates.LATEST_URL and seen["ua"].startswith("VoiceCleaner/")
    assert (r.version, r.tag, r.published) == ("1.2.0", "v1.2.0", "2026-11-01")
    assert r.assets[0] == Asset("VoiceCleaner-1.2.0-macos-arm64.dmg", "https://dl/mac.dmg", 5)


@pytest.mark.parametrize("error, text", [
    (urllib.error.HTTPError(updates.LATEST_URL, 404, "Not Found", {}, None), "No published release"),
    (urllib.error.HTTPError(updates.LATEST_URL, 403, "rate limited", {}, None), "limiting requests"),
    (urllib.error.HTTPError(updates.LATEST_URL, 500, "boom", {}, None), "HTTP 500"),
    (urllib.error.URLError("no route"), "internet connection"),
    (TimeoutError(), "internet connection"),
])
def test_fetch_latest_errors_are_friendly(monkeypatch, error, text):
    def fake_urlopen(req, timeout):
        raise error

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(UpdateError, match=text):
        updates.fetch_latest()


def test_fetch_latest_rejects_unexpected_json(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: _Resp(b"<html>"))
    with pytest.raises(UpdateError, match="unexpected"):
        updates.fetch_latest()
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: _Resp(b"{}"))
    with pytest.raises(UpdateError, match="unexpected"):
        updates.fetch_latest()


def test_update_dialog_offers_this_computers_download(monkeypatch, request):
    import os

    import shiboken6

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from app.ui.update_dialog import UpdateDialog

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(updates, "edition", lambda: "standard")
    dlg = UpdateDialog(release(names=ALL_ASSETS, body="### What's new\n\n- Something."), auto_check=False)
    request.addfinalizer(lambda: shiboken6.delete(dlg))  # parentless: destroy before Qt shuts down
    texts = " ".join(w.text() for w in dlg.findChildren(QLabel))
    assert "VoiceCleaner 1.2.0 is available" in texts
    assert dlg.auto_box.isChecked() is False
    expected = updates.pick_asset(dlg.release)
    assert dlg.asset == expected
    if expected:  # this computer has a published build: the hint names the file
        assert expected.name in texts
    else:
        assert "release page" in texts
