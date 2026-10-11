"""1.0.6: loop playback, live preview, preview overlay, compile position mapping,
undo/redo of settings and the activity time estimates."""
from types import SimpleNamespace

import numpy as np
import pytest

SR = 48_000


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _player(qapp, x, sr=1000):
    from app.ui.audio_player import AudioPlayer

    p = AudioPlayer()
    p.set_source("original", x, sr)
    p.set_source("enhanced", x * 2, sr)
    p._gains = {"original": 1.0, "enhanced": 1.0}
    return p


def _run(p, frames):
    out = np.zeros((frames, 2), np.float32)
    p._callback(out, frames, None, None)
    return out[:, 0]


def test_loop_wraps_with_soft_edges(qapp):
    x = (np.arange(10_000, dtype=np.float32) / 10_000)[:, None]
    p = _player(qapp, x)
    p.set_loop((2.0, 2.5))
    assert p._pos == 2000 and p.loop == (2.0, 2.5)
    y = _run(p, 700)
    assert p._pos == 2200  # 700 frames into a 500-frame loop
    assert y[499] == 0.0 and y[500] == 0.0  # faded at the seam: no click
    assert y[250] == pytest.approx(0.225, abs=1e-6)  # untouched in the middle
    p.seek(9.0)
    assert p._pos == 2000  # a click outside the loop restarts it
    p.set_loop(None)
    p.seek(9.0)
    assert p._pos == 9000


def test_overlay_replaces_only_its_range(qapp):
    x = np.ones((5000, 1), np.float32) * 0.1
    p = _player(qapp, x)
    p.set_active("enhanced")
    p._fade_from = None
    p.set_overlay("enhanced", 1000, np.full((500, 1), 0.7, np.float32))
    p.seek(0.9)
    y = _run(p, 300)
    assert np.allclose(y[:100], 0.2) and np.allclose(y[100:300], 0.7)
    p.set_overlay("enhanced", None)
    p.seek(1.0)
    assert np.allclose(_run(p, 50), 0.2)


def test_overview_patch_matches_full_recompute():
    from app.ui.waveform import WaveformOverview

    rng = np.random.default_rng(0)
    base = rng.standard_normal((20_000, 1)).astype(np.float32) * 0.1
    piece = rng.standard_normal((3_001, 1)).astype(np.float32)
    combined = base.copy()
    combined[7_013:10_014] = piece
    patched = WaveformOverview.compute(base, SR).patched(7_013, piece, base)
    full = WaveformOverview.compute(combined, SR)
    assert np.allclose(patched.maxs, full.maxs) and np.allclose(patched.rms, full.rms)


def test_preview_matches_the_full_run(noisy):
    """The live preview of a window sounds (and measures) like that passage of a full Enhance."""
    from app.audio import loudness
    from app.audio.analyzer import analyze
    from app.audio.loader import AudioData
    from app.audio.pipeline import EnhancementPipeline, slice_analysis
    from app.audio.settings import auto_configure

    x = np.tile(noisy[0], 3)[:, None]  # 18 s
    audio = AudioData(x, SR)
    an = analyze(x, SR)
    s = auto_configure("Clean Voice", an).settings.copy(use_ai=False, resynthesis=0.0)
    pipe = EnhancementPipeline(None)
    full = pipe.run(audio, an, s)
    pr = pipe.preview(audio, an, s, 6.0, 12.0)
    assert pr.window == (6.0, 12.0)
    seg = full.samples[pr.start_frame : pr.start_frame + pr.samples.shape[0]]
    core = slice(SR // 2, -SR // 2)
    lf = loudness.integrated_loudness(seg[core], SR)
    lp = loudness.integrated_loudness(pr.samples[core], SR)
    assert abs(lf - lp) < 1.0, (lf, lp)
    assert np.corrcoef(seg[core, 0], pr.samples[core, 0])[0, 1] > 0.98

    part = slice_analysis(an, 6 * SR, 12 * SR, SR)
    assert part.duration == pytest.approx(6.0)
    assert part.speech_mask.shape[0] == pytest.approx(6.0 / an.frame_hop_s, abs=2)
    assert all(0 <= a < b <= 6.0 for a, b in part.silence_regions)


def test_map_position_keeps_the_same_take():
    from app.ui.compile_panel import map_position

    old = SimpleNamespace(segments=[(0, 2, "a"), (2, 5, "b"), (5, 6, "c")], duration=6)
    new = SimpleNamespace(segments=[(0, 3, "b"), (3, 5, "a"), (5, 6, "c")], duration=6)
    assert map_position(old, new, 3.5) == pytest.approx(1.5)  # 1.5 s into "b"
    assert map_position(old, new, 1.0) == pytest.approx(4.0)  # 1 s into "a"
    gone = SimpleNamespace(segments=[(0, 1, "c")], duration=1)
    assert map_position(old, gone, 3.5) == pytest.approx(0.99)  # take removed: clamped


def test_undo_redo_settings(qapp):
    from app.audio.settings import from_preset
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=False)
    assert not p.can_undo() and not p.can_redo()
    lev = p._advanced["leveler_amount"]
    for v in (0.6, 0.7, 0.8):  # one drag = one step
        lev.set_value(v, emit=True)
    p._settle.stop()
    p._commit()
    p._advanced["comp_ratio"].set_value(5.0, emit=True)
    assert p.undo_text() == "Undo Ratio"  # names the pending change
    p.undo()
    assert p.current().comp_ratio != 5.0 and p.current().leveler_amount == 0.8
    assert p.redo_text() == "Redo Ratio"
    p.undo()
    assert p.current().leveler_amount == from_preset("Clean Voice").leveler_amount
    p.redo()
    p.redo()
    assert p.current().comp_ratio == 5.0
    p.undo()
    p._advanced["eq_tilt"].set_value(0.5, emit=True)  # a new change drops the redo step
    p._settle.stop()
    p._commit()
    assert not p.can_redo()


def test_opening_a_recording_is_not_an_undo_step(qapp, noisy):
    from app.audio.analyzer import analyze
    from app.audio.settings import from_preset
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=True)
    p.set_analysis(analyze(noisy[0][:, None], SR))
    assert not p.can_undo()


def test_activity_estimates():
    import time

    from app.ui.activity import Job, eta_text

    j = Job("k", "Enhancing", fraction=0.0)
    assert j.remaining() is None and "elapsed" in eta_text(j)
    j.started = time.monotonic() - 10
    j.fraction = 0.25
    assert j.remaining() == pytest.approx(30, rel=0.05)
    assert "about 30 s left" in eta_text(j)
