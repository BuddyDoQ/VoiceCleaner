import numpy as np
import pyloudnorm
import pytest

from app.audio import loudness
from app.audio.limiter import LimiterParams, TruePeakLimiter

SR = 48_000


@pytest.mark.parametrize("sr", [44_100, 48_000, 96_000])
def test_integrated_loudness_matches_reference_meter(sr, speech):
    from scipy import signal

    x = signal.resample_poly(speech, sr, 48000) if sr != 48000 else speech
    ours = loudness.integrated_loudness(x[:, None], sr)
    ref = pyloudnorm.Meter(sr).integrated_loudness(x)
    assert abs(ours - ref) < 0.2


@pytest.mark.parametrize("sr", [44_100, 48_000, 96_000])
def test_sine_calibration(sr):
    # BS.1770-4: a 0 dBFS 997 Hz sine in one channel reads -3.01 LKFS
    t = np.arange(sr * 5) / sr
    x = np.sin(2 * np.pi * 997 * t)[:, None]
    assert abs(loudness.integrated_loudness(x, sr) - (-3.01)) < 0.03


def test_k_weighting_matches_standard_coefficients():
    sos = loudness._k_weighting_sos(48_000)
    np.testing.assert_allclose(sos[0], [1.53512485958697, -2.69169618940638, 1.19839281085285,
                                        1.0, -1.69065929318241, 0.73248077421585], atol=1e-9)
    np.testing.assert_allclose(sos[1, 4:], [-1.99004745483398, 0.99007225036621], atol=1e-9)


def test_stereo_sums_channels(speech):
    mono = loudness.integrated_loudness(speech[:, None], SR)
    stereo = loudness.integrated_loudness(np.stack([speech, speech], axis=1), SR)
    assert abs(stereo - mono - 3.01) < 0.05


def test_silence_is_minus_inf():
    assert loudness.integrated_loudness(np.zeros((SR * 2, 1)), SR) == float("-inf")


def test_true_peak_detects_intersample_peaks():
    # fs/4 sine phased so every sample lands at 0.707 of the true peak
    n = np.arange(SR)
    x = np.sin(np.pi / 2 * n + np.pi / 4)[:, None] * 0.9
    sample_peak = loudness.sample_peak_db(x)
    true_peak = loudness.true_peak_db(x, SR)
    assert true_peak - sample_peak > 2.5  # ~3 dB inter-sample overshoot


def test_gain_to_target():
    assert np.isclose(loudness.gain_to_target(-26.0, -16.0), 10 ** (10 / 20))
    assert loudness.gain_to_target(float("-inf"), -16.0) == 1.0


def test_limiter_respects_true_peak_ceiling(speech):
    x = (speech * 6.0)[:, None].astype(np.float32)  # heavily over 0 dBFS
    assert loudness.true_peak_db(x, SR) > 5
    y = TruePeakLimiter(SR, LimiterParams(ceiling_dbtp=-1.0)).process(x)
    assert loudness.true_peak_db(y, SR) <= -1.0 + 0.05


def test_limiter_transparent_below_ceiling(speech):
    x = (speech * 0.5)[:, None].astype(np.float32)
    y = TruePeakLimiter(SR, LimiterParams(ceiling_dbtp=-1.0)).process(x)
    np.testing.assert_allclose(y, x, atol=1e-6)
