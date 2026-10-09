"""Build VoiceCleaner.app for macOS and package it as a DMG for a GitHub release.

Usage:  python tools/build_mac.py [--skip-tests] [--sign IDENTITY] [--notarize PROFILE] [--publish]
        python tools/build_mac.py --no-package      # just dist/mac/VoiceCleaner.app, for testing

Result (release/<version>/, uploaded as-is to the GitHub release v<version>):
    VoiceCleaner-<v>-macos-<arch>.dmg     drag-to-Applications disk image
    VoiceCleaner-<v>-macos-<arch>.zip     the .app, zipped (for scripted installs)
    SHA256SUMS-macos.txt

The app bundles the DeepFilterNet3 model in Contents/Resources/models. The 490 MB
voice re-synthesis model (BigVGAN) is downloaded on demand into
~/Library/Application Support/VoiceCleaner/models the first time it is used.

Without --sign the app is ad-hoc signed: it runs on any Mac, but Gatekeeper asks the
user to confirm on first launch (right-click > Open, or System Settings > Privacy &
Security > Open Anyway). With a "Developer ID Application" identity and a notarytool
keychain profile (xcrun notarytool store-credentials) it opens without warnings.

--publish uploads the files to the existing GitHub release v<version> (or creates it)
with the GitHub CLI (`gh auth login` first).
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils.config import APP_VERSION  # noqa: E402

REPO = "BuddyDoQ/VoiceCleaner"
TAG = f"v{APP_VERSION}"
ARCH = "arm64" if platform.machine() == "arm64" else "x86_64"
BUNDLED_MODELS = ["DeepFilterNet3"]  # everything else is downloaded on demand
APP = ROOT / "dist" / "mac" / "VoiceCleaner.app"
OUT = ROOT / "release" / APP_VERSION


def run(*cmd, cwd=ROOT):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def build(python: Path, skip_tests: bool) -> Path:
    if not skip_tests:
        run(python, "-m", "pytest", "-q")
    run(python, "tools/make_icon.py")
    run(python, "tools/download_models.py", "--no-resynthesis")  # no-op when present
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", APP.parent, "--workpath", ROOT / "build" / "pyi-mac", "VoiceCleaner.spec")
    shutil.rmtree(APP.parent / "VoiceCleaner", ignore_errors=True)  # one-folder copy, already inside the .app

    res = APP / "Contents" / "Resources"
    models_out = res / "models"
    shutil.rmtree(models_out, ignore_errors=True)
    models_out.mkdir()
    shutil.copy(ROOT / "models" / "README.md", models_out / "README.md")
    for name in BUNDLED_MODELS:
        shutil.copytree(ROOT / "models" / name, models_out / name,
                        ignore=shutil.ignore_patterns("*.log", "__pycache__"))
    (res / "edition.txt").write_text("standard", encoding="utf-8")

    lic = res / "LICENSES"
    lic.mkdir(exist_ok=True)
    shutil.copy(ROOT / "models" / "README.md", lic / "MODELS.md")
    shutil.copy(ROOT / "THIRD_PARTY_LICENSES.md", lic / "THIRD_PARTY_LICENSES.md")
    shutil.copy(ROOT / "LICENSE", lic / "VoiceCleaner-LICENSE.txt")
    shutil.copy(ROOT / "app" / "ai" / "vendor" / "bigvgan" / "LICENSE", lic / "BigVGAN-LICENSE.txt")
    for f in (ROOT / "app" / "ui" / "assets" / "fonts").glob("*-OFL.txt"):
        shutil.copy(f, lic / f.name)
    return APP


def sign(app: Path, identity: str | None):
    """Re-sign after adding models (adding files breaks PyInstaller's signature)."""
    if identity:
        run("codesign", "--force", "--deep", "--options", "runtime", "--timestamp", "--sign", identity, app)
    else:
        run("codesign", "--force", "--deep", "--sign", "-", app)
    run("codesign", "--verify", "--deep", "--strict", app)


def make_dmg(app: Path, dmg: Path):
    stage = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        dmg.unlink(missing_ok=True)
        run("hdiutil", "create", "-volname", f"VoiceCleaner {APP_VERSION}", "-srcfolder", stage,
            "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", dmg)
    finally:
        shutil.rmtree(stage)


def notarize(path: Path, profile: str):
    run("xcrun", "notarytool", "submit", path, "--keychain-profile", profile, "--wait")
    run("xcrun", "stapler", "staple", path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def publish(files: list[Path]):
    gh = shutil.which("gh")
    if not gh:
        sys.exit("GitHub CLI (gh) not found. Install it and run `gh auth login`.")
    exists = subprocess.run([gh, "release", "view", TAG, "--repo", REPO], capture_output=True).returncode == 0
    if not exists:
        run(gh, "release", "create", TAG, "--repo", REPO, "--title", f"VoiceCleaner {APP_VERSION}",
            "--notes-file", OUT / "RELEASE_NOTES-macos.md")
    run(gh, "release", "upload", TAG, "--repo", REPO, "--clobber", *files)


NOTES = """## macOS ({arch})

* **VoiceCleaner-{v}-macos-{arch}.dmg**: open it and drag VoiceCleaner to Applications.
* **VoiceCleaner-{v}-macos-{arch}.zip**: the same app, zipped.

Requires macOS 12 or newer on {chip}. Processing runs on the CPU. The 490 MB voice
re-synthesis model downloads the first time that feature is used; everything else works offline.

{gatekeeper}
Desktop-audio (loopback) recording is not available on macOS, because macOS has no system
loopback device. Microphone recording works; macOS asks for permission the first time.

Files: preferences in `~/Library/Application Support/VoiceCleaner`, logs in
`~/Library/Logs/VoiceCleaner`, sessions in `~/Documents/VoiceCleaner Sessions`.
"""
UNSIGNED = """**First launch:** this build is not notarized by Apple. Right-click VoiceCleaner in
Applications and choose **Open**, then **Open** again. (Or: System Settings > Privacy &
Security > *Open Anyway*.) You only need to do this once.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", type=Path, default=ROOT / ".venv" / "bin" / "python")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-build", action="store_true", help="package an existing dist/mac/VoiceCleaner.app")
    ap.add_argument("--sign", metavar="IDENTITY", help='e.g. "Developer ID Application: Name (TEAMID)"')
    ap.add_argument("--notarize", metavar="PROFILE", help="notarytool keychain profile (needs --sign)")
    ap.add_argument("--publish", action="store_true", help="upload to the GitHub release v<version>")
    ap.add_argument("--no-package", action="store_true", help="build and sign the .app only (no DMG/zip/release files)")
    a = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("build_mac.py runs on macOS only.")
    if a.notarize and not a.sign:
        sys.exit("--notarize needs --sign with a Developer ID identity.")

    app = APP if a.skip_build else build(a.python, a.skip_tests)
    sign(app, a.sign)
    if a.no_package:
        print(f"\nBuilt {app}")
        return
    if a.notarize:  # notarize the app itself so the zip is stapled too
        tmpzip = OUT / "notarize.zip"
        OUT.mkdir(parents=True, exist_ok=True)
        run("ditto", "-c", "-k", "--keepParent", app, tmpzip)
        notarize(tmpzip, a.notarize)
        tmpzip.unlink()

    OUT.mkdir(parents=True, exist_ok=True)
    base = f"VoiceCleaner-{APP_VERSION}-macos-{ARCH}"
    dmg, zipf = OUT / f"{base}.dmg", OUT / f"{base}.zip"
    make_dmg(app, dmg)
    if a.sign:
        run("codesign", "--force", "--timestamp", "--sign", a.sign, dmg)
    if a.notarize:
        notarize(dmg, a.notarize)
    zipf.unlink(missing_ok=True)
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, zipf)

    files = [dmg, zipf]
    sums = OUT / "SHA256SUMS-macos.txt"
    sums.write_text("".join(f"{sha256(f)}  {f.name}\n" for f in files), encoding="utf-8")
    chip = "Apple Silicon (M1 or later)" if ARCH == "arm64" else "an Intel Mac"
    (OUT / "RELEASE_NOTES-macos.md").write_text(
        NOTES.format(v=APP_VERSION, arch=ARCH, chip=chip, gatekeeper="" if a.notarize else UNSIGNED),
        encoding="utf-8")
    for f in files + [sums]:
        print(f"  {f.relative_to(ROOT)}  ({f.stat().st_size / 1e6:.0f} MB)")
    if a.publish:
        publish(files + [sums])


if __name__ == "__main__":
    main()
