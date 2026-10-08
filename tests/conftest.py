import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SR = 48_000


def speech_like(seconds: float = 6.0, sr: int = SR, f0: float = 140.0, seed: int = 0) -> np.ndarray:
    """Voiced, formant-filtered pulse train with a syllable envelope and pauses.

    Harmonic and strongly modulated like speech (low spectral flatness, clear
    pauses), which is what the analyzer's voice-activity detection relies on.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    pitch = f0 * (1 + 0.1 * np.sin(2 * np.pi * 0.5 * t) + 0.03 * np.sin(2 * np.pi * 5.1 * t))
    phase = np.cumsum(pitch) / sr
    src = (np.diff(np.floor(phase), prepend=0) > 0).astype(float)  # glottal pulses
    out = np.zeros(n)
    for fc, bw, g in ((650, 90, 1.0), (1150, 110, 0.7), (2500, 170, 0.35), (3400, 250, 0.2)):
        b, a = signal.iirpeak(fc, fc / bw, fs=sr)
        out += g * signal.lfilter(b, a, src)
    # syllables at ~4 Hz, phrases with real pauses
    syll = 0.5 - 0.5 * np.cos(2 * np.pi * 4.0 * t)
    phrase = ((t % 2.0) > 0.6).astype(float)
    phrase[: int(0.8 * sr)] = 0.0  # leading silence for noise profiling
    env = signal.lfilter([0.02], [1, -0.98], syll * phrase)
    out = out * env
    out += 1e-5 * rng.standard_normal(n)
    return (0.3 * out / np.max(np.abs(out))).astype(np.float32)


@pytest.fixture
def speech():
    return speech_like()


@pytest.fixture
def noisy(speech):
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(speech.shape[0]).astype(np.float32)
    return speech + noise * 0.02, speech, noise * 0.02


@pytest.fixture
def wav_file(tmp_path, noisy):
    def make(data=None, sr=SR, subtype="PCM_16", name="test.wav"):
        x = noisy[0] if data is None else data
        p = tmp_path / name
        sf.write(p, x, sr, subtype=subtype)
        return p

    return make
