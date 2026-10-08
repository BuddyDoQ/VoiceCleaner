"""Room reverb and echo reduction.

Late reverberation is modelled statistically (Lebart et al. 2001; Habets
2007): sound energy in a room decays exponentially, so the reverberant
energy expected at time *t* is a delayed, attenuated copy of the energy
that was present ``Td`` earlier::

    P_late(t, f) = exp(-2 * delta * Td) * P(t - Td, f),  delta = 3 ln 10 / RT60

Subtracting this estimate in the spectral domain removes the "room" tail
while leaving the direct sound (the first ~50 ms) untouched. RT60 comes from
the analyzer. Discrete echoes (slap-back from a hard wall) are found by
cepstral analysis and suppressed the same way using the measured delay.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

from .denoise import StftConfig, istft, stft
from .dsp import EPS


@dataclass
class DereverbParams:
    reverb_reduction: float = 0.4  # 0..1
    echo_reduction: float = 0.0  # 0..1
    rt60: float = 0.5  # seconds
    echo_delay_s: float | None = None
    echo_gain: float = 0.0

    @property
    def active(self) -> bool:
        return self.reverb_reduction > 0.01 or self.echo_reduction > 0.01


class Dereverberator:
    def __init__(self, sr: int, params: DereverbParams):
        self.sr = sr
        self.params = params
        self.cfg = StftConfig.for_rate(sr)

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.ndim == 2:
            return np.stack([self._process_mono(x[:, c]) for c in range(x.shape[1])], axis=1)
        return self._process_mono(x)

    def _process_mono(self, x: np.ndarray) -> np.ndarray:
        p = self.params
        if not p.active:
            return x.astype(np.float32)
        hop_s = self.cfg.hop / self.sr
        z = stft(x, self.sr, self.cfg)
        power = np.abs(z) ** 2
        # light temporal smoothing of the power estimate
        smooth = signal.lfilter([0.4], [1, -0.6], power, axis=1)
        gain = np.ones_like(power)

        if p.reverb_reduction > 0.01:
            rt60 = float(np.clip(p.rt60, 0.2, 2.0))
            delta = 3 * np.log(10) / rt60
            # the late-reverb boundary moves earlier as echo reduction increases
            td_s = 0.05 - 0.02 * p.echo_reduction
            nd = max(1, int(round(td_s / hop_s)))
            decay = np.exp(-2 * delta * nd * hop_s)
            late = np.zeros_like(smooth)
            late[:, nd:] = decay * smooth[:, :-nd]
            # gentle by design: beyond ~1.0 over-subtraction and ~12 dB depth, speech
            # onsets get chopped and intelligibility falls (measured on the test set)
            beta = 0.35 + 0.65 * p.reverb_reduction
            g = 1.0 - beta * late / (smooth + EPS)
            floor = 10 ** (-(3 + 9 * p.reverb_reduction) / 20)
            gain *= np.maximum(g, floor)

        if p.echo_reduction > 0.01 and p.echo_delay_s and p.echo_gain > 0:
            nd = max(1, int(round(p.echo_delay_s / hop_s)))
            echo = np.zeros_like(smooth)
            echo[:, nd:] = (p.echo_gain**2) * smooth[:, :-nd]
            g = 1.0 - (0.8 + 1.2 * p.echo_reduction) * echo / (smooth + EPS)
            floor = 10 ** (-(3 + 12 * p.echo_reduction) / 20)
            gain *= np.maximum(g, floor)

        gain = ndimage.uniform_filter(gain, size=(3, 3), mode="nearest")
        return istft(z * gain, self.sr, self.cfg, x.shape[0])


def detect_echo(mono: np.ndarray, sr: int, speech_mask: np.ndarray, hop_s: float = 0.01,
                max_seconds: float = 120.0) -> tuple[float | None, float]:
    """Find a discrete echo (25-250 ms) via the averaged power cepstrum of speech.

    Returns ``(delay_seconds, relative_gain)`` or ``(None, 0)``.
    """
    target = 8000
    x = np.asarray(mono[: int(sr * max_seconds)], dtype=np.float32)
    if sr != target:
        from math import gcd

        g = gcd(sr, target)
        x = signal.resample_poly(x, target // g, sr // g).astype(np.float32)
    nfft = 4096  # 512 ms frames at 8 kHz
    hop = nfft // 2
    if x.shape[0] < nfft * 2:
        return None, 0.0
    win = np.hanning(nfft)
    frames = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop]
    # keep frames that are mostly speech
    centers = (np.arange(frames.shape[0]) * hop + nfft // 2) / target
    idx = np.clip((centers / hop_s).astype(int), 0, max(0, speech_mask.shape[0] - 1))
    if speech_mask.size:
        frames = frames[speech_mask[idx]]
    if frames.shape[0] < 4:
        return None, 0.0
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2
    ceps = np.fft.irfft(np.log(spec + 1e-10), axis=1)
    avg = np.mean(ceps, axis=0)
    q0, q1 = int(0.025 * target), int(0.25 * target)
    region = avg[q0:q1]
    peak_i = int(np.argmax(region))
    noise = np.median(np.abs(region)) + 1e-9
    prominence = region[peak_i] / noise
    # a discrete echo quieter than about -16 dB (gain 0.15) is masked by the direct sound
    if prominence < 6.0 or region[peak_i] < 0.15:
        return None, 0.0
    # cepstral peak amplitude ~ echo gain (log(1+g e^{-jwd}) ~ g e^{-jwd} for small g)
    gain = float(np.clip(region[peak_i], 0.0, 0.9))
    return (q0 + peak_i) / target, gain
