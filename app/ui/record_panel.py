"""Recording strip shown inside the Original track card.

Pick a microphone and/or a desktop-audio source, choose how the take relates
to the current Original (new / append / overdub at playhead), watch the level
meters, and record. The take is drawn live on the Original waveform.
"""
from __future__ import annotations

import sys
import time

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio.loader import format_duration
from ..audio import mac_system_audio
from ..audio.recorder import MIC_PRIVACY_HINT, MODES, InputSource, RecordingError, RecordingSession, list_sources
from ..utils.logging import get_logger
from ..workers.processing_worker import run_task
from . import theme
from .waveform import WaveformOverview

log = get_logger("record_ui")

INPUT_LATENCY_S = 0.03  # typical WASAPI shared-mode capture latency (buffer + driver)


class LevelMeter(QWidget):
    """Horizontal dBFS meter (-60..0) with peak hold."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rms_db = -120.0
        self.peak_db = -120.0
        self.hold_db = -120.0
        self._hold_t = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_level(self, rms_db: float, peak_db: float):
        self.rms_db = rms_db
        self.peak_db = max(peak_db, self.peak_db - 1.5)  # smooth fall-back
        now = time.monotonic()
        if peak_db >= self.hold_db or now - self._hold_t > 1.5:
            self.hold_db, self._hold_t = peak_db, now
        self.update()

    def reset(self):
        self.rms_db = self.peak_db = self.hold_db = -120.0
        self.update()

    @staticmethod
    def _frac(v: float) -> float:
        return float(np.clip((v + 60.0) / 60.0, 0.0, 1.0))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(theme.SURFACE_3))
        p.drawRoundedRect(r, 3, 3)
        grad = QLinearGradient(r.left(), 0, r.right(), 0)
        grad.setColorAt(0.0, QColor(theme.SUCCESS))
        grad.setColorAt(0.78, QColor(theme.SUCCESS))  # -13 dB
        grad.setColorAt(0.88, QColor(theme.WARNING))  # -7 dB
        grad.setColorAt(1.0, QColor(theme.RECORD))
        w = r.width() * self._frac(self.peak_db)
        p.setBrush(grad)
        p.setOpacity(0.55)
        p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), 3, 3)
        p.setOpacity(1.0)
        p.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * self._frac(self.rms_db), r.height()), 3, 3)
        if self.hold_db > -60:
            x = r.left() + r.width() * self._frac(self.hold_db)
            p.fillRect(QRectF(x - 1, r.top(), 2, r.height()), QColor(theme.RECORD if self.hold_db > -1 else theme.TEXT))


class _SourceRow(QWidget):
    """Device picker + level meter + input trim for one source kind."""

    def __init__(self, title: str, none_label: str, parent=None):
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(3)
        t = QLabel(title)
        t.setProperty("role", "muted")
        t.setFixedWidth(92)
        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.none_label = none_label
        self.meter = LevelMeter()
        self.trim = QSlider(Qt.Horizontal)
        self.trim.setProperty("role", "small")
        self.trim.setRange(-24, 12)
        self.trim.setValue(0)
        self.trim.setFixedWidth(90)
        self.trim.setToolTip("Input level trim for this source (applied to the take)")
        self.trim_label = QLabel("0 dB")
        self.trim_label.setProperty("role", "faint")
        self.trim_label.setFixedWidth(40)
        self.trim.valueChanged.connect(lambda v: self.trim_label.setText(f"{v:+d} dB" if v else "0 dB"))
        lay.addWidget(t, 0, 0)
        lay.addWidget(self.combo, 0, 1, 1, 3)
        lay.addWidget(self.meter, 1, 1)
        lay.addWidget(self.trim, 1, 2)
        lay.addWidget(self.trim_label, 1, 3)

    def set_sources(self, sources: list[InputSource], keep_id: str | None, default_none: bool):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self.none_label, None)
        for s in sources:
            self.combo.addItem(s.name + ("  (default)" if s.is_default else ""), s)
        idx = 0
        if keep_id:
            idx = next((i for i in range(1, self.combo.count()) if self.combo.itemData(i).id == keep_id), 0)
        elif not default_none and sources:
            idx = 1
        self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)

    @property
    def source(self) -> InputSource | None:
        return self.combo.currentData()

    @property
    def gain(self) -> float:
        return 10 ** (self.trim.value() / 20)


class CueBanner(QWidget):
    """A big, colour-coded instruction across the Record strip: GET READY (the countdown),
    STAY QUIET (room tone, with a bar running down), SPEAK NOW."""

    COLORS = {"ready": "ACCENT", "quiet": "WARNING", "speak": "SUCCESS"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind, self.text, self.fraction = "", "", None
        self.setFixedHeight(64)
        self.setVisible(False)

    def show_cue(self, kind: str, text: str, fraction: float | None = None):
        if (kind, text, fraction) != (self.kind, self.text, self.fraction):
            self.kind, self.text, self.fraction = kind, text, fraction
            self.update()
        self.setVisible(True)

    def clear(self):
        self.kind, self.text, self.fraction = "", "", None
        self.setVisible(False)

    def paintEvent(self, _e):
        if not self.kind:
            return
        color = getattr(theme, self.COLORS.get(self.kind, "ACCENT"))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(theme.qcolor(color), 2))
        p.setBrush(theme.qcolor(color, 40))
        p.drawRoundedRect(r, 10, 10)
        if self.fraction is not None:  # time left, running down
            bar = QRectF(r.left() + 10, r.bottom() - 9, (r.width() - 20) * max(0.0, min(1.0, self.fraction)), 4)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(color))
            p.drawRoundedRect(bar, 2, 2)
        f = QFont(theme.FONT_UI)
        f.setPointSizeF(theme.pt(20))
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.qcolor(color))
        p.drawText(r.adjusted(0, 0, 0, -6 if self.fraction is not None else 0), Qt.AlignCenter, self.text)
        p.end()


COUNTDOWN_S = 3
ROOM_TONE_S = 1.0  # quiet recorded first, used as the noise profile


class RecordPanel(QFrame):
    captureStarted = Signal()  # audio is arriving; overdub monitoring may start now
    liveUpdate = Signal(object, float)  # WaveformOverview | None, offset seconds
    recordingChanged = Signal(bool)
    finished = Signal(object, str, float, float)  # RecordingResult, mode, position_s, latency_s
    failed = Signal(str, str)
    closeRequested = Signal()
    countdownChanged = Signal(int)  # seconds left before recording starts; 0 = over (or cancelled)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordPanel")
        # keep the strip at its natural height; the waveform below gives way instead
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setProperty("role", "inset")
        self.session: RecordingSession | None = None
        self.output_path_factory = None  # callable -> Path for the next take (set by the window)
        self.has_original = False
        self.original_duration = 0.0
        self.playhead = 0.0
        self._mode = "new"
        self._position = 0.0
        self._play_started: float | None = None
        self._out_latency = 0.0
        self._announced = False
        self._silence_warned = False
        self._stopping = None
        self._count = 0  # countdown in progress (seconds left)
        self._room_tone = 0.0  # this take starts with this much room tone
        self.last_room_tone = 0.0  # of the take just finished (read by the window)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("RECORD")
        title.setProperty("role", "section")
        title.setProperty("tone", "record")
        head.addWidget(title)
        head.addStretch(1)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("↻ Refresh devices")
        self.refresh_btn.setProperty("role", "link")
        self.refresh_btn.clicked.connect(self.refresh_sources)
        head.addWidget(self.refresh_btn)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Hide the recording controls")
        close.setProperty("role", "link")
        close.clicked.connect(self.closeRequested)
        self.close_btn = close
        head.addWidget(close)
        v.addLayout(head)

        self.mic_row = _SourceRow("Microphone", "No microphone")
        if sys.platform == "darwin":
            why = mac_system_audio.unavailable_reason()
            self.desk_row = _SourceRow("Desktop audio", f"None ({why}; or install BlackHole)" if why else "Off")
            self.desk_row.combo.setToolTip(
                "Records what this Mac plays (the other side of a call, a video, a browser tab):\n"
                "System audio records all apps except VoiceCleaner itself (macOS 14.2 or newer; macOS asks\n"
                "once for permission). A loopback driver such as BlackHole is listed here too.")
        else:
            self.desk_row = _SourceRow("Desktop audio", "No desktop audio")
            self.desk_row.combo.setToolTip("Records what this computer plays through the chosen speakers or headphones\n"
                                           "(e.g. the other side of a call, a video, a browser tab).")
        v.addWidget(self.mic_row)
        v.addWidget(self.desk_row)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.mode = QComboBox()
        for key, text in MODES.items():
            self.mode.addItem(text, key)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        bottom.addWidget(self.mode, 1)
        self.monitor = QCheckBox("Play the Original while recording")
        self.monitor.setChecked(True)
        self.monitor.setToolTip("Hear the Original from the playhead while you record the new layer.\n"
                                "Use headphones so the playback is not picked up by the microphone.")
        bottom.addWidget(self.monitor)
        v.addLayout(bottom)

        opts = QHBoxLayout()
        opts.setSpacing(16)
        self.countdown_box = QCheckBox(f"{COUNTDOWN_S}-second countdown")
        self.countdown_box.setToolTip("Count down 3, 2, 1 before recording starts, to get ready.\n"
                                      "Click Record (or press R) again during the countdown to cancel.")
        self.roomtone_box = QCheckBox(f"Record {ROOM_TONE_S:g} s of room tone first")
        self.roomtone_box.setToolTip("Recording starts with a moment of quiet: stay silent until it says Speak now.\n"
                                     "That second is used as the noise profile, so the background noise\n"
                                     "is measured exactly. For new recordings (not Append or Overdub).")
        opts.addWidget(self.countdown_box)
        opts.addWidget(self.roomtone_box)
        opts.addStretch(1)
        v.addLayout(opts)
        self._count_timer = QTimer(self)
        self._count_timer.setInterval(1000)
        self._count_timer.timeout.connect(self._count_tick)

        act = QHBoxLayout()
        self.rec_btn = QPushButton("●  Record")
        self.rec_btn.setProperty("role", "recbig")
        self.rec_btn.setCursor(Qt.PointingHandCursor)
        self.rec_btn.setToolTip("Start / stop recording (R)")
        self.rec_btn.clicked.connect(self.toggle)
        act.addWidget(self.rec_btn)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setToolTip("Stop and throw away this take")
        self.discard_btn.clicked.connect(self.discard)
        self.discard_btn.setVisible(False)
        act.addWidget(self.discard_btn)
        self.time_label = QLabel("")
        self.time_label.setProperty("role", "mono")
        act.addWidget(self.time_label)
        act.addStretch(1)
        v.addLayout(act)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "faint")
        v.addWidget(self.status)
        self.cue = CueBanner()
        v.addWidget(self.cue)
        self._speak_until = 0.0  # show SPEAK NOW until this capture time

        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self._mode_changed()

    # --- configuration --------------------------------------------------------------
    def refresh_sources(self):
        try:
            mics, desk = list_sources()
        except RecordingError as exc:
            self.status.setText(exc.user_message)
            self.rec_btn.setEnabled(False)
            return
        mic_id = self.mic_row.source.id if self.mic_row.source else None
        desk_id = self.desk_row.source.id if self.desk_row.source else None
        self.mic_row.set_sources(mics, mic_id, default_none=False)
        self.desk_row.set_sources(desk, desk_id, default_none=True)
        if not mics and not desk:
            self.status.setText("No recording devices were found. Connect a microphone and click Refresh devices.")
        else:
            self.status.setText("")
        self.rec_btn.setEnabled(bool(mics or desk))

    def set_original(self, has_original: bool, duration: float):
        self.has_original, self.original_duration = has_original, duration
        self.mode.blockSignals(True)
        for i in range(self.mode.count()):
            item = self.mode.model().item(i)
            item.setEnabled(has_original or self.mode.itemData(i) == "new")
        if not has_original:
            self.mode.setCurrentIndex(0)
        self.mode.blockSignals(False)
        self._mode_changed()

    def _mode_changed(self):
        self._mode = self.mode.currentData()
        self.monitor.setVisible(self._mode == "overdub")
        self.mode.setVisible(self.has_original)
        # a quiet second in the middle of an Append or Overdub would be a gap
        self.roomtone_box.setEnabled(self._mode == "new")

    def options(self) -> dict:
        """Remembered between sessions."""
        return {"countdown": self.countdown_box.isChecked(), "room_tone": self.roomtone_box.isChecked()}

    def restore_options(self, o: dict):
        if isinstance(o, dict):
            self.countdown_box.setChecked(bool(o.get("countdown", False)))
            self.roomtone_box.setChecked(bool(o.get("room_tone", False)))

    @property
    def recording(self) -> bool:
        return self.session is not None or self._count > 0

    @property
    def counting_down(self) -> bool:
        return self._count > 0

    # --- record / stop -----------------------------------------------------------------
    def toggle(self):
        if self._stopping is not None:
            return
        if self._count > 0:
            self.cancel_countdown()
        elif self.session is None:
            self.start()
        else:
            self.stop()

    def _sources(self):
        sources, gains = [], []
        for row in (self.mic_row, self.desk_row):
            if row.source is not None:
                sources.append(row.source)
                gains.append(row.gain)
        return sources, gains

    def start(self):
        if not self._sources()[0]:
            self.status.setText("Choose a microphone or a desktop audio source first.")
            return
        if not self.countdown_box.isChecked():
            self._begin_capture()
            return
        self._count = COUNTDOWN_S
        self._set_controls_enabled(False)
        self.rec_btn.setText("\u2715  Cancel")
        self.time_label.setText(f"Starting in {self._count}\u2026")
        self.status.setText("Get ready.")
        self.cue.show_cue("ready", f"GET READY  \u00b7  {self._count}")
        self.countdownChanged.emit(self._count)
        self.recordingChanged.emit(True)
        self._count_timer.start()

    def _count_tick(self):
        self._count -= 1
        if self._count > 0:
            self.time_label.setText(f"Starting in {self._count}\u2026")
            self.cue.show_cue("ready", f"GET READY  \u00b7  {self._count}")
            self.countdownChanged.emit(self._count)
            return
        self._count_timer.stop()
        self.countdownChanged.emit(0)
        self._begin_capture()

    def cancel_countdown(self):
        if self._count <= 0:
            return
        self._count = 0
        self._count_timer.stop()
        self.countdownChanged.emit(0)
        self._reset()
        self.status.setText("Countdown cancelled.")

    def _set_controls_enabled(self, on: bool):
        for w in (self.mic_row, self.desk_row, self.mode, self.monitor, self.refresh_btn, self.close_btn,
                  self.countdown_box):
            w.setEnabled(on)
        self.roomtone_box.setEnabled(on and self._mode == "new")

    def _begin_capture(self):
        sources, gains = self._sources()
        if not sources:
            self._reset()
            self.status.setText("Choose a microphone or a desktop audio source first.")
            return
        try:
            out = self.output_path_factory() if self.output_path_factory else None
            self.session = RecordingSession(sources, gains=gains, output_path=out)
            self.session.start()
        except RecordingError as exc:
            self.session = None
            self.failed.emit(exc.title, exc.user_message)
            return
        self._position = self.playhead if self._mode == "overdub" else 0.0
        self._play_started = None
        self._announced = False
        self._silence_warned = False
        self._room_tone = ROOM_TONE_S if (self.roomtone_box.isChecked() and self._mode == "new") else 0.0
        self.rec_btn.setText("■  Stop")
        self.discard_btn.setVisible(True)
        self._set_controls_enabled(False)
        self.time_label.setText("Starting…")
        self.status.setText("Stay quiet: recording the room tone\u2026" if self._room_tone else "")
        if self._room_tone:
            self.cue.show_cue("quiet", "\u25cf  STAY QUIET", 1.0)
            self._speak_until = self._room_tone + 2.0
        elif self.countdown_box.isChecked():
            self.cue.show_cue("speak", "SPEAK NOW")
            self._speak_until = 2.0
        else:
            self.cue.clear()
            self._speak_until = 0.0
        self.timer.start()
        self.recordingChanged.emit(True)

    def note_playback_started(self, perf_time: float, output_latency_s: float):
        """Called by the window when overdub monitoring playback has started."""
        self._play_started = perf_time
        self._out_latency = output_latency_s

    def _offset(self) -> float:
        if self._mode == "append":
            return self.original_duration
        if self._mode == "overdub":
            return self._position
        return 0.0

    def stop(self):
        session = self.session
        self.timer.stop()
        self.rec_btn.setEnabled(False)
        self.time_label.setText("Saving…")
        mode, position = self._mode, self._position
        play_started, out_latency = self._play_started, self._out_latency
        # only when the quiet second was really recorded
        room = self._room_tone if session is not None and session.elapsed >= self._room_tone + 0.3 else 0.0

        def work(ctx):
            return session.stop()

        def done(result):
            latency = 0.0
            if mode == "overdub" and play_started is not None and result.start_time:
                # the take's sample for "playhead" is where playback began, plus the
                # time the sound took to come out of the speakers and back in
                latency = max(0.0, (play_started - result.start_time) + out_latency + INPUT_LATENCY_S)
            self._reset()
            self.last_room_tone = room
            self.finished.emit(result, mode, position, latency)

        def fail(title, message, _cancelled):
            self._reset()
            self.failed.emit(title, message)

        self._stopping = run_task(work, self, on_success=done, on_error=fail)

    def discard(self):
        if self.session is not None:
            self.timer.stop()
            self.session.cancel()
            log.info("Recording discarded")
        self._reset()
        self.status.setText("Take discarded.")

    def shutdown(self):
        """Window closing: save whatever was recorded rather than losing it."""
        if self.session is not None:
            self.timer.stop()
            try:
                res = self.session.stop()
                log.info("Recording saved on exit: %s", res.path)
            except Exception as exc:
                log.warning("Could not save recording on exit: %s", exc)
            self.session = None

    def _reset(self):
        self.cue.clear()
        self.session = None
        self._stopping = None
        self._count = 0
        self._count_timer.stop()
        self.rec_btn.setEnabled(True)
        self.rec_btn.setText("●  Record")
        self.discard_btn.setVisible(False)
        self._set_controls_enabled(True)
        self.time_label.setText("")
        self.time_label.setProperty("tone", "")
        theme.restyle(self.time_label)
        self.mic_row.meter.reset()
        self.desk_row.meter.reset()
        self.liveUpdate.emit(None, 0.0)
        self.recordingChanged.emit(False)

    # --- live updates ---------------------------------------------------------------------
    def _tick(self):
        s = self.session
        if s is None:
            return
        rows = [r for r in (self.mic_row, self.desk_row) if r.source is not None]
        for row, (rms, peak) in zip(rows, s.levels()):
            row.meter.set_level(rms, peak)
        if s.all_failed:
            self.status.setText(" ".join(s.errors))
            self.stop()
            return
        if not s.capturing:
            return
        if not self._announced:
            self._announced = True
            self.captureStarted.emit()
        self.time_label.setText(f"● REC  {format_duration(s.elapsed)}")
        prompt = []  # the status line is rebuilt every tick: the room-tone prompt goes first
        if self._room_tone and s.elapsed < self._room_tone:
            self.cue.show_cue("quiet", "\u25cf  STAY QUIET", 1.0 - s.elapsed / self._room_tone)
        elif s.elapsed < self._speak_until:
            self.cue.show_cue("speak", "SPEAK NOW")
        elif self.cue.isVisible():
            self.cue.clear()
        if self._room_tone:
            if s.elapsed < self._room_tone:
                self.time_label.setText(f"● REC  {format_duration(s.elapsed)}  \u00b7  stay quiet\u2026")
                prompt = ["Stay quiet: recording the room tone\u2026"]
            elif s.elapsed < self._room_tone + 3.0:
                prompt = ["Speak now."]
        if self.time_label.property("tone") != "record":
            self.time_label.setProperty("tone", "record")
            theme.restyle(self.time_label)
        mins, maxs, rms = s.overview()
        if mins.size:
            ov = WaveformOverview(mins, maxs, rms, 48_000, mins.size * 64)
            self.liveUpdate.emit(ov, self._offset())
        msgs = prompt + list(s.errors)
        if not self._silence_warned:
            silent = s.silent_sources()
            if silent:
                msgs.append(f"No sound from “{silent[0].name}” yet. Check that it is not muted, "
                            f"or that VoiceCleaner may use the microphone ({MIC_PRIVACY_HINT}).")
        self.status.setText(" ".join(msgs))
