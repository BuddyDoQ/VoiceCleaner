"""Shared output selection and guarded PortAudio device discovery.

SoundCard supplies a live OS inventory; PortAudio's inventory can be frozen at
initialization. Default-only changes reuse known devices. Topology changes need
a refresh, performed only after capture has ended and every player has closed.
"""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass


class RoutingError(RuntimeError):
    pass


class AudioLifecycle:
    """Serialize stream admission with refresh, including capture-thread teardown."""

    def __init__(self):
        self.lock = threading.RLock()
        self.recordings = 0
        self.unclosed_recording = False

    @contextmanager
    def recording(self):
        with self.lock:
            self.recordings += 1
        try:
            yield
        finally:
            with self.lock:
                self.recordings -= 1

    @contextmanager
    def stream(self, recorder):
        """A failed native open/close cannot be treated as proof of inactivity."""
        try:
            rec = recorder.__enter__()
        except BaseException:
            with self.lock:
                self.unclosed_recording = True
            raise
        try:
            yield rec
        finally:
            try:
                recorder.__exit__(*sys.exc_info())
            except BaseException:
                with self.lock:
                    self.unclosed_recording = True
                raise


audio_lifecycle = AudioLifecycle()


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    hostapi: str
    channels: int

    @property
    def preference(self) -> dict:
        return {"mode": "device", "name": self.name, "hostapi": self.hostapi}


@dataclass(frozen=True)
class OutputSnapshot:
    # Native IDs are used only to detect reconnects within this process.
    devices: tuple[tuple[str, str], ...]
    default_name: str | None


def live_outputs() -> OutputSnapshot:
    import soundcard as sc

    devices = tuple(sorted((str(d.id), d.name) for d in sc.all_speakers()))
    default = sc.default_speaker().name if devices else None
    return OutputSnapshot(devices, default)


def output_preference(value) -> dict:
    if (isinstance(value, dict) and value.get("mode") == "device"
            and isinstance(value.get("name"), str) and value["name"]
            and isinstance(value.get("hostapi"), str) and value["hostapi"]):
        return {k: value[k] for k in ("mode", "name", "hostapi")}
    return {"mode": "system"}


