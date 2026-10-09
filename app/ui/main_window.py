"""Main application window.

Workflow: Drop WAV -> (automatic) Analyze -> Enhance -> Listen (A/B) -> Export.
All heavy work runs on background threads; the window only reacts to results.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from ..audio import noise_profile as noise_profile_mod
from ..audio.analyzer import analyze
from ..audio.loader import format_duration, format_size, load_wav
from ..audio.recorder import combine_with_original
from ..audio.pipeline import EnhancementPipeline, PipelineResult
from ..audio.settings import ProcessingSettings, from_preset
from ..export.mp3_exporter import export_mp3
from ..export.wav_exporter import ExportOptions, export_wav
from ..utils.config import APP_NAME, APP_VERSION, UserConfig, edition, log_dir, models_dir, user_models_dir
from ..utils.logging import get_logger
from ..utils.shell import reveal
from ..workers.batch_worker import BatchOptions, start_batch
from ..workers.processing_worker import TaskHandle, run_task
from . import theme
from .audio_player import AudioPlayer
from .batch_panel import BatchPanel
from .export_dialog import ExportDialog
from .record_panel import RecordPanel
from .settings_panel import SettingsPanel
from .waveform import WaveformOverview, WaveformView
from .. import sessions
from .brand import STUDIO_NAME, AboutDialog, OpenSessionDialog, SessionNameDialog, app_icon, logo_pixmap
from .playback_panel import PlaybackPanel
from .widgets import DropZone, ElidedLabel, MetricsPanel, card, label

log = get_logger("ui")

WAV_EXTENSIONS = (".wav", ".wave")


class MainWindow(QMainWindow):
    def __init__(self, config: UserConfig):
        super().__init__()
        self.config = config
        self.audio = None
        self.analysis = None
        self.result: PipelineResult | None = None
        self.learned_profile = None
        self.selection: tuple[float, float] | None = None
        self.task: TaskHandle | None = None
        self.model_manager = None
        self._mm_ready = threading.Event()
        self._batch = None
        self._result_settings: dict | None = None
        self._pending_warnings: list[str] = []

        self.session = sessions.current_or_new(config.extra.get("session_path"))
        self.theme_pref = config.extra.get("theme", "auto")
        self.setWindowIcon(app_icon())
        self._update_title()
        self.setAcceptDrops(True)
        self.resize(1360, 880)
        self.setMinimumSize(1060, 700)
        if config.window_geometry:
            self.restoreGeometry(QByteArray.fromHex(config.window_geometry.encode()))

        self.player = AudioPlayer(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.stateChanged.connect(self._on_player_state)
        self.player.sourceChanged.connect(self._on_source_changed)
        self.player.error.connect(lambda m: self._error("Playback problem", m))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_enhance_page(), "ENHANCE")
        self.playback = PlaybackPanel()
        self.playback.openInEnhancer.connect(self._open_from_playback)
        self.playback.addToBatch.connect(self._add_to_batch)
        self.playback.playStarted.connect(self._pause_main_player)
        self.tabs.addTab(self.playback, "PLAYBACK")
        self.batch_panel = BatchPanel()
        self.batch_panel.startRequested.connect(self._start_batch)
        self.batch_panel.cancelRequested.connect(self._cancel_batch)
        self.batch_panel.set_output_dir(config.batch_output_dir)
        self.tabs.addTab(self.batch_panel, "BATCH")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().setStyleSheet("QTabBar { margin-left: 18px; }")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self._install_shortcuts()
        self._set_empty_state()
        self.record_panel.output_path_factory = lambda: self.session.recording_path()
        self.player.stateChanged.connect(lambda st: st == "playing" and self.playback.pause())
        self.playback.set_session(self.session, sessions.sessions_root())
        QTimer.singleShot(50, self._init_engine)

    # ================================================================================== UI
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 16, 10)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(logo_pixmap(40))
        icon.setToolTip(STUDIO_NAME)
        lay.addWidget(icon)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(label("VOICECLEANER", "brand"))
        words.addWidget(label("BY STEAMBURGER STUDIOS", "brandsub"))
        lay.addLayout(words)
        lay.addSpacing(18)
        self.session_btn = QPushButton()
        self.session_btn.setProperty("role", "session")
        self.session_btn.setToolTip("Sessions group related recordings and exports in their own folder")
        self.session_btn.setMenu(self._build_session_menu())
        lay.addWidget(self.session_btn)
        self._update_session_ui()
        lay.addStretch(1)
        self.device_chip = label("Detecting hardware\u2026", "chip")
        self.device_chip.setToolTip("The processor used for AI enhancement. Change it in the menu.")
        self.model_chip = label("Loading AI model\u2026", "chip")
        lay.addWidget(self.device_chip)
        lay.addWidget(self.model_chip)
        self.theme_btn = QPushButton()
        self.theme_btn.setProperty("role", "themetoggle")
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)
        self._update_theme_button()
        menu_btn = QToolButton()
        menu_btn.setText("\u22ef")
        menu_btn.setProperty("role", "menu")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu_btn.setMenu(self._build_menu())
        lay.addWidget(menu_btn)
        return bar

    def _build_session_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("New Session\u2026", self.new_session, QKeySequence("Ctrl+Shift+N"))
        m.addAction("Open Session\u2026", self.open_session_dialog)
        m.addAction("Rename Session\u2026", self.rename_session)
        m.addSeparator()
        m.addAction("Open Session Folder", lambda: self._open_folder(self.session.path))
        m.addAction("Open Default Recordings Folder", lambda: self._open_folder(sessions.sessions_root()))
        return m

    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Open WAV…", self.choose_file, QKeySequence.Open)
        self.export_action = m.addAction("Export Enhanced Audio…", self.export, QKeySequence("Ctrl+E"))
        m.addSeparator()
        dev = m.addMenu("Processing device")
        group = QActionGroup(self)
        self.device_actions = {}
        for key, text in (("auto", "Automatic (GPU if available)"), ("cpu", "CPU only"), ("cuda", "NVIDIA GPU")):
            a = QAction(text, self, checkable=True)
            a.setChecked(self.config.device_preference == key)
            a.triggered.connect(lambda _=False, k=key: self._set_device(k))
            group.addAction(a)
            dev.addAction(a)
            self.device_actions[key] = a
        appearance = m.addMenu("Appearance")
        agroup = QActionGroup(self)
        self.theme_actions = {}
        for key, text in (("auto", "Automatic (follow system)"), ("day", "Day"), ("night", "Night")):
            a = QAction(text, self, checkable=True)
            a.setChecked(self.theme_pref == key)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            agroup.addAction(a)
            appearance.addAction(a)
            self.theme_actions[key] = a
        m.addSeparator()
        m.addAction("Open Log Folder", lambda: self._open_folder(log_dir()))
        m.addAction("Models and Licenses", self._show_models)
        m.addAction(f"About {APP_NAME}", self._show_about)
        return m

    def _build_enhance_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.stack = QStackedWidget()
        self.drop_zone = DropZone()
        self.drop_zone.chooseClicked.connect(self.choose_file)
        self.drop_zone.recordClicked.connect(self.start_new_recording)
        dz_wrap = QWidget()
        dzl = QVBoxLayout(dz_wrap)
        dzl.setContentsMargins(24, 20, 24, 24)
        dzl.addWidget(self.drop_zone)
        self.stack.addWidget(dz_wrap)
        # scrollable, so sections keep their minimum height on small screens
        # (e.g. when the recording strip is open) instead of being squashed
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor_scroll.setFrameShape(QFrame.NoFrame)
        editor_scroll.setWidget(self._build_editor())
        self.stack.addWidget(editor_scroll)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self._build_sidebar())
        return page

    def _build_editor(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 18, 20, 18)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.file_name = ElidedLabel("")
        self.file_name.setProperty("role", "title")
        top.addWidget(self.file_name, 1)
        self.meta_chips = QHBoxLayout()
        self.meta_chips.setSpacing(6)
        top.addLayout(self.meta_chips)
        another = QPushButton("Open Another…")
        another.setProperty("role", "ghost")
        another.clicked.connect(self.choose_file)
        top.addWidget(another)
        lay.addLayout(top)

        self.warning_label = label("", wrap=True)
        self.warning_label.setProperty("tone", "warning")
        self.warning_label.setVisible(False)
        lay.addWidget(self.warning_label)

        self.wave_orig, orig_card, self.vol_orig, self.play_orig = self._wave_card("ORIGINAL", theme.ORIGINAL,
                                                                                   "original", "")
        self.wave_enh, enh_card, self.vol_enh, self.play_enh = self._wave_card(
            "ENHANCED", theme.ACCENT, "enhanced", "Click “Enhance” to hear the improved version here")
        for wv in (self.wave_orig, self.wave_enh):
            wv.seekRequested.connect(self._seek)
            wv.viewChanged.connect(self._sync_view)
        self.wave_orig.selectionChanged.connect(self._selection_changed)
        self.wave_orig.activated.connect(lambda: self._activate("original"))
        self.wave_enh.activated.connect(lambda: self._activate("enhanced"))
        lay.addWidget(orig_card, 1)
        lay.addWidget(enh_card, 1)
        lay.addWidget(self._build_transport())
        self.metrics = MetricsPanel()
        lay.addWidget(self.metrics)
        return w

    def _wave_card(self, title: str, color: str, source: str, placeholder: str):
        c = card()
        v = QVBoxLayout(c)
        v.setContentsMargins(14, 10, 14, 8)
        v.setSpacing(4)
        head = QHBoxLayout()
        tone = "original" if source == "original" else "accent"
        dot = QLabel("●")
        dot.setProperty("tone", tone)
        head.addWidget(dot)
        t = label(title, "section")
        t.setProperty("tone", tone)
        head.addWidget(t)
        head.addStretch(1)
        if source == "original":
            self.record_toggle = QPushButton("\u25cf  Record")
            self.record_toggle.setProperty("role", "record")
            self.record_toggle.setCheckable(True)
            self.record_toggle.setCursor(Qt.PointingHandCursor)
            self.record_toggle.setToolTip("Record from a microphone or desktop audio into the Original track (R)")
            self.record_toggle.toggled.connect(self._show_record_panel)
            head.addWidget(self.record_toggle)
        play = QPushButton("▶  Play")
        play.setProperty("role", "ghost")
        play.setToolTip(f"Play the {source} audio")
        play.clicked.connect(lambda: self._play_source(source))
        head.addWidget(play)
        vol_icon = label("\U0001F50A", "faint")
        head.addWidget(vol_icon)
        vol = QSlider(Qt.Horizontal)
        vol.setProperty("role", "small")
        if source == "original":
            vol.setProperty("role", "small")
        vol.setRange(0, 100)
        vol.setValue(int(self.player.volume(source) * 100))
        vol.setFixedWidth(110)
        vol.setToolTip(f"{title.title()} volume")
        vol.valueChanged.connect(lambda val, s=source: self.player.set_volume(s, val / 100))
        head.addWidget(vol)
        v.addLayout(head)
        if source == "original":
            self.record_panel = RecordPanel()
            self.record_panel.setVisible(False)
            self.record_panel.closeRequested.connect(lambda: self.record_toggle.setChecked(False))
            self.record_panel.captureStarted.connect(self._capture_started)
            self.record_panel.liveUpdate.connect(self._live_recording)
            self.record_panel.recordingChanged.connect(self._recording_changed)
            self.record_panel.finished.connect(self._recording_finished)
            self.record_panel.failed.connect(self._error)
            v.addWidget(self.record_panel)
        wave = WaveformView(source, placeholder)
        v.addWidget(wave, 1)
        return wave, c, vol, play

    def _build_transport(self) -> QWidget:
        c = card()
        lay = QHBoxLayout(c)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        self.ab_orig = QPushButton("ORIGINAL")
        self.ab_enh = QPushButton("ENHANCED")
        for b, side in ((self.ab_orig, "left"), (self.ab_enh, "right")):
            b.setProperty("role", "ab")
            b.setProperty("side", side)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
        self.ab_orig.setToolTip("Listen to the original (shortcut: A)")
        self.ab_enh.setToolTip("Listen to the enhanced version (shortcut: B). Switch any time during playback.")
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.ab_orig)
        group.addButton(self.ab_enh)
        self.ab_orig.setChecked(True)
        self.ab_orig.clicked.connect(lambda: self._activate("original"))
        self.ab_enh.clicked.connect(lambda: self._activate("enhanced"))
        ab = QHBoxLayout()
        ab.setSpacing(0)
        ab.addWidget(self.ab_orig)
        ab.addWidget(self.ab_enh)
        lay.addLayout(ab)
        lay.addSpacing(8)

        self.play_btn = QPushButton("▶")
        self.play_btn.setProperty("role", "round")
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.clicked.connect(self.player.toggle_play)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.play_btn)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setProperty("role", "icon")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self.player.stop)
        lay.addWidget(self.stop_btn)
        self.time_label = label("0:00.0 / 0:00.0")
        self.time_label.setProperty("role", "mono")
        lay.addWidget(self.time_label)
        lay.addStretch(1)

        self.match_box = QCheckBox("Equal-loudness comparison")
        self.match_box.setToolTip("Plays the original at the same loudness as the enhanced version, so the\n"
                                  "comparison is about quality rather than volume.")
        self.match_box.setChecked(self.config.match_loudness_ab)
        self.match_box.toggled.connect(self._update_match_gain)
        lay.addWidget(self.match_box)
        lay.addSpacing(8)

        self.learn_btn = QPushButton("Learn Noise Profile")
        self.learn_btn.setToolTip("Drag across a part of the ORIGINAL waveform that contains only background\n"
                                  "noise (no speech), then click here. VoiceCleaner will remove that noise.")
        self.learn_btn.clicked.connect(self.learn_noise_profile)
        lay.addWidget(self.learn_btn)
        self.profile_label = label("", "faint")
        lay.addWidget(self.profile_label)
        return c

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(360)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setMaximumWidth(360)
        il = QVBoxLayout(inner)
        il.setContentsMargins(20, 18, 20, 18)
        initial = from_preset(self.config.preset)
        initial = initial.copy(target_lufs=self.config.target_lufs, peak_ceiling_dbtp=self.config.peak_ceiling_dbtp)
        if isinstance(self.config.extra.get("eq_gains"), list):
            initial = initial.copy(eq_gains=[float(g) for g in self.config.extra["eq_gains"]],
                                   eq_tilt=float(self.config.extra.get("eq_tilt", 0.0)))
        self.settings_panel = SettingsPanel(initial, self.config.auto_settings, self.config.advanced_open)
        self.settings_panel.restore_section_states(self.config.extra.get("sections", {}))
        self.settings_panel.downloadResynthRequested.connect(self._download_resynth_model)
        il.addWidget(self.settings_panel)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        actions = QFrame()
        actions.setObjectName("sidebarActions")
        actions.setProperty("role", "actions")
        a = QVBoxLayout(actions)
        a.setContentsMargins(20, 14, 20, 18)
        a.setSpacing(8)
        self.status_label = label("", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.speed_label = label("", "faint", wrap=True)
        a.addWidget(self.status_label)
        a.addWidget(self.progress)
        a.addWidget(self.speed_label)
        row = QHBoxLayout()
        self.enhance_btn = QPushButton("Enhance")
        self.enhance_btn.setProperty("role", "primary")
        self.enhance_btn.setCursor(Qt.PointingHandCursor)
        self.enhance_btn.setToolTip("Process the recording with the current settings (Ctrl+Enter)")
        self.enhance_btn.clicked.connect(self.enhance)
        row.addWidget(self.enhance_btn, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_task)
        self.cancel_btn.setVisible(False)
        row.addWidget(self.cancel_btn)
        a.addLayout(row)
        self.export_btn = QPushButton("Export Enhanced WAV…")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.clicked.connect(self.export)
        a.addWidget(self.export_btn)
        v.addWidget(actions)
        self.settings_panel.settingsChanged.connect(self._settings_changed)
        return side

    def _install_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)

        sc(Qt.Key_Space, self._toggle_play_current)
        sc(Qt.Key_A, lambda: self._activate("original"))
        sc(Qt.Key_B, lambda: self._activate("enhanced"))
        sc(Qt.Key_Home, lambda: self._seek(0.0))
        sc("Ctrl+Return", self.enhance)
        sc(Qt.Key_Escape, self._escape)
        sc(Qt.Key_R, self._record_shortcut)

    # ============================================================================ state
    def _set_empty_state(self):
        self.stack.setCurrentIndex(0)
        self.enhance_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.export_action.setEnabled(False)
        self.progress.setVisible(False)
        self.status_label.setText("Open a recording to begin.")
        self.speed_label.setText("")

    def _busy(self, busy: bool, message: str = ""):
        self.enhance_btn.setEnabled(not busy and self.audio is not None)
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        self.drop_zone.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
        has_result = self.result is not None
        self.export_btn.setEnabled(not busy and has_result)
        self.export_action.setEnabled(not busy and has_result)
        self.learn_btn.setEnabled(not busy and self.audio is not None)

    def _on_progress(self, frac: float, message: str):
        self.progress.setValue(int(frac * 1000))
        if message:
            self.status_label.setText(message)

    def _task_running(self) -> bool:
        return self.task is not None and self.task.running

    def _error(self, title: str, message: str):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(f"<b>{title}</b>")
        box.setInformativeText(message)
        box.exec()

    # ============================================================================ engine
    def _init_engine(self):
        pref = self.config.device_preference

        def work(ctx):
            from ..ai.model_manager import ModelManager

            mm = ModelManager(pref)
            mm.preload()
            return mm

        def done(mm):
            self.model_manager = mm
            self._mm_ready.set()
            self._update_engine_chips()

        def failed(title, message, _cancelled):
            self._mm_ready.set()
            self.model_chip.setText("AI model unavailable")
            self.device_chip.setText("CPU")
            self.settings_panel.set_ai_available(False)
            log.warning("Engine init failed: %s", message)

        self._engine_task = run_task(work, self, on_success=done, on_error=failed)

    def _update_engine_chips(self):
        mm = self.model_manager
        if mm is None:
            return
        st = mm.status()
        dev_name = st.device.name.removeprefix("NVIDIA ").removeprefix("GeForce ")
        self.device_chip.setText(f"{'GPU' if st.device.kind == 'cuda' else 'CPU'} · {dev_name}")
        self.device_chip.setToolTip(f"Processing device: {st.device.label}\nChange it in the menu (⋯).")
        if st.device.kind != "cuda" and edition() == "standard":
            from ..utils.hardware import nvidia_gpu_name

            gpu = nvidia_gpu_name()
            if gpu:
                self.device_chip.setText(f"CPU · {gpu.removeprefix('NVIDIA ')} found")
                self.device_chip.setToolTip(
                    f"This is the standard (CPU) edition. Your {gpu} can process much faster with the "
                    "NVIDIA GPU edition of VoiceCleaner (free download on GitHub).")
        if st.installed and not st.error:
            self.model_chip.setText(f"AI · {st.name}")
            self.model_chip.setToolTip(f"{st.name} • license {mm.model.license}\n{mm.model.homepage}")
            self.settings_panel.set_ai_available(True)
        self.settings_panel.set_resynth_status(mm.resynth_available, mm.device.kind)
        if not (st.installed and not st.error):
            self.model_chip.setText("AI model: not installed")
            self.model_chip.setProperty("tone", "warning")
            theme.restyle(self.model_chip)
            self.model_chip.setToolTip(st.error or f"Model files not found in {models_dir()}.\n"
                                                   "Traditional noise reduction will be used.")
            self.settings_panel.set_ai_available(False)

    def _get_model_manager(self):
        self._mm_ready.wait()
        return self.model_manager

    def _set_device(self, pref: str):
        self.config.device_preference = pref
        if self.model_manager is not None:
            self.model_manager.set_device_preference(pref)
            if pref == "cuda" and self.model_manager.device.kind != "cuda":
                self._error("GPU not available", "No compatible NVIDIA GPU was found. Processing will use the CPU.")
            self._update_engine_chips()

    # ============================================================================ files
    def choose_file(self):
        start = self.config.last_open_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open WAV recording", start, "WAV audio (*.wav *.wave);;All files (*)")
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path):
        if self._task_running():
            self.status_label.setText("Please wait for the current task to finish.")
            return
        self.player.stop()
        self.config.last_open_dir = str(path.parent)
        self._busy(True, "Analyzing audio...")
        self.wave_orig.set_busy("Analyzing…") if self.audio else None

        def work(ctx):
            ctx.progress(0.02, "Reading file...")
            audio = load_wav(path)
            ctx.check()
            analysis = analyze(audio.samples, audio.sample_rate,
                               progress=lambda f: ctx.progress(0.1 + 0.8 * f, "Analyzing audio..."))
            ctx.progress(0.92, "Drawing waveform...")
            overview = WaveformOverview.compute(audio.samples, audio.sample_rate)
            return audio, analysis, overview

        self.task = run_task(work, self, on_success=self._file_loaded, on_error=self._task_failed,
                             on_progress=self._on_progress)

    def _file_loaded(self, payload):
        audio, analysis, overview = payload
        self.audio, self.analysis = audio, analysis
        self.result = None
        self.learned_profile = None
        self.selection = None
        self.profile_label.setText("")
        info = audio.info
        self.file_name.setText(info.path.name)
        self.file_name.setToolTip(str(info.path))
        while self.meta_chips.count():
            item = self.meta_chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for text in (format_duration(info.duration), f"{info.sample_rate / 1000:g} kHz", info.channel_label,
                     info.subtype_label, format_size(info.file_size)):
            self.meta_chips.addWidget(label(text, "chip"))
        warnings = self._pending_warnings + analysis.warnings()
        self._pending_warnings = []
        if audio.repaired_nonfinite:
            warnings.append("Some invalid samples (NaN/Inf) were replaced with silence.")
        if audio.is_disk_backed:
            warnings.append("Long recording: audio is processed from disk to save memory.")
        self.warning_label.setText("  ".join(f"⚠ {w}" for w in warnings))
        self.warning_label.setVisible(bool(warnings))

        self.wave_orig.set_busy("")
        self.wave_orig.set_overview(overview)
        self.wave_orig.set_selection(None)
        self.wave_orig.highlight_regions = []
        self.wave_enh.set_overview(None)
        self.wave_enh.set_busy("")
        self.player.set_source("enhanced", None)
        self.player.set_source("original", audio.samples, audio.sample_rate)
        self.player.set_match_gain("original", 1.0)
        self._activate("original")
        self.player.seek(0)

        self.settings_panel.set_analysis(analysis)
        self.metrics.set_original(self._original_rows())
        self.metrics.set_enhanced(None)
        self.metrics.set_score(None, None)
        self.metrics.set_notes(self.settings_panel.auto_notes)
        self.stack.setCurrentIndex(1)
        self.record_panel.set_original(True, audio.duration)
        self._busy(False, "Ready. Adjust settings if you like, then click Enhance.")
        self.speed_label.setText(f"{format_duration(info.duration)} of audio • analyzed automatically")
        log.info("Loaded %s", info.path)

    def _original_rows(self):
        a = self.analysis
        f = lambda v, u: "—" if not np.isfinite(v) else f"{v:.1f} {u}"  # noqa: E731
        return [("Loudness", f(a.lufs, "LUFS")), ("True peak", f(a.true_peak_dbtp, "dBTP")),
                ("Noise floor", f(a.noise_floor_dbfs, "dBFS")), ("Speech-to-noise", f(min(a.snr_db, 80), "dB")),
                ("Dynamic range", f(a.loudness_range_lu, "LU"))]

    def _task_failed(self, title: str, message: str, cancelled: bool):
        self.wave_orig.set_busy("")
        self.wave_enh.set_busy("")
        self._busy(False, "Cancelled." if cancelled else "")
        if not cancelled:
            self.status_label.setText(title)
            self._error(title, message)
        if self.audio is None:
            self._set_empty_state()

    # ============================================================================ enhance
    def enhance(self):
        if self.audio is None or self._task_running():
            return
        self.player.pause() if self.player.active_source == "enhanced" else None
        settings = self.settings_panel.current()
        audio, analysis, profile = self.audio, self.analysis, self.learned_profile
        self._busy(True, "Preparing...")
        self.wave_enh.set_busy("Enhancing…")

        def work(ctx):
            if not self._mm_ready.is_set():
                ctx.progress(0.0, "Loading AI model...")
            mm = self._get_model_manager()
            result = EnhancementPipeline(mm).run(audio, analysis, settings, ctx, learned_profile=profile)
            overview = WaveformOverview.compute(result.samples, result.sample_rate)
            return result, overview

        self.task = run_task(work, self, on_success=self._enhanced, on_error=self._task_failed,
                             on_progress=self._on_progress)

    def _enhanced(self, payload):
        result, overview = payload
        self.result = result
        self._result_settings = result.settings.to_dict()
        pos = self.player.position
        self.player.set_source("enhanced", result.samples, result.sample_rate)
        self.wave_enh.set_busy("")
        self.wave_enh.set_overview(overview, keep_view=False)
        self.wave_enh.set_view(self.wave_orig.t0, self.wave_orig.t1)
        self._update_match_gain()
        self._activate("enhanced")
        self.player.seek(pos)

        self.settings_panel.set_eq_result(result.auto_eq_bands)
        self.metrics.set_enhanced(result.enhanced_metrics.as_rows())
        self.metrics.set_score(result.original_metrics.clarity, result.enhanced_metrics.clarity)
        notes = list(dict.fromkeys(self.settings_panel.auto_notes + result.notes))
        self.metrics.set_notes(notes)
        self._busy(False, "Done. Press Space to listen, and switch A/B to compare.")
        self.speed_label.setText(
            f"Processed {format_duration(result.duration)} in {result.processing_seconds:.1f} s • "
            f"{result.realtime_factor:.1f}x realtime • {result.device_label}")
        if result.attempts > 1:
            self.speed_label.setText(self.speed_label.text() + f"\nQuality check adjusted settings ({result.attempts} passes).")

    def cancel_task(self):
        if self._task_running():
            self.task.cancel()
            self.status_label.setText("Cancelling...")

    def _settings_changed(self, s: ProcessingSettings):
        if self.result is not None and self._result_settings is not None:
            stale = s.to_dict() != self._result_settings
            self.enhance_btn.setText("Enhance Again" if stale else "Enhance")

    # ============================================================================ playback
    def _activate(self, source: str):
        if source == "enhanced" and not self.player.has_source("enhanced"):
            source = "original"
        self.player.set_active(source)
        self._on_source_changed(self.player.active_source)

    def _play_source(self, source: str):
        self._activate(source)
        if self.player.state != "playing":
            self.player.play()

    def _on_source_changed(self, source: str):
        self.ab_orig.setChecked(source == "original")
        self.ab_enh.setChecked(source == "enhanced")
        self.ab_enh.setEnabled(self.player.has_source("enhanced"))
        self.wave_orig.set_active(source == "original")
        self.wave_enh.set_active(source == "enhanced")

    def _on_position(self, seconds: float):
        self.wave_orig.set_playhead(seconds)
        self.wave_enh.set_playhead(seconds)
        dur = self.audio.duration if self.audio else 0.0
        self.time_label.setText(f"{format_duration(seconds)} / {format_duration(dur)}")
        # keep the playhead visible when zoomed in
        wv = self.wave_orig
        if wv.overview is not None and self.player.state == "playing" and not (wv.t0 <= seconds <= wv.t1):
            span = wv.t1 - wv.t0
            self._sync_view(seconds - span * 0.1, seconds + span * 0.9)

    def _on_player_state(self, state: str):
        self.play_btn.setText("❚❚" if state == "playing" else "▶")

    def _seek(self, seconds: float):
        self.player.seek(seconds)

    def _sync_view(self, t0: float, t1: float):
        self.wave_orig.set_view(t0, t1)
        self.wave_enh.set_view(t0, t1)

    def _update_match_gain(self):
        self.config.match_loudness_ab = self.match_box.isChecked()
        gain = 1.0
        if self.match_box.isChecked() and self.result is not None and self.analysis is not None:
            o, e = self.analysis.lufs, self.result.enhanced_metrics.lufs
            if np.isfinite(o) and np.isfinite(e):
                gain = float(np.clip(10 ** ((e - o) / 20), 0.05, 30.0))
        self.player.set_match_gain("original", gain)

    def _escape(self):
        if self._task_running():
            self.cancel_task()
        elif self.selection:
            self.wave_orig.set_selection(None)
            self._selection_changed(None)

    # ============================================================================ noise profile
    def _selection_changed(self, sel):
        self.selection = sel
        if sel and not self.learned_profile:
            self.profile_label.setText(f"Selected {sel[1] - sel[0]:.1f} s")
        elif not sel and not self.learned_profile:
            self.profile_label.setText("")

    def learn_noise_profile(self):
        if self.audio is None:
            return
        if not self.selection:
            if self.learned_profile is not None:
                self.learned_profile = None
                self.learn_btn.setText("Learn Noise Profile")
                self.profile_label.setText("Using automatic noise detection")
                self.wave_orig.highlight_regions = []
                self.wave_orig.update()
                return
            QMessageBox.information(
                self, "Learn Noise Profile",
                "Drag across a part of the ORIGINAL waveform where nobody is speaking, so only the background "
                "noise is selected. Then click “Learn Noise Profile” again.\n\nA second or two is enough.")
            return
        a, b = self.selection
        if b - a < 0.3:
            QMessageBox.information(self, "Selection too short", "Select at least 0.3 seconds of background noise.")
            return
        mono = self.audio.mono() if self.audio.channels > 1 else self.audio.samples[:, 0]
        profile = noise_profile_mod.estimate(mono, self.audio.sample_rate, [(a, b)], source="learned", margin_s=0.0)
        if profile is None:
            self._error("Could not learn noise", "The selection is too short to measure the noise.")
            return
        # warn if the selection looks like it contains speech
        mask = self.analysis.speech_mask
        i0, i1 = int(a / 0.01), int(b / 0.01)
        if mask.size and mask[i0:i1].mean() > 0.5:
            r = QMessageBox.question(self, "Selection contains speech?",
                                     "This part of the recording seems to contain speech. Learning a noise profile "
                                     "from speech would remove parts of the voice.\n\nUse it anyway?")
            if r != QMessageBox.Yes:
                return
        self.learned_profile = profile
        self.wave_orig.highlight_regions = [(a, b)]
        self.wave_orig.set_selection(None)
        self.selection = None
        self.learn_btn.setText("Clear Noise Profile")
        self.profile_label.setText(f"Noise profile learned ({b - a:.1f} s). Click Enhance to apply.")
        log.info("Learned noise profile from %.2f-%.2f s", a, b)

    # ============================================================================ models
    def _download_resynth_model(self):
        if self._task_running():
            self.status_label.setText("Please wait for the current task to finish.")
            return
        r = QMessageBox.question(
            self, "Download voice re-synthesis model",
            "VoiceCleaner will download NVIDIA BigVGAN-v2 (about 490 MB, MIT license) from NVIDIA's official "
            "Hugging Face repository and verify its checksum.\n\nAfter this one-time download, re-synthesis "
            "works offline. Download now?")
        if r != QMessageBox.Yes:
            return
        from ..ai.model_manager import RESYNTH_FOLDER

        def work(ctx):
            from ..ai.model_download import DownloadError, download_file_model
            from ..utils.errors import ModelUnavailableError

            try:
                download_file_model(RESYNTH_FOLDER, user_models_dir(),
                                    progress=lambda f: ctx.progress(f, f"Downloading voice model… {f:.0%}"),
                                    cancelled=lambda: ctx.cancelled)
            except DownloadError as exc:
                ctx.check()  # a cancel shows as "Cancelled", not as an error
                raise ModelUnavailableError(f"The download failed ({exc}).") from None
            except OSError as exc:
                raise ModelUnavailableError("The download failed. Check the internet connection and try again.") from exc
            return True

        def done(_):
            self._busy(False, "Voice re-synthesis model installed.")
            if self.model_manager is not None:
                self.model_manager.refresh_model_paths()
                self.settings_panel.set_resynth_status(True, self.model_manager.device.kind)

        self._busy(True, "Downloading voice model...")
        self.task = run_task(work, self, on_success=done, on_error=self._task_failed, on_progress=self._on_progress)

    # ============================================================================ recording
    def start_new_recording(self):
        """From the start screen: open an empty Original track with the recorder ready."""
        if self._task_running():
            return
        if self.audio is None:
            self.file_name.setText("New recording")
            self.file_name.setToolTip("")
            while self.meta_chips.count():
                item = self.meta_chips.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.warning_label.setVisible(False)
            self.wave_orig.placeholder = "Your recording will appear here"
            self.wave_orig.set_overview(None)
            self.wave_enh.set_overview(None)
            self.metrics.set_enhanced(None)
            self.metrics.set_score(None, None)
            self.record_panel.set_original(False, 0.0)
            self.stack.setCurrentIndex(1)
            self.status_label.setText("Choose an input and press Record.")
        self.record_toggle.setChecked(True)

    def _show_record_panel(self, on: bool):
        if not on and self.record_panel.recording:
            self.record_toggle.setChecked(True)  # cannot hide while recording
            return
        if on:
            self.record_panel.playhead = self.player.position
            self.record_panel.refresh_sources()
        self.record_panel.setVisible(on)
        if not on and self.audio is None:
            self._set_empty_state()

    def _record_shortcut(self):
        if self.tabs.currentIndex() != 0:
            return
        if self.stack.currentIndex() == 0:
            self.start_new_recording()
        elif not self.record_panel.isVisible():
            self.record_toggle.setChecked(True)
        else:
            self.record_panel.playhead = self.player.position
            self.record_panel.toggle()

    def _recording_changed(self, recording: bool):
        self.enhance_btn.setEnabled(not recording and self.audio is not None)
        self.export_btn.setEnabled(not recording and self.result is not None)
        self.export_action.setEnabled(not recording and self.result is not None)
        self.learn_btn.setEnabled(not recording and self.audio is not None)
        self.drop_zone.setEnabled(not recording)
        self.tabs.tabBar().setEnabled(not recording)
        self.setAcceptDrops(not recording)
        if recording:
            self.record_panel.playhead = self.player.position
            if self.player.state == "playing":
                self.player.pause()
            self.status_label.setText("Recording\u2026")
        else:
            self.wave_orig.set_live(None)

    def _capture_started(self):
        rp = self.record_panel
        if rp._mode == "overdub" and rp.monitor.isChecked() and self.audio is not None:
            self._activate("original")
            self.player.seek(rp._position)
            self.player.play()
            rp.note_playback_started(time.perf_counter(), self.player.output_latency)

    def _live_recording(self, overview, offset: float):
        self.wave_orig.set_live(overview, offset)
        if overview is not None and self.wave_orig.overview is not None:
            self.wave_enh.set_view(self.wave_orig.t0, self.wave_orig.t1)

    def _recording_finished(self, result, mode: str, position: float, latency: float):
        self.player.stop()
        self.record_toggle.setChecked(False)
        self._pending_warnings = list(result.warnings)
        log.info("Take finished: %s mode=%s pos=%.2f latency=%.3f", result.path, mode, position, latency)
        self.playback.refresh()
        if mode == "new" or self.audio is None:
            self.open_file(result.path)
            return
        original = self.audio
        self._busy(True, "Combining recording with the Original...")

        def work(ctx):
            out = self.session.recording_path(mode)
            return combine_with_original(original, result.path, mode, position, latency, out_path=out)

        combined: list[Path] = []

        def open_when_finished():
            # open only after the combine thread has fully finished, otherwise
            # open_file() would see a task still running and refuse
            if combined:
                self._busy(False)
                self.open_file(combined[0])

        self.task = run_task(work, self, on_success=combined.append, on_error=self._task_failed,
                             on_finished=open_when_finished)

    # ============================================================================ export
    def export(self):
        if self.result is None or self._task_running():
            return
        suggested = self.session.export_path(self.audio.info.path)
        dlg = ExportDialog(self.audio.info.path, self.result.sample_rate, str(self.session.exports_dir),
                           self.config.output_format, self.config.output_bit_depth, self.config.output_sample_rate, self,
                           suggested_name=suggested.stem)
        if dlg.exec() != ExportDialog.Accepted:
            return
        v = dlg.values()
        self.config.last_export_dir = str(v["path"].parent)
        self.config.output_format = v["format"]
        self.config.output_bit_depth = v["bit_depth"]
        self.config.output_sample_rate = v["sample_rate"]
        result, source = self.result, self.audio.info.path
        self._busy(True, "Exporting...")

        def work(ctx):
            prog = lambda f: ctx.progress(f, "Exporting...")  # noqa: E731
            if v["format"] == "mp3":
                return export_mp3(result.samples, result.sample_rate, v["path"], v["mp3_quality"], v["sample_rate"],
                                  source_path=source, progress=prog)
            return export_wav(result.samples, result.sample_rate, v["path"],
                              ExportOptions(v["bit_depth"], v["sample_rate"]), source_path=source, progress=prog)

        self.task = run_task(work, self, on_success=self._exported, on_error=self._task_failed,
                             on_progress=self._on_progress)

    def _exported(self, path: Path):
        self._busy(False, f"Saved {path.name}")
        self.playback.refresh()
        log.info("Exported %s", path)
        box = QMessageBox(self)
        box.setWindowTitle("Export complete")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Saved {path.name}</b>")
        box.setInformativeText(str(path.parent))
        show = box.addButton("Show in Folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is show:
            reveal(path)

    # ============================================================================ batch
    def _start_batch(self, files: list, output_dir):
        if self._batch is not None:
            return
        self.config.batch_output_dir = str(output_dir) if isinstance(output_dir, Path) else ""
        if output_dir == "session":
            output_dir = self.session.exports_dir
        opts = BatchOptions(settings=self.settings_panel.current(), smart=self.settings_panel.smart.isChecked(),
                            output_dir=output_dir, fmt=self.config.output_format, bit_depth=self.config.output_bit_depth,
                            sample_rate=self.config.output_sample_rate, session=self.session)
        thread, runner = start_batch([Path(f) for f in files], opts, self._get_model_manager)
        self._batch = (thread, runner)
        bp = self.batch_panel
        bp.set_running(True)
        runner.fileStarted.connect(bp.file_started)
        runner.fileProgress.connect(bp.file_progress)
        runner.fileFinished.connect(bp.file_finished)
        runner.finished.connect(self._batch_done)

    def _cancel_batch(self):
        if self._batch:
            self._batch[1].cancel()

    def _batch_done(self):
        thread, _ = self._batch
        thread.wait(2000)
        self._batch = None
        self.batch_panel.set_running(False)
        self.batch_panel.summary.setText(self.batch_panel.summary.text() + " • finished")
        self.playback.refresh()

    # ============================================================================ drag & drop
    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(WAV_EXTENSIONS) for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop_zone.set_hover(True)

    def dragLeaveEvent(self, _e):
        self.drop_zone.set_hover(False)

    def dropEvent(self, e):
        self.drop_zone.set_hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        dirs = [p for p in paths if p.is_dir()]
        for d in dirs:
            paths.extend(sorted(d.glob("*.wav")))
        wavs = [p for p in paths if p.suffix.lower() in WAV_EXTENSIONS]
        if not wavs:
            self._error("Not a WAV file", "VoiceCleaner opens WAV recordings. Drop a .wav file.")
            return
        if len(wavs) == 1 and self.tabs.currentWidget() is not self.batch_panel:
            self.tabs.setCurrentIndex(0)
            self.open_file(wavs[0])
        else:
            self.batch_panel.add_files(wavs)
            self.tabs.setCurrentWidget(self.batch_panel)

    # ============================================================================ misc
    def _open_folder(self, path: Path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_models(self):
        mm = self.model_manager
        name = mm.model.display_name if mm else "DeepFilterNet3"
        QMessageBox.information(
            self, "Models and Licenses",
            f"<b>AI speech enhancement: {name}</b><br>"
            "Schröter et al., “DeepFilterNet: Perceptually Motivated Real-Time Speech Enhancement” (2023).<br>"
            "License: MIT or Apache-2.0 (dual licensed). Source: github.com/Rikorose/DeepFilterNet<br><br>"
            f"Bundled models: {models_dir()}<br>Downloaded models: {user_models_dir()}<br><br>"
            "All other processing (noise profiling, de-reverberation, EQ, dynamics, loudness, limiting) "
            "is VoiceCleaner's own signal processing.")

    def _show_about(self):
        lines = ["AI speech enhancement: DeepFilterNet3 (MIT / Apache-2.0)",
                 "Voice re-synthesis: NVIDIA BigVGAN-v2 (MIT)",
                 "Fonts: Bebas Neue, Syne, DM Mono (SIL Open Font License)"]
        AboutDialog(lines, self).exec()

    # ============================================================================ sessions
    def _update_title(self):
        self.setWindowTitle(f"{self.session.name} \u2014 {APP_NAME} by {STUDIO_NAME}")

    def _update_session_ui(self):
        if hasattr(self, "session_btn"):
            self.session_btn.setText(f"SESSION \u00b7 {self.session.name}  \u25be")
        self._update_title()

    def _switch_session(self, session):
        self.session = session
        self.config.extra["session_path"] = str(session.path)
        self._update_session_ui()
        self.playback.set_session(session, sessions.sessions_root())
        self.status_label.setText(f"Session \u201c{session.name}\u201d. New recordings and exports go to its folder.")
        log.info("Session: %s (%s)", session.name, session.path)

    def new_session(self):
        if self.record_panel.recording:
            self._error("Recording in progress", "Stop the recording before starting a new session.")
            return
        dlg = SessionNameDialog("New Session", sessions.default_name(), "Create Session", self)
        if dlg.exec() != SessionNameDialog.Accepted:
            return
        try:
            self._switch_session(sessions.create(dlg.name))
        except (sessions.SessionError, OSError) as exc:
            self._error("Could not create the session", str(exc))

    def open_session_dialog(self):
        if self.record_panel.recording:
            self._error("Recording in progress", "Stop the recording before switching sessions.")
            return
        dlg = OpenSessionDialog(self.session.name, self)
        if dlg.exec() != OpenSessionDialog.Accepted or not dlg.chosen:
            return
        try:
            self._switch_session(sessions.open_session(Path(dlg.chosen)))
        except (sessions.SessionError, OSError) as exc:
            self._error("Could not open the session", str(exc))

    def rename_session(self):
        if self.record_panel.recording or self._task_running():
            self._error("Busy", "Wait until recording or processing has finished, then rename the session.")
            return
        dlg = SessionNameDialog("Rename Session", self.session.name, "Rename", self)
        if dlg.exec() != SessionNameDialog.Accepted:
            return
        loaded_inside = self.audio is not None and self.audio.info and self.session.path in self.audio.info.path.parents
        if loaded_inside:
            self.player.stop()
        try:
            self._switch_session(sessions.rename(self.session, dlg.name))
        except (sessions.SessionError, OSError) as exc:
            self._error("Could not rename the session", f"{exc}\n\nClose any program using files in the session folder.")

    # ============================================================================ theme
    def set_theme(self, pref: str):
        self.theme_pref = pref
        mode = theme.apply(QApplication.instance(), pref)
        if pref in self.theme_actions:
            self.theme_actions[pref].setChecked(True)
        self._update_theme_button()
        log.info("Theme: %s (%s)", pref, mode)

    def _toggle_theme(self):
        self.set_theme("day" if theme.MODE == "night" else "night")

    def _update_theme_button(self):
        if hasattr(self, "theme_btn"):
            night = theme.MODE == "night"
            self.theme_btn.setText("\u263e" if night else "\u2600")
            self.theme_btn.setToolTip("Switch to day mode" if night else "Switch to night mode")

    def _system_scheme_changed(self, *_):
        if self.theme_pref == "auto":
            self.set_theme("auto")

    # ============================================================================ tabs / playback
    def _tab_changed(self, index: int):
        if self.tabs.widget(index) is self.playback:
            self.playback.refresh()

    def _toggle_play_current(self):
        if self.tabs.currentWidget() is self.playback:
            self.playback.toggle_play()
        else:
            self.player.toggle_play()

    def _pause_main_player(self):
        if self.player.state == "playing":
            self.player.pause()

    def _open_from_playback(self, path):
        self.playback.pause()
        self.tabs.setCurrentIndex(0)
        self.open_file(Path(path))

    def _add_to_batch(self, paths):
        self.batch_panel.add_files([Path(p) for p in paths])
        self.tabs.setCurrentWidget(self.batch_panel)

    def closeEvent(self, e):
        if self._task_running():
            self.task.cancel()
            self.task.wait(3000)
        if self._batch:
            self._batch[1].cancel()
            self._batch[0].wait(3000)
        self.record_panel.shutdown()
        self.player.shutdown()
        self.playback.shutdown()
        s = self.settings_panel.current()
        c = self.config
        c.window_geometry = bytes(self.saveGeometry().toHex()).decode()
        c.preset = s.preset
        c.auto_settings = self.settings_panel.smart.isChecked()
        c.noise_reduction, c.speech_enhancement, c.room_reduction = s.noise_reduction, s.speech_enhancement, s.room_reduction
        c.target_lufs, c.peak_ceiling_dbtp = s.target_lufs, s.peak_ceiling_dbtp
        c.advanced_open = self.settings_panel.advanced_open
        c.extra["sections"] = self.settings_panel.section_states()
        c.extra["eq_gains"] = list(s.eq_gains)
        c.extra["eq_tilt"] = s.eq_tilt
        c.extra["session_path"] = str(self.session.path)
        c.extra["theme"] = self.theme_pref
        try:
            c.save()
        except OSError as exc:
            log.warning("Could not save preferences: %s", exc)
        super().closeEvent(e)
