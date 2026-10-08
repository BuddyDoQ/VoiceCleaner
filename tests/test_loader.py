import numpy as np
import pytest
import soundfile as sf

from app.audio.loader import format_duration, load_wav, probe
from app.utils.errors import AudioLoadError, EmptyAudioError, UnsupportedFormatError


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "FLOAT"])
@pytest.mark.parametrize("sr", [44_100, 48_000, 96_000])
def test_loads_common_formats_as_float32(wav_file, speech, subtype, sr):
    path = wav_file(speech, sr=sr, subtype=subtype)
    audio = load_wav(path)
    assert audio.samples.dtype == np.float32
    assert audio.samples.shape == (speech.shape[0], 1)
    assert audio.sample_rate == sr
    assert np.max(np.abs(audio.samples[:, 0] - speech)) < 1e-3
    assert audio.info.subtype == subtype


def test_stereo_shape(wav_file, speech):
    path = wav_file(np.stack([speech, speech * 0.5], axis=1))
    audio = load_wav(path)
    assert audio.channels == 2
    assert audio.mono().shape == (speech.shape[0],)


def test_disk_backed_loading_matches(wav_file, speech):
    path = wav_file(speech, subtype="FLOAT")
    audio = load_wav(path, force_disk=True)
    assert audio.is_disk_backed
    np.testing.assert_allclose(np.asarray(audio.samples[:, 0]), speech, atol=1e-6)


def test_missing_file(tmp_path):
    with pytest.raises(AudioLoadError):
        load_wav(tmp_path / "nope.wav")


def test_corrupt_file(tmp_path):
    p = tmp_path / "bad.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunkjunkjunk" * 10)
    with pytest.raises(AudioLoadError):
        load_wav(p)


def test_empty_file(tmp_path):
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    with pytest.raises(EmptyAudioError):
        load_wav(p)


def test_zero_frames(tmp_path):
    p = tmp_path / "zero.wav"
    sf.write(p, np.zeros((0, 1), np.float32), 48000)
    with pytest.raises(EmptyAudioError):
        load_wav(p)


def test_digital_silence_rejected(wav_file):
    with pytest.raises(EmptyAudioError):
        load_wav(wav_file(np.zeros(48000, np.float32)))


def test_not_a_wav(tmp_path):
    p = tmp_path / "audio.flac"
    sf.write(p, np.zeros(1000, np.float32) + 0.1, 48000, format="FLAC")
    with pytest.raises(UnsupportedFormatError):
        probe(p)


def test_too_many_channels(wav_file):
    with pytest.raises(UnsupportedFormatError):
        load_wav(wav_file(np.random.default_rng(0).standard_normal((4800, 6)).astype(np.float32) * 0.1))


def test_nan_samples_repaired(wav_file, speech):
    x = speech.copy()
    x[1000:1010] = np.nan
    audio = load_wav(wav_file(x, subtype="FLOAT"))
    assert audio.repaired_nonfinite
    assert np.all(np.isfinite(audio.samples))


def test_format_duration():
    assert format_duration(65.25) == "1:05.2"
    assert format_duration(3725) == "1:02:05.0"
