"""Fetch the LGPL FFmpeg build that VoiceCleaner ships for opening M4A, AAC, WMA and video
files (Windows). It runs as a separate program (ffmpeg.exe), so VoiceCleaner stays MIT.

* The build: BtbN/FFmpeg-Builds, "lgpl-shared" (no GPL components), pinned to one dated
  release and checked against its SHA-256; it is also checked to be an LGPL build
  (no --enable-gpl / --enable-nonfree).
* Only ffmpeg.exe and the libraries it needs are kept (no ffplay/ffprobe), plus the licence.
* ``--source`` also downloads the matching FFmpeg source code, which is published with each
  release (LGPL: the source of what we ship is available from the same place).

Downloads are cached in ``third_party/`` (not in git).

Usage: python tools/fetch_ffmpeg.py <target_dir> [--source <dir>]
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "third_party"

RELEASE = "autobuild-2026-10-14-13-03"
ZIP = "ffmpeg-n8.1.3-18-g46d899d7a0-win64-lgpl-shared-8.1.zip"
ZIP_SHA256 = "38dd3fb973c2e10723489e2173135d60f569953e70a04b885446175f9e9380d6"
URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{RELEASE}/{ZIP}"
VERSION = "n8.1.3-18-g46d899d7a0"
COMMIT = "46d899d7a0"
SOURCE_URL = f"https://github.com/FFmpeg/FFmpeg/archive/{COMMIT}.tar.gz"
SOURCE_NAME = f"ffmpeg-source-{VERSION}.tar.gz"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path) -> Path:
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    part.replace(dest)
    return dest


def fetch(target: Path) -> Path:
    """FFmpeg for Windows in ``target`` (ffmpeg.exe, its DLLs, LICENSE.txt). Returns ffmpeg.exe."""
    z = _download(URL, CACHE / ZIP)
    digest = _sha256(z)
    if digest != ZIP_SHA256:
        z.unlink()
        raise SystemExit(f"{ZIP}: checksum mismatch ({digest}); download discarded")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(z) as zf:
        for name in zf.namelist():
            base = Path(name).name
            keep = ("/bin/" in name and (base == "ffmpeg.exe" or base.endswith(".dll"))) or base == "LICENSE.txt"
            if keep and not name.endswith("/"):
                with zf.open(name) as src, open(target / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    exe = target / "ffmpeg.exe"
    out = subprocess.run([str(exe), "-hide_banner", "-version"], capture_output=True, text=True).stdout
    config = next((ln for ln in out.splitlines() if ln.startswith("configuration:")), "")
    if "--enable-gpl" in config or "--enable-nonfree" in config:
        raise SystemExit("This FFmpeg build is not LGPL: refusing to ship it")
    (target / "README.txt").write_text(
        f"FFmpeg {VERSION} (LGPL build by BtbN/FFmpeg-Builds, {RELEASE}).\n"
        "VoiceCleaner runs it as a separate program to read M4A, AAC, WMA and video files.\n"
        "FFmpeg is licensed under the GNU Lesser General Public License (LICENSE.txt).\n"
        f"Source code: {SOURCE_NAME} on the VoiceCleaner release page, or\n"
        f"https://github.com/FFmpeg/FFmpeg/tree/{COMMIT}\n"
        "Build scripts: https://github.com/BtbN/FFmpeg-Builds\n", encoding="utf-8")
    print(f"FFmpeg {VERSION} (LGPL) in {target}  sha256 {digest}")
    return exe


def fetch_source(dest_dir: Path) -> Path:
    src = _download(SOURCE_URL, CACHE / SOURCE_NAME)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / SOURCE_NAME
    shutil.copy2(src, out)
    return out


if __name__ == "__main__":
    fetch(Path(sys.argv[1]))
    if "--source" in sys.argv:
        print(fetch_source(Path(sys.argv[sys.argv.index("--source") + 1])))
