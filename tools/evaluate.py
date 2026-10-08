"""Objective evaluation on the synthetic test set.

For every degraded recording in ``test_audio`` that has a clean reference,
runs the pipeline and reports, before -> after:

* STOI  - short-time objective intelligibility (0..1, higher is better)
* SI-SDR - scale-invariant signal-to-distortion ratio in dB

Both are computed at 16 kHz against the clean reference speech.

Usage:  python tools/evaluate.py [--preset "Clean Voice"] [--device auto|cpu|cuda] [--no-ai]
"""
from __future__ import annotations

import argparse
import logging
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.model_manager import ModelManager  # noqa: E402
from app.audio.analyzer import analyze  # noqa: E402
from app.audio.loader import load_wav  # noqa: E402
from app.audio.pipeline import EnhancementPipeline  # noqa: E402
from app.audio.settings import auto_configure  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

EVAL_SR = 16_000


def to_eval(x: np.ndarray, sr: int) -> np.ndarray:
    x = x.mean(axis=1) if x.ndim == 2 else x
    g = gcd(sr, EVAL_SR)
    return signal.resample_poly(x, EVAL_SR // g, sr // g).astype(np.float64)


def align(ref: np.ndarray, est: np.ndarray, max_lag: int = 800) -> np.ndarray:
    """Compensate small processing delays (filters) by cross-correlation."""
    n = min(ref.shape[0], est.shape[0])
    ref, est = ref[:n], est[:n]
    corr = signal.correlate(est, ref, mode="full", method="fft")
    mid = n - 1
    lag = int(np.argmax(np.abs(corr[mid - max_lag : mid + max_lag + 1]))) - max_lag
    if lag > 0:
        est = np.concatenate([est[lag:], np.zeros(lag)])
    elif lag < 0:
        est = np.concatenate([np.zeros(-lag), est[:lag]])
    return est


def si_sdr(ref: np.ndarray, est: np.ndarray) -> float:
    n = min(ref.shape[0], est.shape[0])
    ref, est = ref[:n] - ref[:n].mean(), est[:n] - est[:n].mean()
    alpha = np.dot(est, ref) / (np.dot(ref, ref) + 1e-12)
    target = alpha * ref
    return float(10 * np.log10(np.sum(target**2) / (np.sum((est - target) ** 2) + 1e-12)))


def stoi(ref: np.ndarray, est: np.ndarray) -> float:
    from pystoi import stoi as _stoi

    n = min(ref.shape[0], est.shape[0])
    return float(_stoi(ref[:n], est[:n], EVAL_SR, extended=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="Clean Voice")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--save", action="store_true", help="write outputs to test_audio/out")
    args = ap.parse_args()
    setup_logging(logging.WARNING)

    test_dir = ROOT / "test_audio"
    pipe = EnhancementPipeline(ModelManager(args.device))
    print(f"{'file':32s} {'STOI before':>11s} {'after':>6s}   {'SI-SDR before':>13s} {'after':>6s}  speed")
    rows = []
    for ref_path in sorted((test_dir / "reference").glob("*.wav")):
        noisy_path = test_dir / ref_path.name
        if not noisy_path.exists() or ref_path.name.startswith("clean"):
            continue
        audio = load_wav(noisy_path)
        analysis = analyze(audio.samples, audio.sample_rate)
        settings = auto_configure(args.preset, analysis).settings
        if args.no_ai:
            settings.use_ai = False
        result = pipe.run(audio, analysis, settings)
        if args.save:
            out = test_dir / "out"
            out.mkdir(exist_ok=True)
            sf.write(out / noisy_path.name, result.samples, result.sample_rate, subtype="PCM_24")
        ref, _ = sf.read(ref_path, dtype="float64")
        ref_e = to_eval(ref, audio.sample_rate)
        noisy_e = align(ref_e, to_eval(np.asarray(audio.samples), audio.sample_rate))
        enh_e = align(ref_e, to_eval(result.samples, audio.sample_rate))
        r = (stoi(ref_e, noisy_e), stoi(ref_e, enh_e), si_sdr(ref_e, noisy_e), si_sdr(ref_e, enh_e))
        rows.append(r)
        print(f"{noisy_path.name:32s} {r[0]:11.3f} {r[1]:6.3f}   {r[2]:13.1f} {r[3]:6.1f}  {result.realtime_factor:5.1f}x"
              + (f"  (QC: {', '.join(i.code for i in result.qc.issues)})" if result.qc.issues else ""))
    if rows:
        m = np.mean(rows, axis=0)
        print(f"{'mean':32s} {m[0]:11.3f} {m[1]:6.3f}   {m[2]:13.1f} {m[3]:6.1f}")


if __name__ == "__main__":
    main()
