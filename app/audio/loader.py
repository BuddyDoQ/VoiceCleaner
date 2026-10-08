"""WAV loading and validation.

Audio is converted to float32 in the range [-1, 1] with shape
``(frames, channels)``. Recordings that would not comfortably fit in RAM are
streamed block-by-block into a disk-backed ``numpy.memmap`` so the rest of
the application can treat them like a normal array while the operating
system pages data in and out as needed.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from ..utils.errors import AudioLoadError, EmptyAudioError, InsufficientMemoryError, UnsupportedFormatError
from ..utils.hardware import available_ram_bytes
from ..utils.logging import get_logger

log = get_logger("loader")

WAV_CONTAINERS = {"WAV", "WAVEX", "RF64", "W64"}
SUBTYPE_LABELS = {
    "PCM_U8": "8-bit PCM",
    "PCM_16": "16-bit PCM",
    "PCM_24": "24-bit PCM",
    "PCM_32": "32-bit PCM",
    "FLOAT": "32-bit float",
    "DOUBLE": "64-bit float",
    "ULAW": "µ-law",
    "ALAW": "A-law",
    "IMA_ADPCM": "IMA ADPCM",
    "MS_ADPCM": "MS ADPCM",
}
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 384_000
MAX_DURATION_S = 6 * 3600
# How many float32 copies of the audio a full processing run keeps around
# (original, enhanced, working buffers). Used for the memory estimate.
WORKING_COPIES = 5
READ_BLOCK_FRAMES = 1 << 20


@dataclass(frozen=True)
class AudioFileInfo:
    path: Path
    sample_rate: int
    channels: int
    frames: int
    subtype: str
    container: str
    file_size: int

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    @property
    def subtype_label(self) -> str:
        return SUBTYPE_LABELS.get(self.subtype, self.subtype)

    @property
    def bit_depth_label(self) -> str:
        return self.subtype_label

    @property
    def channel_label(self) -> str:
        return {1: "Mono", 2: "Stereo"}.get(self.channels, f"{self.channels} channels")


@dataclass
class AudioData:
    samples: np.ndarray  # float32, shape (frames, channels); may be a memmap
    sample_rate: int
    info: AudioFileInfo | None = None
    repaired_nonfinite: bool = False

    @property
    def frames(self) -> int:
        return self.samples.shape[0]

    @property
    def channels(self) -> int:
        return self.samples.shape[1]

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    @property
    def is_disk_backed(self) -> bool:
        return isinstance(self.samples, np.memmap)

    def mono(self) -> np.ndarray:
        return self.samples.mean(axis=1, dtype=np.float32) if self.channels > 1 else self.samples[:, 0]


def format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{s:04.1f}"
    return f"{int(m)}:{s:04.1f}"


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def probe(path: str | os.PathLike) -> AudioFileInfo:
    """Read and validate the header of a WAV file without loading samples."""
    path = Path(path)
    if not path.exists():
        raise AudioLoadError(f"The file “{path.name}” could not be found.")
    if path.is_dir():
        raise AudioLoadError(f"“{path.name}” is a folder, not a WAV file.")
    size = path.stat().st_size
    if size == 0:
        raise EmptyAudioError(f"“{path.name}” is empty (0 bytes).")
    try:
        info = sf.info(str(path))
    except Exception as exc:
        log.info("probe failed for %s: %s", path, exc)
        if path.suffix.lower() not in (".wav", ".wave"):
            raise UnsupportedFormatError(
                f"“{path.name}” is not a WAV file. VoiceCleaner opens WAV recordings only."
            ) from exc
        raise AudioLoadError(
            f"“{path.name}” appears to be damaged or is not a valid WAV file."
        ) from exc

    if info.format not in WAV_CONTAINERS:
        raise UnsupportedFormatError(
            f"“{path.name}” is a {info.format_info or info.format} file. "
            "VoiceCleaner opens WAV recordings only."
        )
    if info.channels < 1 or info.channels > 2:
        raise UnsupportedFormatError(
            f"“{path.name}” has {info.channels} channels. Only mono and stereo WAV files are supported."
        )
    if not MIN_SAMPLE_RATE <= info.samplerate <= MAX_SAMPLE_RATE:
        raise UnsupportedFormatError(
            f"The sample rate of “{path.name}” ({info.samplerate} Hz) is not supported. "
            f"Use a recording between {MIN_SAMPLE_RATE // 1000} kHz and {MAX_SAMPLE_RATE // 1000} kHz."
        )
    if info.frames <= 0:
        raise EmptyAudioError(f"“{path.name}” contains no audio samples.")
    if info.frames / info.samplerate > MAX_DURATION_S:
        raise AudioLoadError(
            f"“{path.name}” is longer than {MAX_DURATION_S // 3600} hours. "
            "Split it into shorter parts before enhancing."
        )
    return AudioFileInfo(path, info.samplerate, info.channels, info.frames, info.subtype, info.format, size)


class _TempStore:
    """Owns the folder holding disk-backed audio buffers for this session."""

    def __init__(self):
        self._dir: Path | None = None

    @property
    def directory(self) -> Path:
        if self._dir is None:
            self._dir = Path(tempfile.mkdtemp(prefix="voicecleaner_"))
            atexit.register(self.cleanup)
        return self._dir

    def new_buffer(self, frames: int, channels: int) -> np.ndarray:
        needed = frames * channels * 4
        free = shutil.disk_usage(self.directory).free
        if needed > free * 0.9:
            raise InsufficientMemoryError(
                "This recording is too large for the available memory and temporary disk space."
            )
        fd, name = tempfile.mkstemp(suffix=".f32", dir=self.directory)
        os.close(fd)
        return np.memmap(name, dtype=np.float32, mode="w+", shape=(frames, channels))

    def cleanup(self):
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


temp_store = _TempStore()


def should_use_disk(frames: int, channels: int) -> bool:
    return frames * channels * 4 * WORKING_COPIES > available_ram_bytes() * 0.6


def allocate(frames: int, channels: int, disk: bool | None = None) -> np.ndarray:
    """Allocate a float32 working buffer, on disk if it would not fit in RAM."""
    if disk is None:
        disk = should_use_disk(frames, channels)
    if disk:
        return temp_store.new_buffer(frames, channels)
    try:
        return np.zeros((frames, channels), dtype=np.float32)
    except MemoryError:
        return temp_store.new_buffer(frames, channels)


def load_wav(path: str | os.PathLike, force_disk: bool | None = None) -> AudioData:
    info = probe(path)
    disk = should_use_disk(info.frames, info.channels) if force_disk is None else force_disk
    log.info(
        "Loading %s (%d Hz, %d ch, %s, %.1f s, %s)",
        info.path, info.sample_rate, info.channels, info.subtype, info.duration,
        "disk-backed" if disk else "in memory",
    )
    try:
        samples = allocate(info.frames, info.channels, disk)
        pos = 0
        with sf.SoundFile(str(info.path)) as f:
            for block in f.blocks(blocksize=READ_BLOCK_FRAMES, dtype="float32", always_2d=True):
                n = min(block.shape[0], info.frames - pos)
                samples[pos : pos + n] = block[:n]
                pos += n
    except (AudioLoadError, InsufficientMemoryError):
        raise
    except MemoryError as exc:
        raise InsufficientMemoryError("There is not enough free memory to open this recording.") from exc
    except Exception as exc:
        log.exception("Failed reading samples from %s", info.path)
        raise AudioLoadError(
            f"“{info.path.name}” could not be read completely. The file may be damaged or truncated."
        ) from exc

    if pos == 0:
        raise EmptyAudioError(f"“{info.path.name}” contains no readable audio.")
    if pos < info.frames:  # truncated file: header claims more data than exists
        log.warning("File %s truncated: %d of %d frames", info.path, pos, info.frames)
        samples = samples[:pos]
        info = AudioFileInfo(info.path, info.sample_rate, info.channels, pos, info.subtype, info.container, info.file_size)

    repaired = _repair_nonfinite(samples)
    if not _has_signal(samples):
        raise EmptyAudioError(f"“{info.path.name}” is completely silent.")
    return AudioData(samples, info.sample_rate, info, repaired)


def _repair_nonfinite(samples: np.ndarray) -> bool:
    repaired = False
    for start in range(0, samples.shape[0], READ_BLOCK_FRAMES):
        block = samples[start : start + READ_BLOCK_FRAMES]
        bad = ~np.isfinite(block)
        if bad.any():
            block[bad] = 0.0
            repaired = True
    if repaired:
        log.warning("Replaced NaN/Inf samples with silence")
    return repaired


def _has_signal(samples: np.ndarray) -> bool:
    for start in range(0, samples.shape[0], READ_BLOCK_FRAMES):
        if np.abs(samples[start : start + READ_BLOCK_FRAMES]).max() > 1e-7:
            return True
    return False
