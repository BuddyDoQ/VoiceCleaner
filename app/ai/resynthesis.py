"""Neural voice re-synthesis with NVIDIA BigVGAN-v2 (44.1 kHz, 128-band, MIT).

The (already cleaned) voice is analysed into a log-mel spectrogram and then
*re-generated* by a universal neural vocoder. The vocoder only knows how to
produce natural-sounding voices, so leftovers that live between the mel bands
- musical-noise speckle, phase smear from spectral processing, faint hiss -
are not reproduced, and harmonics come out clean and coherent.

Re-synthesis strength blends magnitude and phase in the STFT domain, never
waveforms: two independently generated waveforms mixed together would
comb-filter.
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
from math import gcd
from pathlib import Path

import numpy as np
from scipy import signal

from ..audio.dsp import process_in_chunks
from ..utils.errors import ModelUnavailableError, ProcessingError
from ..utils.logging import get_logger

log = get_logger("resynthesis")

CHUNK_S = 10.0
OVERLAP_S = 0.5



def mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float = 0.0, fmax: float | None = None) -> np.ndarray:
    """Slaney-style mel filterbank (identical to ``librosa.filters.mel`` defaults)."""
    fmax = sr / 2 if fmax is None else fmax

    def hz_to_mel(f):
        f = np.asanyarray(f, dtype=np.float64)
        f_sp = 200.0 / 3
        mels = f / f_sp
        min_log_hz = 1000.0
        min_log_mel = min_log_hz / f_sp
        logstep = np.log(6.4) / 27.0
        return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, mels)

    def mel_to_hz(m):
        m = np.asanyarray(m, dtype=np.float64)
        f_sp = 200.0 / 3
        freqs = f_sp * m
        min_log_hz = 1000.0
        min_log_mel = min_log_hz / f_sp
        logstep = np.log(6.4) / 27.0
        return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), freqs)

    fftfreqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fftfreqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (mel_f[2 : n_mels + 2] - mel_f[:n_mels])
    return (weights * enorm[:, None]).astype(np.float32)


class BigVGANResynthesizer:
    key = "bigvgan_v2_44k"
    display_name = "BigVGAN-v2 (44 kHz)"
    license = "MIT"
    homepage = "https://github.com/NVIDIA/BigVGAN"

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.device = "cpu"
        self._model = None
        self._torch = None
        self._h: dict = {}
        self._mel_basis = None
        self._window = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        return (self.model_dir / "config.json").is_file() and (self.model_dir / "bigvgan_generator.pt").is_file()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def sample_rate(self) -> int:
        return int(self._h.get("sampling_rate", 44100))

    def load(self, device: str):
        with self._lock:
            if self.loaded and device == self.device:
                return
            if not self.is_installed():
                raise ModelUnavailableError(
                    "The voice re-synthesis model is not installed. Download it from the Re-synthesis "
                    "section, or run tools/download_models.py.")
            import torch

            import warnings

            warnings.filterwarnings("ignore", message=".*weight_norm.*")
            from .vendor.bigvgan import AttrDict, BigVGAN

            self._h = AttrDict(json.loads((self.model_dir / "config.json").read_text(encoding="utf-8")))
            model = BigVGAN(self._h, use_cuda_kernel=False)
            import warnings

            warnings.filterwarnings("ignore", message=".*weight_norm.*")
            state = torch.load(self.model_dir / "bigvgan_generator.pt", map_location="cpu", weights_only=True)
            model.load_state_dict(state["generator"])
            with contextlib.redirect_stdout(io.StringIO()):  # it prints a status line
                model.remove_weight_norm()
            self._model = model.eval().to(device)
            self._torch = torch
            self.device = device
            h = self._h
            self._mel_basis = torch.from_numpy(
                mel_filterbank(h.sampling_rate, h.n_fft, h.num_mels, h.fmin, h.fmax)).to(device)
            self._window = torch.hann_window(h.win_size).to(device)
            log.info("Loaded %s on %s", self.display_name, device)

    def unload(self):
        with self._lock:
            self._model = None
            if self._torch is not None and self.device.startswith("cuda"):
                self._torch.cuda.empty_cache()

    # --- core ---------------------------------------------------------------------
    def log_mel(self, y):
        """BigVGAN's analysis: reflect pad, |STFT|, mel, log(clamp(1e-5))."""
        torch, h = self._torch, self._h
        pad = (h.n_fft - h.hop_size) // 2
        y = torch.nn.functional.pad(y.unsqueeze(1), (pad, pad), mode="reflect").squeeze(1)
        spec = torch.stft(y, h.n_fft, hop_length=h.hop_size, win_length=h.win_size, window=self._window,
                          center=False, normalized=False, onesided=True, return_complex=True)
        mag = torch.sqrt(spec.real**2 + spec.imag**2 + 1e-9)
        return torch.log(torch.clamp(self._mel_basis @ mag, min=1e-5))

    def vocode(self, x: np.ndarray) -> np.ndarray:
        """Re-synthesise mono ``x`` at the model rate; output has the same length."""
        torch = self._torch
        n = x.shape[0]
        hop = self._h.hop_size
        padded = np.pad(x, (0, (-n) % hop)).astype(np.float32)
        peak = float(np.abs(padded).max(initial=0.0))
        scale = 0.95 / peak if peak > 0.95 else 1.0  # the analysis expects [-1, 1]
        with torch.inference_mode():
            y = torch.from_numpy(padded * scale).to(self.device)[None]
            out = self._model(self.log_mel(y))[0, 0].float().cpu().numpy()
        return (out[:n] / scale).astype(np.float32)


