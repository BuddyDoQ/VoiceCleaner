"""Running an enhancement model over recordings of any length.

* resamples to the model rate and back (polyphase, high quality)
* splits long audio into overlapping chunks (default 20 s with 1 s
  overlap) and cross-fades them, so memory stays bounded and seams are
  inaudible
* falls back from GPU to CPU on out-of-memory or CUDA errors
"""
from __future__ import annotations

from math import gcd

import numpy as np
from scipy import signal

from ..audio.dsp import process_in_chunks
from ..utils.errors import ProcessingError
from ..utils.logging import get_logger
from .model_manager import ModelManager

log = get_logger("inference")

DEFAULT_CHUNK_S = 20.0
DEFAULT_OVERLAP_S = 1.0


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return np.asarray(x, dtype=np.float32)
    g = gcd(sr_from, sr_to)
    return signal.resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def strength_to_attenuation_db(strength: float) -> float | None:
    """Map 0..1 noise-reduction strength to DeepFilterNet's attenuation limit.

    0.25 -> ~8 dB, 0.5 -> ~16 dB, 0.75 -> ~27 dB, 1.0 -> unlimited.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength >= 0.99:
        return None
    return 40.0 * strength**1.3


class ModelInference:
    def __init__(self, manager: ModelManager, chunk_s: float = DEFAULT_CHUNK_S, overlap_s: float = DEFAULT_OVERLAP_S):
        self.manager = manager
        self.chunk_s = chunk_s
        self.overlap_s = overlap_s

    def enhance(self, x: np.ndarray, sr: int, strength: float, progress=None, check_cancel=None,
                max_attenuation_db: float | None = None) -> np.ndarray:
        """Enhance mono ``x`` (1-D, any rate). Returns same length and rate."""
        model = self.manager.get()
        atten = strength_to_attenuation_db(strength)
        if max_attenuation_db is not None:
            atten = max_attenuation_db if atten is None else min(atten, max_attenuation_db)
        msr = model.sample_rate
        n = x.shape[0]

        def run_chunk(chunk: np.ndarray) -> np.ndarray:
            if check_cancel:
                check_cancel()
            y = resample(chunk, sr, msr)
            out = self._infer(y, atten)
            out = resample(out, msr, sr)
            # resampling round trip can differ by a sample or two
            if out.shape[0] < chunk.shape[0]:
                out = np.pad(out, (0, chunk.shape[0] - out.shape[0]))
            return out[: chunk.shape[0]]

        chunk = int(self.chunk_s * sr)
        overlap = int(self.overlap_s * sr)
        result = process_in_chunks(np.asarray(x, dtype=np.float32), run_chunk, chunk, overlap, progress=progress)
        return result[:n]

    def _infer(self, x: np.ndarray, atten: float | None) -> np.ndarray:
        model = self.manager.get()
        try:
            return model.enhance(x, atten)
        except Exception as exc:
            if self.manager.device.kind == "cuda" and _is_gpu_error(exc):
                log.warning("GPU inference failed (%s); falling back to CPU", exc)
                self.manager.fallback_to_cpu()
                return self.manager.get().enhance(x, atten)
            log.exception("Model inference failed")
            raise ProcessingError("The AI enhancement model failed while processing this audio.") from exc


def _is_gpu_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("cuda", "out of memory", "cublas", "cudnn", "device-side"))
