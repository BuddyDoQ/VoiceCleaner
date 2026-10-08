import numpy as np
from scipy import signal

from app.audio.analyzer import analyze, detect_clipping, yin_pitch
from app.audio.settings import auto_configure

SR = 48_000


def test_clean_speech_analysis(speech):
    r = analyze(speech[:, None], SR)
    assert r.speech_detected
    assert 0.2 < r.speech_fraction < 0.9
    assert r.snr_db > 40
    assert r.silence_regions, "pauses should be found"
    assert not r.hum.detected
    assert r.clipping_regions < 5
    assert r.f0_median_hz is not None and 110 < r.f0_median_hz < 175


def test_snr_estimate_tracks_noise_level(speech):
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(speech.shape[0]).astype(np.float32)
    snrs = []
    for level in (0.003, 0.01, 0.03):
        r = analyze((speech + noise * level)[:, None], SR)
        snrs.append(r.snr_db)
    assert snrs[0] > snrs[1] > snrs[2]
    assert snrs[0] - snrs[2] > 12  # 20 dB more noise -> clearly lower estimate


def test_noise_floor_estimate(noisy):
    x, _, noise = noisy
    r = analyze(x[:, None], SR)
    # white noise 0.02 RMS -> -34 dBFS overall; analysis band is 80 Hz - 16 kHz (2/3 of the band)
    true_db = 10 * np.log10(np.mean(noise**2) * (16000 - 80) / 24000)
    assert abs(r.noise_floor_dbfs - true_db) < 3


def test_hum_detection(speech):
    t = np.arange(speech.shape[0]) / SR
    hum = 0.01 * (np.sin(2 * np.pi * 50 * t) + 0.5 * np.sin(2 * np.pi * 100 * t) + 0.3 * np.sin(2 * np.pi * 150 * t))
    r = analyze((speech + hum.astype(np.float32))[:, None], SR)
    assert r.hum.detected
    assert abs(r.hum.fundamental - 50) < 1.0


def test_reverb_detection():
    from tests.conftest import speech_like

    speech = speech_like(10.0)  # RT60 needs at least three phrase endings
    rng = np.random.default_rng(5)
    rt60 = 0.9
    n = int(rt60 * SR)
    ir = rng.standard_normal(n) * np.exp(-6.91 * np.arange(n) / SR / rt60)
    ir[0] = 4.0
    wet = signal.fftconvolve(speech, ir)[: speech.shape[0]]
    wet = (wet / np.max(np.abs(wet)) * 0.3).astype(np.float32)
    dry = analyze(speech[:, None], SR)
    r = analyze(wet[:, None], SR)
    assert r.rt60_s is not None and r.rt60_s > 0.5
    assert r.reverb_amount > dry.reverb_amount


def test_clipping_detection(speech):
    clipped = np.clip(speech * 8, -0.5, 0.5)
    samples, regions = detect_clipping(clipped[:, None])
    assert regions > 50
    assert detect_clipping(speech[:, None])[1] < 5


def test_noise_only_has_no_speech():
    rng = np.random.default_rng(9)
    noise = signal.lfilter([1], [1, -0.95], rng.standard_normal(SR * 6)).astype(np.float32) * 0.01
    r = analyze(noise[:, None], SR)
    assert not r.speech_detected


def test_stereo_modes(speech):
    assert analyze(np.stack([speech, speech], 1), SR).stereo.mode == "dual_mono"
    assert analyze(np.stack([speech, speech * 0.001], 1), SR).stereo.mode == "left_only"
    rng = np.random.default_rng(2)
    other = np.roll(speech, 12345) + 0.01 * rng.standard_normal(speech.shape[0]).astype(np.float32)
    assert analyze(np.stack([speech, other], 1), SR).stereo.mode == "stereo"


def test_yin_pitch():
    t = np.arange(16000) / 16000
    x = signal.sawtooth(2 * np.pi * 120 * t).astype(np.float32)
    f0 = yin_pitch(x, 16000)
    assert abs(np.nanmedian(f0) - 120) < 2


def test_auto_settings_scale_with_noise(speech):
    rng = np.random.default_rng(4)
    noise = rng.standard_normal(speech.shape[0]).astype(np.float32)
    clean = auto_configure("Clean Voice", analyze(speech[:, None], SR)).settings
    dirty = auto_configure("Clean Voice", analyze((speech + 0.05 * noise)[:, None], SR)).settings
    assert dirty.noise_reduction > clean.noise_reduction + 0.2
    assert clean.noise_reduction <= 0.2  # clean audio is left mostly alone
