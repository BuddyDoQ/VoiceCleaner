"""User presets, Smart settings ownership, "modified" and settings that survive a restart."""
import numpy as np
import pytest

from app.audio.analyzer import analyze
from app.audio.settings import (PRESETS, SMART_FIELDS, USER_PRESETS, ProcessingSettings, from_preset,
                                modified_fields, preset_names, preset_reference, smart_adapt)
SR = 48_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    """User presets in a temporary profile; the registry is empty before and after."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.utils import user_presets

    USER_PRESETS.clear()
    yield user_presets
    USER_PRESETS.clear()


@pytest.fixture
def analysis(noisy):
    return analyze(noisy[0][:, None], SR)


def test_save_load_rename_delete(store):
    mine = from_preset("Podcast").copy(leveler_amount=0.9, eq_tilt=0.3, eq_gains=[1.0] * 8, target_lufs=-19.0)
    name = store.save("  My <Voice>  ", mine)
    assert name == "My Voice"
    assert (store.presets_dir() / "My Voice.json").exists()
    assert "My Voice" in preset_names() and preset_names()[: len(PRESETS)] == list(PRESETS)

    USER_PRESETS.clear()
    assert store.load_all() == ["My Voice"]  # read back from disk
    s = from_preset("My Voice", ProcessingSettings())
    assert s.preset == "My Voice"
    assert (s.leveler_amount, s.eq_tilt, s.eq_gains, s.target_lufs) == (0.9, 0.3, [1.0] * 8, -19.0)

    assert store.rename("My Voice", "Studio") == "Studio"
    assert store.load_all() == ["Studio"]
    store.delete("Studio")
    assert store.load_all() == [] and "Studio" not in preset_names()


def test_names_are_validated(store):
    with pytest.raises(store.PresetError):
        store.save("   ", ProcessingSettings())
    with pytest.raises(store.PresetError):
        store.save("podcast", ProcessingSettings())  # a built-in name, any case
    assert len(store.clean_name("x" * 100)) == store.MAX_NAME
    store.save("Alpha", ProcessingSettings())
    store.save("Beta", ProcessingSettings())
    with pytest.raises(store.PresetError):
        store.rename("Alpha", "beta")  # would collide


def test_damaged_preset_file_is_skipped(store):
    (store.presets_dir() / "broken.json").write_text("{not json", encoding="utf-8")
    store.save("Good", ProcessingSettings())
    assert store.load_all() == ["Good"]


def test_smart_only_changes_its_own_fields(analysis):
    mine = from_preset("Clean Voice").copy(resynthesis=0.35, leveler_amount=0.85, comp_ratio=4.0, eq_tilt=-0.4,
                                           noise_reduction=0.99)
    out = smart_adapt(mine, analysis).settings
    for k in ("resynthesis", "leveler_amount", "comp_ratio", "eq_tilt"):
        assert getattr(out, k) == getattr(mine, k), k  # the user's choices stay
    assert out.noise_reduction != 0.99  # Smart wins on its own fields
    assert set(f for f in SMART_FIELDS if getattr(out, f) != getattr(mine, f)) <= set(SMART_FIELDS)


def test_smart_works_from_a_user_preset(store, analysis):
    store.save("Gentle", from_preset("Natural").copy(noise_reduction=0.1, leveler_amount=0.75))
    s = smart_adapt(from_preset("Gentle", ProcessingSettings()), analysis).settings
    assert s.preset == "Gentle" and s.leveler_amount == 0.75


def test_modified_tracks_user_changes_not_smart(analysis):
    ref = preset_reference("Clean Voice")
    assert modified_fields(ref, ref, smart=False) == []
    assert modified_fields(ref.copy(leveler_amount=0.9), ref, smart=False) == ["leveler_amount"]
    # with Smart on, the fields Smart owns never count as the user's changes
    assert modified_fields(ref.copy(noise_reduction=0.9), ref, smart=True) == []
    assert modified_fields(ref.copy(noise_reduction=0.9), ref, smart=False) == ["noise_reduction"]
    # the user's own fields (EQ, loudness, pauses) are not part of a built-in preset
    assert modified_fields(ref.copy(eq_tilt=0.5, pause_shorten=True), ref, smart=False) == []


def test_preset_reference_keeps_user_fields_and_resets_the_rest():
    cur = from_preset("Podcast").copy(eq_tilt=0.4, target_lufs=-20.0, comp_attack_ms=55.0, pause_shorten=True)
    ref = preset_reference("Natural", cur)
    assert (ref.eq_tilt, ref.target_lufs, ref.pause_shorten) == (0.4, -20.0, True)
    assert ref.comp_attack_ms == ProcessingSettings().comp_attack_ms  # not a leftover from Podcast
    assert ref.preset == "Natural"


def test_batch_smart_keeps_user_choices(tmp_path, monkeypatch, noisy):
    """Batch with Smart on used to re-apply the whole preset; now only Smart's fields change."""
    import soundfile as sf

    from app.workers import batch_worker

    seen = {}

    class FakePipeline:
        def __init__(self, _mm):
            pass

        def run(self, audio, analysis, settings, ctx):
            seen["settings"] = settings
            raise RuntimeError("stop here")

    monkeypatch.setattr(batch_worker, "EnhancementPipeline", FakePipeline)
    p = tmp_path / "take.wav"
    sf.write(p, noisy[0], SR)
    mine = from_preset("Clean Voice").copy(leveler_amount=0.85, resynthesis=0.0, use_ai=False, comp_ratio=5.0)
    opts = batch_worker.BatchOptions(settings=mine, smart=True, output_dir=tmp_path)

    class Ctx:
        def progress(self, *a):
            pass

        def check(self):
            pass

    with pytest.raises(RuntimeError):
        batch_worker.enhance_file(p, opts, None, Ctx())
    s = seen["settings"]
    assert (s.leveler_amount, s.comp_ratio, s.use_ai) == (0.85, 5.0, False)


