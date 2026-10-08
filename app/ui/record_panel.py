"""Recording strip shown inside the Original track card.

Pick a microphone and/or a desktop-audio source, choose how the take relates
to the current Original (new / append / overdub at playhead), watch the level
meters, and record. The take is drawn live on the Original waveform.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio.loader import format_duration
from ..audio.recorder import MODES, InputSource, RecordingError, RecordingSession, list_sources
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


class RecordPanel(QFrame):
    captureStarted = Signal()  # audio is arriving; overdub monitoring may start now
    liveUpdate = Signal(object, float)  # WaveformOverview | None, offset seconds
    recordingChanged = Signal(bool)
    finished = Signal(object, str, float, float)  # RecordingResult, mode, position_s, latency_s
    failed = Signal(str, str)
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordPanel")
        # keep the strip at its natural height; the waveform below gives way instead
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setStyleSheet(f"#recordPanel {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER}; "
                           "border-radius: 10px; }")
        self.session: RecordingSession | None = None
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

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("RECORD")
        title.setProperty("role", "section")
        title.setStyleSheet(f"color: {theme.RECORD};")
        head.addWidget(title)
        head.addStretch(1)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("↻ Refresh devices")
        self.refresh_btn.setStyleSheet(f"QToolButton {{ border: none; color: {theme.MUTED}; }} QToolButton:hover {{ color: {theme.TEXT}; }}")
        self.refresh_btn.clicked.connect(self.refresh_sources)
        head.addWidget(self.refresh_btn)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Hide the recording controls")
        close.setStyleSheet(f"QToolButton {{ border: none; color: {theme.MUTED}; padding: 0 4px; }} QToolButton:hover {{ color: {theme.TEXT}; }}")
        close.clicked.connect(self.closeRequested)
        self.close_btn = close
        head.addWidget(close)
        v.addLayout(head)

        self.mic_row = _SourceRow("Microphone", "No microphone")
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
        self.time_label.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11pt;")
        act.addWidget(self.time_label)
        act.addStretch(1)
        v.addLayout(act)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "faint")
        v.addWidget(self.status)

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

    @property
    def recording(self) -> bool:
        return self.session is not None

    # --- record / stop -----------------------------------------------------------------
    def toggle(self):
        if self._stopping is not None:
            return
        if self.session is None:
            self.start()
        else:
            self.stop()

    def start(self):
        sources, gains = [], []
        for row in (self.mic_row, self.desk_row):
            if row.source is not None:
                sources.append(row.source)
                gains.append(row.gain)
        if not sources:
            self.status.setText("Choose a microphone or a desktop audio source first.")
            return
        try:
            self.session = RecordingSession(sources, gains=gains)
            self.session.start()
        except RecordingError as exc:
            self.session = None
            self.failed.emit(exc.title, exc.user_message)
            return
        self._position = self.playhead if self._mode == "overdub" else 0.0
        self._play_started = None
        self._announced = False
        self._silence_warned = False
        self.rec_btn.setText("■  Stop")
        self.discard_btn.setVisible(True)
        for w in (self.mic_row, self.desk_row, self.mode, self.monitor, self.refresh_btn, self.close_btn):
            w.setEnabled(False)
        self.time_label.setText("Starting…")
        self.status.setText("")
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

        def work(ctx):
            return session.stop()

        def done(result):
            latency = 0.0
            if mode == "overdub" and play_started is not None and result.start_time:
                # the take's sample for "playhead" is where playback began, plus the
                # time the sound took to come out of the speakers and back in
                latency = max(0.0, (play_started - result.start_time) + out_latency + INPUT_LATENCY_S)
            self._reset()
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
        self.session = None
        self._stopping = None
        self.rec_btn.setEnabled(True)
        self.rec_btn.setText("●  Record")
        self.discard_btn.setVisible(False)
        for w in (self.mic_row, self.desk_row, self.mode, self.monitor, self.refresh_btn, self.close_btn):
            w.setEnabled(True)
        self.time_label.setText("")
        self.time_label.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11pt;")
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
        self.time_label.setStyleSheet(f"color: {theme.RECORD}; font-family: 'Cascadia Mono', Consolas, monospace; "
                                      "font-size: 11pt; font-weight: 600;")
        mins, maxs, rms = s.overview()
        if mins.size:
            ov = WaveformOverview(mins, maxs, rms, 48_000, mins.size * 64)
            self.liveUpdate.emit(ov, self._offset())
        msgs = list(s.errors)
        if not self._silence_warned:
            silent = s.silent_sources()
            if silent:
                msgs.append(f"No sound from “{silent[0].name}” yet. Check that it is not muted, "
                            "or that Windows allows apps to use the microphone.")
        self.status.setText(" ".join(msgs))
