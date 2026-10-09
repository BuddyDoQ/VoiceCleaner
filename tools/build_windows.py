"""Build dist/VoiceCleaner/VoiceCleaner.exe and place the models beside it.

Usage:  python tools/build_windows.py [--skip-tests]

Result:
    dist/VoiceCleaner/
        VoiceCleaner.exe
        _internal/            Python, Qt, PyTorch, ...
        models/               AI models (replaceable without rebuilding)
            README.md
            DeepFilterNet3/
        LICENSES/             third-party license notes
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "VoiceCleaner"


def run(*cmd: str):
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main():
    py = sys.executable
    if "--skip-tests" not in sys.argv:
        run(py, "-m", "pytest", "-q")
    run(py, "tools/make_icon.py")
    run(py, "tools/download_models.py")  # no-op when already present
    run(py, "-m", "PyInstaller", "--noconfirm", "--clean", "VoiceCleaner.spec")

    models_out = DIST / "models"
    if models_out.exists():
        shutil.rmtree(models_out)
    shutil.copytree(ROOT / "models", models_out, ignore=shutil.ignore_patterns("*.log", "__pycache__"))

    lic = DIST / "LICENSES"
    lic.mkdir(exist_ok=True)
    shutil.copy(ROOT / "models" / "README.md", lic / "MODELS.md")
    shutil.copy(ROOT / "THIRD_PARTY_LICENSES.md", lic / "THIRD_PARTY_LICENSES.md")
    shutil.copy(ROOT / "app" / "ai" / "vendor" / "bigvgan" / "LICENSE", lic / "BigVGAN-LICENSE.txt")
    for f in (ROOT / "app" / "ui" / "assets" / "fonts").glob("*-OFL.txt"):
        shutil.copy(f, lic / f.name)
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\nBuilt {DIST / 'VoiceCleaner.exe'}  ({size / 1e9:.2f} GB total)")


if __name__ == "__main__":
    main()
