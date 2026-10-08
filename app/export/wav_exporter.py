"""WAV export.

* 16-bit (with TPDF dither), 24-bit or 32-bit float
* optional sample-rate conversion (polyphase, high quality)
* written to a temporary file first and then renamed, so a failed export
  never leaves a half-written file behind
* refuses to overwrite the source recording
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from ..utils.errors import ExportError
from ..utils.logging import get_logger

log = get_logger("export")

BIT_DEPTHS = {"16": "PCM_16", "24": "PCM_24", "32f": "FLOAT"}
BIT_DEPTH_LABELS = {"16": "16-bit", "24": "24-bit", "32f": "32-bit float"}
WRITE_BLOCK = 1 << 19
RNG = np.random.default_rng()


@dataclass
class ExportOptions:
    bit_depth: str = "24"
    sample_rate: int = 0  # 0 = same as source


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


def suggest_output_path(source: Path, directory: Path | None = None, suffix: str = "_enhanced", ext: str = ".wav") -> Path:
    directory = directory or source.parent
    base = directory / f"{source.stem}{suffix}{ext}"
    candidate, i = base, 2
    while candidate.exists():
        candidate = directory / f"{source.stem}{suffix}_{i}{ext}"
        i += 1
    return candidate


def resample_blocks(samples: np.ndarray, sr_from: int, sr_to: int):
    """Yield resampled blocks with enough context that block edges are seamless."""
    if sr_from == sr_to:
        for a in range(0, samples.shape[0], WRITE_BLOCK):
            yield np.asarray(samples[a : a + WRITE_BLOCK], dtype=np.float32)
        return
    g = gcd(sr_from, sr_to)
    up, down = sr_to // g, sr_from // g
    pad = 2048
    n = samples.shape[0]
    block = (WRITE_BLOCK // down) * down
    for a in range(0, n, block):
        lo, hi = max(0, a - pad), min(n, a + block + pad)
        seg = np.asarray(samples[lo:hi], dtype=np.float64)
        y = signal.resample_poly(seg, up, down, axis=0)
        start = (a - lo) * up // down
        length = (min(n, a + block) - a) * up // down
        yield y[start : start + length].astype(np.float32)


def export_wav(samples: np.ndarray, sr: int, path: str | os.PathLike, options: ExportOptions,
               source_path: Path | None = None, progress=None) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".wav":
        path = path.with_suffix(".wav")
    if source_path is not None and (same_file(path, source_path) or path.resolve() == Path(source_path).resolve()):
        raise ExportError("The enhanced file cannot replace the original recording. Choose a different name.")
    subtype = BIT_DEPTHS.get(options.bit_depth)
    if subtype is None:
        raise ExportError(f"Unsupported bit depth: {options.bit_depth}")
    out_sr = options.sample_rate or sr
    if not path.parent.exists():
        raise ExportError(f"The folder “{path.parent}” does not exist.")

    tmp = path.with_name(f".{path.name}.part")
    total = samples.shape[0]
    done = 0
    log.info("Exporting %s (%s, %d Hz)", path, subtype, out_sr)
    try:
        with sf.SoundFile(str(tmp), "w", samplerate=out_sr, channels=samples.shape[1], subtype=subtype, format="WAV") as f:
            for block in resample_blocks(samples, sr, out_sr):
                if subtype == "PCM_16":
                    # TPDF dither at 1 LSB avoids truncation distortion in quiet passages
                    lsb = 1.0 / 32768
                    block = block + (RNG.random(block.shape) - RNG.random(block.shape)).astype(np.float32) * lsb
                if subtype != "FLOAT":
                    np.clip(block, -1.0, 1.0 - 1.0 / 32768, out=block)
                f.write(block)
                done += block.shape[0] * sr / out_sr
                if progress:
                    progress(min(1.0, done / total))
        os.replace(tmp, path)
    except ExportError:
        raise
    except PermissionError as exc:
        _cleanup(tmp)
        raise ExportError(f"Windows did not allow writing to “{path.parent}”. Choose another folder, "
                          "or close the file if it is open in another program.") from exc
    except OSError as exc:
        _cleanup(tmp)
        if getattr(exc, "errno", None) == 28:
            raise ExportError("The disk is full. Free up space or export to another drive.") from exc
        raise ExportError(f"The file could not be written: {exc.strerror or exc}") from exc
    except Exception as exc:
        _cleanup(tmp)
        log.exception("WAV export failed")
        raise ExportError("The file could not be written.") from exc
    return path


def _cleanup(tmp: Path):
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
