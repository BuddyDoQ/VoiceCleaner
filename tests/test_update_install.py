"""In-app updates: choosing how to update, verified (and resumed) downloads, and the helper
that installs after VoiceCleaner has closed."""
import hashlib
import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.utils import update_install as ui
from app.utils import updates


def _release(assets, version="9.9.9"):
    return updates.ReleaseInfo(version=version, tag=f"v{version}", page_url="https://github.com/x", assets=tuple(assets))


def test_digest_is_read_from_the_api():
    r = updates.parse_release({"tag_name": "v2.0", "assets": [
        {"name": "VoiceCleaner-2.0-win64.msi", "browser_download_url": "u", "size": 5, "digest": "sha256:" + "ab" * 32},
        {"name": "x", "browser_download_url": "u"}]})
    assert r.assets[0].sha256 == "ab" * 32 and r.assets[1].sha256 == ""


def test_plan_per_computer(tmp_path):
    msi = updates.Asset("VoiceCleaner-9.9.9-win64.msi", "https://github.com/a")
    web = updates.Asset("VoiceCleaner-GPU-9.9.9-web.exe", "https://github.com/b")
    dmg = updates.Asset("VoiceCleaner-9.9.9-macos-arm64.dmg", "https://github.com/c")
    rel = _release([msi, web, dmg])
    with pytest.MonkeyPatch.context() as m:
        m.setattr(updates, "edition", lambda: "standard")
        p = ui.plan(rel, plat="win32", installed_dir=tmp_path)
        assert (p.mode, p.asset.name, p.install_dir, p.installs) == ("msi", msi.name, tmp_path, True)
        assert ui.plan(rel, plat="win32", installed_dir=None).mode == "browser"  # portable copy
        m.setattr(updates, "edition", lambda: "gpu")
        assert ui.plan(rel, plat="win32", installed_dir=tmp_path).mode == "bundle"
    with pytest.MonkeyPatch.context() as m:
        m.setattr(updates.platform, "machine", lambda: "arm64")
        assert ui.plan(rel, plat="darwin", installed_dir=None).mode == "show"
    assert ui.plan(_release([]), plat="win32", installed_dir=tmp_path).mode == "browser"


# ---------------------------------------------------------------- downloads from a local server
class _Server:
    def __init__(self, files: dict):
        self.files, self.requests = files, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = outer.files.get(self.path)
                outer.requests.append((self.path, self.headers.get("Range")))
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                start = 0
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("VOICECLEANER_UPDATE_FEED", "http://127.0.0.1/test")  # allows the local host
    payload = os.urandom(700_000)
    s = _Server({"/f.msi": payload,
                 "/SHA256SUMS.txt": f"{hashlib.sha256(payload).hexdigest()} *f.msi\n".encode()})
    s.payload = payload
    yield s
    s.close()


def test_download_verifies_and_resumes(server, tmp_path):
    sha = hashlib.sha256(server.payload).hexdigest()
    asset = updates.Asset("f.msi", server.base + "/f.msi", len(server.payload), sha)
    rel = _release([asset])
    # a partial download from before is continued, not restarted
    (tmp_path / "f.msi.part").write_bytes(server.payload[:300_000])
    seen = []
    out = ui.download(rel, asset, progress=lambda d, t: seen.append((d, t)), folder=tmp_path)
    assert out.read_bytes() == server.payload and not (tmp_path / "f.msi.part").exists()
    assert server.requests[-1] == ("/f.msi", "bytes=300000-")
    assert seen[-1] == (len(server.payload), len(server.payload))
    # already downloaded and valid: no new request
    n = len(server.requests)
    assert ui.download(rel, asset, folder=tmp_path) == out and len(server.requests) == n


def test_checksum_from_sha256sums_and_mismatch_is_deleted(server, tmp_path):
    asset = updates.Asset("f.msi", server.base + "/f.msi", len(server.payload))  # no digest in the API
    sums = updates.Asset("SHA256SUMS.txt", server.base + "/SHA256SUMS.txt")
    rel = _release([asset, sums])
    assert ui.expected_sha256(rel, asset) == hashlib.sha256(server.payload).hexdigest()
    bad = updates.Asset("f.msi", server.base + "/f.msi", len(server.payload), "0" * 64)
    with pytest.raises(updates.UpdateError, match="does not match"):
        ui.download(_release([bad]), bad, folder=tmp_path / "bad")
    assert not any((tmp_path / "bad").iterdir())  # nothing unverified is left behind
    with pytest.raises(updates.UpdateError, match="checksum"):
        ui.download(_release([asset]), asset, folder=tmp_path / "none")  # no checksum at all


