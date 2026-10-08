"""Downloading model files with SHA-256 verification (used by the app and tools/download_models.py).

Only official sources are listed. Files are written to ``*.part`` first and
only renamed into place once their checksum matches.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path


class DownloadError(Exception):
    pass


# Individual files (no archive): NVIDIA BigVGAN-v2 vocoder for voice re-synthesis (MIT).
FILE_MODELS = {
    "BigVGAN-v2-44k": {
        "size": 489_042_694,
        "files": {
            "config.json": ("https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x/resolve/main/config.json",
                            "65b7f487bfaf15256056a75f6667ef5f640214c981922a111927873705ea4ddb"),
            "bigvgan_generator.pt": (
                "https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x/resolve/main/bigvgan_generator.pt",
                "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357"),
        },
    },
}


def download_file_model(name: str, target: Path, progress=None, cancelled=None) -> Path:
    """Download a multi-file model with SHA-256 verification; ``progress(fraction)``."""
    spec = FILE_MODELS[name]
    dest = target / name
    dest.mkdir(parents=True, exist_ok=True)
    total, done = spec["size"], 0
    for fname, (url, sha) in spec["files"].items():
        out = dest / fname
        if out.is_file() and _sha256(out) == sha:
            done += out.stat().st_size
            continue
        part = out.with_suffix(out.suffix + ".part")
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
            while True:
                if cancelled and cancelled():
                    f.close()
                    part.unlink(missing_ok=True)
                    raise DownloadError("cancelled")
                block = r.read(1 << 20)
                if not block:
                    break
                f.write(block)
                h.update(block)
                done += len(block)
                if progress:
                    progress(min(1.0, done / total))
        if h.hexdigest() != sha:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{name}/{fname}: checksum mismatch; download discarded")
        part.replace(out)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


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
