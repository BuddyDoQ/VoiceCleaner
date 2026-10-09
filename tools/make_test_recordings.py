"""Generate synthetic test recordings for evaluating the enhancement pipeline.

Clean speech comes from the Windows speech synthesizer (System.Speech), or from
``say`` on macOS, so it is real, intelligible speech rather than tones. Degraded versions are made by
adding controlled noise / hum / reverb at known signal-to-noise ratios. The
clean signal is kept in ``test_audio/reference`` so objective metrics can be
computed against it (see ``tools/evaluate.py``).

Usage:  python tools/make_test_recordings.py [output_dir]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio import loudness  # noqa: E402

SR = 48_000
RNG = np.random.default_rng(1234)

TEXT_FEMALE = (
    "Welcome back to the show. Today we are talking about how small changes in your recording setup "
    "can make a big difference. First, find a quiet room. Soft furniture helps a lot. "
    "Second, keep the microphone close, about a hand's width from your mouth. "
    "And finally, always record a few seconds of silence before you start speaking."
)
TEXT_MALE = (
    "Thanks for having me. I started this project three years ago, mostly out of curiosity. "
    "We tested dozens of microphones, from cheap headsets to studio condensers. "
    "Honestly, the room mattered more than the microphone in almost every case."
)


MAC_VOICES = {"Microsoft Zira Desktop": "Samantha", "Microsoft David Desktop": "Fred"}


def tts(text: str, voice: str, rate: int = 0) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tts.wav"
        if sys.platform == "darwin":
            subprocess.run(["say", "-v", MAC_VOICES.get(voice, voice), "--file-format=WAVE",
                            f"--data-format=LEI16@{SR}", "-o", str(out), text], check=True, capture_output=True)
            x, sr = sf.read(out, dtype="float32")
            assert sr == SR
            return x
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SelectVoice('{voice}'); $s.Rate = {rate};"
            "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000, "
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{out}', $fmt); $s.Speak(@'\n{text}\n'@); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
        x, sr = sf.read(out, dtype="float32")
    assert sr == SR
    return x


def fallback_speech(seconds: float, f0: float) -> np.ndarray:
    """Crude formant-synthesised 'speech' for systems without a synthesizer."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    pitch = f0 * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * t))
    phase = 2 * np.pi * np.cumsum(pitch) / SR
    src = signal.sawtooth(phase) * 0.3
    out = np.zeros(n)
    for fc, bw in ((700, 110), (1220, 120), (2600, 160), (3400, 250)):
        b, a = signal.iirpeak(fc, fc / bw, fs=SR)
        out += signal.lfilter(b, a, src)
    syll = (np.sin(2 * np.pi * 3.5 * t) > -0.2) * (np.sin(2 * np.pi * 0.3 * t) > -0.6)
    env = signal.lfilter([0.01], [1, -0.99], syll.astype(float))
    return (out * env).astype(np.float32)


def normalize_lufs(x: np.ndarray, target: float) -> np.ndarray:
    lufs = loudness.integrated_loudness(x[:, None] if x.ndim == 1 else x, SR)
    return x * 10 ** ((target - lufs) / 20)


def pad_with_room_tone(x: np.ndarray, lead: float = 1.5, tail: float = 1.0) -> np.ndarray:
    return np.concatenate([np.zeros(int(lead * SR)), x, np.zeros(int(tail * SR))]).astype(np.float32)


