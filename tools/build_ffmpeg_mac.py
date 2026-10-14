"""Build the LGPL FFmpeg that the Mac app ships for opening M4A, AAC, WMA, AMR and video files.

There is no LGPL macOS build like BtbN's for Windows, so it is compiled here from the SAME
source the Windows build comes from (tools/fetch_ffmpeg.py pins it). That source tarball is
attached to every release (``ffmpeg-source-*.tar.gz``), so it covers the Mac binary too.

* Checked against the SHA-256 of the tarball published with the releases.
* LGPL only: no --enable-gpl / --enable-nonfree, and no external libraries
  (--disable-autodetect): FFmpeg's own decoders read every format VoiceCleaner opens.
* A single static ``ffmpeg`` program (no ffplay/ffprobe, no network), macOS 12 or newer,
  run as a separate program, so VoiceCleaner stays MIT.

Built once and cached in third_party/ (not in git); a few minutes on Apple Silicon.

Usage: python tools/build_ffmpeg_mac.py [target_dir]     (prints the path of the program)
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fetch_ffmpeg import CACHE, SOURCE_NAME, SOURCE_URL, VERSION, _download  # noqa: E402

# ffmpeg-source-n8.1.3-18-g46d899d7a0.tar.gz as attached to the v1.1.4 release
SOURCE_SHA256 = "6626e1f622324597146a8cc90a89bb4c3529f7df55f0a8c998fde55ac5ae4161"
MIN_MACOS = "12.0"
ARCH = platform.machine()  # arm64 or x86_64
BUILD = CACHE / f"ffmpeg-mac-{VERSION}-{ARCH}"
PROGRAM = BUILD / "install" / "bin" / "ffmpeg"

CONFIGURE = [
    "--disable-gpl", "--disable-nonfree", "--disable-version3",  # plain LGPL-2.1+
    "--disable-autodetect",  # no system or third-party libraries: FFmpeg's own code only
    "--disable-network", "--disable-doc", "--disable-debug",
    "--disable-ffplay", "--disable-ffprobe",  # avdevice kept (lavfi), as in the Windows build
    "--enable-static", "--disable-shared",
    f"--extra-cflags=-mmacosx-version-min={MIN_MACOS}", f"--extra-ldflags=-mmacosx-version-min={MIN_MACOS}",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _run(*cmd, cwd: Path, env=None):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, env=env)


def build() -> Path:
    if sys.platform != "darwin":
        sys.exit("build_ffmpeg_mac.py runs on macOS only.")
    if PROGRAM.is_file():
        return PROGRAM
    tarball = _download(SOURCE_URL, CACHE / SOURCE_NAME)
    if _sha256(tarball) != SOURCE_SHA256:
        tarball.unlink()
        sys.exit(f"{SOURCE_NAME}: checksum mismatch, download discarded")
    src = BUILD / "src"
    shutil.rmtree(BUILD, ignore_errors=True)
    src.mkdir(parents=True)
    with tarfile.open(tarball) as t:
        top = t.getmembers()[0].name.split("/")[0]
        t.extractall(BUILD, filter="data")
    (BUILD / top).rename(src) if (BUILD / top) != src else None
    # FFmpeg names itself after `git describe`: inside this repository that would be
    # VoiceCleaner's tag. A VERSION file (as in FFmpeg's release tarballs) names it instead.
    (src / "VERSION").write_text(VERSION + "\n")
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(BUILD))
    _run("./configure", f"--prefix={BUILD / 'install'}", *CONFIGURE, cwd=src, env=env)
    _run("make", f"-j{os.cpu_count() or 4}", cwd=src, env=env)
    _run("make", "install", cwd=src, env=env)
    check(PROGRAM)
    return PROGRAM


def check(program: Path) -> None:
    """Refuse a build that is not plain LGPL or depends on non-system libraries."""
    version = subprocess.run([str(program), "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    if VERSION not in version:
        sys.exit(f"FFmpeg names the wrong version: {version}")
    conf = subprocess.run([str(program), "-hide_banner", "-buildconf"], capture_output=True, text=True).stdout
    for bad in ("--enable-gpl", "--enable-nonfree", "--enable-version3"):
        if bad in conf:
            sys.exit(f"FFmpeg build is not plain LGPL ({bad})")
    libs = subprocess.run(["otool", "-L", str(program)], capture_output=True, text=True).stdout.splitlines()[1:]
    foreign = [l.strip() for l in libs if not l.strip().startswith(("/usr/lib/", "/System/"))]
    if foreign:
        sys.exit(f"FFmpeg links libraries that are not part of macOS: {foreign}")


def install(target: Path) -> Path:
    """Copy the program and its licence into ``target`` (VoiceCleaner.app/Contents/Resources/ffmpeg)."""
    program = build()
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(program, target / "ffmpeg")
    src = BUILD / "src"
    shutil.copy2(src / "COPYING.LGPLv2.1", target / "LICENSE.txt")
    (target / "README.txt").write_text(
        f"FFmpeg {VERSION} (LGPL-2.1-or-later), built for VoiceCleaner on macOS from the unmodified\n"
        f"source attached to each VoiceCleaner release as {SOURCE_NAME}\n"
        f"(a VERSION file naming {VERSION} is added for the build, as in FFmpeg's release tarballs)\n"
        f"(also at {SOURCE_URL}).\n\nconfigure {' '.join(CONFIGURE)}\n", encoding="utf-8")
    return target / "ffmpeg"


if __name__ == "__main__":
    out = install(Path(sys.argv[1])) if len(sys.argv) > 1 else build()
    print(out)
