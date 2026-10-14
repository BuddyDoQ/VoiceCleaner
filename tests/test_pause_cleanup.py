"""1.1.5: pause clean-up, noise learned from short pauses, and the heavy-EQ warning."""
import numpy as np
import pytest

from app.audio.analyzer import FRAME_HOP_S, analyze
from app.audio.loader import AudioData
from app.audio.pause_cleanup import PauseCleaner, PauseCleanupParams
from app.audio.pipeline import EnhancementPipeline
from app.audio.settings import SMART_FIELDS, ProcessingSettings, apply_simple, from_preset

from .conftest import speech_like

SR = 48_000
TAIL_S = 0.7


def _db(x):
    return 10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20)


def _take(tail_noise_gain: float = 1.0, seed: int = 3):
    """Speech with a lead-in (0.8 s, from the fixture) and a trailing pause, over steady
    noise about 40 dB below the speech (what the AI typically leaves); ``tail_noise_gain`` makes the noise after the last word louder, the way the AI
    leaves it when its gate stays open after speech."""
    s = speech_like(6.0)
    s = np.concatenate([s, np.zeros(int(TAIL_S * SR), np.float32)])
    noise = 0.0003 * np.random.default_rng(seed).standard_normal(s.shape[0]).astype(np.float32)
    noise[-int(TAIL_S * SR):] *= tail_noise_gain
    return (s + noise)[:, None], s


def _pauses(y):
    y = np.asarray(y, dtype=np.float64)[:, 0]
    lead = _db(y[int(0.05 * SR):int(0.45 * SR)])
    tail = _db(y[-int(0.4 * SR):-int(0.02 * SR)])
    return lead, tail


def _speech_db(y, mask):
    keep = np.repeat(mask, int(FRAME_HOP_S * SR))[: y.shape[0]]
    return _db(np.asarray(y)[: keep.shape[0], 0][keep])


# --- pause clean-up stage ----------------------------------------------------------------------
def test_lead_and_trailing_pause_end_up_even():
    x, _ = _take(tail_noise_gain=10 ** (10 / 20))  # 10 dB more noise left after the last word
    a = analyze(x, SR)
    lead_in, tail_in = _pauses(x)
    assert tail_in - lead_in > 8
    pc = PauseCleaner(SR, PauseCleanupParams(0.6))
    y = pc.process(x, a.speech_mask, FRAME_HOP_S)
    lead, tail = _pauses(y)
    assert pc.pauses >= 2
    assert abs(tail - lead) < 3.0
    # both pauses sit near the room-tone target below the speech, never at digital silence
    target = _speech_db(x, a.speech_mask) - pc.p.depth_db
    assert lead < lead_in - 3 and tail < tail_in - 10
    assert target - 3 < lead < target + 3 and target - 3 < tail < target + 3


def test_speech_is_untouched_and_nothing_is_boosted():
    x, _ = _take()
    a = analyze(x, SR)
    pc = PauseCleaner(SR, PauseCleanupParams(1.0))
    curve = pc.gain_curve_db(x, a.speech_mask, FRAME_HOP_S)
    assert curve.max() <= 0.0
    assert curve.min() >= -pc.p.max_cut_db - 1e-9
    y = pc.process(x, a.speech_mask, FRAME_HOP_S)
    keep = np.repeat(a.speech_mask, int(FRAME_HOP_S * SR))[: x.shape[0]]
    np.testing.assert_allclose(y[: keep.shape[0]][keep], x[: keep.shape[0]][keep], atol=1e-7)


