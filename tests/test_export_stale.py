"""Export after Enhance: the "settings changed" prompt only appears when they really changed,
and "Enhance and Export" exports the new result."""
from types import SimpleNamespace

import pytest

SR = 48_000


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    from app import sessions
    from app.teleprompter import window as prompter_window
    from app.ui import main_window, playback_panel, record_panel
    from app.utils.config import UserConfig

    calls = []
    monkeypatch.setattr(QTimer, "singleShot", lambda _ms, fn=None, *a: calls.append(fn))
    monkeypatch.setattr(sessions, "sessions_root", lambda: tmp_path)
    monkeypatch.setattr(playback_panel.PlaybackPanel, "refresh", lambda self: None)
    monkeypatch.setattr(record_panel, "list_sources", lambda: ([], []))
    monkeypatch.setattr(prompter_window, "list_sources", lambda: ([], []))
    monkeypatch.setattr(main_window.MainWindow, "_refresh_session_recordings", lambda self: None)
    return main_window.MainWindow(UserConfig()), calls


def _result(noisy, settings, qc_adjusted):
    from app.audio.analyzer import analyze
    from app.audio.loader import AudioData
    from app.audio.pipeline import EnhancementPipeline
    from app.ui.waveform import WaveformOverview

    x = noisy[0][:, None]
    audio = AudioData(x, SR)
    r = EnhancementPipeline(None).run(audio, analyze(x, SR), settings)
    if qc_adjusted:  # the quality check rolled something back
        r.settings = settings.copy(noise_reduction=settings.noise_reduction * 0.5)
    return audio, (r, WaveformOverview.compute(r.samples, r.sample_rate), settings)


def test_qc_adjusted_result_is_not_stale(qapp, tmp_path, monkeypatch, noisy):
    window, _ = _window(qapp, tmp_path, monkeypatch)
    s = window.settings_panel.current().copy(use_ai=False, resynthesis=0.0)
    window.settings_panel.set_settings(s)
    s = window.settings_panel.current()
    window.audio, payload = _result(noisy, s, qc_adjusted=True)
    window._enhanced(payload)
    window._settings_changed(window.settings_panel.current())  # any refresh, nothing changed
    assert window.enhance_btn.text() == "Enhance"


def test_enhance_and_export_exports_the_new_result(qapp, tmp_path, monkeypatch, noisy):
    from app.ui import main_window

    window, calls = _window(qapp, tmp_path, monkeypatch)
    s = window.settings_panel.current().copy(use_ai=False, resynthesis=0.0)
    window.settings_panel.set_settings(s)
    old_settings = window.settings_panel.current()
    window.audio, payload = _result(noisy, old_settings, qc_adjusted=False)
    window._enhanced(payload)
    assert window.enhance_btn.text() == "Enhance"

    # change a setting: now stale
    window.settings_panel.set_settings(old_settings.copy(noise_reduction=old_settings.noise_reduction * 0.5))
    window._settings_changed(window.settings_panel.current())
    assert window.enhance_btn.text() == "Enhance Again"

    prompts, dialogs = [], []
    monkeypatch.setattr(main_window.MainWindow, "_confirm_export_stale",
                        lambda self, then=None: prompts.append(then) or False)
    window.export()
    assert len(prompts) == 1

    # the re-enhance finishes; its export continuation must reach the dialog without asking again
    _, new_payload = _result(noisy, window.settings_panel.current(), qc_adjusted=False)
    window.audio.info = SimpleNamespace(path=str(tmp_path / "take.wav"))
    window._export_after_enhance = lambda: window.export()
    calls.clear()

    class Dialog:
        Accepted = 1

        def __init__(self, *a, **k):
            dialogs.append(window.result)

        def exec(self):
            return 0  # cancel; we only check that it was reached, with the new result

    monkeypatch.setattr(main_window, "ExportDialog", Dialog)
    window._enhanced(new_payload)
    for fn in [c for c in calls if callable(c)]:
        fn()
    assert window.enhance_btn.text() == "Enhance"
    assert len(prompts) == 1  # no second prompt
    assert dialogs == [new_payload[0]]


def test_enhance_and_export_with_the_real_task_runner(qapp, tmp_path, monkeypatch, noisy):
    """The real flow: the prompt's "Enhance and Export" runs Enhance in the background and the
    export dialog opens when it is done (it used to find the app still busy and do nothing)."""
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.audio.analyzer import analyze
    from app.ui import main_window

    real_single_shot = QTimer.singleShot
    window, _ = _window(qapp, tmp_path, monkeypatch)
    monkeypatch.setattr(QTimer, "singleShot", real_single_shot)  # the window is built; timers run now
    window._mm_ready.set()  # no AI model: use_ai is off
    s = window.settings_panel.current().copy(use_ai=False, resynthesis=0.0)
    window.settings_panel.set_settings(s)
    window.audio, payload = _result(noisy, window.settings_panel.current(), qc_adjusted=False)
    window.audio.info = SimpleNamespace(path=tmp_path / "take.wav")
    window.analysis = analyze(window.audio.samples, SR)
    window._enhanced(payload)
    first = window.result
    window.settings_panel.set_settings(window.settings_panel.current().copy(noise_reduction=0.2))
    window._settings_changed(window.settings_panel.current())
    assert window.enhance_btn.text() == "Enhance Again"

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == "Enhance and Export"))
    dialogs = []

    class Dialog:
        Accepted = 1

        def __init__(self, *a, **k):
            dialogs.append(window.result)

        def exec(self):
            return 0

    monkeypatch.setattr(main_window, "ExportDialog", Dialog)
    window.export()
    deadline = time.monotonic() + 60
    while not dialogs and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    if window.task is not None:
        window.task.wait()
    assert dialogs, "the export dialog never opened after Enhance"
    assert dialogs[0] is window.result and window.result is not first
    assert window.enhance_btn.text() == "Enhance"
