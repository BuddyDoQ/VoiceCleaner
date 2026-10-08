import numpy as np
from scipy import signal

from app.audio import noise_profile
from app.audio.analyzer import analyze
from app.audio.denoise import DenoiseParams, SpectralDenoiser, TransientSuppressor
from app.audio.dereverb import Dereverberator, DereverbParams
from app.audio.dsp import process_in_chunks
from app.audio.eq import CleanupFilter, CleanupParams

SR = 48_000


def _rms_db(x):
    return 10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20)


def test_noise_profile_from_silence(noisy):
    x, _, noise = noisy
    r = analyze(x[:, None], SR)
    prof = noise_profile.estimate(x, SR, r.silence_regions)
    assert prof is not None and prof.reliable
    assert prof.stationarity > 0.5  # white noise is steady
    assert abs(prof.level_dbfs - _rms_db(noise)) < 2.0


def test_profile_denoiser_reduces_noise_and_keeps_speech(noisy):
    x, clean, noise = noisy
    r = analyze(x[:, None], SR)
    prof = noise_profile.estimate(x, SR, r.silence_regions)
    den = SpectralDenoiser(prof.psd, SR, prof.cfg, DenoiseParams(strength=1.0, max_attenuation_db=25))
    y = den.process(x)
    # background in pauses goes down a lot
    lead = slice(int(0.1 * SR), int(0.7 * SR))
    assert _rms_db(y[lead]) < _rms_db(x[lead]) - 15
    # error relative to clean speech improves
    assert _rms_db(y - clean) < _rms_db(x - clean) - 6
    # speech energy largely preserved
    speech_part = slice(int(2.8 * SR), int(3.8 * SR))
    assert abs(_rms_db(y[speech_part]) - _rms_db(clean[speech_part])) < 2.0


def test_denoiser_strength_zero_is_bypass(noisy):
    x, _, _ = noisy
    prof = noise_profile.estimate(x, SR, [(0.0, 0.8)])
    y = SpectralDenoiser(prof.psd, SR, prof.cfg, DenoiseParams(strength=0.0)).process(x)
    np.testing.assert_allclose(y, x, atol=1e-6)


def test_residual_floor_limits_attenuation(noisy):
    x, _, _ = noisy
    prof = noise_profile.estimate(x, SR, [(0.0, 0.8)])
    y = SpectralDenoiser(prof.psd, SR, prof.cfg, DenoiseParams(strength=1.0, max_attenuation_db=10)).process(x)
    lead = slice(int(0.1 * SR), int(0.7 * SR))
    assert _rms_db(y[lead]) > _rms_db(x[lead]) - 12


def test_chunked_processing_is_seamless(noisy):
    x, _, _ = noisy
    prof = noise_profile.estimate(x, SR, [(0.0, 0.8)])
    den = SpectralDenoiser(prof.psd, SR, prof.cfg, DenoiseParams(strength=0.8))
    whole = den.process(x)
    chunked = process_in_chunks(x, den.process, chunk=int(1.5 * SR), overlap=int(0.25 * SR))
    # differences only from the decision-directed state restarting per chunk; no clicks at seams
    diff = np.abs(whole - chunked)
    assert _rms_db(whole - chunked) < _rms_db(whole) - 20
    assert diff.max() < 0.05


def test_hum_notch_removes_hum(speech):
    t = np.arange(speech.shape[0]) / SR
    hum = 0.05 * np.sin(2 * np.pi * 60 * t) + 0.03 * np.sin(2 * np.pi * 180 * t)
    x = (speech + hum).astype(np.float32)[:, None]
    y = CleanupFilter(SR, CleanupParams(highpass_hz=40, hum_freqs=[60, 120, 180])).process(x)[:, 0]
    f, p_in = signal.welch(x[:, 0], SR, nperseg=SR)
    _, p_out = signal.welch(y, SR, nperseg=SR)
    i60, i180 = np.argmin(abs(f - 60)), np.argmin(abs(f - 180))
    assert 10 * np.log10(p_in[i60] / p_out[i60]) > 25
    assert 10 * np.log10(p_in[i180] / p_out[i180]) > 20


def test_highpass_is_zero_phase(speech):
    x = speech[:, None]
    y = CleanupFilter(SR, CleanupParams(highpass_hz=60))
    out = y.process(x)[:, 0]
    # no delay: cross-correlation peaks at lag 0
    c = signal.correlate(out, speech, mode="full", method="fft")
    assert np.argmax(c) - (len(speech) - 1) == 0


def test_dereverb_reduces_tail_energy(speech):
    rng = np.random.default_rng(5)
    rt60 = 0.8
    n = int(rt60 * SR)
    ir = rng.standard_normal(n) * np.exp(-6.91 * np.arange(n) / SR / rt60) * 0.15
    ir[0] = 1.0
    wet = signal.fftconvolve(speech, ir)[: speech.shape[0]].astype(np.float32)
    y = Dereverberator(SR, DereverbParams(reverb_reduction=0.8, rt60=rt60)).process(wet)
    # energy right after phrases end (tails) goes down relative to the phrase itself
    tail = slice(int(2.05 * SR), int(2.5 * SR))
    body = slice(int(1.3 * SR), int(1.9 * SR))
    ratio_in = _rms_db(wet[tail]) - _rms_db(wet[body])
    ratio_out = _rms_db(y[tail]) - _rms_db(y[body])
    assert ratio_out < ratio_in - 3


def test_transient_suppressor_removes_clicks_in_pauses(speech):
    x = speech.copy()
    click_at = int(0.4 * SR)  # inside the leading pause
    rng = np.random.default_rng(0)
    burst = rng.standard_normal(300) * np.exp(-np.arange(300) / 60.0)  # keyboard-like: broadband, ~6 ms
    x[click_at : click_at + 300] += (burst * 0.3).astype(np.float32)
    r = analyze(x[:, None], SR)
    out = np.empty((x.shape[0], 1), np.float32)
    ts = TransientSuppressor(SR, strength=1.0)
    ts.process(x[:, None], r.speech_mask, 0.01, out)
    assert ts.events >= 1
    seg = slice(click_at - 100, click_at + 300)
    assert _rms_db(out[seg, 0]) < _rms_db(x[seg]) - 10
    # speech untouched
    speech_part = slice(int(3.0 * SR), int(3.5 * SR))
    np.testing.assert_allclose(out[speech_part, 0], x[speech_part], atol=1e-6)


def test_stages_handle_input_shorter_than_one_frame(speech):
    x = speech[int(3 * SR) : int(3 * SR) + 600]  # shorter than one STFT frame
    y = Dereverberator(SR, DereverbParams(reverb_reduction=0.5)).process(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