class PlaybackRouting:
    def __init__(self, preference=None, *, backend=None, probe=live_outputs,
                 lifecycle=audio_lifecycle, report_error=None):
        self.preference = output_preference(preference)
        self.lifecycle = lifecycle
        self.report_error = report_error or (lambda message, parent=None: None)
        self._backend = backend
        self._probe = probe
        # Retain players until their handles close successfully. A failed close
        # must not disappear from the refresh safety check through garbage collection.
        self._players = set()
        self._snapshot: OutputSnapshot | None = None
        self._pending_refresh = False
        self._refresh_failed = False
        self._initialized = True
        self._termination_failed = False

    @property
    def backend(self):
        if self._backend is None:
            import sounddevice

            self._backend = sounddevice
        return self._backend

    def register(self, player):
        self._players.add(player)

    def unregister(self, player):
        self._players.discard(player)

    def outputs(self) -> list[OutputDevice]:
        if self._termination_failed:
            raise RoutingError("Audio output did not shut down safely. Restart VoiceCleaner.")
        if not self._initialized:
            raise RoutingError("Audio output is unavailable. Use Audio output > Refresh devices to retry.")
        apis = self.backend.query_hostapis()
        return [OutputDevice(i, d["name"], apis[d["hostapi"]]["name"], int(d["max_output_channels"]))
                for i, d in enumerate(self.backend.query_devices()) if d["max_output_channels"] > 0]

    def _observe(self) -> OutputSnapshot:
        snapshot = self._probe()
        if self._snapshot is not None and snapshot.devices != self._snapshot.devices:
            self._pending_refresh = True
            self._refresh_failed = False
        self._snapshot = snapshot
        return snapshot

    def _resolve(self, snapshot: OutputSnapshot) -> OutputDevice:
        outputs = self.outputs()
        if self.preference["mode"] == "device":
            name, api = self.preference["name"], self.preference["hostapi"]
            matches = [d for d in outputs if d.name == name and d.hostapi == api]
        else:
            name = snapshot.default_name
            matches = [d for d in outputs if d.name == name]
            # Windows exposes the same endpoint through several APIs. Prefer its
            # native shared-mode backend, whose names are not MME-truncated.
            native = {"darwin": "Core Audio", "win32": "Windows WASAPI"}.get(sys.platform)
            preferred = [d for d in matches if d.hostapi == native]
            if preferred:
                matches = preferred
        if not name or name not in {n for _id, n in snapshot.devices} or not matches:
            raise RoutingError(f'Audio output "{name or "System default"}" is unavailable. '
                               "Connect it or choose another device in Audio output.")
        if len(matches) != 1:
            raise RoutingError(f'Audio output "{name}" is ambiguous. Choose a unique device in Audio output.')
        return matches[0]

    def prepare(self, player) -> OutputDevice:
        """Called under the lifecycle lock before opening or starting playback."""
        snapshot = self._observe()
        if self.lifecycle.recordings and player.output_device is not None:
            device = player.output_device
            if device.name not in {n for _id, n in snapshot.devices}:
                raise RoutingError("The monitoring output was disconnected. Finish recording before "
                                   "changing the audio output.")
            return device
        if self._pending_refresh or not self._initialized:
            self.refresh(requester=player)
            snapshot = self._observe()
        try:
            return self._resolve(snapshot)
        except RoutingError:
            # A device may have appeared after sounddevice was first imported but
            # before our first snapshot. Try one guarded refresh for that case.
            name = (self.preference.get("name") if self.preference["mode"] == "device"
                    else snapshot.default_name)
            known = [d for d in self.outputs() if d.name == name
                     and (self.preference["mode"] == "system" or d.hostapi == self.preference["hostapi"])]
            if not known and name and name in {n for _id, n in snapshot.devices}:
                self.refresh(requester=player)
                return self._resolve(self._observe())
            raise

    def refresh(self, requester=None):
        """No callback lock is held here. Closed handles never survive a reset."""
        with self.lifecycle.lock:
            if self.lifecycle.recordings:
                self._pending_refresh = True
                raise RoutingError("Audio device refresh is deferred until recording has finished.")
            if self.lifecycle.unclosed_recording:
                self._refresh_failed = True
                raise RoutingError("A recording device did not close safely. Restart VoiceCleaner "
                                   "before refreshing audio devices.")
            if self._termination_failed:
                raise RoutingError("Audio output did not shut down safely. Restart VoiceCleaner.")
            sd = self.backend
            # Unexpected sounddevice initialization nesting cannot be safely reset.
            # The app must remain the sole PortAudio client in this process; the
            # sounddevice counter cannot detect a foreign C client's ownership.
            if self._initialized and getattr(sd, "_initialized", 1) != 1:
                self._refresh_failed = True
                raise RoutingError("Audio output cannot be refreshed because PortAudio has another owner. "
                                   "Restart VoiceCleaner after recording has finished.")
            players = list(self._players)
            playing = [p for p in players if p.state == "playing"]
            self._refresh_failed = True
            for p in players:
                p.suspend_output()
            if any(p._stream is not None for p in players):
                raise RoutingError("An audio stream could not be closed. Audio devices were not refreshed. "
                                   "Restart VoiceCleaner after recording has finished.")
            self._pending_refresh = True
            if self._initialized:
                try:
                    sd._terminate()
                except Exception as exc:
                    self._termination_failed = True
                    raise RoutingError("Audio output did not shut down safely. Restart VoiceCleaner.") from exc
                self._initialized = False
            sd._initialize()
            self._initialized = True
            self._pending_refresh = False
            self._snapshot = self._probe()
            self._refresh_failed = False
            for p in playing:
                if p is not requester:
                    p.play()

    def select(self, preference):
        with self.lifecycle.lock:
            if self.lifecycle.recordings:
                raise RoutingError("Finish recording before changing the audio output.")
            self.preference = output_preference(preference)
            self.poll()

    def poll(self):
        """One UI timer owns polling; capture keeps its original monitoring latency."""
        with self.lifecycle.lock:
            if self.lifecycle.recordings:
                return
            playing = [p for p in self._players if p.state == "playing"]
            try:
                snapshot = self._observe()
                if self._pending_refresh:
                    if not self._refresh_failed:
                        self.refresh()
                    return
                if not playing:
                    return
                device = self._resolve(snapshot)
                for p in playing:
                    if p.output_device != device:
                        p.suspend_output()
                        p.play()
            except Exception as exc:
                for p in playing:
                    p.fail_output(str(exc))

    def shutdown(self):
        for p in list(self._players):
            p.shutdown()
