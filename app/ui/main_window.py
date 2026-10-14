"""Main application window.

Workflow: Drop WAV -> (automatic) Analyze -> Enhance -> Listen (A/B) -> Export.
All heavy work runs on background threads; the window only reacts to results.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QBoxLayout, QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from ..audio import noise_profile as noise_profile_mod
from ..audio.analyzer import FRAME_HOP_S, analyze
from ..audio.loader import format_duration, format_size, load_wav
from ..audio.recorder import combine_with_original
from ..audio.pipeline import EnhancementPipeline, PipelineResult
from ..audio.settings import ProcessingSettings, apply_simple, from_preset, preset_names
from ..export.mp3_exporter import export_mp3
from ..export.wav_exporter import ExportOptions, export_wav
from ..utils import update_install, updates, user_presets
from ..utils.config import APP_NAME, APP_VERSION, UserConfig, edition, log_dir, models_dir, user_models_dir
from ..utils.logging import get_logger
from ..utils.shell import REDO_KEYS, reveal, shortcut_text
from ..workers.batch_worker import BatchOptions, start_batch
from ..workers.processing_worker import TaskHandle, run_task
from . import theme
from .activity import ActivityTracker, eta_text
from .audio_player import AudioPlayer
from .batch_panel import BatchPanel
from .export_dialog import ExportDialog
from .record_panel import RecordPanel
from .settings_panel import SettingsPanel
from .waveform import WaveformOverview, WaveformView
from .. import sessions
from .brand import STUDIO_NAME, AboutDialog, OpenSessionDialog, SessionNameDialog, app_icon, logo_pixmap
from .compile_panel import CompilePanel
from .playback_panel import PlaybackPanel
from .update_dialog import UpdateChecker, UpdateDialog
from .widgets import DropZone, ElidedLabel, MetricsPanel, ThemeToggle, card, label, reserve_bold_width

log = get_logger("ui")

from ..audio.decode import AUDIO_EXTENSIONS, FILE_FILTER  # noqa: E402
MAX_RECENT = 10
PREVIEW_SETTLE_MS = 600  # live preview starts this long after the last settings change
PREVIEW_SECONDS = 10.0  # length of the live preview around the playhead
PREVIEW_MAX_SELECTION_S = 30.0  # a longer selection is previewed from its start

SHORTCUTS = [
    ("Space", "Play / pause"),
    ("A", "Listen to the original"),
    ("B", "Listen to the enhanced version"),
    ("Home", "Back to the start"),
    ("Ctrl+Enter", "Enhance"),
    ("Ctrl+E", "Export enhanced audio"),
    ("Ctrl+O", "Open a recording or video"),
    ("R", "Record / stop recording"),
    ("L", "Loop the selection (or the whole recording)"),
    ("Ctrl+Shift+N", "New session"),
    ("Ctrl+T", "Teleprompter (its own keys: press H in it)"),
    ("Ctrl+Z / " + REDO_KEYS, "Undo / redo a settings change"),
    ("Esc", "Cancel"),
    ("Ctrl+/", "This list"),
    ("Ctrl+mouse wheel", "Zoom the waveform"),
    ("Double-click a slider", "Reset it to the preset's value"),
    ("E (Compile)", f"Edit the selected take: X cut, S silence, {shortcut_text('Ctrl+Z')} undo"),
    ("P (Batch)", "Preview on/off: a sample of the selected file; Space plays it, A / B compare"),
]


class MainWindow(QMainWindow):
    engineStatus = Signal(str)  # what the engine is loading (shown on the splash)
    engineReady = Signal()  # the AI engine is ready, or failed and DSP will be used

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
        self._enh_overview = None  # the enhanced track's waveform without a live preview
        self._exported_result = None  # the enhanced result last exported (to warn before discarding others)
        self._after_export = None  # runs after the Enhance tab's export succeeds (e.g. switch session)
        self._compile_link = None  # (recording, enhanced file, Compile row) when opened from Compile
        self._tour = None  # the Getting Started / What's New overlay, while shown
        from . import wheel_guard

        wheel_guard.install()  # the wheel scrolls panels; it adjusts only a control that was clicked
        self._preview_task = None
        self._preview_gen = 0
        self._export_after_enhance = None  # what to finish once "Enhance Again" is done (export / update)
        self._pending_update = None
        self.prompter = None  # the teleprompter window, while open
        from ..teleprompter.enhancer import TakeEnhancer

        self.take_enhancer = TakeEnhancer(self._get_model_manager, self)
        self.take_enhancer.enhanced.connect(self._take_enhanced)
        self.take_enhancer.failed.connect(lambda name, msg: log.warning("Take %s not enhanced: %s", name, msg))
        self.take_enhancer.failed.connect(lambda name, msg: self.compile_panel.on_take_failed(name, msg))
        self.take_enhancer.pendingChanged.connect(self._takes_pending)  # (plan, installer, version, relaunch): installed when the app closes
        self._pending_warnings: list[str] = []
        self._last_notes: list[str] = []

        self.session = sessions.current_or_new(config.extra.get("session_path"))
        self.theme_pref = config.extra.get("theme", "auto")
        self.setWindowIcon(app_icon())
        self._update_title()
        self.setAcceptDrops(True)
        self.resize(1360, 880)
        avail = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        # never larger than the screen, so small or scaled displays can still fit the window
        self.setMinimumSize(min(1060, avail.width()) if avail else 1060, min(700, avail.height() - 40) if avail else 700)
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
        self.compile_panel = CompilePanel()
        if isinstance(self.config.extra.get("compile"), dict):
            self.compile_panel.restore_options(self.config.extra["compile"])
        self.compile_panel.exportRequested.connect(self._export_compilation)
        self.compile_panel.reenhanceRequested.connect(self._reenhance_takes)
        self.compile_panel.openInEnhanceRequested.connect(self.open_take_in_enhance)
        self.compile_panel.playStarted.connect(self._compile_play_started)
        self.playback.addToCompile.connect(self._add_to_compile)
        self.tabs.addTab(self.compile_panel, "COMPILE")
        self.batch_panel = BatchPanel()
        self.batch_panel.startRequested.connect(self._start_batch)
        self.batch_panel.cancelRequested.connect(self._cancel_batch)
        self.batch_panel.openRequested.connect(self._open_batch_result)
        self.batch_panel.previewRequested.connect(self._batch_preview)
        self.batch_panel.previewStopped.connect(self._stop_batch_preview)
        self.batch_panel.player.stateChanged.connect(
            lambda st: st == "playing" and self._pause_main_player())
        self._bp_cache = None  # (path, mtime, audio, analysis) of the file last previewed in Batch
        self._bp_task = None
        self._bp_gen = 0
        self._bp_timer = QTimer(self)
        self._bp_timer.setSingleShot(True)
        self._bp_timer.setInterval(700)
        self._bp_timer.timeout.connect(lambda: self.batch_panel.preview_path
                                       and self._batch_preview(self.batch_panel.preview_path))
        self.batch_panel.set_output_dir(config.batch_output_dir)
        # the Batch tab shows the same settings sidebar as Enhance (moved on tab change)
        self.batch_page = QWidget()
        self._batch_layout = QHBoxLayout(self.batch_page)
        self._batch_layout.setContentsMargins(0, 0, 0, 0)
        self._batch_layout.setSpacing(0)
        self._batch_layout.addWidget(self.batch_panel, 1)
        self.tabs.addTab(self.batch_page, "BATCH")
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_titles = {self.tabs.widget(i): self.tabs.tabText(i) for i in range(self.tabs.count())}
        self.activity = ActivityTracker(self)
        self.activity.changed.connect(self._activity_changed)
        self.compile_panel.activity = self.activity
        self._activity_timer = QTimer(self)  # keeps elapsed / time-left fresh
        self._activity_timer.setInterval(1000)
        self._activity_timer.timeout.connect(self._activity_changed)
        self._job_key: str | None = None
        self._job_failed = False
        self.tabs.tabBar().setStyleSheet("QTabBar { margin-left: 18px; }")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self._install_shortcuts()
        self._set_empty_state()
        self.record_panel.output_path_factory = lambda: self.session.recording_path()
        self.player.stateChanged.connect(lambda st: st == "playing" and (self.playback.pause(),
                                                                         self.compile_panel.pause(),
                                                                         self.batch_panel.player.pause()))
        self.playback.set_session(self.session, sessions.sessions_root())
        self.compile_panel.set_session(self.session)
        self._refresh_session_recordings()
        last = self.config.extra.get("last_tab", 0)
        if isinstance(last, int) and 0 < last < self.tabs.count():
            self.tabs.setCurrentIndex(last)  # where the user left off
        QTimer.singleShot(50, self._init_engine)

        self.update_checker = UpdateChecker(self)
        self.update_checker.found.connect(self._update_found)
        self.update_checker.upToDate.connect(self._update_up_to_date)
        self.update_checker.failed.connect(self._update_failed)
        QTimer.singleShot(4000, self._auto_check_updates)  # after startup has settled
        QTimer.singleShot(1500, self._report_update_result)
        QTimer.singleShot(3000, self._resume_take_enhancing)  # takes left unenhanced last time

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
        self.prompter_btn = QPushButton()
        self.prompter_btn.setProperty("role", "session")
        self.prompter_btn.setCursor(Qt.PointingHandCursor)
        self.prompter_btn.clicked.connect(self.open_teleprompter)
        self.prompter_btn.ensurePolished()
        # one width for every state, so the header never shifts
        bold = QFont(self.prompter_btn.font())
        bold.setBold(True)  # the recording state is bold
        widest = max(QFontMetrics(bold).horizontalAdvance(t)
                     for t in ("\u25a4  TELEPROMPTER", "\u25cf  REC \u00b7 TELEPROMPTER"))
        self.prompter_btn.setFixedWidth(widest + 30)
        lay.addWidget(self.prompter_btn)
        self._update_prompter_button()
        lay.addStretch(1)
        self.activity_chip = QPushButton()
        self.activity_chip.setProperty("role", "activity")
        self.activity_chip.setCursor(Qt.PointingHandCursor)
        self.activity_chip.clicked.connect(self._activity_clicked)
        self.activity_chip.setVisible(False)
        self.activity_chip.ensurePolished()
        # one width for every label it shows, so the header never shifts while it updates
        longest = "● UPDATING PREVIEW · 100%  +9"
        self.activity_chip.setFixedWidth(self.activity_chip.fontMetrics().horizontalAdvance(longest) + 34)
        lay.addWidget(self.activity_chip)
        # status, not buttons: plain text
        self.device_chip = label("Detecting hardware\u2026", "status")
        self.device_chip.setToolTip("The processor used for AI enhancement. Change it in the menu.")
        self.model_chip = label("Loading AI model\u2026", "status")
        lay.addWidget(self.device_chip)
        lay.addWidget(label("\u00b7", "status"))
        lay.addWidget(self.model_chip)
        lay.addSpacing(8)
        self.theme_btn = ThemeToggle()
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)
        self._update_theme_button()
        menu_btn = QToolButton()
        menu_btn.setText("\u22ef")
        menu_btn.setProperty("role", "menu")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu_btn.setMenu(self._build_menu())
        lay.addWidget(menu_btn)
        self.menu_btn = menu_btn
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
        m.addAction("Open Recording…", self.choose_file, QKeySequence.Open)
        self.recent_menu = m.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.export_action = m.addAction("Export Enhanced Audio…", lambda: self.export(), QKeySequence("Ctrl+E"))
        m.addSeparator()
        dev = m.addMenu("Processing device")
        group = QActionGroup(self)
        self.device_actions = {}
        if sys.platform == "darwin":  # no NVIDIA GPUs on a Mac; the Apple GPU runs re-synthesis
            choices = (("auto", "Automatic (Apple GPU for re-synthesis)"), ("cpu", "CPU only"))
        else:
            choices = (("auto", "Automatic (GPU if available)"), ("cpu", "CPU only"), ("cuda", "NVIDIA GPU"))
        for key, text in choices:
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
        appearance.addSeparator()
        fgroup = QActionGroup(self)
        self.font_actions = {}
        for key, text in (("system", "System Fonts (most readable)"), ("brand", "Brand Fonts (Steamburger Studios)")):
            a = QAction(text, self, checkable=True)
            a.setChecked(self.config.extra.get("fonts", "system") == key)
            a.triggered.connect(lambda _=False, k=key: self.set_fonts(k))
            fgroup.addAction(a)
            appearance.addAction(a)
            self.font_actions[key] = a
        m.addSeparator()
        m.addAction("Teleprompter\u2026", self.open_teleprompter, QKeySequence("Ctrl+T"))
        m.addSeparator()
        m.addAction("Getting Started", lambda: self.start_tour("basics"))
        m.addAction("What's New", lambda: self.start_tour("news"))
        m.addAction("Keyboard Shortcuts", self._show_shortcuts, QKeySequence("Ctrl+/"))
        m.addAction("Show Tips Again", self._show_tips)
        m.addAction("Open Log Folder", lambda: self._open_folder(log_dir()))
        m.addAction("Models and Licenses", self._show_models)
        self.update_action = m.addAction("Check for Updates\u2026", lambda: self.check_for_updates(manual=True))
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
        self.drop_zone.prompterClicked.connect(self.open_teleprompter)
        self.drop_zone.recentClicked.connect(lambda p: self._open_recent(Path(p)))
        self.drop_zone.set_recent([p for p in self.config.extra.get("recent_files", []) if isinstance(p, str)])
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
        self.editor_scroll = editor_scroll
        editor_scroll.installEventFilter(self)
        self.stack.addWidget(editor_scroll)
        self.stack.currentChanged.connect(lambda _: QTimer.singleShot(0, self._fit_transport))
        lay.addWidget(self.stack, 1)
        self.sidebar = self._build_sidebar()
        self._enhance_layout = lay
        lay.addWidget(self.sidebar)
        return page

    def _build_editor(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 18, 20, 18)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        # the file name, with its details in one quiet line right under it
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        self.file_name = ElidedLabel("")
        self.file_name.setProperty("role", "title")
        name_col.addWidget(self.file_name)
        self.meta_chips = QHBoxLayout()
        self.meta_chips.setSpacing(0)
        self.meta_chips.setContentsMargins(1, 0, 0, 0)
        name_col.addLayout(self.meta_chips)
        top.addLayout(name_col, 1)
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
        # one selection shared by both tracks: drag on either to loop or to learn noise
        self.wave_orig.selectionChanged.connect(self._selection_changed)
        self.wave_enh.selectionChanged.connect(self._selection_changed)
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
            reserve_bold_width(self.record_toggle)  # bold and red while the recorder is open
            head.addWidget(self.record_toggle)
            prompter = QPushButton("\u25a4  Teleprompter")
            prompter.setCursor(Qt.PointingHandCursor)
            prompter.setToolTip(f"Read a script while it records, one take per section ({shortcut_text('Ctrl+T')})")
            prompter.clicked.connect(self.open_teleprompter)
            head.addWidget(prompter)
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
            self.record_panel.countdownChanged.connect(self._record_countdown)
            self.record_panel.restore_options(self.config.extra.get("record", {}))
            self.record_panel.failed.connect(self._error)
            v.addWidget(self.record_panel)
        wave = WaveformView(source, placeholder)
        v.addWidget(wave, 1)
        return wave, c, vol, play

    def _build_transport(self) -> QWidget:
        c = card()
        # two groups that sit side by side, or stack when the window is narrow (see _fit_transport)
        self.transport_box = QBoxLayout(QBoxLayout.LeftToRight, c)
        self.transport_box.setContentsMargins(14, 10, 14, 10)
        self.transport_box.setSpacing(10)
        self.transport_left, self.transport_right = QWidget(), QWidget()
        lay = QHBoxLayout(self.transport_left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        right = QHBoxLayout(self.transport_right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        self.transport_box.addWidget(self.transport_left, 0, Qt.AlignLeft)
        self.transport_box.addStretch(1)
        self.transport_box.addWidget(self.transport_right, 0, Qt.AlignLeft)
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
        self.play_btn.clicked.connect(self._toggle_main_play)
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
        self.loop_btn = QPushButton("\u27f2 LOOP")
        self.loop_btn.setProperty("role", "toggle")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setCursor(Qt.PointingHandCursor)
        self.loop_btn.setToolTip("Loop playback (L).\nDrag across either waveform to loop just that passage,\n"
                                 "then switch A/B while it repeats. Without a selection the whole recording loops.")
        self.loop_btn.toggled.connect(lambda _: self._update_loop())
        lay.addWidget(self.loop_btn)

        self.match_box = QCheckBox("Equal-loudness comparison")
        self.match_box.setToolTip("Plays the original at the same loudness as the enhanced version, so the\n"
                                  "comparison is about quality rather than volume.")
        self.match_box.setChecked(self.config.match_loudness_ab)
        self.match_box.toggled.connect(self._update_match_gain)
        right.addWidget(self.match_box)
        right.addSpacing(8)

        self.learn_btn = QPushButton("Learn Noise Profile")
        self.learn_btn.setToolTip("Drag across a part of the ORIGINAL waveform that contains only background\n"
                                  "noise (no speech), then click here. VoiceCleaner will remove that noise.")
        self.learn_btn.clicked.connect(self.learn_noise_profile)
        right.addWidget(self.learn_btn)
        # one width for both texts (Learn / Clear), so the row does not change size
        fm = self.learn_btn.fontMetrics()
        self.learn_btn.setMinimumWidth(max(fm.horizontalAdvance(t) for t in ("Learn Noise Profile",
                                                                           "Clear Noise Profile")) + 36)
        self.profile_label = label("", "faint")
        # a status note: it may be cut short, but never makes the editor wider than the window
        self.profile_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.profile_label.setMinimumWidth(0)
        right.addWidget(self.profile_label, 1)
        return c

    def _set_profile_note(self, text: str):
        """The note next to Learn Noise Profile (cut short when space is tight; never moves the row)."""
        self.profile_label.setText(text)
        self.profile_label.setToolTip(text)

    def _fit_transport(self):
        """Stack the transport controls in two rows when one row does not fit the editor,
        instead of letting the sidebar cover them. Decided from the visible width, so the
        choice cannot feed back into the layout's own minimum size."""
        if not hasattr(self, "transport_box"):
            return
        avail = self.editor_scroll.viewport().width() - 44 - 28  # editor and card margins
        # without the note next to Learn Noise Profile: it is cut short rather than moving the row
        note = self.profile_label.sizeHint().width() if self.profile_label.isVisible() else 0
        need = self.transport_left.sizeHint().width() + self.transport_right.sizeHint().width() - note + 30
        direction = QBoxLayout.LeftToRight if need <= avail else QBoxLayout.TopToBottom
        if self.transport_box.direction() != direction:
            self.transport_box.setDirection(direction)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show) and obj is getattr(self, "editor_scroll", None):
            self._fit_transport()
        return super().eventFilter(obj, event)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit_transport)

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
        initial = self._initial_settings()
        if initial is None:  # preferences from 1.0.2 or earlier
            initial = from_preset(self.config.preset if self.config.preset in preset_names() else "Clean Voice")
            initial = initial.copy(target_lufs=self.config.target_lufs, peak_ceiling_dbtp=self.config.peak_ceiling_dbtp)
            if not self.config.auto_settings:
                initial = apply_simple(initial, self.config.noise_reduction, self.config.speech_enhancement,
                                       self.config.room_reduction)
        if isinstance(self.config.extra.get("eq_gains"), list) and "settings" not in self.config.extra:
            initial = initial.copy(eq_gains=[float(g) for g in self.config.extra["eq_gains"]],
                                   eq_tilt=float(self.config.extra.get("eq_tilt", 0.0)))
        pauses = self.config.extra.get("pauses")
        if isinstance(pauses, dict) and "settings" not in self.config.extra:
            try:
                initial = initial.copy(pause_shorten=bool(pauses.get("shorten", False)),
                                       pause_min_s=float(pauses.get("min_s", initial.pause_min_s)),
                                       pause_keep_ms=float(pauses.get("keep_ms", initial.pause_keep_ms)))
            except (TypeError, ValueError):
                pass
        self.settings_panel = SettingsPanel(initial, self.config.auto_settings, self.config.advanced_open,
                                            smart_note_hidden=bool(self.config.extra.get("smart_note_hidden")))
        self.settings_panel.smartNoteHidden.connect(lambda: self.config.extra.update(smart_note_hidden=True))
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
        # one line each (full text in the tooltip): wrapped text here used to take its
        # height from the buttons below and clip their labels
        self.status_label = ElidedLabel("", mode=Qt.ElideRight, tooltip=True)
        self.status_label.setProperty("role", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.speed_label = ElidedLabel("", mode=Qt.ElideRight, tooltip=True)
        self.speed_label.setProperty("role", "faint")
        a.addWidget(self.status_label)
        a.addWidget(self.progress)
        a.addWidget(self.speed_label)
        self.live_box = QCheckBox("Live preview of changes")
        self.live_box.setToolTip(
            "After the first Enhance, changing a setting re-processes about 10 seconds around the\n"
            "playhead (or your selection) within a second or two, so you hear the change right away.\n"
            "Enhance again to apply it to the whole recording. Fast with an NVIDIA GPU or on Apple\n"
            "Silicon; slower on other CPUs and with Voice Re-synthesis.")
        live = self.config.extra.get("live_preview")
        self.live_box.setChecked(bool(live) if live is not None else False)  # default set once the device is known
        self.live_box.toggled.connect(self._live_toggled)
        a.addWidget(self.live_box)
        row = QHBoxLayout()
        self.enhance_btn = QPushButton("Enhance")
        self.enhance_btn.setProperty("role", "primary")
        self.enhance_btn.setCursor(Qt.PointingHandCursor)
        self.enhance_btn.setToolTip(f"Process the recording with the current settings ({shortcut_text('Ctrl+Enter')})")
        self.enhance_btn.clicked.connect(self.enhance)
        row.addWidget(self.enhance_btn, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_task)
        self.cancel_btn.setVisible(False)
        row.addWidget(self.cancel_btn)
        a.addLayout(row)
        self.export_btn = QPushButton("Export Enhanced WAV…")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.clicked.connect(lambda: self.export())
        a.addWidget(self.export_btn)
        self.update_take_btn = QPushButton("\u21bb Update Take in Compile")
        self.update_take_btn.setCursor(Qt.PointingHandCursor)
        self.update_take_btn.setToolTip("Replace this take's enhanced version in Compile with this result "
                                        "(the version it replaces is kept; trims stay in place)")
        self.update_take_btn.clicked.connect(self.update_take_in_compile)
        self.update_take_btn.setVisible(False)
        a.addWidget(self.update_take_btn)
        for b in (self.enhance_btn, self.cancel_btn, self.export_btn, self.update_take_btn):
            b.setSizePolicy(b.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)
            b.setMinimumHeight(b.sizeHint().height())
        actions.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.sidebar_actions = actions  # Enhance/Export: hidden when the sidebar is in the Batch tab
        v.addWidget(actions)
        self.settings_panel.settingsChanged.connect(self._settings_changed)
        return side

    def _install_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)

        sc(Qt.Key_Space, self._toggle_play_current)
        sc(Qt.Key_A, lambda: self._ab_shortcut("original"))
        sc(Qt.Key_B, lambda: self._ab_shortcut("enhanced"))
        sc(Qt.Key_P, lambda: self.tabs.currentWidget() is self.batch_page and self.batch_panel.toggle_preview())
        sc(Qt.Key_Home, lambda: self._seek(0.0))
        sc("Ctrl+Return", self.enhance)
        sc(Qt.Key_Escape, self._escape)
        sc(Qt.Key_R, self._record_shortcut)
        sc(QKeySequence.Undo, self._undo_shortcut)
        sc(QKeySequence.Redo, self._redo_shortcut)
        # A standard-key shortcut uses only the platform's first binding (Ctrl+Y on Windows), so
        # add Ctrl+Shift+Z too. On macOS Shift+Cmd+Z already IS that first binding; binding it
        # twice makes the shortcut ambiguous and neither one fires.
        if QKeySequence(QKeySequence.Redo) != QKeySequence("Ctrl+Shift+Z"):
            sc("Ctrl+Shift+Z", self._redo_shortcut)
        sc(Qt.Key_L, lambda: self.audio is not None and self.loop_btn.toggle())

    # ============================================================================ state
    def _set_empty_state(self):
        self._refresh_session_recordings()
        self.stack.setCurrentIndex(0)
        self.enhance_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.export_action.setEnabled(False)
        self.progress.setVisible(False)
        self.status_label.setText("Open a recording to begin.")
        self.speed_label.setText("")

    def _begin_job(self, key: str, label_text: str):
        """A task of the Enhance tab starts: report it to the activity tracker."""
        self._job_key, self._job_failed = key, False
        self.activity.start(key, label_text, self.tabs.widget(0))

    def _busy(self, busy: bool, message: str = ""):
        if not busy and self._job_key:
            self.activity.finish(self._job_key, ok=not self._job_failed)
            self._job_key = None
            self.speed_label.setText("")  # drop the elapsed-time line; callers may set a result line
        self.enhance_btn.setEnabled(not busy and self.audio is not None)
        self.play_btn.setEnabled(not (busy and self._job_key == "enhance"))
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        self.drop_zone.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
        has_result = self.result is not None
        self.export_btn.setEnabled(not busy and has_result)
        self.update_take_btn.setEnabled(not busy and has_result)
        self.export_action.setEnabled(not busy and has_result)
        self.learn_btn.setEnabled(not busy and self.audio is not None)

    def _on_progress(self, frac: float, message: str):
        self.progress.setValue(int(frac * 1000))
        if message:
            self.status_label.setText(message)
        if self._job_key:
            self.activity.update(self._job_key, frac, message)
            self.speed_label.setText(eta_text(self.activity.job(self._job_key)))
        if self._job_key == "enhance":
            self.wave_enh.set_busy(message or "Enhancing\u2026", frac)
        elif self._job_key == "analyze" and self.wave_orig.busy_text:
            self.wave_orig.set_busy(message or "Analyzing\u2026", frac)

    # ============================================================================ activity
    SHOW_AFTER_S = 0.5

    def _activity_changed(self):
        # jobs younger than SHOW_AFTER_S are not shown yet: quick ones (e.g. an automatic
        # compile preview) would only make the chip flicker
        young = [j for j in self.activity.jobs.values() if j.elapsed < self.SHOW_AFTER_S]
        if young:
            QTimer.singleShot(int((self.SHOW_AFTER_S - min(j.elapsed for j in young)) * 1000) + 20,
                              self._activity_changed)
        shown = {k: j for k, j in self.activity.jobs.items() if j.elapsed >= self.SHOW_AFTER_S}
        job = max(shown.values(), key=lambda j: j.started, default=None)
        tabs = {}
        for j in sorted(shown.values(), key=lambda j: j.started):
            if j.tab is not None:
                tabs[j.tab] = j
        for page, title in self._tab_titles.items():
            j = tabs.get(page)
            badge = "" if j is None else (j.badge or ("" if j.fraction is None else f"{j.fraction * 100:.0f}%"))
            text = f"{title} \u00b7 {badge}" if badge else (f"{title} \u2022" if j is not None else title)
            i = self.tabs.indexOf(page)
            if i >= 0 and self.tabs.tabText(i) != text:
                self.tabs.setTabText(i, text)
        self.activity_chip.setVisible(job is not None)
        if job is None:
            self._activity_timer.stop()
            return
        if not self._activity_timer.isActive():
            self._activity_timer.start()
        pct = "" if job.fraction is None else f" \u00b7 {job.fraction * 100:.0f}%"
        more = len(shown) - 1
        self.activity_chip.setText(f"\u25cf {job.label.upper()}{pct}" + (f"  +{more}" if more > 0 else ""))
        tip = [f"{j.label}: {j.message or 'working'} ({eta_text(j)})" for j in shown.values()]
        self.activity_chip.setToolTip("\n".join(tip) + ("\nClick to go to it." if job.tab is not None else ""))

    def _activity_clicked(self):
        job = max(self.activity.jobs.values(), key=lambda j: j.started, default=None)
        if job is not None and job.tab is not None:
            self.tabs.setCurrentWidget(job.tab)

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

            ctx.progress(0.5, "Loading AI model…")
            mm = ModelManager(pref)
            if mm.device.kind == "cuda":
                ctx.progress(0.6, f"Loading AI model on the GPU ({mm.device.name.removeprefix('NVIDIA ')})…")
            mm.preload()
            return mm

        def done(mm):
            self.model_manager = mm
            self._mm_ready.set()
            self._update_engine_chips()
            if self.config.extra.get("live_preview") is None:  # first run: on where it is fast
                self.live_box.blockSignals(True)
                # fast with an NVIDIA GPU, and on Apple Silicon (~0.4 s per preview on an M-series CPU)
                self.live_box.setChecked(mm.device.kind == "cuda" or mm.resynth_device.kind == "mps")
                self.live_box.blockSignals(False)
            self.engineStatus.emit("Ready")
            self.engineReady.emit()

        def failed(title, message, _cancelled):
            self._mm_ready.set()
            self.model_chip.setText("AI model unavailable")
            self.device_chip.setText("CPU")
            self.settings_panel.set_ai_available(False)
            log.warning("Engine init failed: %s", message)
            self.engineReady.emit()

        self.engineStatus.emit("Loading AI engine (PyTorch)…")
        self._engine_task = run_task(work, self, on_success=done, on_error=failed,
                                     on_progress=lambda f, m: m and self.engineStatus.emit(m))

    @property
    def engine_ready(self) -> bool:
        return self._mm_ready.is_set()

    def _update_engine_chips(self):
        mm = self.model_manager
        if mm is None:
            return
        st = mm.status()
        dev_name = st.device.name.removeprefix("NVIDIA ").removeprefix("GeForce ")
        self.device_chip.setText(f"{'GPU' if st.device.kind == 'cuda' else 'CPU'} · {dev_name}")
        self.device_chip.setToolTip(f"Processing device: {st.device.label}\nChange it in the menu (⋯).")
        if mm.resynth_device.kind == "mps":  # Apple Silicon: noise reduction on the CPU, re-synthesis on the GPU
            self.device_chip.setText(f"CPU + GPU · {dev_name}")
            self.device_chip.setToolTip(
                f"Noise reduction: {st.device.label}, the fastest for this model\n"
                f"Voice re-synthesis: {mm.resynth_device.label}\nChange it in the menu (⋯).")
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
        self.settings_panel.set_resynth_status(mm.resynth_available, mm.resynth_device.kind)
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
        path, _ = QFileDialog.getOpenFileName(self, "Open a recording", start, FILE_FILTER)
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path):
        if self._task_running():
            self.status_label.setText("Please wait for the current task to finish.")
            return
        self.player.stop()
        self.config.last_open_dir = str(path.parent)
        self._begin_job("analyze", "Analyzing")
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
        if self._compile_link is not None and Path(audio.info.path) != self._compile_link[0]:
            self._compile_link = None  # another recording: no longer a Compile take
        self.update_take_btn.setVisible(self._compile_link is not None)
        self.audio, self.analysis = audio, analysis
        self.result = None
        self.learned_profile = None
        self.selection = None
        self._cancel_preview()
        self._enh_overview = None
        self.wave_enh.preview_region = None
        self.player.set_overlay("enhanced", None)
        self.wave_enh.set_selection(None)
        self._set_profile_note("")
        info = audio.info
        self.file_name.setText(info.path.name)
        self.file_name.setToolTip(str(info.path))
        while self.meta_chips.count():
            item = self.meta_chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # plain information, not buttons: one quiet line
        facts = (format_duration(info.duration), f"{info.sample_rate / 1000:g} kHz", info.channel_label,
                 info.subtype_label, format_size(info.file_size))
        meta = label(" · ".join(facts), "meta")
        meta.setToolTip("Length · sample rate · channels · format · file size")
        self.meta_chips.addWidget(meta)
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
        self._update_loop()
        self.player.seek(0)

        self.settings_panel.set_analysis(analysis)
        self.metrics.set_original(self._original_rows())
        self.metrics.set_enhanced(None)
        self.metrics.set_score(None, None)
        self.metrics.set_notes(self.settings_panel.auto_notes)
        self.stack.setCurrentIndex(1)
        self.record_panel.set_original(True, audio.duration)
        self._remember_recent(info.path)
        self._busy(False, "Ready. Adjust settings if you like, then click Enhance.")
        self.speed_label.setText(f"{format_duration(info.duration)} of audio • analyzed automatically")
        log.info("Loaded %s", info.path)
        self._learn_leading_noise()

    def _learn_leading_noise(self):
        """A recording that starts with background noise before the first word (a third of a
        second of steady noise is enough, a second of any): learn the noise profile from it,
        as if it had been selected by hand."""
        a = self.analysis
        pending, self._pending_room_tone = getattr(self, "_pending_room_tone", None), None
        if pending is not None and Path(self.audio.info.path) == pending[0]:
            region = (0.05, max(0.35, pending[1] - 0.05))  # the room tone recorded before speaking
            if self._use_noise_profile(*region):
                self._set_profile_note(f"Noise learned from the room tone ({region[1] - region[0]:.1f} s)")
                self.status_label.setText("Ready. The room tone recorded first is the noise profile "
                                          "(Clear Noise Profile to undo). Click Enhance.")
                log.info("Noise profile from the recorded room tone %.2f-%.2f s", *region)
                return
        profile = noise_profile_mod.learn_leading_noise(self.audio.samples, self.audio.sample_rate, a.speech_mask,
                                                        a.frame_level_db, FRAME_HOP_S)
        if profile is None:
            return
        region = profile.regions[0]
        if self._use_noise_profile(*region, profile=profile):
            self._set_profile_note(f"Noise learned from the start ({region[1] - region[0]:.1f} s)")
            self.status_label.setText("Ready. The noise before the first word was learned automatically "
                                      "(Clear Noise Profile to undo). Click Enhance.")
            log.info("Noise profile learned automatically from %.2f-%.2f s", *region)

    def _use_noise_profile(self, a: float, b: float, profile=None) -> bool:
        """Learn the noise from ``a``..``b`` seconds (or use ``profile``, already measured
        there) and use it from now on."""
        if profile is None:
            mono = self.audio.mono() if self.audio.channels > 1 else self.audio.samples[:, 0]
            profile = noise_profile_mod.estimate(mono, self.audio.sample_rate, [(a, b)], source="learned",
                                                 margin_s=0.0)
        if profile is None:
            return False
        self.learned_profile = profile
        self.wave_orig.highlight_regions = [(a, b)]
        self.wave_orig.update()
        self.learn_btn.setText("Clear Noise Profile")
        return True

    def _original_rows(self):
        a = self.analysis
        f = lambda v, u: "—" if not np.isfinite(v) else f"{v:.1f} {u}"  # noqa: E731
        return [("Loudness", f(a.lufs, "LUFS")), ("True peak", f(a.true_peak_dbtp, "dBTP")),
                ("Noise floor", f(a.noise_floor_dbfs, "dBFS")), ("Speech-to-noise", f(min(a.snr_db, 80), "dB")),
                ("Dynamic range", f(a.loudness_range_lu, "LU"))]

    def _task_failed(self, title: str, message: str, cancelled: bool):
        self._job_failed = True
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
        self._cancel_preview(wait=True)
        if self.player.state == "playing":
            self.player.pause()  # nothing plays while the recording is being enhanced
        settings = self.settings_panel.current()
        audio, analysis, profile = self.audio, self.analysis, self.learned_profile
        self._begin_job("enhance", "Enhancing")
        self._busy(True, "Preparing...")
        self.wave_enh.set_busy("Preparing\u2026", 0.0)

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
        self._enh_overview = overview
        self._clear_preview()
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
        self._last_notes = notes
        self.metrics.set_notes(notes)
        self._update_pause_note()
        self._busy(False, "Done. Press Space to listen, and switch A/B to compare.")
        self.speed_label.setText(
            f"Processed {format_duration(result.duration)} in {result.processing_seconds:.1f} s • "
            f"{result.realtime_factor:.1f}x realtime • {result.device_label}")
        if result.attempts > 1:
            self.speed_label.setText(self.speed_label.text() + f" • Quality check adjusted settings ({result.attempts} passes).")
        if self._export_after_enhance is not None:
            then, self._export_after_enhance = self._export_after_enhance, None
            QTimer.singleShot(0, then)

    def cancel_task(self):
        if self._task_running():
            self.task.cancel()
            self.status_label.setText("Cancelling...")

    PAUSE_FIELDS = ("pause_shorten", "pause_min_s", "pause_keep_ms")

    def _settings_changed(self, s: ProcessingSettings):
        if self.batch_panel.preview_on and self.batch_panel.preview_path is not None \
                and self.tabs.currentWidget() is self.batch_page:
            self._bp_timer.start()  # the batch sample follows the settings
        if self.result is not None and self._result_settings is not None:
            # pause shortening happens on export, so it never requires enhancing again
            now = {k: v for k, v in s.to_dict().items() if k not in self.PAUSE_FIELDS}
            then = {k: v for k, v in self._result_settings.items() if k not in self.PAUSE_FIELDS}
            self.enhance_btn.setText("Enhance Again" if now != then else "Enhance")
            self._update_pause_note()
            if now == then:
                self._clear_preview()  # back to what the enhanced track already has
            else:
                self._schedule_preview()

    def _pause_note(self) -> str:
        if self.result is None:
            return ""
        ps = self.settings_panel.current().pause_settings()
        if not ps.active:
            return ""
        from ..audio.pauses import find_cuts, removed_samples

        cuts = find_cuts(self.result.samples, self.result.sample_rate, ps.min_pause_s, ps.keep_ms)
        if not cuts:
            return f"No pauses longer than {ps.min_pause_s:.1f} s were found."
        how = "removed" if ps.keep_ms < 1 else f"shortened to {ps.keep_ms:.0f} ms"
        return (f"On export, {len(cuts)} long pause{'s' if len(cuts) != 1 else ''} will be {how} "
                f"(\u2212{removed_samples(cuts) / self.result.sample_rate:.1f} s).")

    def _update_pause_note(self):
        if self.result is None:
            return
        notes = [n for n in self._last_notes if not n.startswith(("On export, ", "No pauses longer"))]
        note = self._pause_note()
        self.metrics.set_notes(notes + ([note] if note else []))

    # ============================================================================ playback
    def _activate(self, source: str):
        if source == "enhanced" and not self.player.has_source("enhanced"):
            source = "original"
        self.player.set_active(source)
        self._on_source_changed(self.player.active_source)

    def _play_source(self, source: str):
        self._activate(source)
        if self._enhancing:
            return  # playback waits until enhancing is done
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

    # ============================================================================ teleprompter
    def open_teleprompter(self):
        if self.prompter is not None:
            self.prompter.showNormal() if self.prompter.isMinimized() else None
            self.prompter.raise_()
            self.prompter.activateWindow()
            return
        from ..teleprompter.window import TeleprompterWindow

        prefs = self.config.extra.setdefault("teleprompter", {})
        rp = self.record_panel
        mic = rp.mic_row.source if hasattr(rp, "mic_row") else None
        gain = rp.mic_row.gain if hasattr(rp, "mic_row") else 1.0
        self.player.pause() if self.player.state == "playing" else None
        self.prompter = TeleprompterWindow(self.session, prefs, mic, gain)
        self.prompter.setWindowIcon(self.windowIcon())
        self.prompter.takeSaved.connect(self._prompter_take)
        self.prompter.scriptChanged.connect(self.compile_panel.takes_changed)
        self.prompter.recordingChanged.connect(self._prompter_recording)
        self.prompter.closed.connect(self._prompter_closed)  # before Qt deletes it
        self.prompter.setAttribute(Qt.WA_DeleteOnClose)
        self.prompter.show()
        self.prompter.raise_()
        self.prompter.activateWindow()
        self._update_prompter_button()

    def _prompter_recording(self, on: bool):
        if on:
            self.activity.start("prompter", "Teleprompter recording", None, fraction=None)
        else:
            self.activity.finish("prompter")
        self._update_prompter_button(recording=on)

    def _prompter_closed(self):
        self.prompter = None
        self.activity.finish("prompter")
        self._update_prompter_button()

    def _update_prompter_button(self, recording: bool = False):
        """Closed: opens it. Open: brings it to the front (accent). Recording: red REC."""
        if not hasattr(self, "prompter_btn"):
            return
        b = self.prompter_btn
        is_open = getattr(self, "prompter", None) is not None
        state = "rec" if is_open and recording else "open" if is_open else ""
        b.setText("\u25cf  REC \u00b7 TELEPROMPTER" if state == "rec" else "\u25a4  TELEPROMPTER")
        tips = {"": f"Read a script while it records, one take per section ({shortcut_text('Ctrl+T')})",
                "open": "The teleprompter is open: click to bring it to the front",
                "rec": "The teleprompter is recording: click to bring it to the front"}
        b.setToolTip(tips[state])
        if b.property("state") != state:
            b.setProperty("state", state)
            b.style().unpolish(b)
            b.style().polish(b)

    def _prompter_take(self, info, path: Path):
        """A take was saved: enhance it in the background; Compile and Playback show it."""
        from ..teleprompter.takes import TakeBook

        book = TakeBook.shared(self.session.path)
        out = self.session.export_path(path)
        self.take_enhancer.enqueue(path, out, self.settings_panel.current(), self.settings_panel.smart.isChecked(),
                                   book)
        self.compile_panel.takes_changed()
        self.playback.refresh()
        self._refresh_session_recordings()

    def _take_enhanced(self, take: str, path: str):
        self.compile_panel.on_take_enhanced(take, path)
        self.compile_panel.takes_changed()
        self.playback.refresh()

    def _reenhance_takes(self, paths: list, preset: str):
        """Compile's Re-enhance: each take from its original recording, in the background.
        An enhanced file is replaced (the old version is kept); a recording gets one."""
        from ..audio.settings import from_preset
        from ..teleprompter.takes import TakeBook

        current = self.settings_panel.current()
        settings = from_preset(preset, base=current) if preset else current
        smart = self.settings_panel.smart.isChecked()
        book = TakeBook.shared(self.session.path)
        queued = 0
        for p in paths:
            row = Path(p)
            raw = sessions.original_recording(row)
            if raw is None:
                continue
            target = self._enhanced_target(row, raw)
            mine = raw.parent == self.session.recordings_dir
            self.take_enhancer.enqueue(raw, target, settings, smart, book if mine else None, backup=True)
            self.compile_panel.mark_reenhancing(str(row), str(target), raw.name)
            queued += 1
        what = f"preset \u201c{preset}\u201d" if preset else "the current settings"
        self.status_label.setText(f"Re-enhancing {queued} take{'s' if queued != 1 else ''} with {what}\u2026")

    def _enhanced_target(self, row: Path, raw: Path) -> Path:
        """The enhanced file a Compile row stands for: the row itself when it is enhanced,
        else the recording's enhanced version (existing, or a new name)."""
        from ..teleprompter.takes import TakeBook

        if row.parent.name == sessions.EXPORTS:
            return row
        take = next((t for t in TakeBook.shared(self.session.path).takes if t.file == raw.name and t.enhanced), None)
        return (self.session.exports_dir / take.enhanced) if take else self.session.export_path(raw)

    def open_take_in_enhance(self, row_path: str):
        """Compile: fine-tune a take from its original recording in the Enhance tab."""
        row = Path(row_path)
        raw = sessions.original_recording(row)
        if raw is None:
            return

        def go():
            self._compile_link = (raw, self._enhanced_target(row, raw), row)
            self.tabs.setCurrentIndex(0)
            self.open_file(raw)
            self.status_label.setText("Opened from Compile. Enhance, then Update Take in Compile.")

        if self.audio is not None and Path(self.audio.info.path) == raw:
            self._compile_link = (raw, self._enhanced_target(row, raw), row)
            self.update_take_btn.setVisible(True)
            self.tabs.setCurrentIndex(0)
            return
        if self._confirm_leave_recording(go, "open this take"):
            go()

    def update_take_in_compile(self):
        """Write the result over the take's enhanced file (keeping the one before) and show
        it in Compile."""
        if self.result is None or self._compile_link is None or self._task_running():
            return
        if self.enhance_btn.text() == "Enhance Again" and not self._confirm_export_stale(self.update_take_in_compile):
            return
        raw, target, row = self._compile_link
        result = self.result
        self._begin_job("export", "Updating take")
        self._busy(True, "Updating take...")

        def work(ctx):
            ctx.progress(0.1, "Updating take...")
            sessions.replace_keeping_previous(target, lambda p: export_wav(
                result.samples, result.sample_rate, p, ExportOptions("24", 0), source_path=raw))
            return target

        def done(_path):
            from ..teleprompter.takes import TakeBook

            self._exported_result = result
            if raw.parent == self.session.recordings_dir:
                TakeBook.shared(self.session.path).set_enhanced(raw.name, target.name)
            self._busy(False, f"Updated {target.name} in Compile.")
            self.compile_panel.mark_reenhancing(str(row), str(target), raw.name)
            self.compile_panel.on_take_enhanced(raw.name, str(target))
            self.compile_panel.takes_changed()
            self.playback.refresh()
            self._compile_link = (raw, target, target)  # the row now uses the enhanced file
            self.tabs.setCurrentWidget(self.compile_panel)

        self.task = run_task(work, self, on_success=done, on_error=self._task_failed,
                             on_progress=self._on_progress)

    def _takes_pending(self, n: int):
        if n > 0:
            if self.activity.job("takes") is None:
                self.activity.start("takes", "Enhancing takes", self.compile_panel, fraction=None)
            self.activity.update("takes", None, f"{n} take{'s' if n != 1 else ''} to go", badge=f"{n} to enhance")
        else:
            self.activity.finish("takes")

    def _resume_take_enhancing(self):
        """Takes recorded before the app was closed, not enhanced yet: enhance them now."""
        from ..teleprompter.takes import TakeBook

        book = TakeBook.shared(self.session.path)
        todo = [t for t in book.takes if not t.enhanced and (self.session.recordings_dir / t.file).exists()]
        for t in todo:
            path = self.session.recordings_dir / t.file
            self.take_enhancer.enqueue(path, self.session.export_path(path), self.settings_panel.current(),
                                       self.settings_panel.smart.isChecked(), book)
        if todo:
            log.info("Enhancing %d teleprompter take(s) left from last time", len(todo))

    # ============================================================================ undo
    def _undo_shortcut(self):
        if self.tabs.currentWidget() in (self.tabs.widget(0), self.batch_page):  # where the settings are
            self.settings_panel.undo()

    def _redo_shortcut(self):
        if self.tabs.currentWidget() in (self.tabs.widget(0), self.batch_page):
            self.settings_panel.redo()

    # ============================================================================ live preview
    def _live_toggled(self, on: bool):
        self.config.extra["live_preview"] = on
        if on:
            self._schedule_preview()
        else:
            self._cancel_preview()
            self._clear_preview()

    def _preview_possible(self) -> bool:
        return (self.live_box.isChecked() and self.result is not None and self.audio is not None
                and not self.audio.is_disk_backed and self._mm_ready.is_set() and not self._task_running())

    def _schedule_preview(self):
        if not self._preview_possible():
            return
        if not hasattr(self, "_preview_timer"):
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._start_preview)
        self._preview_timer.start(PREVIEW_SETTLE_MS)

    def _preview_window(self) -> tuple[float, float]:
        dur = self.audio.duration
        if self.selection:
            a, b = self.selection
            return a, min(b, a + PREVIEW_MAX_SELECTION_S)
        pos = self.player.position
        a = max(0.0, min(pos - 0.5, dur - PREVIEW_SECONDS))
        return a, min(dur, a + PREVIEW_SECONDS)

    def _start_preview(self):
        if not self._preview_possible() or self.enhance_btn.text() != "Enhance Again":
            return
        self._cancel_preview()
        self._preview_gen += 1
        gen = self._preview_gen
        settings = self.settings_panel.current()
        audio, analysis, profile = self.audio, self.analysis, self.learned_profile
        t0, t1 = self._preview_window()
        self.activity.start("preview", "Live preview", self.tabs.widget(0))

        def work(ctx):
            ctx.progress(0.0, "Previewing the new settings...")
            return EnhancementPipeline(self._get_model_manager()).preview(
                audio, analysis, settings, t0, t1, ctx, learned_profile=profile)

        def done(pr):
            self.activity.finish("preview")
            if gen == self._preview_gen and self.result is not None and audio is self.audio:
                self._show_preview(pr)

        def failed(_title, message, cancelled):
            self.activity.finish("preview", ok=False)
            if not cancelled:
                log.warning("Live preview failed: %s", message)

        self._preview_task = run_task(work, self, on_success=done, on_error=failed,
                                      on_progress=lambda f, m: self.activity.update("preview", f, m))

    def _show_preview(self, pr):
        base = self.result.samples
        a, x = pr.start_frame, pr.samples
        n = min(x.shape[0], base.shape[0] - a)
        x = x[:n].copy()
        # blend into the enhanced track at both ends, so switching in and out does not click
        k = max(1, min(n // 4, int(0.03 * pr.sample_rate)))
        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)[:, None]
        edge = np.asarray(base[a : a + k], dtype=np.float32)
        x[:k] = edge * (1 - ramp) + x[:k] * ramp
        edge = np.asarray(base[a + n - k : a + n], dtype=np.float32)
        x[n - k :] = x[n - k :] * (1 - ramp) + edge * ramp
        self.player.set_overlay("enhanced", a, x)
        if self._enh_overview is not None:
            self.wave_enh.set_overview(self._enh_overview.patched(a, x, base), keep_view=True)
        self.wave_enh.preview_region = pr.window
        self.wave_enh.update()
        t0, t1 = pr.window
        self.speed_label.setText(f"Live preview {format_duration(t0)}\u2013{format_duration(t1)} "
                                 f"({pr.processing_seconds:.1f} s) \u2022 Enhance Again for the whole recording")

    def _cancel_preview(self, wait: bool = False):
        if hasattr(self, "_preview_timer"):
            self._preview_timer.stop()
        t = self._preview_task
        if t is not None and t.running:
            self._preview_gen += 1  # whatever it returns is outdated
            t.cancel()
            if wait:
                t.wait(5000)

    def _clear_preview(self):
        if self.wave_enh.preview_region is None:
            return
        self.player.set_overlay("enhanced", None)
        self.wave_enh.preview_region = None
        if self._enh_overview is not None:
            self.wave_enh.set_overview(self._enh_overview, keep_view=True)
        self.wave_enh.update()

    def _confirm_export_stale(self, then=None) -> bool:
        """Settings changed since the last Enhance: offer to apply them before exporting."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Export")
        box.setText("<b>Your settings changed since the last Enhance.</b>")
        box.setInformativeText("The exported file uses the last Enhance. Live preview only covers a short part. "
                               "Enhance the whole recording with the new settings first?")
        again = box.addButton("Enhance and Export", QMessageBox.AcceptRole)
        old = box.addButton("Export Previous Result", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(again)
        box.exec()
        if box.clickedButton() is again:
            self._export_after_enhance = then or (lambda: self.export())
            self.enhance()
            return False
        return box.clickedButton() is old

    # ============================================================================ loop
    def _update_loop(self):
        """Loop the selection, or the whole recording without one, while Loop is on."""
        on = self.loop_btn.isChecked() and self.audio is not None
        region = (self.selection or (0.0, self.audio.duration)) if on else None
        self.player.set_loop(region)
        for wv in (self.wave_orig, self.wave_enh):
            wv.loop_region = region
            wv.update()

    # ============================================================================ noise profile
    def _selection_changed(self, sel):
        self.selection = sel
        for wv in (self.wave_orig, self.wave_enh):
            if wv.selection != sel:
                wv.set_selection(sel)
        self._update_loop()
        if sel and not self.learned_profile:
            self._set_profile_note(f"Selected {sel[1] - sel[0]:.1f} s")
        elif not sel and not self.learned_profile:
            self._set_profile_note("")

    def learn_noise_profile(self):
        if self.audio is None:
            return
        if self.learned_profile is not None:  # the button reads "Clear Noise Profile"
            self.learned_profile = None
            self.learn_btn.setText("Learn Noise Profile")
            self._set_profile_note("")
            self.status_label.setText("Noise profile cleared: noise is detected automatically again.")
            self.wave_orig.highlight_regions = []
            self.wave_orig.update()
            return
        if not self.selection:
            QMessageBox.information(
                self, "Learn Noise Profile",
                "Drag across a part of either waveform where nobody is speaking, so only the background "
                "noise is selected. Then click “Learn Noise Profile” again.\n\nA second or two is enough.")
            return
        a, b = self.selection
        if b - a < 0.3:
            QMessageBox.information(self, "Selection too short", "Select at least 0.3 seconds of background noise.")
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
        if not self._use_noise_profile(a, b):
            self._error("Could not learn noise", "The selection is too short to measure the noise.")
            return
        self._selection_changed(None)  # one selection, shared by both waveforms
        self._set_profile_note(f"Noise profile learned ({b - a:.1f} s)")
        self.status_label.setText("Noise profile learned. Click Enhance to apply it.")
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
                self.settings_panel.set_resynth_status(True, self.model_manager.resynth_device.kind)

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

    def _record_countdown(self, seconds: int):
        """Show the countdown big on the Original track."""
        self.wave_orig.set_busy(f"Recording starts in {seconds}\u2026" if seconds > 0 else "")
        if seconds > 0:
            self.status_label.setText(f"Recording starts in {seconds}\u2026")

    def _recording_finished(self, result, mode: str, position: float, latency: float):
        if mode == "new" and self.record_panel.last_room_tone:
            # the quiet second at the start is the room tone: the noise profile, exactly
            self._pending_room_tone = (Path(result.path), self.record_panel.last_room_tone)
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
    def export(self, then=None):
        """Export the enhanced recording; ``then`` runs once it is saved."""
        if self.result is None or self._task_running():
            return
        if self.enhance_btn.text() == "Enhance Again" and not self._confirm_export_stale():
            return
        suggested = self.session.export_path(self.audio.info.path)
        dlg = ExportDialog(self.audio.info.path, self.result.sample_rate, str(self.session.exports_dir),
                           self.config.output_format, self.config.output_bit_depth, self.config.output_sample_rate, self,
                           suggested_name=suggested.stem, reveal_after=self._reveal_after_export)
        if dlg.exec() != ExportDialog.Accepted:
            return
        v = dlg.values()
        self.config.extra["reveal_after_export"] = v["reveal"]
        self.config.last_export_dir = str(v["path"].parent)
        self.config.output_format = v["format"]
        self.config.output_bit_depth = v["bit_depth"]
        self.config.output_sample_rate = v["sample_rate"]
        result, source = self.result, self.audio.info.path
        pauses = self.settings_panel.current().pause_settings()
        self._begin_job("export", "Exporting")
        self._busy(True, "Exporting...")

        def work(ctx):
            prog = lambda f: ctx.progress(f, "Exporting...")  # noqa: E731
            samples = result.samples
            if pauses.active:
                from ..audio.pauses import shorten_pauses

                ctx.progress(0.0, "Shortening long pauses...")
                samples, removed = shorten_pauses(result.samples, result.sample_rate, pauses)
                log.info("Shortened long pauses on export: -%.1f s", removed)
            if v["format"] == "mp3":
                return export_mp3(samples, result.sample_rate, v["path"], v["mp3_quality"], v["sample_rate"],
                                  source_path=source, progress=prog)
            return export_wav(samples, result.sample_rate, v["path"],
                              ExportOptions(v["bit_depth"], v["sample_rate"]), source_path=source, progress=prog)

        def saved(path):
            self._exported_result = result
            self._exported(path)
            if callable(then):
                then()

        self.task = run_task(work, self, on_success=saved, on_error=self._task_failed,
                             on_progress=self._on_progress)

    @property
    def unexported_result(self) -> bool:
        """An enhanced version exists that has not been exported."""
        return self.result is not None and self.result is not self._exported_result

    @property
    def _reveal_after_export(self) -> bool:
        return bool(self.config.extra.get("reveal_after_export", False))

    def _exported(self, path: Path):
        self._busy(False, f"Saved {path.name}")
        self.playback.refresh()
        log.info("Exported %s", path)
        if self._reveal_after_export:  # the user chose to go straight to the file
            reveal(path)
            return
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
        runner.fileOutput.connect(bp.file_output)
        runner.finished.connect(self._batch_done)
        n = len(files)
        self.activity.start("batch", "Batch", self.batch_page)
        self.activity.update("batch", 0.0, "Starting", badge=f"0/{n}")
        runner.fileStarted.connect(lambda i: self.activity.update("batch", i / n, f"File {i + 1} of {n}",
                                                                  badge=f"{i + 1}/{n}"))
        runner.fileProgress.connect(lambda i, f, m: self.activity.update("batch", (i + f) / n, m))

    def _cancel_batch(self):
        if self._batch:
            self._batch[1].cancel()

    def _batch_done(self):
        thread, _ = self._batch
        thread.wait(2000)
        self._batch = None
        self.activity.finish("batch")
        self.batch_panel.set_running(False)
        self.batch_panel.summary.setText(self.batch_panel.summary.text() + " • finished")
        self.playback.refresh()

    # ============================================================================ drag & drop
    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(AUDIO_EXTENSIONS) or Path(u.toLocalFile()).is_dir()
               for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop_zone.set_hover(True)

    def dragLeaveEvent(self, _e):
        self.drop_zone.set_hover(False)

    def dropEvent(self, e):
        self.drop_zone.set_hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        dirs = [p for p in paths if p.is_dir()]
        for d in dirs:
            paths.extend(sorted(q for q in d.iterdir() if q.suffix.lower() in AUDIO_EXTENSIONS))
        wavs = [p for p in paths if p.suffix.lower() in AUDIO_EXTENSIONS]
        if not wavs:
            self._error("Not a recording", "VoiceCleaner opens recordings (WAV, MP3, M4A, FLAC, Opus, WMA ...) "
                        "and the sound of video files (MP4, MOV, MKV, WebM ...).")
            return
        if len(wavs) == 1 and self.tabs.currentWidget() is not self.batch_page:
            self.tabs.setCurrentIndex(0)
            self.open_file(wavs[0])
        else:
            self.batch_panel.add_files(wavs)
            self.tabs.setCurrentWidget(self.batch_page)

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

    # ============================================================================= updates
    def _auto_updates_enabled(self) -> bool:
        return bool(self.config.extra.get("update_auto", True)) and not os.environ.get("VOICECLEANER_NO_UPDATE_CHECK")

    def _auto_check_updates(self):
        """Every start: one small request. Whether a found update is shown is decided by
        Skip/Later in _update_found, never by when the last check happened."""
        if not self._auto_updates_enabled():
            return
        if not self.isVisible():  # still behind the splash: ask again once the window is up
            QTimer.singleShot(2000, self._auto_check_updates)
            return
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = True):
        if self.update_checker.check(manual):
            self.update_action.setEnabled(False)
            self.update_action.setText("Checking for Updates\u2026")

    def _update_check_done(self):
        self.update_action.setEnabled(True)
        self.update_action.setText("Check for Updates\u2026")

    def _update_checked(self):
        self._update_check_done()
        self.config.extra["update_last_check"] = time.time()
        try:
            self.config.save()
        except OSError:
            pass

    def _update_found(self, release, manual: bool):
        self._update_checked()
        if not manual and (self.config.extra.get("update_skip") == release.version
                           or updates.is_snoozed(self.config.extra.get("update_snooze"), release.version)):
            return
        dlg = UpdateDialog(release, bool(self.config.extra.get("update_auto", True)), self)

        def closed(code: int):
            self.config.extra["update_auto"] = dlg.auto_box.isChecked()
            if code == UpdateDialog.SKIP:
                self.config.extra["update_skip"] = release.version
            else:  # Later, Download or closed: remind again tomorrow, not at every start
                self.config.extra["update_snooze"] = updates.snooze(release.version)
            try:
                self.config.save()
            except OSError:
                pass
            if code in (UpdateDialog.INSTALL_NOW, UpdateDialog.INSTALL_ON_QUIT) and dlg.installer is not None:
                now = code == UpdateDialog.INSTALL_NOW
                self._pending_update = (dlg.plan, dlg.installer, release.version, now)
                if now:
                    QTimer.singleShot(0, self.close)  # asks first if work is in progress
                else:
                    self.status_label.setText(f"VoiceCleaner {release.version} will be installed when you quit.")

        dlg.finished.connect(closed)
        dlg.open()  # window-modal but non-blocking: an automatic check never stalls the app

    def _report_update_result(self):
        """After an update was installed (or not), say so once."""
        if not self.isVisible():
            QTimer.singleShot(1000, self._report_update_result)
            return
        outcome = update_install.take_last_result()
        if outcome is None:
            return
        log.info("Last update install: code %s for %s", outcome.code, outcome.version)
        if outcome.ok and outcome.version == APP_VERSION:
            update_install.cleanup()  # the installer is no longer needed
            self.status_label.setText(outcome.message())
            QMessageBox.information(self, "Update installed", outcome.message())
        else:
            QMessageBox.warning(self, "Update not installed", outcome.message())

    def _start_pending_update(self):
        """Run the downloaded installer once this window has closed."""
        plan, installer, version, relaunch = self._pending_update
        try:
            update_install.launch_installer(plan, installer, version, relaunch)
        except Exception as exc:  # noqa: BLE001 - quitting must still work
            log.exception("Could not start the update installer")
            QMessageBox.warning(self, "Update not installed",
                                f"The installer could not be started ({exc}). Install it from {installer.parent}.")

    def _update_up_to_date(self, release, manual: bool):
        self._update_checked()
        if manual:
            QMessageBox.information(self, "Check for Updates",
                                    f"<b>You have the latest version.</b><br>{APP_NAME} {APP_VERSION} is up to date "
                                    f"(latest release: {release.version}).")

    def _update_failed(self, message: str, manual: bool):
        self._update_check_done()
        if manual:
            self._error("Update check failed", message)

    # ============================================================================ sessions
    def _update_title(self):
        self.setWindowTitle(f"{self.session.name} \u2014 {APP_NAME} by {STUDIO_NAME}")

    def _update_session_ui(self):
        if hasattr(self, "session_btn"):
            self.session_btn.setText(f"SESSION \u00b7 {self.session.name}  \u25be")
        self._update_title()

    def _confirm_leave_recording(self, proceed, action: str) -> bool:
        """Before the open recording is closed: if its enhanced version was never exported,
        offer to export it first. True when ``proceed`` may run now; after an export it runs
        once the file is saved."""
        if self._task_running():
            self._error("Busy", f"Wait until processing has finished, then {action}.")
            return False
        if not self.unexported_result:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Enhanced audio not exported")
        box.setText(f"<b>\u201c{self.audio.info.path.name}\u201d has been enhanced but not exported.</b>")
        box.setInformativeText(f"It will be closed when you {action}. Export the enhanced version first?")
        export_btn = box.addButton("Export\u2026", QMessageBox.AcceptRole)
        discard = box.addButton("Don\u2019t Export", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(export_btn)
        box.exec()
        if box.clickedButton() is export_btn:
            self.tabs.setCurrentIndex(0)
            self.export(then=proceed)
            return False
        return box.clickedButton() is discard

    def close_recording(self):
        """Back to the empty Enhance tab (a new or another session was opened)."""
        if self.audio is None:
            return
        self._cancel_preview(wait=True)
        self.player.stop()
        self.player.set_source("enhanced", None)
        self.player.set_source("original", None)
        self.audio = self.analysis = self.result = None
        self._exported_result = None
        self._compile_link = None
        self.update_take_btn.setVisible(False)
        self.learned_profile = None
        self.selection = None
        self._enh_overview = None
        self._set_profile_note("")
        self.warning_label.setVisible(False)
        self.file_name.setText("")
        self.file_name.setToolTip("")
        while self.meta_chips.count():
            item = self.meta_chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.wave_enh.preview_region = None
        for w in (self.wave_orig, self.wave_enh):
            w.set_busy("")
            w.set_selection(None)
            w.highlight_regions = []
            w.set_overview(None)
        self.metrics.set_enhanced(None)
        self.metrics.set_score(None, None)
        self.record_panel.set_original(False, 0.0)
        self.settings_panel.set_analysis(None)
        self._set_empty_state()

    def _switch_session(self, session, keep_recording: bool = False):
        if not keep_recording:
            self.close_recording()  # the open recording belongs to the previous session
        if self.prompter is not None:
            self.prompter.close()  # its takes belong to the previous session
        self.session = session
        self.config.extra["session_path"] = str(session.path)
        self._update_session_ui()
        self.playback.set_session(session, sessions.sessions_root())
        self.compile_panel.set_session(session)
        self._refresh_session_recordings()
        if self.tabs.currentWidget() is self.compile_panel:
            self.compile_panel.ensure_populated()
        self.status_label.setText(f"Session \u201c{session.name}\u201d. New recordings and exports go to its folder.")
        log.info("Session: %s (%s)", session.name, session.path)
        self._resume_take_enhancing()

    def new_session(self):
        if self.record_panel.recording:
            self._error("Recording in progress", "Stop the recording before starting a new session.")
            return
        dlg = SessionNameDialog("New Session", sessions.default_name(), "Create Session", self)
        if dlg.exec() != SessionNameDialog.Accepted:
            return
        name = dlg.name

        def go():
            try:
                self._switch_session(sessions.create(name))
                self.tabs.setCurrentIndex(0)  # a new session starts on the Enhance tab
            except (sessions.SessionError, OSError) as exc:
                self._error("Could not create the session", str(exc))

        if self._confirm_leave_recording(go, "start a new session"):
            go()

    def open_session_dialog(self):
        if self.record_panel.recording:
            self._error("Recording in progress", "Stop the recording before switching sessions.")
            return
        dlg = OpenSessionDialog(self.session.name, self)
        if dlg.exec() != OpenSessionDialog.Accepted or not dlg.chosen:
            return
        chosen = Path(dlg.chosen)
        if chosen == self.session.path:
            return  # already open: keep the recording

        def go():
            try:
                self._switch_session(sessions.open_session(chosen))
            except (sessions.SessionError, OSError) as exc:
                self._error("Could not open the session", str(exc))

        if self._confirm_leave_recording(go, "open another session"):
            go()

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
            self._switch_session(sessions.rename(self.session, dlg.name), keep_recording=True)
        except (sessions.SessionError, OSError) as exc:
            self._error("Could not rename the session", f"{exc}\n\nClose any program using files in the session folder.")

    # ============================================================================ theme
    def set_theme(self, pref: str):
        self.theme_pref = pref
        mode = theme.apply(QApplication.instance(), pref)
        if pref in self.theme_actions:
            self.theme_actions[pref].setChecked(True)
        self._update_theme_button()
        if getattr(self, "prompter", None) is not None:
            self.prompter.apply_theme()  # it follows the app unless set to Dark or Light
        log.info("Theme: %s (%s)", pref, mode)

    def set_fonts(self, style: str):
        """System or brand fonts, right away."""
        self.config.extra["fonts"] = style
        theme.apply(QApplication.instance(), self.theme_pref, fonts=style)
        if style in getattr(self, "font_actions", {}):
            self.font_actions[style].setChecked(True)
        if getattr(self, "prompter", None) is not None:
            self.prompter.apply_theme()
        for w in QApplication.instance().allWidgets():
            w.updateGeometry()
        log.info("Fonts: %s", style)

    def _toggle_theme(self):
        self.set_theme("day" if theme.MODE == "night" else "night")

    def _update_theme_button(self):
        if hasattr(self, "theme_btn"):
            night = theme.MODE == "night"
            self.theme_btn.setAccessibleName("Switch to day mode" if night else "Switch to night mode")
            self.theme_btn.setToolTip("Switch to day mode" if night else "Switch to night mode")
            self.theme_btn.update()

    def _system_scheme_changed(self, *_):
        if self.theme_pref == "auto":
            self.set_theme("auto")

    # ============================================================================ tabs / playback
    def _tab_changed(self, index: int):
        page = self.tabs.widget(index)
        if index == 0 and self.audio is None:
            self._refresh_session_recordings()  # recordings may have been added elsewhere
        if page is self.batch_page and self.sidebar.parent() is not self.batch_page:
            self._batch_layout.addWidget(self.sidebar)
            self.sidebar_actions.setVisible(False)
            self.settings_panel.set_context("batch")
        elif index == 0 and self.sidebar.parent() is self.batch_page:
            self._enhance_layout.addWidget(self.sidebar)
            self.sidebar_actions.setVisible(True)
            self.settings_panel.set_context("enhance")
        if self.tabs.widget(index) is self.playback:
            self.playback.refresh()
        elif self.tabs.widget(index) is self.compile_panel:
            self.compile_panel.ensure_populated()

    def _batch_preview(self, path):
        """Batch tab: a 10-second sample of ``path`` (the part with the most speech) with the
        current settings, exactly as the batch would process it."""
        from ..audio import loudness
        from ..audio.analyzer import analyze
        from ..audio.loader import load_wav
        from ..audio.pipeline import best_preview_window
        from ..audio.settings import smart_adapt

        path = Path(path)
        if self._bp_task is not None and self._bp_task.running:
            self._bp_task.cancel()
        self._bp_gen += 1
        gen = self._bp_gen
        settings = self.settings_panel.current()
        smart = self.settings_panel.smart.isChecked()
        cached = self._bp_cache
        self.batch_panel.preview_busy(path)
        self.activity.start("batch-preview", "Batch preview", self.batch_page)

        def work(ctx):
            mtime = path.stat().st_mtime
            if cached and cached[0] == path and cached[1] == mtime:
                audio, analysis = cached[2], cached[3]
            else:
                ctx.progress(0.05, "Reading the file...")
                audio = load_wav(path)
                ctx.check()
                ctx.progress(0.2, "Analyzing...")
                analysis = analyze(audio.samples, audio.sample_rate)
            ctx.check()
            s = smart_adapt(settings, analysis).settings if smart else settings
            t0, t1 = best_preview_window(analysis)
            # the same lead-in noise profile the batch itself will learn (batch_worker.enhance_file)
            learned = noise_profile_mod.learn_leading_noise(audio.samples, audio.sample_rate, analysis.speech_mask,
                                                            analysis.frame_level_db, FRAME_HOP_S)
            sub = type("Sub", (), {"progress": lambda _s, f, m="": ctx.progress(0.3 + 0.7 * f, m),
                                   "check": lambda _s: ctx.check()})()
            pr = EnhancementPipeline(self._get_model_manager()).preview(audio, analysis, s, t0, t1, sub,
                                                                         learned_profile=learned)
            orig = np.asarray(audio.samples[pr.start_frame: pr.start_frame + pr.samples.shape[0]], np.float32)
            lo, le = (loudness.integrated_loudness(x, audio.sample_rate) for x in (orig, pr.samples))
            gain = float(10 ** ((le - lo) / 20)) if np.isfinite(lo) and np.isfinite(le) else 1.0
            return path, mtime, audio, analysis, orig, pr, gain

        def done(res):
            p, mtime, audio, analysis, orig, pr, gain = res
            self.activity.finish("batch-preview")
            self._bp_cache = (p, mtime, audio, analysis)
            if gen == self._bp_gen and self.batch_panel.preview_on:
                self.batch_panel.show_preview(p, orig, pr.samples, pr.sample_rate, pr.window, min(gain, 8.0),
                                              pr.processing_seconds)

        def failed(_title, message, cancelled):
            self.activity.finish("batch-preview", ok=False)
            if gen == self._bp_gen and not cancelled:
                self.batch_panel.preview_failed(message)

        self._bp_task = run_task(work, self, on_success=done, on_error=failed,
                                 on_progress=lambda f, m: self.activity.update("batch-preview", f, m))

    def _stop_batch_preview(self):
        self._bp_timer.stop()
        self._bp_gen += 1  # a sample still rendering is dropped
        if self._bp_task is not None and self._bp_task.running:
            self._bp_task.cancel()
        self.activity.finish("batch-preview")

    def _ab_shortcut(self, source: str):
        if self.tabs.currentWidget() is self.batch_page:
            self.batch_panel.switch(source)  # the batch preview's A/B
        else:
            self._activate(source)

    def _toggle_play_current(self):
        if self.tabs.currentWidget() is self.batch_page:
            self.batch_panel.toggle_play()
        elif self.tabs.currentWidget() is self.playback:
            self.playback.toggle_play()
        elif self.tabs.currentWidget() is self.compile_panel:
            self.compile_panel.toggle_play()
        else:
            self._toggle_main_play()

    @property
    def _enhancing(self) -> bool:
        return self._job_key == "enhance"

    def _toggle_main_play(self):
        """Play / pause the Enhance tab; playback waits while the recording is being enhanced."""
        if self._enhancing and self.player.state != "playing":
            self.status_label.setText("Playback is paused while enhancing. Press Play again when it is done.")
            return
        self.player.toggle_play()

    def _pause_main_player(self):
        if self.player.state == "playing":
            self.player.pause()

    def _open_from_playback(self, path):
        self.playback.pause()
        self.tabs.setCurrentIndex(0)
        self.open_file(Path(path))

    def _compile_play_started(self):
        self._pause_main_player()
        self.playback.pause()

    def _add_to_compile(self, paths):
        self.playback.pause()
        self.compile_panel.add_files([Path(p) for p in paths])
        self.tabs.setCurrentWidget(self.compile_panel)

    def _export_compilation(self, comp, name: str):
        if comp is None or self._task_running():
            return
        source = Path(comp.segments[0][2]) if comp.segments else Path(name)
        folder = self.session.exports_dir
        suggested, i = name, 2
        while (folder / f"{suggested}.wav").exists():
            suggested = f"{name} ({i})"
            i += 1
        dlg = ExportDialog(folder / source.name, comp.sample_rate, str(folder), self.config.output_format,
                           self.config.output_bit_depth, self.config.output_sample_rate, self,
                           suggested_name=suggested, reveal_after=self._reveal_after_export)
        dlg.setWindowTitle("Export Compilation")
        if dlg.exec() != ExportDialog.Accepted:
            return
        v = dlg.values()
        self.config.extra["reveal_after_export"] = v["reveal"]
        self.status_label.setText("Exporting compilation...")

        def work(ctx):
            prog = lambda f: ctx.progress(f, "Exporting compilation...")  # noqa: E731
            if v["format"] == "mp3":
                return export_mp3(comp.samples, comp.sample_rate, v["path"], v["mp3_quality"], v["sample_rate"],
                                  progress=prog)
            return export_wav(comp.samples, comp.sample_rate, v["path"], ExportOptions(v["bit_depth"], v["sample_rate"]),
                              progress=prog)

        log.info("Exporting compilation of %d takes to %s", len(comp.segments), v["path"])
        self._job_key, self._job_failed = "export-compilation", False
        self.activity.start("export-compilation", "Exporting", self.compile_panel)

        def progress(f, m):
            self.compile_panel.preview_state.setText(f"{m} {f:.0%}")
            self.activity.update("export-compilation", f, m)

        self.task = run_task(work, self, on_success=self._exported, on_error=self._task_failed,
                             on_progress=progress)

    def _add_to_batch(self, paths):
        self.batch_panel.add_files([Path(p) for p in paths])
        self.tabs.setCurrentWidget(self.batch_page)

    def _open_batch_result(self, path: Path):
        """Compare a finished batch file: open it in the Enhance tab."""
        self.tabs.setCurrentIndex(0)
        if not self._task_running():
            self.open_file(Path(path))

    # ============================================================================ settings
    def _initial_settings(self) -> ProcessingSettings | None:
        """The settings from the last session (1.0.3+), or None."""
        user_presets.load_all()
        saved = self.config.extra.get("settings")
        if not isinstance(saved, dict):
            return None
        try:
            s = ProcessingSettings.from_dict(saved)
        except (TypeError, ValueError):
            log.warning("Saved settings could not be read; starting from the preset")
            return None
        if s.preset not in preset_names():  # its user preset was deleted: keep the sound
            s = s.copy(preset="Clean Voice")
        return s

    # ============================================================================ recent files
    def _refresh_session_recordings(self):
        """The drop zone's THIS SESSION column: the session's newest recordings."""
        session = getattr(self, "session", None)
        files = []
        if session is not None:
            try:
                files = sorted((p for p in session.recordings_dir.glob("*.wav") if p.is_file()),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                files = []
        self.drop_zone.set_session_recordings(files, session.name if session is not None else "")

    def _remember_recent(self, path: Path):
        items = [p for p in self.config.extra.get("recent_files", []) if isinstance(p, str)]
        p = str(path)
        items = [p] + [x for x in items if os.path.normcase(x) != os.path.normcase(p)]
        self.config.extra["recent_files"] = items[:MAX_RECENT]
        self.drop_zone.set_recent(items[:MAX_RECENT])

    def _fill_recent_menu(self):
        m = self.recent_menu
        m.clear()
        items = [p for p in self.config.extra.get("recent_files", []) if isinstance(p, str)]
        for i, p in enumerate(items):
            path = Path(p)
            text = f"&{(i + 1) % 10}  {path.name}" if i < 10 else path.name
            a = m.addAction(text, lambda _=False, x=path: self._open_recent(x))
            a.setToolTip(str(path))
            a.setStatusTip(str(path))
            if not path.exists():
                a.setEnabled(False)
                a.setText(f"{text}  (missing)")
        if not items:
            m.addAction("No recent files").setEnabled(False)
        else:
            m.addSeparator()
            m.addAction("Clear Recent Files", self._clear_recent)
        m.setToolTipsVisible(True)

    def _clear_recent(self):
        self.config.extra["recent_files"] = []
        self.drop_zone.set_recent([])

    def _open_recent(self, path: Path):
        self.tabs.setCurrentIndex(0)
        if path.exists():
            self.open_file(path)

    def _show_tips(self):
        self.config.extra["smart_note_hidden"] = False
        self.settings_panel.show_tips()

    # ============================================================================ tour
    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_tour_checked", False):
            self._tour_checked = True
            QTimer.singleShot(900, self._tour_on_start)

    def _tour_on_start(self):
        """First start of a new install: the basics. First start after an update: what is
        new since the version last seen (nothing when there is nothing new)."""
        from . import tour

        if os.environ.get("VOICECLEANER_NO_TOUR"):
            return
        seen = self.config.extra.get("tour_seen_version")
        if seen == APP_VERSION:
            return
        fresh = not self.config.existed and seen is None
        steps = tour.steps_on_start(fresh, seen)
        if not steps:
            self._tour_done()
            return
        self.start_tour("basics" if fresh else "news", steps)

    def start_tour(self, kind: str, steps=None):
        from . import tour

        if getattr(self, "_tour", None) is not None:
            return
        steps = steps or (list(tour.BASICS) if kind == "basics" else tour.latest_news())
        if not steps:
            return
        heading = "GETTING STARTED" if kind == "basics" else f"WHAT'S NEW IN {APP_VERSION}"
        self._tour = tour.TourOverlay(self, steps, heading)
        self._tour.finished.connect(self._tour_done)
        # the window's single-key shortcuts (Space, R, A/B, Esc...) would act behind the tour
        self._tour_paused = [sc for sc in self.findChildren(QShortcut) if sc.isEnabled()]
        for sc in self._tour_paused:
            sc.setEnabled(False)
        self._tour.start()

    def _tour_done(self):
        self._tour = None
        for sc in getattr(self, "_tour_paused", []):
            sc.setEnabled(True)
        self._tour_paused = []
        self.config.extra["tour_seen_version"] = APP_VERSION
        try:
            self.config.save()
        except OSError:
            pass

    def _show_shortcuts(self):
        rows = "".join(f"<tr><td style='padding:3px 18px 3px 0'><b>{shortcut_text(k)}</b></td><td>{v}</td></tr>"
                       for k, v in SHORTCUTS)
        QMessageBox.information(self, "Keyboard Shortcuts", f"<table>{rows}</table>")

    def _work_in_progress(self) -> str:
        """What would be lost by quitting now, or ""."""
        if self.prompter is not None and self.prompter.recording:
            return "The teleprompter is recording. Quitting stops it; the takes so far are kept."
        if self.take_enhancer.pending:
            n = self.take_enhancer.pending
            return (f"{n} teleprompter take{'s are' if n != 1 else ' is'} still being enhanced. Quitting stops "
                    "that; the recordings are kept and are enhanced the next time you open this session.")
        if self.record_panel.recording:
            return "A recording is in progress. Quitting stops it; what was captured so far is kept."
        if self._batch:
            job = self.activity.job("batch")
            where = f" ({job.badge})" if job is not None and job.badge else ""
            return f"A batch is running{where}. Quitting cancels the files not finished yet."
        if self._task_running():
            job = self.activity.job(self._job_key) if self._job_key else None
            what = (job.label if job is not None else "Processing").lower()
            pct = f" ({job.fraction * 100:.0f}%)" if job is not None and job.fraction is not None else ""
            return f"VoiceCleaner is still {what}{pct}. Quitting cancels it."
        return ""

    def closeEvent(self, e):
        busy = self._work_in_progress()
        if busy and not getattr(self, "_force_close", False):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(f"Quit {APP_NAME}?")
            box.setText("<b>Quit while work is in progress?</b>")
            box.setInformativeText(busy)
            quit_btn = box.addButton("Quit", QMessageBox.DestructiveRole)
            keep = box.addButton("Keep Working", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is not quit_btn:
                e.ignore()
                if self._pending_update is not None and self._pending_update[3]:
                    # "Install and Restart" while busy: install when the user quits instead
                    self._pending_update = self._pending_update[:3] + (False,)
                    self.status_label.setText(f"VoiceCleaner {self._pending_update[2]} will be installed when you quit.")
                return
        if self._task_running():
            self.task.cancel()
            self.task.wait(3000)
        if self._batch:
            self._batch[1].cancel()
            self._batch[0].wait(3000)
        if self.prompter is not None:
            self.prompter.close()
        self.take_enhancer.shutdown()
        self.record_panel.shutdown()
        self.player.shutdown()
        self.playback.shutdown()
        self.compile_panel.shutdown()
        self.batch_panel.shutdown()
        s = self.settings_panel.current()
        c = self.config
        c.window_geometry = bytes(self.saveGeometry().toHex()).decode()
        c.preset = s.preset
        c.auto_settings = self.settings_panel.smart.isChecked()
        c.noise_reduction, c.speech_enhancement, c.room_reduction = s.noise_reduction, s.speech_enhancement, s.room_reduction
        c.target_lufs, c.peak_ceiling_dbtp = s.target_lufs, s.peak_ceiling_dbtp
        c.advanced_open = self.settings_panel.advanced_open
        c.extra["sections"] = self.settings_panel.section_states()
        c.extra["settings"] = s.to_dict()  # every control, restored at the next start
        c.extra["pauses"] = {"shorten": s.pause_shorten, "min_s": s.pause_min_s, "keep_ms": s.pause_keep_ms}
        c.extra["compile"] = self.compile_panel.options()
        c.extra["record"] = self.record_panel.options()
        c.extra["eq_gains"] = list(s.eq_gains)
        c.extra["eq_tilt"] = s.eq_tilt
        c.extra["session_path"] = str(self.session.path)
        c.extra["theme"] = self.theme_pref
        c.extra["last_tab"] = self.tabs.currentIndex()
        try:
            c.save()
        except OSError as exc:
            log.warning("Could not save preferences: %s", exc)
        if self._pending_update is not None:
            self._start_pending_update()
        super().closeEvent(e)
