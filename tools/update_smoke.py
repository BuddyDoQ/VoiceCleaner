"""Drive the in-app updater against a fake release served locally: check, Install Update,
download + checksum, "Install When I Quit", then quit (the installer launch is recorded,
not performed). Screenshots of each state.

Usage: python tools/update_smoke.py <screenshot_dir>
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="vc_appdata_")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="vc_local_")
os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="vc_profile_")
os.environ["HOME"] = os.environ["USERPROFILE"]
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"  # the check is started by hand below

PAYLOAD = os.urandom(3_000_000)
FILES = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = FILES.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        for i in range(0, len(data), 64 * 1024):  # slow enough to see the progress
            self.wfile.write(data[i: i + 64 * 1024])
            time.sleep(0.02)


httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_port}"
os.environ["VOICECLEANER_UPDATE_FEED"] = BASE + "/latest.json"

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import theme  # noqa: E402
from app.utils import update_install, updates  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

name = "VoiceCleaner-GPU-99.0.0-web.exe" if updates.edition() == "gpu" else "VoiceCleaner-99.0.0-win64.msi"
if sys.platform == "darwin":
    name = "VoiceCleaner-99.0.0-macos-arm64.dmg"
FILES["/" + name] = PAYLOAD
FILES["/latest.json"] = json.dumps({
    "tag_name": "v99.0.0", "html_url": BASE + "/release", "published_at": "2026-12-01T00:00:00Z",
    "body": "### What's new in 99.0.0\n\n- **Test release** served locally by tools/update_smoke.py.\n",
    "assets": [{"name": name, "browser_download_url": BASE + "/" + name, "size": len(PAYLOAD),
                "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}],
}).encode()


def pump(app, cond, timeout=60):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(10):
        app.processEvents()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    # pretend to be an installed copy, so the in-place install path is used
    fake_install = Path(tempfile.mkdtemp(prefix="vc_install_"))
    update_install.install_dir = lambda: fake_install if sys.platform == "win32" else None
    launched = []
    update_install.launch_installer = lambda *a, **k: launched.append((a, k))

    from app.ui.main_window import MainWindow
    from app.ui.update_dialog import UpdateDialog

    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    w = MainWindow(UserConfig.load())
    w.resize(1360, 880)
    w.show()
    pump(app, lambda: w._mm_ready.is_set(), 120)
    w.check_for_updates(manual=True)
    pump(app, lambda: any(isinstance(x, UpdateDialog) and x.isVisible() for x in app.topLevelWidgets()), 30)
    dlg = next(x for x in app.topLevelWidgets() if isinstance(x, UpdateDialog))
    print("plan:", dlg.plan.mode, dlg.plan.asset.name)
    check("Install Update offered", dlg.action_btn.text() in ("Install Update", "Download"))
    dlg.grab().save(str(out / "1_update_available.png"))
    dlg.action_btn.click()
    pump(app, lambda: dlg.bar.value() > 100, 30)
    dlg.grab().save(str(out / "2_downloading.png"))
    check("progress shows MB and time", "MB" in dlg.state.text())
    pump(app, lambda: dlg.installer is not None, 60)
    check("downloaded and verified", dlg.installer.read_bytes() == PAYLOAD and "verified" in dlg.state.text())
    dlg.grab().save(str(out / "3_ready.png"))
    if dlg.plan.installs:
        check("install buttons shown", dlg.now_btn.isVisible() and dlg.on_quit_btn.isVisible())
        dlg.on_quit_btn.click()
        pump(app, lambda: not dlg.isVisible(), 10)
        check("install-on-quit noted", "when you quit" in w.status_label.text())
        w.close()
        pump(app, lambda: bool(launched), 10)
        a, k = launched[0]
        check(f"installer started on quit ({a[0].mode}, relaunch={a[3]})", a[1] == dlg.installer and a[3] is False)
    else:
        w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    httpd.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