# ---------------------------------------------------------------------------- UI
@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_smart_locks_its_controls_and_explains(qapp, analysis):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=True)
    assert not p.noise.slider.isEnabled() and p.noise.value_label.text().startswith("AUTO")
    assert not p._advanced["compressor_amount"].slider.isEnabled()
    assert p._advanced["leveler_amount"].slider.isEnabled()  # not Smart's
    assert not p.smart_note.isHidden() and "Smart is on" in p.smart_note.text()
    p.smart.setChecked(False)
    assert p.noise.slider.isEnabled() and not p.noise.value_label.text().startswith("AUTO")
    assert p.smart_note.isHidden()


def test_panel_loading_a_recording_keeps_settings_when_smart_is_off(qapp, analysis):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=False)
    p.noise.set_value(0.83, emit=True)
    p._advanced["leveler_amount"].set_value(0.9, emit=True)
    p.set_analysis(analysis)  # a new recording
    s = p.current()
    assert (s.noise_reduction, s.leveler_amount) == (0.83, 0.9)


def test_panel_smart_on_loading_keeps_non_smart_choices(qapp, analysis):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=True)
    p._advanced["leveler_amount"].set_value(0.9, emit=True)
    p.set_analysis(analysis)
    assert p.current().leveler_amount == 0.9
    assert p.current().noise_reduction == smart_adapt(p.current(), analysis).settings.noise_reduction


def test_panel_modified_tag_reset_and_double_click(qapp):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=False)
    assert p.modified_tag.isHidden()
    w = p._advanced["leveler_amount"]
    w.set_value(0.95, emit=True)
    assert not p.modified_tag.isHidden() and p.modified() == ["leveler_amount"]
    w.resetRequested.emit()  # what a double-click does
    assert p.current().leveler_amount == from_preset("Clean Voice").leveler_amount
    assert p.modified_tag.isHidden()
    p.noise.set_value(0.9, emit=True)
    p.reset_to_preset()
    assert p.current().noise_reduction == from_preset("Clean Voice").noise_reduction


def test_panel_save_as_preset_selects_it(qapp, store):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=False)
    p._advanced["leveler_amount"].set_value(0.95, emit=True)
    p._select_saved(store.save("Mine", p._settings_to_save()))
    assert p.preset.currentText() == "Mine" and p.modified() == []
    assert p.current().leveler_amount == 0.95  # nothing changed audibly
    p.preset.setCurrentText("Podcast")
    p.preset.setCurrentText("Mine")
    assert p.current().leveler_amount == 0.95


def test_panel_saving_with_smart_on_stores_the_presets_own_amounts(qapp, store, analysis):
    from app.ui.settings_panel import SettingsPanel

    p = SettingsPanel(from_preset("Clean Voice"), smart=True)
    p.set_analysis(analysis)
    store.save("FromSmart", p._settings_to_save())
    saved = USER_PRESETS["FromSmart"]
    assert np.isclose(saved["noise_reduction"], from_preset("Clean Voice").noise_reduction)
