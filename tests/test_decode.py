"""Opening recordings that are not WAV: libsndfile formats directly, the rest through FFmpeg."""
import subprocess

import numpy as np
import pytest
import soundfile as sf

from app.audio import decode
from app.audio.loader import load_wav, probe
from app.utils.errors import UnsupportedFormatError

SR = 48000


def _speechlike(seconds=2.0, channels=1):
    t = np.arange(int(SR * seconds)) / SR
    x = (0.3 * np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    return np.repeat(x[:, None], channels, axis=1)


def test_flac_and_mp3_load_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICECLEANER_FFMPEG", str(tmp_path / "missing.exe"))
    for name, kw in (("a.flac", {"subtype": "PCM_24"}), ("a.mp3", {"format": "MP3"})):
        p = tmp_path / name
        sf.write(p, _speechlike(), SR, **kw)
        a = load_wav(p)
        assert a.sample_rate == SR and abs(a.duration - 2.0) < 0.1
        assert not a.info.decoded


def test_file_types_needing_ffmpeg_report_it_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICECLEANER_FFMPEG", str(tmp_path / "missing.exe"))
    p = tmp_path / "memo.m4a"
    p.write_bytes(b"\0" * 100)
    with pytest.raises(UnsupportedFormatError, match="FFmpeg"):
        probe(p)


ffmpeg = pytest.mark.skipif(decode.ffmpeg_path() is None, reason="FFmpeg not available")


def _ffmpeg(*args):
    subprocess.run([str(decode.ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@ffmpeg
def test_m4a_and_video_are_decoded(tmp_path):
    src = tmp_path / "src.wav"
    sf.write(src, _speechlike(3.0), SR)
    _ffmpeg("-i", str(src), "-c:a", "aac", str(tmp_path / "memo.m4a"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=navy:s=160x120:r=10", "-i", str(src), "-shortest",
            "-c:v", "mpeg4", "-c:a", "aac", str(tmp_path / "call.mp4"))
    for name, label in (("memo.m4a", "AAC (M4A)"), ("call.mp4", "AAC (MP4)")):
        info = probe(tmp_path / name)
        assert info.decoded and info.subtype_label == label and abs(info.duration - 3.0) < 0.2
        a = load_wav(tmp_path / name)
        assert a.sample_rate == SR and a.channels == 1 and abs(a.duration - 3.0) < 0.2
        assert a.info.path.name == name  # exports are named after the original
    assert not list(tmp_path.glob("*.part*"))


@ffmpeg
def test_surround_is_mixed_down_without_clipping(tmp_path):
    src = tmp_path / "src.wav"
    sf.write(src, _speechlike(2.0), SR)
    out = tmp_path / "movie.m4a"
    _ffmpeg("-i", str(src), "-af", "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0", "-c:a", "aac", str(out))
    a = load_wav(out)
    assert a.channels == 2 and np.abs(a.samples).max() <= 1.0


@ffmpeg
def test_video_without_sound_and_junk_are_refused(tmp_path):
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x120:r=10", "-t", "2", "-c:v", "mpeg4", str(tmp_path / "v.mp4"))
    with pytest.raises(UnsupportedFormatError, match="without a sound track"):
        probe(tmp_path / "v.mp4")
    (tmp_path / "junk.m4a").write_bytes(b"not audio at all")
    with pytest.raises(UnsupportedFormatError):
        probe(tmp_path / "junk.m4a")
