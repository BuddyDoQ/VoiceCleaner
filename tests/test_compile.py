import numpy as np
import pytest
import soundfile as sf

from app.audio import loudness
from app.audio.compile import assemble, detect_sound_bounds, load_take, retrim, trim_points

SR = 48_000


def _take(tmp_path, name, lead_s, body_s, tail_s, level=0.3, sr=SR, channels=1, noise=1e-5):
    rng = np.random.default_rng(len(name))
    t = np.arange(int(body_s * sr)) / sr
    body = level * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    x = np.concatenate([np.zeros(int(lead_s * sr)), body, np.zeros(int(tail_s * sr))])
    x = x + noise * rng.standard_normal(x.shape[0])
    x = np.repeat(x[:, None], channels, axis=1).astype(np.float32)
    p = tmp_path / name
    sf.write(p, x, sr, subtype="PCM_24")
    return p


def test_bounds_find_the_sound(tmp_path):
    p = _take(tmp_path, "a.wav", 1.0, 2.0, 1.5)
    x, sr = sf.read(p, dtype="float32", always_2d=True)
    first, last = detect_sound_bounds(x, sr)
    assert abs(first / sr - 1.0) < 0.02 and abs(last / sr - 3.0) < 0.02


@pytest.mark.parametrize("handle_ms", [0, 100, 250])
def test_trim_keeps_up_to_handle(tmp_path, handle_ms):
    p = _take(tmp_path, "a.wav", 1.0, 2.0, 1.5)
    x, sr = sf.read(p, dtype="float32", always_2d=True)
    start, end = trim_points(x, sr, handle_ms)
    assert abs((1.0 - start / sr) * 1000 - handle_ms) < 20
    assert abs((end / sr - 3.0) * 1000 - handle_ms) < 20


def test_short_silence_is_kept_not_padded(tmp_path):
    # only 40 ms of silence before the sound: nothing to trim, nothing invented
    p = _take(tmp_path, "a.wav", 0.04, 1.0, 0.04)
    x, sr = sf.read(p, dtype="float32", always_2d=True)
    start, end = trim_points(x, sr, 100)
    assert start == 0 and end == x.shape[0]


def test_silent_take(tmp_path):
    p = tmp_path / "silent.wav"
    sf.write(p, np.full(SR, 1e-6, np.float32), SR, subtype="FLOAT")
    t = load_take(p)
    assert t.silent


def test_assemble_order_trim_and_gap(tmp_path):
    a = load_take(_take(tmp_path, "a.wav", 1.0, 1.0, 1.0))
    b = load_take(_take(tmp_path, "b.wav", 0.5, 2.0, 0.5))
    comp = assemble([b, a], gap_ms=300, match_loudness=False)
    names = [s[2] for s in comp.segments]
    assert names == ["b.wav", "a.wav"]  # the given order, not file order
    expected = (2.0 + 0.2) + 0.3 + (1.0 + 0.2)
    assert abs(comp.duration - expected) < 0.05
    assert abs(comp.removed_seconds - ((1.0 - 0.2) + (2.0 - 0.2))) < 0.05
    assert abs(comp.segments[1][0] - comp.segments[0][1] - 0.3) < 0.002


def test_assemble_levels_and_limits(tmp_path):
    quiet = load_take(_take(tmp_path, "q.wav", 0.5, 2.0, 0.5, level=0.05))
    loud = load_take(_take(tmp_path, "l.wav", 0.5, 2.0, 0.5, level=0.8))
    comp = assemble([quiet, loud], match_loudness=True)
    sr = comp.sample_rate
    seg = [comp.samples[int(a * sr):int(b * sr)] for a, b, _ in comp.segments]
    l1, l2 = (loudness.integrated_loudness(s, sr) for s in seg)
    assert abs(l1 - l2) < 1.0
    assert loudness.true_peak_db(comp.samples, sr) <= -1.0 + 0.05


def test_assemble_mixed_formats(tmp_path):
    mono48 = load_take(_take(tmp_path, "m.wav", 0.2, 1.0, 0.2))
    stereo44 = load_take(_take(tmp_path, "s.wav", 0.2, 1.0, 0.2, sr=44_100, channels=2))
    comp = assemble([mono48, stereo44], match_loudness=False)
    assert comp.sample_rate == 48_000 and comp.samples.shape[1] == 2
    assert abs(comp.duration - 2 * 1.2) < 0.05


def test_joins_do_not_click(tmp_path):
    # takes cut mid-tone: the 5 ms fades keep the join smooth
    a = load_take(_take(tmp_path, "a.wav", 0.0, 1.0, 0.0, noise=0))
    b = load_take(_take(tmp_path, "b.wav", 0.0, 1.0, 0.0, noise=0))
    comp = assemble([a, b], match_loudness=False)
    join = int(comp.segments[1][0] * comp.sample_rate)
    jump = np.abs(np.diff(comp.samples[join - 5: join + 5, 0])).max()
    assert jump < 0.02


def test_retrim_changes_handles(tmp_path):
    t = load_take(_take(tmp_path, "a.wav", 1.0, 1.0, 1.0), handle_ms=100)
    d100 = t.trimmed_duration
    retrim(t, 300)
    assert abs((t.trimmed_duration - d100) - 0.4) < 0.03


def test_no_audible_takes_raises(tmp_path):
    p = tmp_path / "silent.wav"
    sf.write(p, np.zeros(SR, np.float32) + 1e-6, SR, subtype="FLOAT")
    with pytest.raises(ValueError):
        assemble([load_take(p)])


def test_noisy_raw_take_is_trimmed(tmp_path):
    # un-enhanced take with audible room noise (-40 dBFS): trimming must still find the speech
    p = _take(tmp_path, "noisy.wav", 1.0, 2.0, 1.5, noise=0.01)
    x, sr = sf.read(p, dtype="float32", always_2d=True)
    start, end = trim_points(x, sr, 100)
    assert abs(start / sr - 0.9) < 0.05 and abs(end / sr - 3.1) < 0.05


def test_pause_shortening_in_compilation(tmp_path):
    from app.audio.pauses import PauseSettings

    sr = SR
    t = np.arange(sr) / sr
    burst = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    x = np.concatenate([burst, np.zeros(3 * sr, np.float32), burst]) + 1e-4 * np.random.default_rng(1).standard_normal(5 * sr).astype(np.float32)
    p = tmp_path / "paused.wav"
    sf.write(p, x, sr, subtype="FLOAT")
    plain = load_take(p)
    short = load_take(p, pauses=PauseSettings(enabled=True, min_pause_s=0.7, keep_ms=250))
    assert plain.cuts == [] and len(short.cuts) == 1
    assert abs(short.pause_seconds - 2.75) < 0.05
    comp = assemble([short], match_loudness=False)
    assert abs(comp.duration - short.final_duration) < 0.01
    assert abs(comp.pause_seconds - 2.75) < 0.05
    retrim(short, 100, PauseSettings())  # turning it off again restores the pause
    assert short.cuts == []
