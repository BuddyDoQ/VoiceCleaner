"""Opening recordings that are not WAV: phone memos (M4A), MP3, FLAC, Zoom recordings and
the sound of video files.

* WAV, and what libsndfile reads itself (FLAC, MP3, OGG/Opus, AIFF, CAF), is read directly.
* Everything else is decoded by FFmpeg (an LGPL build shipped next to the app, run as a
  separate program) into a temporary 32-bit float WAV, which is then loaded like any other
  recording and deleted. Only the first audio track is used; the picture of a video is
  ignored. More than two channels are mixed down to stereo.

The original file is never changed, and exports are named after it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..utils.errors import AudioLoadError, UnsupportedFormatError
from ..utils.logging import get_logger

log = get_logger("decode")

WAV_EXTENSIONS = (".wav", ".wave")
# read by libsndfile itself (no FFmpeg needed)
NATIVE_EXTENSIONS = WAV_EXTENSIONS + (".flac", ".mp3", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".aifc", ".caf")
# decoded by FFmpeg
FFMPEG_AUDIO = (".m4a", ".aac", ".wma", ".amr", ".3ga", ".ac3", ".mka", ".alac")
FFMPEG_VIDEO = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".3gp", ".flv", ".mpg", ".mpeg", ".ts")
AUDIO_EXTENSIONS = NATIVE_EXTENSIONS + FFMPEG_AUDIO + FFMPEG_VIDEO

# for file dialogs
FILE_FILTER = ("Recordings and videos (" + " ".join(f"*{e}" for e in AUDIO_EXTENSIONS) + ");;"
               "WAV audio (*.wav *.wave);;All files (*)")


def is_supported(path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def needs_ffmpeg(path) -> bool:
    return Path(path).suffix.lower() not in NATIVE_EXTENSIONS


def ffmpeg_path() -> Path | None:
    """The FFmpeg program: the one shipped with the app, else one on the PATH."""
    override = os.environ.get("VOICECLEANER_FFMPEG")
    if override:
        return Path(override) if Path(override).is_file() else None
    from ..utils.config import app_root

    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    for folder in (app_root() / "ffmpeg", app_root() / "ffmpeg" / "bin"):
        if (folder / exe).is_file():
            return folder / exe
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def _startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # no console window flashing up
    return si


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    exe = ffmpeg_path()
    if exe is None:
        raise UnsupportedFormatError(
            "Opening this kind of file needs FFmpeg, which is missing from this copy of VoiceCleaner. "
            "Reinstall VoiceCleaner, or convert the file to WAV first.")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run([str(exe), "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags,
                          startupinfo=_startupinfo())


@dataclass
class StreamInfo:
    codec: str  # "AAC", "Opus", ...
    sample_rate: int
    channels: int
    duration: float  # seconds (0 when unknown)
    has_video: bool


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO = re.compile(r"Stream #\S+.*?: Audio: (\w+)[^,]*,\s*(\d+) Hz,\s*([^,]+)")
_LAYOUT_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "4.0": 4, "5.0": 5, "5.1": 6, "6.1": 7,
                    "7.1": 8}
CODEC_NAMES = {"aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC", "alac": "ALAC",
               "wmav2": "WMA", "wmav1": "WMA", "wmapro": "WMA Pro", "amr_nb": "AMR", "amr_wb": "AMR-WB",
               "ac3": "AC-3", "eac3": "E-AC-3", "pcm_s16le": "16-bit PCM", "pcm_s24le": "24-bit PCM"}


def inspect(path) -> StreamInfo:
    """What FFmpeg sees in ``path`` (fast: nothing is decoded)."""
    path = Path(path)
    try:
        res = _run(["-i", str(path)], timeout=30)  # "no output file" is expected: the details are on stderr
    except subprocess.TimeoutExpired as exc:
        raise AudioLoadError(f"“{path.name}” could not be read (FFmpeg did not answer).") from exc
    text = res.stderr
    m = _AUDIO.search(text)
    if m is None:
        if "Invalid data found" in text or "could not find codec" in text.lower():
            raise UnsupportedFormatError(f"“{path.name}” is not a recording VoiceCleaner can open.")
        if re.search(r"Stream #\S+.*?: Video:", text):
            raise UnsupportedFormatError(f"“{path.name}” is a video without a sound track.")
        raise UnsupportedFormatError(f"“{path.name}” contains no audio VoiceCleaner can read.")
    codec, rate, layout = m.group(1), int(m.group(2)), m.group(3).strip().split("(")[0].strip()
    channels = _LAYOUT_CHANNELS.get(layout)
    if channels is None:
        cm = re.match(r"(\d+) channels", layout)
        channels = int(cm.group(1)) if cm else 2
    d = _DURATION.search(text)
    duration = (int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))) if d else 0.0
    return StreamInfo(CODEC_NAMES.get(codec, codec.upper()), rate, channels, duration,
                      bool(re.search(r"Stream #\S+.*?: Video:", text)))


def decode_to_wav(path, out: Path) -> Path:
    """Decode the first audio track of ``path`` into a 32-bit float WAV at ``out`` (more
    than two channels are mixed down to stereo)."""
    path, out = Path(path), Path(out)
    exe = ffmpeg_path()
    if exe is None:
        _run([])  # raises the "FFmpeg is missing" message
    if inspect(path).channels > 2:  # surround: a stereo downmix scaled so it cannot clip
        audio_filter = "aresample=rematrix_maxval=1.0:out_chlayout=stereo,aformat=sample_fmts=flt"
    else:
        audio_filter = "aformat=sample_fmts=flt:channel_layouts=mono|stereo"
    args = [str(exe), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", audio_filter,
            "-c:a", "pcm_f32le", "-rf64", "auto", "-f", "wav", str(out)]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log.info("Decoding %s with FFmpeg", path)
    res = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=flags, startupinfo=_startupinfo())
    if res.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
        out.unlink(missing_ok=True)
        detail = res.stderr.strip().splitlines()[-1:] or ["unknown error"]
        log.warning("FFmpeg could not decode %s: %s", path, detail[0])
        raise AudioLoadError(f"“{path.name}” could not be decoded. The file may be damaged ({detail[0]}).")
    return out


def ffmpeg_version() -> str:
    """For the About box and logs: the FFmpeg build in use, or "" when there is none."""
    try:
        first = _run(["-version"], timeout=10).stdout.splitlines()[0]
        return first.replace("ffmpeg version ", "").split(" Copyright")[0]
    except Exception:
        return ""
