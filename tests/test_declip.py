import numpy as np

from app.audio.declip import DeclipReport, clip_mask, declip

SR = 48000


def _voice(seconds=3.0, seed=0):
    """Harmonics with an irregular, speech-like loudness envelope: like real speech, its
    loudest peak happens once (a strictly periodic test tone hits the same maximum on
    every cycle, which is exactly what clipping looks like)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds)) / SR
    x = sum(a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
            for a, f in ((0.5, 160), (0.3, 320), (0.15, 480), (0.05, 1200)))
    env = np.convolve(rng.random(int(seconds * 20) + 2), np.ones(3) / 3, mode="same")
    x *= np.interp(t, np.linspace(0, seconds, env.size), env) + 0.05
    return (x / np.abs(x).max() * 0.9).astype(np.float32)[:, None]


def _sdr(ref, est):
    return 10 * np.log10(np.sum(ref ** 2) / np.sum((ref - est) ** 2))


def test_clean_audio_is_untouched():
    x = _voice()
    rep = DeclipReport()
    assert np.array_equal(declip(x, rep), x) and rep.clipped_samples == 0


def test_rebuilds_clipped_peaks_and_keeps_everything_else():
    x = _voice()
    level = 0.45
    c = np.clip(x, -level, level)
    rep = DeclipReport()
    y = declip(c, rep)
    mask, _ = clip_mask(c[:, 0])
    assert rep.clipped_samples > 1000
    assert np.array_equal(y[~mask, 0], c[~mask, 0])  # unclipped samples exactly as recorded
    pos, neg = mask & (c[:, 0] > 0), mask & (c[:, 0] < 0)
    assert y[pos, 0].min() >= level * 0.98 - 1e-6 and y[neg, 0].max() <= -level * 0.98 + 1e-6  # beyond the clip
    assert _sdr(x, y) > _sdr(x, c) + 6  # much closer to the original than the clipped version


def test_input_is_never_changed_and_out_buffer_used():
    x = np.clip(_voice(), -0.4, 0.4)
    before = x.copy()
    out = np.zeros_like(x)
    y = declip(x, out=out)
    assert y is out and np.array_equal(x, before)


def test_pipeline_repairs_only_when_clipped(tmp_path):
    from app.audio.analyzer import analyze

    clipped = np.clip(_voice(), -0.45, 0.45) / 0.45 * 0.98
    assert analyze(clipped.astype(np.float32), SR).is_clipped
    assert not analyze(_voice(), SR).is_clipped
