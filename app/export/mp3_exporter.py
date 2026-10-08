"""MP3 export through libsndfile's bundled LAME encoder (no extra dependency).

MP3 supports at most 48 kHz, so higher rates are converted to 48 kHz.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf

from ..utils.errors import ExportError
from ..utils.logging import get_logger
from .wav_exporter import _cleanup, resample_blocks, same_file

log = get_logger("export")

MP3_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
QUALITY = {"high": 0.1, "standard": 0.35, "small": 0.6}  # libsndfile compression level (VBR)


def mp3_supported() -> bool:
    return "MP3" in sf.available_formats()


def export_mp3(samples: np.ndarray, sr: int, path: str | os.PathLike, quality: str = "high",
               sample_rate: int = 0, source_path: Path | None = None, progress=None) -> Path:
    if not mp3_supported():
        raise ExportError("MP3 export is not available in this installation. Export as WAV instead.")
    path = Path(path)
    if path.suffix.lower() != ".mp3":
        path = path.with_suffix(".mp3")
    if source_path is not None and same_file(path, source_path):
        raise ExportError("The enhanced file cannot replace the original recording.")
    out_sr = sample_rate or sr
    if out_sr not in MP3_RATES:
        out_sr = 48000 if out_sr > 48000 else min(MP3_RATES, key=lambda r: abs(r - out_sr))
    tmp = path.with_name(f".{path.name}.part")
    total, done = samples.shape[0], 0
    log.info("Exporting MP3 %s (%d Hz, %s)", path, out_sr, quality)
    try:
        with sf.SoundFile(str(tmp), "w", samplerate=out_sr, channels=samples.shape[1], format="MP3",
                          subtype="MPEG_LAYER_III", compression_level=QUALITY.get(quality, 0.1),
                          bitrate_mode="VARIABLE") as f:
            for block in resample_blocks(samples, sr, out_sr):
                f.write(np.clip(block, -1.0, 1.0))
                done += block.shape[0] * sr / out_sr
                if progress:
                    progress(min(1.0, done / total))
        os.replace(tmp, path)
    except PermissionError as exc:
        _cleanup(tmp)
        raise ExportError(f"Windows did not allow writing to “{path.parent}”.") from exc
    except Exception as exc:
        _cleanup(tmp)
        log.exception("MP3 export failed")
        raise ExportError("The MP3 file could not be written.") from exc
    return path
