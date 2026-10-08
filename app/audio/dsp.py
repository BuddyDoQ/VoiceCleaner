"""Small DSP building blocks shared by the processing stages."""
from __future__ import annotations

from typing import Iterator

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import signal

EPS = 1e-12


def db(x: np.ndarray | float, floor: float = EPS) -> np.ndarray | float:
    """Power ratio to decibels."""
    return 10 * np.log10(np.maximum(x, floor))


def db_amp(x: np.ndarray | float, floor: float = 1e-9) -> np.ndarray | float:
    return 20 * np.log10(np.maximum(np.abs(x), floor))


def from_db_amp(x: np.ndarray | float) -> np.ndarray | float:
    return 10 ** (np.asarray(x) / 20)


def next_pow2(n: int) -> int:
    return 1 << max(0, int(np.ceil(np.log2(max(1, n)))))


def analysis_fft_size(sr: int, seconds: float = 0.04) -> int:
    return next_pow2(int(sr * seconds))


def iter_power_frames(
    mono: np.ndarray, nfft: int, hop: int, frames_per_block: int = 2048
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield ``(first_frame_index, power_spectra[frames, bins])`` in blocks.

    Frame ``i`` covers ``mono[i*hop : i*hop + nfft]`` (zero padded at the end).
    Scaled so that summing a frame's bins gives the frame's mean square
    (one-sided spectrum, window-compensated). Streams over the input so it is
    safe for disk-backed arrays.
    """
    window = np.hanning(nfft).astype(np.float32)
    norm = 2.0 / (nfft * np.sum(window**2))
    n_frames = max(1, int(np.ceil(mono.shape[0] / hop)))
    for f0 in range(0, n_frames, frames_per_block):
        f1 = min(n_frames, f0 + frames_per_block)
        a = f0 * hop
        b = (f1 - 1) * hop + nfft
        seg = np.asarray(mono[a:b], dtype=np.float32)
        if seg.shape[0] < b - a:
            seg = np.pad(seg, (0, b - a - seg.shape[0]))
        frames = sliding_window_view(seg, nfft)[::hop][: f1 - f0] * window
        spec = np.fft.rfft(frames, axis=1)
        yield f0, (spec.real**2 + spec.imag**2) * norm


def band_edges_bins(freqs: np.ndarray, lo: float, hi: float) -> slice:
    i0 = int(np.searchsorted(freqs, lo))
    i1 = int(np.searchsorted(freqs, hi))
    return slice(i0, max(i0 + 1, i1))


def third_octave_centers(lo: float = 50.0, hi: float = 20_000.0) -> np.ndarray:
    n = np.arange(-20, 14)
    centers = 1000.0 * 2.0 ** (n / 3)
    return centers[(centers >= lo) & (centers <= hi)]


def band_average(freqs: np.ndarray, power: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Average power in 1/3 octave bands (last axis of ``power`` = frequency)."""
    out = np.zeros(power.shape[:-1] + (centers.shape[0],))
    for i, fc in enumerate(centers):
        sl = band_edges_bins(freqs, fc / 2 ** (1 / 6), fc * 2 ** (1 / 6))
        out[..., i] = power[..., sl].mean(axis=-1)
    return out


# --- biquads (RBJ audio EQ cookbook) ------------------------------------------------

def _norm(b, a) -> np.ndarray:
    b = np.asarray(b, dtype=np.float64) / a[0]
    a = np.asarray(a, dtype=np.float64) / a[0]
    return np.concatenate([b, a])


def peaking(f0: float, gain_db: float, q: float, sr: int) -> np.ndarray:
    a_ = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * min(f0, sr * 0.45) / sr
    alpha = np.sin(w0) / (2 * q)
    cw = np.cos(w0)
    return _norm([1 + alpha * a_, -2 * cw, 1 - alpha * a_], [1 + alpha / a_, -2 * cw, 1 - alpha / a_])


def low_shelf(f0: float, gain_db: float, sr: int, s: float = 1.0) -> np.ndarray:
    a_ = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / sr
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / 2 * np.sqrt((a_ + 1 / a_) * (1 / s - 1) + 2)
    sa = 2 * np.sqrt(a_) * alpha
    return _norm(
        [a_ * ((a_ + 1) - (a_ - 1) * cw + sa), 2 * a_ * ((a_ - 1) - (a_ + 1) * cw), a_ * ((a_ + 1) - (a_ - 1) * cw - sa)],
        [(a_ + 1) + (a_ - 1) * cw + sa, -2 * ((a_ - 1) + (a_ + 1) * cw), (a_ + 1) + (a_ - 1) * cw - sa],
    )


def high_shelf(f0: float, gain_db: float, sr: int, s: float = 1.0) -> np.ndarray:
    a_ = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * min(f0, sr * 0.45) / sr
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / 2 * np.sqrt((a_ + 1 / a_) * (1 / s - 1) + 2)
    sa = 2 * np.sqrt(a_) * alpha
    return _norm(
        [a_ * ((a_ + 1) + (a_ - 1) * cw + sa), -2 * a_ * ((a_ - 1) + (a_ + 1) * cw), a_ * ((a_ + 1) + (a_ - 1) * cw - sa)],
        [(a_ + 1) - (a_ - 1) * cw + sa, 2 * ((a_ - 1) - (a_ + 1) * cw), (a_ + 1) - (a_ - 1) * cw - sa],
    )


def highpass_sos(cutoff: float, sr: int, order: int = 4) -> np.ndarray:
    return signal.butter(order, cutoff, btype="highpass", fs=sr, output="sos")


def notch_sos(freqs: list[float], sr: int, q: float = 30.0) -> np.ndarray:
    rows = []
    for f in freqs:
        if 0 < f < sr * 0.45:
            b, a = signal.iirnotch(f, q, fs=sr)
            rows.append(np.concatenate([b, a]))
    return np.array(rows) if rows else np.zeros((0, 6))


def sos_response_db(sos: np.ndarray, freqs: np.ndarray, sr: int) -> np.ndarray:
    if sos.shape[0] == 0:
        return np.zeros_like(freqs)
    _, h = signal.sosfreqz(sos, worN=freqs, fs=sr)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


# --- chunking ---------------------------------------------------------------------

def chunk_spans(n: int, chunk: int, overlap: int) -> list[tuple[int, int]]:
    """Overlapping ``(start, end)`` spans covering ``range(n)``."""
    if n <= chunk:
        return [(0, n)]
    step = chunk - overlap
    spans = []
    start = 0
    while True:
        end = min(n, start + chunk)
        spans.append((start, end))
        if end >= n:
            break
        start += step
    return spans


def equal_power_fades(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Fade-in / fade-out curves that sum to 1 (raised cosine, complementary)."""
    t = (np.arange(n) + 0.5) / n
    fade_in = (0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32)
    return fade_in, 1.0 - fade_in


def process_in_chunks(x: np.ndarray, fn, chunk: int, overlap: int, out: np.ndarray | None = None,
                      progress=None) -> np.ndarray:
    """Apply ``fn`` (array -> same-length array) to overlapping chunks and
    cross-fade the overlaps so chunk boundaries are inaudible.

    ``x`` is 1-D or 2-D (frames first). Raised-cosine crossfades sum to unity,
    so where neighbouring chunks agree the output is unchanged.
    """
    n = x.shape[0]
    if out is None:
        out = np.zeros(x.shape, dtype=np.float32)
    spans = chunk_spans(n, chunk, overlap)
    prev_end = 0
    for i, (a, b) in enumerate(spans):
        y = np.asarray(fn(np.asarray(x[a:b], dtype=np.float32)), dtype=np.float32)
        if y.shape[0] != b - a:
            raise ValueError("chunk function changed the signal length")
        if i == 0:
            out[a:b] = y
        else:
            ov = prev_end - a
            fade_in, fade_out = equal_power_fades(ov)
            if y.ndim == 2:
                fade_in, fade_out = fade_in[:, None], fade_out[:, None]
            out[a:prev_end] = out[a:prev_end] * fade_out + y[:ov] * fade_in
            out[prev_end:b] = y[ov:]
        prev_end = b
        if progress is not None:
            progress((i + 1) / len(spans))
    return out
