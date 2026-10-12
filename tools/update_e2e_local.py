"""Try the real in-app update on this PC, before publishing a version.

Serves a locally built installer as a pretend newer release (99.0.0) and starts the
INSTALLED VoiceCleaner pointed at it. In the app: Check for Updates > Install Update >
Install and Restart > allow Windows' permission prompt. The installer reinstalls the
local build (same version, which the MSI allows), and VoiceCleaner starts again and
reports the result ("the installer finished" - the version stays the same, as expected).

Usage (after installing release/<version>/VoiceCleaner-<version>-win64.msi):
    python tools/update_e2e_local.py [path to the .msi to serve]
Stop with Ctrl+C when done.
"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.utils.config import APP_VERSION  # noqa: E402

msi = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "release" / APP_VERSION / f"VoiceCleaner-{APP_VERSION}-win64.msi"
if not msi.exists():
    sys.exit(f"Installer not found: {msi}")
exe = Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Steamburger Studios" / "VoiceCleaner" / "VoiceCleaner.exe"
if not exe.exists():
    sys.exit(f"Install {msi.name} first (VoiceCleaner.exe not found at {exe}).")

data = msi.read_bytes()
NAME = "VoiceCleaner-99.0.0-win64.msi"


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("  served", self.path)

    def do_GET(self):
        body = FEED if self.path == "/latest.json" else data if self.path == "/" + NAME else None
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
base = f"http://127.0.0.1:{httpd.server_port}"
FEED = json.dumps({
    "tag_name": "v99.0.0", "html_url": base + "/", "published_at": "2026-12-01T00:00:00Z",
    "body": f"### What's new in 99.0.0\n\n- Local update test: reinstalls {msi.name}.\n",
    "assets": [{"name": NAME, "browser_download_url": f"{base}/{NAME}", "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest()}],
}).encode()
threading.Thread(target=httpd.serve_forever, daemon=True).start()
env = dict(os.environ, VOICECLEANER_UPDATE_FEED=f"{base}/latest.json")
print(f"Serving {msi.name} as version 99.0.0 at {base}")
print(f"Starting {exe} ... use Check for Updates in its menu (\u22ef). Ctrl+C here when done.")
subprocess.Popen([str(exe)], env=env)
try:
    threading.Event().wait()
except KeyboardInterrupt:
    httpd.shutdown()