def add_at_snr(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    # SNR relative to the active speech level (loudness), not the whole-file RMS
    s = loudness.integrated_loudness(clean[:, None], SR)
    n_rms_db = 10 * np.log10(np.mean(noise**2) + 1e-20)
    gain = 10 ** ((s - snr_db - n_rms_db) / 20)
    return clean + noise * gain


def white(n):
    return RNG.standard_normal(n)


def pink(n):
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    return signal.lfilter(b, a, RNG.standard_normal(n))


def hvac(n):
    t = np.arange(n) / SR
    rumble = signal.sosfilt(signal.butter(2, 300, fs=SR, output="sos"), pink(n)) * 4
    airflow = signal.sosfilt(signal.butter(2, [400, 3000], btype="band", fs=SR, output="sos"), pink(n))
    motor = 0.05 * np.sin(2 * np.pi * 118 * t) + 0.02 * np.sin(2 * np.pi * 236 * t)
    blade = 0.015 * np.sin(2 * np.pi * 410 * t) * (1 + 0.3 * np.sin(2 * np.pi * 2 * t))
    return rumble + airflow * 0.5 + motor + blade


def hum(n, f=60.0):
    t = np.arange(n) / SR
    return sum((0.5 / k) * np.sin(2 * np.pi * f * k * t + k) for k in range(1, 9))


def clicks(n, rate_hz=3.0):
    """Keyboard-like transient clicks at random times."""
    out = np.zeros(n)
    times = np.cumsum(RNG.exponential(1 / rate_hz, int(n / SR * rate_hz * 2)))
    burst = RNG.standard_normal(int(0.006 * SR)) * np.exp(-np.arange(int(0.006 * SR)) / (0.0015 * SR))
    burst = signal.sosfilt(signal.butter(2, 1500, btype="high", fs=SR, output="sos"), burst)
    for tt in times:
        i = int(tt * SR)
        if i + burst.shape[0] < n:
            out[i : i + burst.shape[0]] += burst * RNG.uniform(0.3, 1.0)
    return out


def room_ir(rt60: float, pre_delay: float = 0.004) -> np.ndarray:
    n = int(rt60 * 1.2 * SR)
    t = np.arange(n) / SR
    decay = np.exp(-6.91 * t / rt60)
    tail = RNG.standard_normal(n) * decay
    tail = signal.sosfilt(signal.butter(1, 5000, fs=SR, output="sos"), tail)  # air absorption
    ir = np.zeros(n)
    ir[int(pre_delay * SR)] = 1.0
    for d, g in ((0.011, 0.6), (0.017, 0.5), (0.023, 0.45), (0.031, 0.35)):  # early reflections
        ir[int((pre_delay + d) * SR)] += g * RNG.choice([-1, 1])
    ir += tail * 0.12
    return ir / np.sqrt(np.sum(ir**2))


def reverberate(x: np.ndarray, rt60: float) -> np.ndarray:
    y = signal.fftconvolve(x, room_ir(rt60))[: x.shape[0]]
    return y


def save(path: Path, x: np.ndarray, subtype: str = "PCM_16", sr: int = SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = np.max(np.abs(x))
    if peak > 0.99 and subtype != "FLOAT":
        x = x * (0.99 / peak)
    sf.write(path, x.astype(np.float32), sr, subtype=subtype)
    print(f"  wrote {path.name}")


def main(out_dir: Path):
    print("Synthesizing clean speech...")
    try:
        female = tts(TEXT_FEMALE, "Microsoft Zira Desktop")
        male = tts(TEXT_MALE, "Microsoft David Desktop")
    except Exception as exc:
        print(f"  speech synthesizer unavailable ({exc}); using formant fallback")
        female, male = fallback_speech(18, 210), fallback_speech(14, 115)

    female = normalize_lufs(pad_with_room_tone(female), -23)
    male = normalize_lufs(pad_with_room_tone(male), -23)
    n = female.shape[0]
    ref = out_dir / "reference"

    save(out_dir / "clean_speech.wav", female + white(n) * 10 ** (-80 / 20))
    save(ref / "clean_speech.wav", female)
    save(out_dir / "clean_speech_male.wav", male)
    save(ref / "clean_speech_male.wav", male)

    save(out_dir / "speech_white_noise.wav", add_at_snr(female, white(n), 10))
    save(ref / "speech_white_noise.wav", female)

    save(out_dir / "speech_hvac.wav", add_at_snr(male, hvac(male.shape[0]), 6))
    save(ref / "speech_hvac.wav", male)

    hum_noise = hum(n, 60) + 0.05 * white(n)
    save(out_dir / "speech_hum.wav", add_at_snr(female, hum_noise, 12))
    save(ref / "speech_hum.wav", female)

    save(out_dir / "speech_reverb.wav", normalize_lufs(reverberate(female, 0.9), -23) + white(n) * 1e-4)
    save(ref / "speech_reverb.wav", female)

    m = male.shape[0]
    wet = normalize_lufs(reverberate(male, 0.5), -23)
    mix = (add_at_snr(wet, hvac(m), 10) - wet) + (add_at_snr(wet, hum(m, 50), 20) - wet) \
        + (add_at_snr(wet, white(m), 22) - wet) + (add_at_snr(wet, clicks(m), 12) - wet) + wet
    save(out_dir / "speech_multiple_noises.wav", mix)
    save(ref / "speech_multiple_noises.wav", male)

    # format coverage
    noisy = add_at_snr(female, white(n), 15)
    save(out_dir / "format_stereo_dualmono_24bit.wav", np.stack([noisy, noisy], axis=1), "PCM_24")
    save(ref / "format_stereo_dualmono_24bit.wav", female)
    hi = signal.resample_poly(noisy, 2, 1)
    save(out_dir / "format_96k_float.wav", hi * 0.5, "FLOAT", sr=96_000)
    save(out_dir / "format_44k1_16bit.wav", signal.resample_poly(noisy, 147, 160), sr=44_100)
    clipped = np.clip(female * 10 ** (20 / 20), -0.6, 0.6) + white(n) * 0.002
    save(out_dir / "speech_clipped.wav", clipped)
    save(out_dir / "noise_only.wav", hvac(SR * 8) * 0.05)
    print("Done.")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test_audio")
