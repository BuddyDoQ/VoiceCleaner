import numpy as np
import pytest
import soundfile as sf

from app import sessions
from app.ui.playback_panel import scan_folder


def _wav(path, seconds=0.5, sr=48000):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr)) / sr
    sf.write(path, (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)
    return path


def test_scan_lists_playable_wavs_recursively_and_skips_broken(tmp_path):
    _wav(tmp_path / "a.wav")
    _wav(tmp_path / "sub" / "deeper" / "b.WAV")
    (tmp_path / "broken.wav").write_bytes(b"RIFF....nonsense")
    (tmp_path / "notes.txt").write_text("hi")
    _wav(tmp_path / ".parts" / "hidden.wav")  # in-progress capture parts are not listed
    entries, skipped = scan_folder(tmp_path)
    names = sorted(e["path"].name for e in entries)
    assert names == ["a.wav", "b.WAV"]
    assert skipped == 1
    assert all(e["info"].duration == pytest.approx(0.5, abs=0.01) for e in entries)


def test_scan_session_folder(tmp_path):
    s = sessions.create("Ep 1", tmp_path)
    _wav(s.recording_path())
    _wav(s.exports_dir / "Ep 1 - Rec 001 enhanced.wav")
    entries, _ = scan_folder(s.path)
    assert {e["path"].parent.name for e in entries} == {"Recordings", "Exports"}


def test_theme_day_night_switch():
    from PySide6.QtWidgets import QApplication

    from app.ui import theme

    app = QApplication.instance() or QApplication([])
    assert theme.apply(app, "night") == "night"
    night_bg = theme.BG
    assert "#080810" in theme.STYLESHEET
    assert theme.apply(app, "day") == "day"
    assert theme.BG != night_bg and "#f2f2f5" in theme.STYLESHEET
    assert theme.FONT_DISPLAY == "Bebas Neue" and theme.FONT_UI == "Syne" and theme.FONT_MONO == "DM Mono"
    theme.apply(app, "night")


def test_batch_export_uses_session_name(tmp_path, speech):
    from app.workers.batch_worker import BatchOptions, enhance_file
    from app.workers.processing_worker import TaskContext
    from app.audio.settings import from_preset

    s = sessions.create("Show 7", tmp_path)
    src = tmp_path / "guest call.wav"
    sf.write(src, speech, 48000)
    opts = BatchOptions(settings=from_preset("Natural").copy(use_ai=False, resynthesis=0.0), smart=False,
                        output_dir=s.exports_dir, session=s)
    out, _ = enhance_file(src, opts, None, TaskContext(lambda f, m: None))
    assert out.parent == s.exports_dir
    assert out.name == "Show 7 - guest call enhanced.wav"
