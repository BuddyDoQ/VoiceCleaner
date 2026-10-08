"""Profile-based spectral noise reduction.

A decision-directed Wiener filter (Ephraim & Malah 1984) driven by a noise
power spectrum learned from speech-free parts of the recording. This is the
most transparent way to remove *steady* noise (fans, HVAC, hiss, mic
self-noise): it attenuates only where the noise profile says noise is,
never gates the speech, and keeps a residual floor so the result does not
sound "underwater". Non-stationary noise is left to the AI model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

from .dsp import EPS


@dataclass(frozen=True)
class StftConfig:
    nfft: int
    hop: int

    @classmethod
    def for_rate(cls, sr: int) -> "StftConfig":
        nfft = 1024 if sr <= 48_000 else 2048 if sr <= 96_000 else 4096
        if sr < 32_000:
            nfft = 512
        return cls(nfft, nfft // 4)


def stft(x: np.ndarray, sr: int, cfg: StftConfig) -> np.ndarray:
    if x.shape[0] < 2 * cfg.nfft:  # very short input: pad; istft() crops back to length
        x = np.pad(x, (0, 2 * cfg.nfft - x.shape[0]))
    _, _, z = signal.stft(x, fs=sr, window="hann", nperseg=cfg.nfft, noverlap=cfg.nfft - cfg.hop,
                          boundary="even", padded=True)
    return z  # (bins, frames)


def istft(z: np.ndarray, sr: int, cfg: StftConfig, length: int) -> np.ndarray:
    _, y = signal.istft(z, fs=sr, window="hann", nperseg=cfg.nfft, noverlap=cfg.nfft - cfg.hop, boundary=True)
    if y.shape[0] < length:
        y = np.pad(y, (0, length - y.shape[0]))
    return y[:length].astype(np.float32)


@dataclass
class DenoiseParams:
    strength: float = 0.5  # 0..1, overall amount
    oversubtraction: float = 1.5  # scales the noise estimate (spectral subtraction strength)
    noise_offset_db: float = 0.0  # bias of the noise floor estimate
    max_attenuation_db: float = 18.0  # never reduce noise by more than this (keeps ambience)
    dd_alpha: float = 0.98  # decision-directed smoothing (higher = fewer musical-noise artefacts)

    @property
    def gain_floor(self) -> float:
        return 10 ** (-self.max_attenuation_db / 20)


class SpectralDenoiser:
    """Applies a :class:`NoiseProfile` to a mono or multichannel signal chunk."""

    def __init__(self, noise_psd: np.ndarray, sr: int, cfg: StftConfig, params: DenoiseParams):
        self.noise_psd = np.asarray(noise_psd, dtype=np.float64)
        self.sr = sr
        self.cfg = cfg
        self.params = params

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.ndim == 2:
            return np.stack([self._process_mono(x[:, c]) for c in range(x.shape[1])], axis=1)
        return self._process_mono(x)

    def _process_mono(self, x: np.ndarray) -> np.ndarray:
        p = self.params
        if p.strength <= 0:
            return x.astype(np.float32)
        z = stft(x, self.sr, self.cfg)
        power = np.abs(z) ** 2
        noise = self.noise_psd[:, None] * p.oversubtraction * 10 ** (p.noise_offset_db / 10) + EPS
        gain = self._wiener_dd(power, noise)
        # smooth gains across frequency and time a little: avoids isolated
        # bins flickering on/off ("musical noise")
        gain = ndimage.uniform_filter(gain, size=(3, 3), mode="nearest")
        # strength interpolates the attenuation in dB (perceptually even)
        floor = p.gain_floor
        gain = np.maximum(gain, floor)
        gain = gain ** p.strength
        return istft(z * gain, self.sr, self.cfg, x.shape[0])

    def _wiener_dd(self, power: np.ndarray, noise: np.ndarray) -> np.ndarray:  # noqa: D401
        alpha = self.params.dd_alpha
        n_bins, n_frames = power.shape
        gamma = power / noise  # a posteriori SNR
        gain = np.empty_like(power)
        prev = np.ones(n_bins)
        prev_gamma = np.ones(n_bins)
        for t in range(n_frames):
            g_t = gamma[:, t]
            xi = alpha * (prev**2) * prev_gamma + (1 - alpha) * np.maximum(g_t - 1.0, 0.0)
            xi = np.maximum(xi, 10 ** (-25 / 10))
            g = xi / (1.0 + xi)
            gain[:, t] = g
            prev, prev_gamma = g, g_t
        return gain


class TransientSuppressor:
    """Attenuates short impulsive noises (clicks, taps, keyboard) in speech pauses.

    Works on a 1 ms envelope of the >1 kHz band: an event that jumps at least
    ``threshold_db`` above the local background (100 ms median) and lasts no
    longer than ``max_len_ms`` is pulled down toward the background. Speech
    regions are left alone so plosives and consonant onsets are never touched;
    clicks under speech are left to the AI model.
    """

    def __init__(self, sr: int, strength: float, threshold_db: float = 12.0, max_len_ms: float = 25.0):
        self.sr = sr
        self.strength = float(np.clip(strength, 0, 1))
        self.threshold_db = threshold_db
        self.max_len_ms = max_len_ms
        self.events = 0

    def process(self, x: np.ndarray, speech_mask: np.ndarray, mask_hop_s: float, out: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        block = max(1, int(0.001 * self.sr))
        sos = signal.butter(2, 1000, btype="highpass", fs=self.sr, output="sos")
        mono = np.asarray(x, dtype=np.float32).mean(axis=1)
        hf = signal.sosfilt(sos, mono)
        nb = n // block
        env = np.sqrt(np.mean(hf[: nb * block].reshape(nb, block) ** 2, axis=1) + 1e-12)
        bg = ndimage.median_filter(env, size=101, mode="nearest")
        ratio_db = 20 * np.log10(env / (bg + 1e-12))
        hot = ratio_db > self.threshold_db
        # keep only short events
        labels, count = ndimage.label(hot)
        if count:
            sizes = ndimage.sum(hot, labels, index=np.arange(1, count + 1))
            too_long = np.flatnonzero(sizes > self.max_len_ms) + 1
            hot[np.isin(labels, too_long)] = False
        # outside speech only (speech mask is on a 10 ms grid, with hangover)
        t_blocks = (np.arange(nb) * block / self.sr / mask_hop_s).astype(int)
        in_speech = speech_mask[np.clip(t_blocks, 0, max(0, speech_mask.shape[0] - 1))] if speech_mask.size else \
            np.zeros(nb, bool)
        hot &= ~in_speech
        hot = ndimage.binary_dilation(hot, structure=np.ones(5))  # cover attack and decay
        self.events = int(ndimage.label(hot)[1])
        gain_db = np.where(hot, np.maximum(-ratio_db.clip(min=0) * self.strength, -30.0), 0.0)
        gain_db = np.minimum(gain_db, ndimage.minimum_filter1d(gain_db, 5))
        gain_db = ndimage.uniform_filter1d(gain_db, 3)  # ~3 ms ramps
        g = 10 ** (np.interp(np.arange(n), (np.arange(nb) + 0.5) * block, gain_db) / 20)
        out[:] = np.asarray(x, dtype=np.float32) * g[:, None].astype(np.float32)
        return out
