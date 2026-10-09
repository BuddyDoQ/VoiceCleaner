import numpy as np
import pytest

from app.audio.pauses import PauseSettings, apply_cuts, find_cuts, removed_samples, shorten_pauses

SR = 48_000


def _burst(seconds, freq=220.0, level=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def _speech_with_pauses(pauses, noise=3e-4, seed=0):
    """Bursts of 'speech' separated by pauses of the given lengths (seconds)."""
    parts = [np.zeros(int(0.3 * SR), np.float32), _burst(1.0)]
    for p in pauses:
        parts += [np.zeros(int(p * SR), np.float32), _burst(1.0)]
    parts.append(np.zeros(int(0.3 * SR), np.float32))
    x = np.concatenate(parts)
    return x + noise * np.random.default_rng(seed).standard_normal(x.shape[0]).astype(np.float32)


def test_off_by_default_changes_nothing():
    x = _speech_with_pauses([2.0])
    y, removed = shorten_pauses(x, SR, PauseSettings())
    assert removed == 0.0 and y is x


def test_long_pauses_shortened_to_keep_length():
    x = _speech_with_pauses([2.0, 3.0])
    y, removed = shorten_pauses(x, SR, PauseSettings(enabled=True, keep_ms=250))
    assert removed == pytest.approx((2.0 - 0.25) + (3.0 - 0.25), abs=0.05)
    assert y.shape[0] == x.shape[0] - int(round(removed * SR))


def test_short_pauses_untouched():
    x = _speech_with_pauses([0.4, 0.5])  # word gaps, below the 0.7 s threshold
    assert find_cuts(x, SR, 0.7, 250) == []


def test_threshold_is_respected():
    x = _speech_with_pauses([1.0])
    assert find_cuts(x, SR, 1.5, 250) == []
    assert len(find_cuts(x, SR, 0.7, 250)) == 1


def test_remove_completely_is_an_explicit_choice():
    x = _speech_with_pauses([2.0])
    _, kept = shorten_pauses(x, SR, PauseSettings(enabled=True, keep_ms=250))
    _, removed_all = shorten_pauses(x, SR, PauseSettings(enabled=True, keep_ms=0))
    assert removed_all == pytest.approx(2.0 - 0.02, abs=0.03)  # only the 20 ms crossfade remains
    assert removed_all > kept


def test_leading_and_trailing_silence_not_touched():
    x = np.concatenate([np.zeros(3 * SR, np.float32), _burst(1.0), np.zeros(3 * SR, np.float32)])
    assert find_cuts(x, SR, 0.7, 250) == []  # that is the trim step's job


def test_speech_preserved_around_cuts():
    x = _speech_with_pauses([2.0])
    y, _ = shorten_pauses(x, SR, PauseSettings(enabled=True, keep_ms=250))
    # both bursts come through unchanged: same energy before and after shortening
    rms = lambda s: float(np.sqrt(np.mean(s**2)))  # noqa: E731
    first_x, first_y = x[int(0.3 * SR): int(1.3 * SR)], y[int(0.3 * SR): int(1.3 * SR)]
    second_x, second_y = x[-int(1.3 * SR): -int(0.3 * SR)], y[-int(1.3 * SR): -int(0.3 * SR)]
    assert rms(first_y) == pytest.approx(rms(first_x), rel=1e-3)
    assert rms(second_y) == pytest.approx(rms(second_x), rel=1e-3)
    np.testing.assert_array_equal(second_y, second_x)  # identical samples, only shifted in time


def test_joins_are_smooth():
    x = _speech_with_pauses([2.0], noise=0.01)  # noisy room tone across the cut
    cuts = find_cuts(x, SR, 0.7, 250)
    y = apply_cuts(x, cuts, SR)
    a = cuts[0][0]
    window = y[a - 2000: a + 2000]
    assert np.abs(np.diff(window)).max() < 0.06  # no step at the join
    assert removed_samples(cuts) == x.shape[0] - y.shape[0]


def test_stereo():
    x = _speech_with_pauses([2.0])
    st = np.stack([x, x * 0.5], axis=1)
    y, removed = shorten_pauses(st, SR, PauseSettings(enabled=True))
    assert y.shape[1] == 2 and removed > 1.5


def test_never_enabled_by_default_or_by_presets():
    from app.audio.settings import PRESETS, ProcessingSettings, from_preset

    assert ProcessingSettings().pause_shorten is False
    assert all(from_preset(name).pause_shorten is False for name in PRESETS)
    chosen = ProcessingSettings(pause_shorten=True, pause_keep_ms=0.0)
    for name in PRESETS:  # switching presets keeps the user's own choice
        s = from_preset(name, chosen)
        assert s.pause_shorten is True and s.pause_keep_ms == 0.0


def test_batch_applies_pause_choice(tmp_path):
    import soundfile as sf

    from app.audio.settings import from_preset
    from app.workers.batch_worker import BatchOptions, enhance_file
    from app.workers.processing_worker import TaskContext

    x = _speech_with_pauses([2.5])
    src = tmp_path / "take.wav"
    sf.write(src, x, SR, subtype="FLOAT")
    base = from_preset("Natural").copy(use_ai=False, resynthesis=0.0, quality_control=False)
    outs = {}
    for flag in (False, True):
        opts = BatchOptions(settings=base.copy(pause_shorten=flag), smart=False, output_dir=tmp_path / str(flag))
        opts.output_dir.mkdir()
        out, _ = enhance_file(src, opts, None, TaskContext(lambda f, m: None))
        outs[flag] = sf.info(out).duration
    assert outs[False] == pytest.approx(x.shape[0] / SR, abs=0.01)
    assert outs[False] - outs[True] == pytest.approx(2.5 - 0.25, abs=0.1)
