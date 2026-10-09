"""Build one edition of VoiceCleaner.exe and place the bundled models beside it.

Usage:  python tools/build_windows.py [--edition standard|gpu] [--python PATH] [--skip-tests]

Editions (same source code, different PyTorch build):
    gpu       NVIDIA CUDA PyTorch (.venv):     dist/gpu/VoiceCleaner/       ~4.6 GB
    standard  CPU-only PyTorch (.venv-cpu):    dist/standard/VoiceCleaner/  ~0.7 GB

Result:
    dist/<edition>/VoiceCleaner/
        VoiceCleaner.exe
        edition.txt           "standard" or "gpu" (read by the app)
        _internal/            Python, Qt, PyTorch, ...
        models/               bundled AI model (DeepFilterNet3, 9 MB)
        LICENSES/             third-party license notes

The 490 MB voice re-synthesis model (BigVGAN) is not bundled: the app downloads it
on demand into %LOCALAPPDATA%\\VoiceCleaner\\models the first time it is needed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = {"gpu": ROOT / ".venv" / "Scripts" / "python.exe",
                  "standard": ROOT / ".venv-cpu" / "Scripts" / "python.exe"}
BUNDLED_MODELS = ["DeepFilterNet3"]  # everything else is downloaded on demand


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)


def dist_dir(edition: str) -> Path:
    return ROOT / "dist" / edition / "VoiceCleaner"


def build(edition: str, python: Path, skip_tests: bool = False) -> Path:
    dist = dist_dir(edition)
    if not skip_tests:
        run(python, "-m", "pytest", "-q")
    run(python, "tools/make_icon.py")
    run(python, "tools/download_models.py", "--no-resynthesis")  # no-op when present
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", dist.parent, "--workpath", ROOT / "build" / f"pyi-{edition}", "VoiceCleaner.spec")

    models_out = dist / "models"
    if models_out.exists():
        shutil.rmtree(models_out)
    models_out.mkdir()
    shutil.copy(ROOT / "models" / "README.md", models_out / "README.md")
    for name in BUNDLED_MODELS:
        shutil.copytree(ROOT / "models" / name, models_out / name,
                        ignore=shutil.ignore_patterns("*.log", "__pycache__"))
    (dist / "edition.txt").write_text(edition, encoding="utf-8")

    lic = dist / "LICENSES"
    lic.mkdir(exist_ok=True)
    shutil.copy(ROOT / "models" / "README.md", lic / "MODELS.md")
    shutil.copy(ROOT / "THIRD_PARTY_LICENSES.md", lic / "THIRD_PARTY_LICENSES.md")
    shutil.copy(ROOT / "LICENSE", lic / "VoiceCleaner-LICENSE.txt")
    shutil.copy(ROOT / "app" / "ai" / "vendor" / "bigvgan" / "LICENSE", lic / "BigVGAN-LICENSE.txt")
    for f in (ROOT / "app" / "ui" / "assets" / "fonts").glob("*-OFL.txt"):
        shutil.copy(f, lic / f.name)
    size = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file())
    print(f"\nBuilt {edition} edition: {dist / 'VoiceCleaner.exe'}  ({size / 1e9:.2f} GB total)", flush=True)
    return dist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=["standard", "gpu"], default="gpu")
    ap.add_argument("--python", type=Path, default=None)
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    python = a.python or DEFAULT_PYTHON[a.edition]
    if not python.exists():
        sys.exit(f"Python for the {a.edition} edition not found: {python}")
    build(a.edition, python, a.skip_tests)


if __name__ == "__main__":
    main()
