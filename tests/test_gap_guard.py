"""Gap guard: the pauses between words never end up louder than in the original, and the
quality check notices when they do."""
import numpy as np

SR = 48_000


def _louder(x, db):
    return (x * 10 ** (db / 20)).astype(np.float32)


def _speech_frames(analysis, n):
    from app.audio.analyzer import FRAME_HOP_S

    hop = int(round(FRAME_HOP_S * SR))
    return np.repeat(analysis.speech_mask, hop)[:n]


def test_turned_up_pauses_come_back_to_the_original_level(noisy):
    from app.audio.analyzer import FRAME_HOP_S, analyze
    from app.audio.gap_guard import TOLERANCE_DB, GapGuard, gap_rise

    original = noisy[0][:, None]
    an = analyze(original, SR)
    processed = _louder(original, 10.0)  # loudness normalization with no noise removed
    assert gap_rise(original, processed, SR, an.speech_mask, FRAME_HOP_S).worst_db() > 9.0

    before = processed.copy()
    guard = GapGuard(SR)
    guard.process(original, processed, an.speech_mask, FRAME_HOP_S)
    assert guard.frames_cut > 0
    assert gap_rise(original, processed, SR, an.speech_mask, FRAME_HOP_S).worst_db() <= TOLERANCE_DB + 0.5
    # speech itself is untouched
    sp = _speech_frames(an, processed.shape[0])
    assert np.array_equal(processed[: sp.size][sp], before[: sp.size][sp])


def test_a_noise_in_a_pause_is_not_louder_than_it_was(noisy):
    from app.audio.analyzer import FRAME_HOP_S, analyze
    from app.audio.gap_guard import GapGuard, gap_rise
    from app.audio.pause_cleanup import pause_frames

    original = noisy[0][:, None].copy()
    an = analyze(original, SR)
    pauses = pause_frames(an.speech_mask, an.speech_mask.size, FRAME_HOP_S)
    first = int(np.argmax(pauses))
    length = int(np.argmin(pauses[first:]))
    mid = (first + length // 2) * int(FRAME_HOP_S * SR)
    rng = np.random.default_rng(3)
    burst = slice(mid - SR // 40, mid + SR // 40)  # 50 ms of hiss, like a click or a rustle
    original[burst, 0] += 0.02 * rng.standard_normal(SR // 20).astype(np.float32)
    processed = original.copy()
    processed[burst] *= 4.0  # the noise came out 12 dB louder

    GapGuard(SR).process(original, processed, an.speech_mask, FRAME_HOP_S)
    level = lambda x: 10 * np.log10(np.mean(x[burst] ** 2))  # noqa: E731
    assert level(processed) <= level(original) + 1.5
    assert gap_rise(original, processed, SR, an.speech_mask, FRAME_HOP_S).rise_db.max() <= 2.0


def test_nothing_changes_when_pauses_are_already_quieter(noisy):
    from app.audio.analyzer import FRAME_HOP_S, analyze
    from app.audio.gap_guard import GapGuard

    original = noisy[0][:, None]
    an = analyze(original, SR)
    processed = _louder(noisy[1][:, None], 6.0)  # the noise removed, the speech turned up
    before = processed.copy()
    guard = GapGuard(SR)
    guard.process(original, processed, an.speech_mask, FRAME_HOP_S)
    assert guard.frames_cut == 0
    assert np.array_equal(processed, before)


def test_quality_check_reports_louder_pauses(noisy):
    from app.audio.analyzer import FRAME_HOP_S, analyze
    from app.audio.gap_guard import GapGuard
    from app.audio.quality import check, propose_rollback
    from app.audio.settings import from_preset

    original = noisy[0][:, None]
    an = analyze(original, SR)
    s = from_preset("Clean Voice")
    processed = _louder(original, 10.0)
    rep, _ = check(original, processed, SR, an, s)
    assert "louder_pauses" in {i.code for i in rep.issues}
    assert rep.stats["pause_rise_db"] > 9.0
    gentler, notes = propose_rollback(rep, s)
    assert gentler.pause_cleanup > s.pause_cleanup and notes

    GapGuard(SR).process(original, processed, an.speech_mask, FRAME_HOP_S)
    rep, _ = check(original, processed, SR, an, s)
    assert "louder_pauses" not in {i.code for i in rep.issues}


def test_enhance_keeps_pauses_at_the_original_level(noisy):
    """Light settings (little noise removed, no pause clean-up): the gain alone would lift the
    pauses; the result keeps them at the original's level."""
    from app.audio.analyzer import analyze
    from app.audio.loader import AudioData
    from app.audio.pipeline import EnhancementPipeline
    from app.audio.settings import from_preset

    x = noisy[0][:, None]
    an = analyze(x, SR)
    s = from_preset("Natural").copy(use_ai=False, noise_reduction=0.05, max_attenuation_db=3.0, pause_cleanup=0.0)
    r = EnhancementPipeline(None).run(AudioData(x, SR), an, s)
    assert r.qc.stats["pause_rise_db"] <= 2.0
    assert "louder_pauses" not in {i.code for i in r.qc.issues}
    assert any(st.startswith("pauses kept at original level") for st in r.stages)


def test_preview_keeps_pauses_at_the_original_level(noisy):
    from app.audio.analyzer import FRAME_HOP_S, analyze
    from app.audio.gap_guard import gap_rise
    from app.audio.loader import AudioData
    from app.audio.pipeline import EnhancementPipeline, slice_analysis
    from app.audio.settings import from_preset

    x = np.tile(noisy[0], 3)[:, None]
    an = analyze(x, SR)
    s = from_preset("Natural").copy(use_ai=False, noise_reduction=0.05, max_attenuation_db=3.0, pause_cleanup=0.0)
    pr = EnhancementPipeline(None).preview(AudioData(x, SR), an, s, 6.0, 12.0)
    a, b = pr.start_frame, pr.start_frame + pr.samples.shape[0]
    part = slice_analysis(an, a, b, SR)
    assert gap_rise(x[a:b], pr.samples, SR, part.speech_mask, FRAME_HOP_S).worst_db() <= 2.0
