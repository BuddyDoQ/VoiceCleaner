import numpy as np
import pytest

from app.audio import loudness
from app.audio.analyzer import analyze
from app.audio.eq import VoiceEQ, VoiceEQParams, manual_bands, response_db
from app.audio.leveler import Leveler, LevelerParams
from app.audio.settings import EQ_BANDS, ProcessingSettings

SR = 48_000


def _lufs(x):
    return loudness.integrated_loudness(x[:, None] if x.ndim == 1 else x, SR)


# --------------------------------------------------------------------------- leveler
def _two_level_speech(speech, drop_db=12.0):
    """Same speech twice; the second half much quieter (speaker moved away)."""
    x = np.concatenate([speech, speech * 10 ** (-drop_db / 20)]).astype(np.float32)
    rng = np.random.default_rng(0)
    return x + rng.standard_normal(x.shape[0]).astype(np.float32) * 1e-4


def test_leveler_evens_out_quiet_passage(speech):
    x = _two_level_speech(speech)
    a = analyze(x[:, None], SR)
    half = speech.shape[0]
    before = _lufs(x[:half]) - _lufs(x[half:])
    y = Leveler(SR, LevelerParams(amount=1.0, range_db=12.0, speed_s=1.0)).process(x[:, None], a.speech_mask, 0.01)
    after = _lufs(y[:half, 0]) - _lufs(y[half:, 0])
    assert before > 11
    assert after < 4  # most of the 12 dB difference removed


def test_leveler_does_not_pump_pauses(speech):
    x = _two_level_speech(speech)
    a = analyze(x[:, None], SR)
    lev = Leveler(SR, LevelerParams(amount=1.0, range_db=12.0, speed_s=1.0))
    y = lev.process(x[:, None], a.speech_mask, 0.01)
    # the leading pause (no speech yet) gets no more gain than the speech that follows
    lead = slice(int(0.1 * SR), int(0.6 * SR))
    gain_lead = 20 * np.log10(np.std(y[lead, 0]) / np.std(x[lead]))
    assert gain_lead <= lev.max_boost_db + 0.5
    assert lev.max_boost_db <= 12.0 + 1e-6 and lev.max_cut_db >= -12.0 - 1e-6


def test_leveler_amount_zero_is_bypass(speech):
    a = analyze(speech[:, None], SR)
    y = Leveler(SR, LevelerParams(amount=0.0)).process(speech[:, None], a.speech_mask, 0.01)
    np.testing.assert_array_equal(y[:, 0], speech)


def test_leveler_respects_range(speech):
    x = _two_level_speech(speech, drop_db=20)
    a = analyze(x[:, None], SR)
    lev = Leveler(SR, LevelerParams(amount=1.0, range_db=6.0, speed_s=1.0))
    lev.process(x[:, None], a.speech_mask, 0.01)
    assert lev.max_boost_db - lev.max_cut_db <= 12.0 + 1e-6


# --------------------------------------------------------------------------- EQ & tone
def test_manual_band_response_matches_slider():
    gains = [0.0] * len(EQ_BANDS)
    gains[5] = 6.0  # Clarity @ 2.5 kHz
    bands = manual_bands(VoiceEQParams(manual_gains=gains))
    r = response_db(bands, np.array([100.0, 2500.0, 15000.0]))
    assert r[1] == pytest.approx(6.0, abs=0.2)
    assert abs(r[0]) < 0.3 and abs(r[2]) < 0.6


def test_tilt_brighter_and_darker():
    f = np.array([100.0, 1000.0, 10000.0])
    bright = response_db(manual_bands(VoiceEQParams(tilt=1.0)), f)
    dark = response_db(manual_bands(VoiceEQParams(tilt=-1.0)), f)
    assert bright[2] - bright[0] > 3 and dark[0] - dark[2] > 3
    assert abs(bright[1]) < 1.0  # pivot


def test_graphic_eq_applied_with_correct_gain(speech):
    """Zero-phase filtering must still produce exactly the slider's gain."""
    t = np.arange(SR * 2) / SR
    tone = (0.1 * np.sin(2 * np.pi * 1250 * t)).astype(np.float32)[:, None]
    gains = [0.0] * len(EQ_BANDS)
    gains[4] = -6.0  # Nasal @ 1.25 kHz
    eq = VoiceEQ(SR, VoiceEQParams(amount=0.0, lf_cleanup=0.0, manual_gains=gains))
    y = eq.process(tone, np.ones(200, bool), 0.01)
    mid = slice(SR // 2, 3 * SR // 2)
    assert 20 * np.log10(np.std(y[mid]) / np.std(tone[mid])) == pytest.approx(-6.0, abs=0.3)


def test_settings_copy_does_not_share_eq_list():
    a = ProcessingSettings()
    b = a.copy()
    b.eq_gains[0] = 5.0
    assert a.eq_gains[0] == 0.0


# --------------------------------------------------------------------------- re-synthesis
def test_mel_filterbank_matches_librosa():
    librosa = pytest.importorskip("librosa")
    from app.ai.resynthesis import mel_filterbank

    np.testing.assert_allclose(mel_filterbank(44100, 2048, 128), librosa.filters.mel(sr=44100, n_fft=2048, n_mels=128),
                               atol=1e-7)


def test_blend_endpoints_and_coherence(speech):
    from app.ai.resynthesis import blend_magnitudes

    other = np.roll(speech, 37) * 0.8  # stand-in "re-synthesis" (different phase)
    np.testing.assert_allclose(blend_magnitudes(speech, other, SR, 0.0), speech, atol=1e-6)
    np.testing.assert_allclose(blend_magnitudes(speech, other, SR, 1.0), other, atol=1e-6)
    half = blend_magnitudes(speech, other, SR, 0.5)
    # no comb-filter collapse: level stays between the two inputs (a waveform mix would lose energy)
    lv = lambda s: 20 * np.log10(np.std(s))  # noqa: E731
    assert min(lv(speech), lv(other)) - 1.0 < lv(half) < max(lv(speech), lv(other)) + 1.0


@pytest.fixture(scope="module")
def resynth_manager():
    from app.ai.model_manager import ModelManager

    mm = ModelManager("auto")
    if not mm.resynth_available:
        pytest.skip("BigVGAN model not installed")
    return mm


def test_resynthesis_preserves_speech(speech, resynth_manager):
    from app.ai.resynthesis import Resynthesis

    y = Resynthesis(resynth_manager).process(speech[:, None], SR, 1.0)[:, 0]
    assert y.shape == speech.shape and np.all(np.isfinite(y))
    assert abs(20 * np.log10(np.std(y) / np.std(speech))) < 1.5
    # spectral envelope preserved (log-spectral distance in speech band)
    from scipy import signal

    f, p1 = signal.welch(speech, SR, nperseg=2048)
    _, p2 = signal.welch(y, SR, nperseg=2048)
    band = (f > 200) & (f < 6000)
    assert np.mean(np.abs(10 * np.log10(p1[band] / p2[band]))) < 3.0