def test_download_can_be_cancelled(server, tmp_path):
    sha = hashlib.sha256(server.payload).hexdigest()
    asset = updates.Asset("f.msi", server.base + "/f.msi", len(server.payload), sha)
    with pytest.raises(updates.UpdateError, match="cancelled"):
        ui.download(_release([asset]), asset, cancelled=lambda: True, folder=tmp_path)


def test_other_hosts_are_refused(monkeypatch):
    monkeypatch.delenv("VOICECLEANER_UPDATE_FEED", raising=False)
    with pytest.raises(updates.UpdateError, match="unexpected site"):
        ui._check_host("https://evil.example.com/f.msi")
    ui._check_host("https://github.com/BuddyDoQ/VoiceCleaner/releases/download/v1/f.msi")
    ui._check_host("https://objects.githubusercontent.com/x")


def test_outcome_messages(monkeypatch):
    monkeypatch.setattr(ui, "APP_VERSION", "2.0")
    assert "updated to version 2.0" in ui.InstallOutcome(0, "2.0").message()
    assert ui.InstallOutcome(3010, "2.0").ok
    assert "declined" in ui.InstallOutcome(1602, "2.0").message()
    assert "error 1603" in ui.InstallOutcome(1603, "2.0").message()


# ---------------------------------------------------------------- the install helper, end to end
FAKE_SRC = r"""
using System; using System.IO;
class P { static int Main(string[] a) {
  File.AppendAllText(Environment.GetEnvironmentVariable("FAKE_OUT"),
                     Path.GetFileName(Environment.GetCommandLineArgs()[0]) + "|" + Environment.CommandLine + "\n");
  return int.Parse(Environment.GetEnvironmentVariable("FAKE_CODE") ?? "0"); } }
"""


@pytest.fixture(scope="module")
def fake_exe(tmp_path_factory):
    """A tiny program that records how it was started (stands in for msiexec, the web
    installer and VoiceCleaner itself)."""
    if sys.platform != "win32":
        pytest.skip("Windows helper")
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not csc.exists():
        pytest.skip("no C# compiler to build the stand-in installer")
    d = tmp_path_factory.mktemp("fake")
    (d / "fake.cs").write_text(FAKE_SRC)
    subprocess.run([str(csc), "/nologo", f"/out:{d / 'fake.exe'}", str(d / "fake.cs")], check=True,
                   capture_output=True)
    return d / "fake.exe"


@pytest.mark.parametrize("mode,code", [("msi", 0), ("bundle", 1602)])
def test_helper_waits_installs_reports_and_relaunches(fake_exe, tmp_path, monkeypatch, mode, code):
    import shutil

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    install_dir = tmp_path / "Program Files" / "Steamburger Studios" / "VoiceCleaner"  # spaces on purpose
    install_dir.mkdir(parents=True)
    app_exe = install_dir / "VoiceCleaner.exe"
    shutil.copy(fake_exe, app_exe)
    folder = ui.updates_dir() / "9.9.9"
    folder.mkdir(parents=True)
    installer = folder / ("VoiceCleaner-9.9.9-win64.msi" if mode == "msi" else "VoiceCleaner-GPU-9.9.9-web.exe")
    if mode == "bundle":
        shutil.copy(fake_exe, installer)
    else:
        installer.write_bytes(b"msi")
    out = tmp_path / "calls.txt"
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("FAKE_CODE", str(code))
    # "VoiceCleaner" still running for a moment: the helper must wait for it
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    plan = ui.UpdatePlan(updates.Asset(installer.name, "u"), mode, install_dir)
    t0 = time.time()
    ui.launch_installer(plan, installer, "9.9.9", relaunch=True, app_pid=app.pid, msiexec=str(fake_exe))
    deadline = time.time() + 60
    while time.time() < deadline and not (out.exists() and len(out.read_text().splitlines()) >= 2):
        time.sleep(0.2)
    app.wait()
    calls = out.read_text().splitlines()
    installer_call, relaunch_call = calls[0], calls[1]
    assert time.time() - t0 >= 2.5  # waited for the app to exit
    assert f'"{installer}"' in installer_call
    if mode == "msi":
        assert installer_call.startswith("fake.exe|") and "/i" in installer_call and "/passive" in installer_call
        assert f'INSTALLFOLDER="{install_dir}"' in installer_call
    else:
        assert "/passive" in installer_call and f'InstallFolder="{install_dir}"' in installer_call
    assert relaunch_call.startswith("VoiceCleaner.exe|")  # started again afterwards
    outcome = ui.take_last_result()
    assert (outcome.code, outcome.version) == (code, "9.9.9")
    assert ui.take_last_result() is None  # reported once
