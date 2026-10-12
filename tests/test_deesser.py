import numpy as np
from scipy import signal

from app.audio.deesser import DeEsser, DeEsserParams


def _voice_with_sibilants(sr=48000, seconds=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr * seconds)) / sr
    x = 0.2 * np.sin(2 * np.pi * 150 * t) + 0.1 * np.sin(2 * np.pi * 450 * t)
    hiss = rng.standard_normal(t.size) * 0.3
    for start in (0.5, 1.5, 2.5):  # short bursts of "s"
        a, b = int(start * sr), int((start + 0.12) * sr)
        x[a:b] += hiss[a:b]
    return np.stack([x, x], axis=1).astype(np.float32)


def test_reduces_sibilant_bursts_only():
    sr = 48000
    x = _voice_with_sibilants(sr)
    d = DeEsser(sr, DeEsserParams(amount=0.8))
    y = d.process(x)
    burst = slice(int(0.52 * sr), int(0.6 * sr))
    calm = slice(int(1.0 * sr), int(1.3 * sr))
    assert d.max_reduction_db > 2.0
    sos = signal.butter(4, [5000, 9000], btype="bandpass", fs=sr, output="sos")
    hf = lambda z: np.sqrt(np.mean(signal.sosfilt(sos, z[:, 0])[burst] ** 2))  # noqa: E731
    assert hf(y) < 0.7 * hf(x)  # the "s" band is turned down
    assert np.allclose(y[calm], x[calm], atol=1e-3)  # vowels untouched


def test_off_is_bit_exact():
    x = _voice_with_sibilants()
    assert np.array_equal(DeEsser(48000, DeEsserParams(amount=0.0)).process(x), x)