def test_loud_sound_in_a_pause_is_left_alone():
    """Something nearly as loud as speech where the detector heard none (a laugh, a missed
    word) is not turned down with the background."""
    x, _ = _take()
    a = analyze(x, SR)
    mask = a.speech_mask.copy()
    t = np.arange(int(0.3 * SR)) / SR
    burst = (0.05 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    start = x.shape[0] - int(0.5 * SR)
    x = x.copy()
    x[start : start + burst.shape[0], 0] += burst
    y = PauseCleaner(SR, PauseCleanupParams(1.0)).process(x, mask, FRAME_HOP_S)
    seg = slice(start + int(0.05 * SR), start + int(0.25 * SR))
    assert abs(_db(y[seg]) - _db(x[seg])) < 1.0


def test_off_and_without_speech_leaves_audio_unchanged():
    x, _ = _take()
    a = analyze(x, SR)
    y = PauseCleaner(SR, PauseCleanupParams(0.0)).process(x, a.speech_mask, FRAME_HOP_S)
    np.testing.assert_array_equal(y, x)
    noise = x[: int(0.5 * SR)]
    y = PauseCleaner(SR, PauseCleanupParams(1.0)).process(noise, np.zeros(50, bool), FRAME_HOP_S)
    np.testing.assert_array_equal(y, noise)
    silent = np.zeros((SR, 1), np.float32)  # digital silence stays silence, no NaNs
    y = PauseCleaner(SR, PauseCleanupParams(1.0)).process(silent, a.speech_mask[:100], FRAME_HOP_S)
    assert np.all(y == 0)


def test_pipeline_pauses_even_and_on_target():
    """Lead-in and trailing pause of the finished file stay within 3 dB of each other."""
    x, _ = _take()
    audio = AudioData(x.astype(np.float32), SR)
    a = analyze(audio.samples, SR)
    s = from_preset("Clean Voice").copy(use_ai=False)
    result = EnhancementPipeline(None).run(audio, a, s)
    lead, tail = _pauses(result.samples)
    assert abs(tail - lead) < 3.0
    off = EnhancementPipeline(None).run(audio, a, s.copy(pause_cleanup=0.0))
    lead_off, tail_off = _pauses(off.samples)
    assert tail < tail_off - 3 and lead < lead_off - 3


@pytest.fixture(scope="module")
def model_manager():
    from app.ai.model_manager import ModelManager

    mm = ModelManager("cpu")
    if not mm.model.is_installed():
        pytest.skip("DeepFilterNet3 model not installed")
    return mm


def test_ai_pipeline_cleans_the_end_like_the_start(model_manager):
    """The AI keeps its gate open after the last word; the finished file must not
    (1.1.4 left the trailing pause about 10 dB noisier than the lead-in on a real take)."""
    x, _ = _take()
    audio = AudioData(x.astype(np.float32), SR)
    a = analyze(audio.samples, SR)
    result = EnhancementPipeline(model_manager).run(audio, a, from_preset("Clean Voice"))
    assert any("AI" in st for st in result.stages)
    lead, tail = _pauses(result.samples)
    assert abs(tail - lead) < 3.0


# --- settings ----------------------------------------------------------------------------------
def test_pause_cleanup_follows_noise_reduction_and_smart():
    assert "pause_cleanup" in SMART_FIELDS
    lo = apply_simple(ProcessingSettings(), 0.2, 0.5, 0.3).pause_cleanup
    hi = apply_simple(ProcessingSettings(), 0.9, 0.5, 0.3).pause_cleanup
    assert 0 < lo < hi <= 1
    assert from_preset("Natural").pause_cleanup < from_preset("Noisy Recording").pause_cleanup
    # presets saved before 1.1.5 have no such field: the default applies
    old = {k: v for k, v in ProcessingSettings().to_dict().items() if k != "pause_cleanup"}
    assert ProcessingSettings.from_dict(old).pause_cleanup == ProcessingSettings().pause_cleanup


def test_speech_loss_rollback_softens_pause_cleanup():
    from app.audio.quality import QCIssue, QCReport, propose_rollback

    rep = QCReport()
    rep.issues.append(QCIssue("speech_loss", "x"))
    s, _ = propose_rollback(rep, ProcessingSettings(pause_cleanup=0.8))
    assert s.pause_cleanup < 0.8


# --- noise learned from short pauses -------------------------------------------------------------
def _lead_take(lead_s: float, steady: bool = True, seed: int = 5):
    rng = np.random.default_rng(seed)
    s = speech_like(4.0)[int(0.8 * SR):]  # starts with speech
    s = np.concatenate([np.zeros(int(lead_s * SR), np.float32), s])
    noise = 0.003 * rng.standard_normal(s.shape[0])
    if not steady:  # a door, a rustle: loud and quiet stretches in turn
        env = np.repeat(rng.choice([0.1, 3.0], size=s.shape[0] // 2400 + 1), 2400)[: s.shape[0]]
        noise *= env
    return (s + noise).astype(np.float32)


def test_short_steady_lead_in_is_learned():
    from app.audio import noise_profile as npm

    x = _lead_take(0.75)  # the speech mask (with its hangover) starts about 0.6 s in
    a = analyze(x[:, None], SR)
    p = npm.learn_leading_noise(x, SR, a.speech_mask, a.frame_level_db, FRAME_HOP_S)
    assert p is not None and p.source == "learned"
    assert 0.3 <= p.seconds < 0.75
    assert p.stationarity >= npm.SHORT_MIN_STATIONARITY


def test_short_unsteady_lead_in_is_not_learned_but_a_long_one_is():
    from app.audio import noise_profile as npm

    x = _lead_take(0.75, steady=False)
    a = analyze(x[:, None], SR)
    region = npm.leading_noise_region(a.speech_mask, a.frame_level_db, FRAME_HOP_S)
    if region is not None:  # the bursts may even read as speech; either way nothing is learned
        assert npm.learn_leading_noise(x, SR, a.speech_mask, a.frame_level_db, FRAME_HOP_S) is None
    x = _lead_take(1.6)
    a = analyze(x[:, None], SR)
    assert npm.learn_leading_noise(x, SR, a.speech_mask, a.frame_level_db, FRAME_HOP_S) is not None


def test_short_steady_automatic_profile_is_trusted():
    from app.audio.noise_profile import NoiseProfile, StftConfig

    cfg = StftConfig.for_rate(SR)
    psd = np.ones(cfg.nfft // 2 + 1)
    assert NoiseProfile(psd, SR, cfg, 0.5, "auto", 0.85).reliable
    assert not NoiseProfile(psd, SR, cfg, 0.5, "auto", 0.4).reliable  # short and changing
    assert not NoiseProfile(psd, SR, cfg, 0.3, "auto", 0.95).reliable  # too short
    assert NoiseProfile(psd, SR, cfg, 1.0, "auto", 0.4).reliable  # as before 1.1.5


def test_short_lead_in_runs_the_profile_denoiser_with_ai_settings():
    """A short lead-in is enough for the profile denoiser to take the edge off the noise."""
    from app.audio import noise_profile as npm

    x = _lead_take(0.75)
    audio = AudioData(x[:, None], SR)
    a = analyze(audio.samples, SR)
    learned = npm.learn_leading_noise(x, SR, a.speech_mask, a.frame_level_db, FRAME_HOP_S)
    s = from_preset("Clean Voice").copy(use_ai=False)
    result = EnhancementPipeline(None).run(audio, a, s, learned_profile=learned)
    assert any("profile denoise" in st for st in result.stages)


# --- heavy EQ warning --------------------------------------------------------------------------
def test_heavy_eq_warning():
    from app.audio.eq import VoiceEQParams, heavy_eq_warning

    # the 1.1.4 demo export: -12 dB at 640 Hz, -6 dB at 5 kHz, darker tilt
    w = heavy_eq_warning(VoiceEQParams(tilt=-0.39, manual_gains=[0, 0, 0, -12, 0, 0, -6, 0]))
    assert w and "12 dB" in w and "hollow" in w
    assert heavy_eq_warning(VoiceEQParams(manual_gains=[0, 0, -9, 0, 0, 0, 0, 0]))
    assert heavy_eq_warning(VoiceEQParams(manual_gains=[0, 0, 0, -6, 0, 0, 0, 0])) is None
    assert heavy_eq_warning(VoiceEQParams(manual_gains=[0] * 7 + [-12])) is None  # air, not body
    assert heavy_eq_warning(VoiceEQParams(tilt=-1.0, manual_gains=[0] * 8)) is None
    assert heavy_eq_warning(VoiceEQParams(manual_gains=[0] * 8)) is None


def test_panel_shows_and_hides_the_eq_warning_and_has_pause_cleanup():
    from PySide6.QtWidgets import QApplication

    from app.ui.settings_panel import SettingsPanel

    QApplication.instance() or QApplication([])
    p = SettingsPanel(from_preset("Clean Voice"), smart=True)
    assert p.eq_warning.isHidden()
    p._eq_changed([0, 0, 0, -12, 0, 0, 0, 0])
    assert not p.eq_warning.isHidden() and "hollow" in p.eq_warning.text()
    p._reset_eq()
    assert p.eq_warning.isHidden()
    # Smart owns the new control, like the other noise-reduction amounts
    assert not p._advanced["pause_cleanup"].slider.isEnabled()


def test_batch_learns_the_lead_in_noise_like_the_enhance_tab(tmp_path):
    """Batch and the command line learn the noise before the first word, as opening the
    recording in the Enhance tab does, so both give the same result."""
    import soundfile as sf

    from app.workers.batch_worker import BatchOptions, enhance_file

    x = np.stack([_lead_take(0.75)] * 2, axis=1)  # stereo: only the lead-in is mixed down
    path = tmp_path / "take.wav"
    sf.write(path, x, SR, subtype="FLOAT")

    class Ctx:
        def progress(self, f, m=""):
            pass

        def check(self):
            pass

    opts = BatchOptions(settings=from_preset("Clean Voice").copy(use_ai=False), smart=False, output_dir=tmp_path)
    out, result = enhance_file(path, opts, None, Ctx())
    assert out.exists()
    assert "profile denoise (learned noise)" in result.stages
    assert result.noise_profile.startswith("Learned")