class Resynthesis:
    """Pipeline-facing wrapper: rate conversion, chunking, strength, GPU fallback."""

    def __init__(self, manager):
        self.manager = manager

    def process(self, x: np.ndarray, sr: int, strength: float, progress=None, check_cancel=None) -> np.ndarray:
        """``x``: (frames, channels). Returns the same shape at the same rate."""
        model = self.manager.get_resynthesizer()
        msr = model.sample_rate
        g = gcd(sr, msr)

        def run(chunk: np.ndarray) -> np.ndarray:
            if check_cancel:
                check_cancel()
            y = signal.resample_poly(chunk, msr // g, sr // g).astype(np.float32) if sr != msr else chunk
            try:
                v = model.vocode(y)
            except Exception as exc:
                if self.manager.device.kind == "cuda" and "cuda" in f"{exc}".lower() + type(exc).__name__.lower():
                    log.warning("GPU re-synthesis failed (%s); retrying on CPU", exc)
                    self.manager.resynth_fallback_to_cpu()
                    v = self.manager.get_resynthesizer().vocode(y)
                else:
                    log.exception("Re-synthesis failed")
                    raise ProcessingError("Voice re-synthesis failed for this recording.") from exc
            if sr != msr:
                v = signal.resample_poly(v, sr // g, msr // g).astype(np.float32)
            v = np.pad(v, (0, max(0, chunk.shape[0] - v.shape[0])))[: chunk.shape[0]]
            return blend_magnitudes(chunk, v, sr, strength)

        out = np.empty_like(x, dtype=np.float32)
        channels = x.shape[1]
        for c in range(channels):
            col = process_in_chunks(np.asarray(x[:, c], dtype=np.float32), run, int(CHUNK_S * sr), int(OVERLAP_S * sr),
                                    progress=(lambda f, c=c: progress((c + f) / channels)) if progress else None)
            out[:, c] = col
        return out


def blend_magnitudes(original: np.ndarray, resynth: np.ndarray, sr: int, strength: float) -> np.ndarray:
    """Mix original and re-synthesised voice coherently.

    Magnitudes are interpolated in the log domain and phases as unit phasors,
    so partial strengths move smoothly between the two without the comb
    filtering a plain waveform mix would cause. At full strength the
    re-synthesis is returned unchanged.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength >= 0.999:
        return resynth
    if strength <= 0.001:
        return original.astype(np.float32)
    nfft = 2048 if sr >= 32000 else 1024
    kw = dict(fs=sr, window="hann", nperseg=nfft, noverlap=nfft * 3 // 4)
    _, _, zo = signal.stft(original, **kw)
    _, _, zr = signal.stft(resynth, **kw)
    mo, mr = np.abs(zo) + 1e-9, np.abs(zr) + 1e-9
    mag = np.exp((1 - strength) * np.log(mo) + strength * np.log(mr))
    # phase: interpolate unit phasors, so low strengths keep the original phase
    # (and timing) while high strengths take on the re-synthesised harmonics
    phasor = (1 - strength) * zo / mo + strength * zr / mr
    phasor /= np.abs(phasor) + 1e-9
    _, y = signal.istft(mag * phasor, **kw)
    y = np.pad(y, (0, max(0, original.shape[0] - y.shape[0])))[: original.shape[0]]
    return y.astype(np.float32)
