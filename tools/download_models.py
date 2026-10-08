"""Download the AI model files into ./models.

Only official sources are used: DeepFilterNet3 comes from the author's GitHub
repository (Rikorose/DeepFilterNet, MIT/Apache-2.0). The archive is checked
against a known SHA-256 before it is unpacked.

Also downloads NVIDIA BigVGAN-v2 (MIT, ~490 MB) for voice re-synthesis
unless --no-resynthesis is given.

Usage:  python tools/download_models.py [target_dir] [--no-resynthesis]
"""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODELS = {
    "DeepFilterNet3": {
        "url": "https://github.com/Rikorose/DeepFilterNet/raw/main/models/DeepFilterNet3.zip",
        "sha256": "49c52edc8947ae1f9bf50d81530beaf3a2c3245aeaf34b6f31ff535cd22284d2",
        "files": ["config.ini", "checkpoints/model_120.ckpt.best"],
    },
}

from app.ai.model_download import FILE_MODELS, download_file_model  # noqa: E402


def download(name: str, spec: dict, target: Path) -> None:
    dest = target / name
    if all((dest / f).is_file() for f in spec["files"]):
        print(f"{name}: already installed at {dest}")
        return
    print(f"{name}: downloading {spec['url']}")
    with urllib.request.urlopen(spec["url"], timeout=60) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if spec["sha256"] and digest != spec["sha256"]:
        raise SystemExit(f"{name}: checksum mismatch ({digest}); refusing to install")
    print(f"{name}: sha256 {digest}")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            # guard against path traversal in archive entries
            out = (target / member).resolve()
            if not str(out).startswith(str(target.resolve())):
                raise SystemExit(f"unsafe path in archive: {member}")
        z.extractall(target)
    missing = [f for f in spec["files"] if not (dest / f).is_file()]
    if missing:
        raise SystemExit(f"{name}: archive incomplete, missing {missing}")
    print(f"{name}: installed to {dest}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = Path(args[0]) if args else ROOT / "models"
    target.mkdir(parents=True, exist_ok=True)
    for name, spec in MODELS.items():
        download(name, spec, target)
    if "--no-resynthesis" not in sys.argv:
        for name in FILE_MODELS:
            print(f"{name}: checking / downloading (about 490 MB)...")
            download_file_model(name, target, progress=lambda f: print(f"\r  {f:6.1%}", end="", flush=True))
            print(f"\n{name}: installed to {target / name}")


if __name__ == "__main__":
    main()
