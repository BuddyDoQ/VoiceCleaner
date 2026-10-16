"""Routing safety and transport recovery without opening real audio devices."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from app.audio.playback_routing import (AudioLifecycle, OutputSnapshot, PlaybackRouting,
                                        RoutingError, audio_lifecycle, output_preference)
from app.ui.audio_player import AudioPlayer, MAC_BLOCK_S, MAC_LATENCY_S


class FakeStream:
    def __init__(self, backend, **options):
        self.backend, self.options = backend, options
        self.active = self.closed = False
        self.latency = 0.05
        self.fail_close = self.fail_abort = False

    def start(self):
        assert not self.closed
        self.active = True

    def stop(self):
        self.active = False

    def abort(self):
        if self.fail_abort:
            raise RuntimeError("device disappeared")
        self.active = False

    def close(self):
        if self.fail_close:
            raise RuntimeError("close failed")
        self.active = False
        self.closed = True


class FakeBackend:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.names = ["Built-in", "USB"]
        self.streams = []
        self.events = []
        self._initialized = 1
        self.fail_initialize = False

    def query_hostapis(self):
        return [{"name": "Core Audio"}]

    def query_devices(self):
        return [{"name": n, "hostapi": 0, "max_output_channels": 2} for n in self.names]

    def check_output_settings(self, **options):
        assert 0 <= options["device"] < len(self.names)

    def OutputStream(self, **options):
        stream = FakeStream(self, **options)
        self.streams.append(stream)
        return stream

    def _terminate(self):
        assert self.lifecycle.recordings == 0
        assert not self.lifecycle.unclosed_recording
        assert all(s.closed and not s.active for s in self.streams)
        assert self._initialized == 1
        self.events.append("terminate")
        self._initialized = 0

    def _initialize(self):
        assert self._initialized == 0
        self.events.append("initialize")
        if self.fail_initialize:
            raise RuntimeError("initialization failed")
        self._initialized = 1


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(qapp):
    gate = AudioLifecycle()
    backend = FakeBackend(gate)
    live = SimpleNamespace(devices=(("1", "Built-in"), ("2", "USB")), default="Built-in")
    errors, players = [], []
    routing = PlaybackRouting(backend=backend, lifecycle=gate,
                              probe=lambda: OutputSnapshot(live.devices, live.default),
                              report_error=lambda message, parent=None: errors.append(message))

    def player():
        p = AudioPlayer(routing=routing)
        p.set_source("original", np.ones((480000, 1), np.float32) * 0.1, 48000)
        p.set_source("enhanced", np.ones((480000, 1), np.float32) * 0.2, 48000)
        players.append(p)
        return p

    yield SimpleNamespace(gate=gate, backend=backend, live=live, errors=errors,
                          routing=routing, player=player)
    for p in players:
        if p._stream is not None:
            p._stream.fail_close = False
        p.shutdown()


@pytest.mark.parametrize("paused", [False, True])
def test_default_change_reopens_only_player_without_refresh(rig, paused):
    p = rig.player()
    p.play()
    p.seek(2)
    p.set_active("enhanced")
    p.set_loop((1, 4))
    old, buffers = p._stream, dict(p._buffers)
    if paused:
        p.pause()
    rig.live.default = "USB"
    rig.routing.poll()
    if paused:
        assert p.state == "paused"
        p.play()
    assert old.closed and p._stream.options["device"] == 1
    assert p.position == 2 and p.active_source == "enhanced" and p.loop == (1, 4)
    assert all(p._buffers[k] is v for k, v in buffers.items())
    assert rig.backend.events == []


def test_explicit_output_ignores_system_default_changes(rig):
    pref = {"mode": "device", "name": "USB", "hostapi": "Core Audio"}
    rig.routing.select(pref)
    p = rig.player()
    p.play()
    stream = p._stream
    rig.live.default = "USB"
    rig.routing.poll()
    rig.live.default = "Built-in"
    rig.routing.poll()
    assert p._stream is stream and rig.routing.preference == pref
    assert rig.backend.events == []


def test_hotplug_closes_all_five_streams_before_refresh_and_keeps_positions(rig):
    players = [rig.player() for _ in range(5)]
    for i, p in enumerate(players):
        p.play()
        p.seek(i + 0.5)
        if i:
            p.pause()
    old = list(rig.backend.streams)
    rig.live.devices += (("3", "Headphones"),)
    rig.live.default = "Headphones"
    rig.backend.names.append("Headphones")
    rig.routing.poll()
    assert rig.backend.events == ["terminate", "initialize"]
    assert all(s.closed for s in old)
    assert players[0].state == "playing" and players[0]._stream.options["device"] == 2
    assert all(p.state == "paused" and p._stream is None for p in players[1:])
    assert [p.position for p in players] == [i + 0.5 for i in range(5)]


def test_missing_explicit_device_pauses_without_switching_to_speakers(rig):
    pref = {"mode": "device", "name": "USB", "hostapi": "Core Audio"}
    rig.routing.select(pref)
    p = rig.player()
    p.play()
    p.seek(3)
    rig.live.devices = (("1", "Built-in"),)
    rig.backend.names = ["Built-in"]
    rig.routing.poll()
    assert p.state == "paused" and p.position == 3 and p._stream is None
    assert rig.routing.preference == pref and "USB" in rig.errors[-1]
    rig.live.devices += (("9", "USB"),)
    rig.backend.names = ["USB", "Built-in"]  # indexes changed after reconnect
    p.play()
    assert p.state == "playing" and p._stream.options["device"] == 0 and p.position == 3


def test_no_default_device_reports_error_and_never_opens_stream(rig):
    rig.live.devices, rig.live.default = (), None
    rig.backend.names = []
    p = rig.player()
    p.play()
    assert p.state == "paused" and not rig.backend.streams
    assert "System default" in rig.errors[-1]


def test_unexpected_termination_pauses_and_can_retry_at_same_position(rig):
    p = rig.player()
    p.play()
    p.seek(2.5)
    p._stream.active = False
    p._tick()
    assert p.state == "paused" and not p._timer.isActive() and p.position == 2.5
    assert "unexpectedly" in rig.errors[-1]
    p.play()
    assert p.state == "playing" and p.position == 2.5


def test_failed_close_blocks_global_refresh(rig):
    p = rig.player()
    p.play()
    p._stream.fail_close = p._stream.fail_abort = True
    with pytest.raises(RoutingError, match="could not be closed"):
        rig.routing.refresh()
    assert rig.backend.events == [] and p._stream is not None


def test_abort_failure_still_attempts_close(rig):
    p = rig.player()
    p.play()
    p._stream.fail_abort = True
    rig.routing.refresh()
    assert rig.backend.events == ["terminate", "initialize"]


def test_capture_defers_refresh_and_default_rerouting(rig):
    p = rig.player()
    p.play()
    old = p._stream
    with rig.gate.recording():
        rig.live.default = "USB"
        rig.routing.poll()
        assert p._stream is old
        with pytest.raises(RoutingError, match="deferred"):
            rig.routing.refresh()
        with pytest.raises(RoutingError, match="Finish recording"):
            rig.routing.select({"mode": "device", "name": "USB", "hostapi": "Core Audio"})
        assert p._stream is old and rig.backend.events == []
    rig.routing.poll()
    assert p.state == "playing" and p._stream.options["device"] == 1


def test_pause_resume_during_capture_keeps_monitoring_device(rig):
    p = rig.player()
    p.play()
    with rig.gate.recording():
        p.pause()
        rig.live.default = "USB"
        p.play()
        assert p._stream.options["device"] == 0 and rig.backend.events == []


def test_initialization_failure_can_retry_without_double_termination(rig):
    p = rig.player()
    p.play()
    rig.backend.fail_initialize = True
    with pytest.raises(RuntimeError, match="initialization failed"):
        rig.routing.refresh()
    assert p.state == "paused" and p._stream is None
    rig.backend.fail_initialize = False
    p.play()
    assert rig.backend.events == ["terminate", "initialize", "initialize"]
    assert p.state == "playing"


def test_another_portaudio_owner_blocks_reset(rig):
    rig.backend._initialized = 2
    with pytest.raises(RoutingError, match="another owner"):
        rig.routing.refresh()
    assert rig.backend.events == []


def test_native_close_failure_quarantines_refresh(rig):
    recorder = SimpleNamespace(__enter__=lambda: object(),
                               __exit__=lambda *args: (_ for _ in ()).throw(RuntimeError("close failed")))
    with pytest.raises(RuntimeError, match="close failed"):
        with rig.gate.recording(), rig.gate.stream(recorder):
            pass
    assert rig.gate.recordings == 0
    with pytest.raises(RoutingError, match="did not close safely"):
        rig.routing.refresh()
    assert rig.backend.events == []


def test_ambiguous_names_fail_without_refresh_or_guessing(rig):
    rig.backend.names = ["USB", "USB"]
    rig.live.devices = (("2", "USB"), ("3", "USB"))
    rig.live.default = "USB"
    p = rig.player()
    p.play()
    assert p.state == "paused" and "ambiguous" in rig.errors[-1]
    assert rig.backend.events == [] and not rig.backend.streams


def test_windows_system_default_prefers_wasapi(rig, monkeypatch):
    monkeypatch.setattr("app.audio.playback_routing.sys.platform", "win32")
    rig.backend.query_hostapis = lambda: [{"name": "MME"}, {"name": "Windows WASAPI"}]
    rig.backend.query_devices = lambda: [
        {"name": "Built-in", "hostapi": 0, "max_output_channels": 2},
        {"name": "Built-in", "hostapi": 1, "max_output_channels": 2},
    ]
    p = rig.player()
    p.play()
    assert p._stream.options["device"] == 1
    assert rig.backend.events == []


def test_mono_output_uses_playback_downmix(rig):
    rig.backend.query_devices = lambda: [{"name": "Built-in", "hostapi": 0, "max_output_channels": 1}]
    p = rig.player()
    p.set_source("original", np.tile([0.1, 0.3], (480000, 1)).astype(np.float32), 48000)
    p.play()
    assert p._stream.options["channels"] == 1
    out = np.empty((100, 1), np.float32)
    p._callback(out, 100, None, None)
    assert out == pytest.approx(np.full((100, 1), 0.2, np.float32))


def test_normal_completion_is_not_reported_as_device_failure(rig):
    p = rig.player()
    p.play()
    p._ended = True
    p._stream.active = False
    p._tick()
    assert p.state == "stopped" and not rig.errors


def test_refresh_never_holds_callback_lock(rig):
    p = rig.player()
    p.play()
    original = p._stream.abort

    def abort():
        assert p._lock.acquire(blocking=False)
        p._lock.release()
        original()

    p._stream.abort = abort
    rig.routing.refresh()


def test_failed_refresh_is_not_retried_by_timer(rig):
    p = rig.player()
    p.play()
    rig.backend.fail_initialize = True
    rig.live.devices += (("3", "Headphones"),)
    rig.routing.poll()
    for _ in range(3):
        rig.routing.poll()
    assert rig.backend.events == ["terminate", "initialize"]
    assert p.state == "paused" and len(rig.errors) == 1


def test_termination_failure_requires_restart(rig):
    def terminate():
        rig.backend.events.append("failed termination")
        raise RuntimeError("backend teardown failed")

    rig.backend._terminate = terminate
    with pytest.raises(RoutingError, match="Restart VoiceCleaner"):
        rig.routing.refresh()
    with pytest.raises(RoutingError, match="Restart VoiceCleaner"):
        rig.routing.refresh()
    assert rig.backend.events == ["failed termination"]


def test_unsupported_format_reaches_user(rig):
    def check(**options):
        raise RuntimeError("unsupported sample rate 22050")

    rig.backend.check_output_settings = check
    p = rig.player()
    p.play()
    assert p.state == "paused" and not rig.backend.streams
    assert "unsupported sample rate 22050" in rig.errors[-1]


def test_closed_player_leaves_registry_only_after_success(rig):
    p = rig.player()
    p.play()
    p._stream.fail_close = True
    p.shutdown()
    assert p in rig.routing._players
    p._stream.fail_close = False
    p.shutdown()
    assert p not in rig.routing._players


def test_system_audio_kill_waits_for_helper_exit():
    import subprocess
    from app.audio.mac_system_audio import SystemAudioRecorder

    events = []

    def wait(timeout):
        events.append("wait")
        assert audio_lifecycle.recordings > 0
        if events.count("wait") < 3:
            raise subprocess.TimeoutExpired("helper", timeout)

    helper = SimpleNamespace(stdin=None, stdout=None, wait=wait,
                             terminate=lambda: events.append("terminate"),
                             kill=lambda: events.append("kill"))
    recorder = SystemAudioRecorder()
    recorder.proc = helper
    with audio_lifecycle.recording():
        recorder.__exit__(None, None, None)
    assert events == ["wait", "terminate", "wait", "kill", "wait"]
    assert recorder.proc is None and audio_lifecycle.recordings == 0


def test_recording_admission_waits_for_refresh(rig):
    entered, leave, recorded = threading.Event(), threading.Event(), threading.Event()
    original = rig.backend._terminate

    def terminate():
        original()
        entered.set()
        assert leave.wait(3)

    rig.backend._terminate = terminate
    refresh = threading.Thread(target=rig.routing.refresh)
    refresh.start()
    assert entered.wait(3)

    def capture():
        with rig.gate.recording():
            recorded.set()

    capture_thread = threading.Thread(target=capture)
    capture_thread.start()
    assert not recorded.is_set()
    leave.set()
    refresh.join(3)
    capture_thread.join(3)
    assert not refresh.is_alive() and not capture_thread.is_alive() and recorded.is_set()


def test_mac_buffering_and_ab_callback_are_preserved(rig, monkeypatch):
    monkeypatch.setattr("app.ui.audio_player.sys.platform", "darwin")
    p = rig.player()
    p.play()
    assert p._stream.options["blocksize"] == int(48000 * MAC_BLOCK_S)
    assert p._stream.options["latency"] == MAC_LATENCY_S
    out = np.empty((960, 2), np.float32)
    p._callback(out, 960, None, None)
    original = out[-1].copy()
    p.set_active("enhanced")
    p._callback(out, 960, None, None)
    assert out[-1] == pytest.approx(original * 2)


def test_preference_roundtrip_uses_existing_config(tmp_path, monkeypatch):
    from app.utils.config import UserConfig

    monkeypatch.setattr(UserConfig, "path", classmethod(lambda cls: tmp_path / "config.json"))
    pref = {"mode": "device", "name": "USB", "hostapi": "Core Audio"}
    cfg = UserConfig()
    cfg.extra["audio_output"] = pref
    cfg.save()
    assert UserConfig.load().extra["audio_output"] == pref
    assert UserConfig.load().device_preference == "auto"
    assert output_preference({"mode": "device", "name": 123}) == {"mode": "system"}


def test_all_playback_surfaces_share_router_and_report_errors(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox
    import shiboken6

    from app import sessions
    from app.audio.compile import Take
    from app.ui import main_window, playback_panel, record_panel
    from app.ui.take_editor import TakeEditor
    from app.teleprompter import window as prompter_window
    from app.utils.config import UserConfig

    # Build the real window without starting model loading, capture, or workers.
    monkeypatch.setattr(QTimer, "singleShot", lambda *args: None)
    monkeypatch.setattr(sessions, "sessions_root", lambda: tmp_path)
    monkeypatch.setattr(playback_panel.PlaybackPanel, "refresh", lambda self: None)
    monkeypatch.setattr(record_panel, "list_sources", lambda: ([], []))
    monkeypatch.setattr(prompter_window, "list_sources", lambda: ([], []))
    monkeypatch.setattr(main_window.MainWindow, "_refresh_session_recordings", lambda self: None)
    reports = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, message: reports.append((parent, message)))
    window = main_window.MainWindow(UserConfig())
    take = Take(tmp_path / "take.wav", np.zeros((4800, 1), np.float32), 48000, 0, 4800)
    editor = TakeEditor(take, "Test", window, routing=window.audio_routing)
    prompter = prompter_window.TeleprompterWindow(window.session, {}, routing=window.audio_routing)
    try:
        players = [window.player, window.playback.player, window.compile_panel.player,
                   window.batch_panel.player, editor.player]
        assert all(p.routing is window.audio_routing for p in players)
        assert len(window.audio_routing._players) == 5
        for i, p in enumerate(players):
            p.error.emit(f"Surface {i}")
        assert [message for _parent, message in reports] == [f"Surface {i}" for i in range(5)]
        assert reports[-1][0] is editor
        backend = FakeBackend(window.audio_routing.lifecycle)
        window.audio_routing._backend = backend
        window.audio_routing._probe = lambda: OutputSnapshot((("1", "Built-in"), ("2", "USB")), "Built-in")
        window._fill_output_menu()
        next(a for a in window.output_menu.actions() if a.text() == "USB (Core Audio)").trigger()
        assert window.config.extra["audio_output"] == {
            "mode": "device", "name": "USB", "hostapi": "Core Audio"}
        assert window.config.device_preference == "auto"
        window._fill_output_menu()
        next(a for a in window.output_menu.actions() if a.text() == "System default").trigger()
        assert window.config.extra["audio_output"] == {"mode": "system"}
        # The sixth player is lazy. Exercise the real preview construction path.
        monkeypatch.setattr("app.audio.loader.load_wav", lambda path: SimpleNamespace(
            samples=take.samples, sample_rate=take.sample_rate))
        monkeypatch.setattr(AudioPlayer, "play", lambda self: None)
        prompter._last_take = (SimpleNamespace(number=1, duration=0.1), tmp_path / "take.wav")
        prompter.play_last()
        assert prompter._player.routing is window.audio_routing
        assert len(window.audio_routing._players) == 6
        prompter._player.error.emit("Teleprompter preview failed")
        assert reports[-1] == (prompter, "Teleprompter preview failed")
    finally:
        window._output_timer.stop()
        window.audio_routing.shutdown()
        shiboken6.delete(prompter)
        shiboken6.delete(editor)
        shiboken6.delete(window)


@pytest.mark.parametrize("teleprompter", [False, True])
def test_capture_thread_holds_guard_through_native_close(tmp_path, monkeypatch, teleprompter):
    from app.audio import recorder as recording
    from app.teleprompter import takes

    closing, release = threading.Event(), threading.Event()
    observations = []

    class Recorder:
        def __enter__(self):
            observations.append(audio_lifecycle.recordings)
            return self

        def record(self, **options):
            capture._stop.set()
            return np.zeros((48, 1), np.float32)

        def __exit__(self, *args):
            closing.set()
            assert release.wait(3)
            observations.append(audio_lifecycle.recordings)

    source = recording.InputSource("mic", "Mic", "microphone", 1)
    sc = SimpleNamespace(get_microphone=lambda *a, **kw: SimpleNamespace(recorder=lambda **kw: Recorder()))
    monkeypatch.setattr(recording, "_soundcard", lambda: sc)
    monkeypatch.setattr(takes, "_soundcard", lambda: sc)
    if teleprompter:
        capture = takes.TakeRecorder(tmp_path / "raw.f32", source)
    else:
        capture = recording.RecordingSession([source], out_dir=tmp_path)
    backend = FakeBackend(audio_lifecycle)
    routing = PlaybackRouting(backend=backend, probe=lambda: OutputSnapshot((), None))
    capture.start()
    try:
        assert closing.wait(3)
        with pytest.raises(RoutingError, match="deferred"):
            routing.refresh()
        assert backend.events == []
    finally:
        release.set()
        if teleprompter:
            capture.stop()
        else:
            capture.cancel()
    assert all(n > 0 for n in observations)
    assert audio_lifecycle.recordings == 0
    routing.refresh()
    assert backend.events == ["terminate", "initialize"]
