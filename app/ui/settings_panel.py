"""Enhancement controls: preset, three simple sliders, collapsible advanced section.

Smart settings ("adapt to each recording") always win on the controls they set
(:data:`SMART_FIELDS`): while Smart is on those controls are locked and marked
AUTO. Every other control is the user's and survives loading another recording.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio.analyzer import AnalysisResult
from ..audio.eq import VoiceEQParams, choose_highpass, manual_bands
from ..audio.settings import (DEFAULT_PRESET, PRESETS, SMART_FIELDS, ProcessingSettings, apply_simple,
                              is_user_preset, modified_fields, preset_names, preset_reference, smart_adapt)
from ..utils import user_presets
from ..utils.shell import FILE_MANAGER, open_folder
from . import theme
from .tone_widgets import CollapsibleSection, EQCurve, GraphicEQ


class ValueSlider(QWidget):
    """Slider mapping an integer track onto a float range, with a value label."""

    valueChanged = Signal(float)
    resetRequested = Signal()  # double-click: back to the preset's value

    def __init__(self, label: str, lo: float, hi: float, step: float, fmt: Callable[[float], str],
                 tooltip: str = "", large: bool = False, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
        self._auto = False
        self._tip = tooltip
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4 if large else 2)
        row = QHBoxLayout()
        self.label = QLabel(label)
        # let long labels shrink instead of widening the sidebar
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.label.setMinimumWidth(10)
        if large:
            self.label.setStyleSheet("font-size: 10.5pt; font-weight: 600;")
        self.value_label = QLabel()
        self.value_label.setProperty("role", "muted")
        self.value_label.setMinimumWidth(64)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.label, 1)
        row.addWidget(self.value_label)
        lay.addLayout(row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        if not large:
            self.slider.setProperty("role", "small")
        self.slider.valueChanged.connect(self._changed)
        self.slider.installEventFilter(self)
        self.label.installEventFilter(self)
        lay.addWidget(self.slider)
        self._update_tooltip()
        self._update_label()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonDblClick and self.slider.isEnabled():
            self.resetRequested.emit()
            return True
        return super().eventFilter(obj, event)

    def set_auto(self, on: bool):
        """Locked and marked AUTO: Smart settings set this control for each recording."""
        if on == self._auto:
            return
        self._auto = on
        self.slider.setEnabled(not on)
        self.value_label.setProperty("auto", "true" if on else "false")
        self.value_label.style().unpolish(self.value_label)
        self.value_label.style().polish(self.value_label)
        self._update_tooltip()
        self._update_label()

    def _update_tooltip(self):
        tip = self._tip
        if self._auto:
            tip = (tip + "\n\n" if tip else "") + "AUTO: set for each recording by Smart settings.\n" \
                "Turn off \u201cSmart settings\u201d to adjust it yourself."
        else:
            tip = (tip + "\n\n" if tip else "") + "Double-click to reset to the preset's value."
        self.setToolTip(tip)

    def value(self) -> float:
        return round(self.lo + self.slider.value() * self.step, 6)  # no float noise like 1.5000000000000002

    def set_value(self, v: float, emit: bool = False):
        self.slider.blockSignals(not emit)
        self.slider.setValue(int(round((min(max(v, self.lo), self.hi) - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self._update_label()

    def _changed(self, _):
        self._update_label()
        self.valueChanged.emit(self.value())

    def _update_label(self):
        self.value_label.setText(("AUTO \u00b7 " if self._auto else "") + self.fmt(self.value()))


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def signed_pct(v: float) -> str:
    return f"{v * 100:+.0f}%" if abs(v) > 0.005 else "0"


def db_fmt(v: float) -> str:
    return f"{v:+.1f} dB" if v else "0 dB"


class SettingsPanel(QWidget):
    settingsChanged = Signal(object)
    downloadResynthRequested = Signal()

    def __init__(self, initial: ProcessingSettings, smart: bool = True, advanced_open: bool = False, parent=None):
        super().__init__(parent)
        self.settings = initial
        self.analysis: AnalysisResult | None = None
        self.auto_notes: list[str] = []
        self._advanced: dict[str, ValueSlider] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(8)
        sec = QLabel("PRESET")
        sec.setProperty("role", "section")
        head.addWidget(sec)
        head.addStretch(1)
        self.modified_tag = QLabel("MODIFIED")
        self.modified_tag.setProperty("role", "tag")
        head.addWidget(self.modified_tag)
        self.reset_link = QToolButton()
        self.reset_link.setText("Reset")
        self.reset_link.setProperty("role", "link")
        self.reset_link.setCursor(Qt.PointingHandCursor)
        self.reset_link.setToolTip("Return every control to the preset's values")
        self.reset_link.clicked.connect(self.reset_to_preset)
        head.addWidget(self.reset_link)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.preset = QComboBox()
        self.preset.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset.setMinimumContentsLength(8)
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.currentTextChanged.connect(self._preset_selected)
        row.addWidget(self.preset, 1)
        self.save_preset_btn = QPushButton("Save as\u2026")
        self.save_preset_btn.setToolTip("Save the current settings as your own preset")
        self.save_preset_btn.clicked.connect(self.save_preset_as)
        row.addWidget(self.save_preset_btn)
        self.preset_menu_btn = QToolButton()
        self.preset_menu_btn.setText("\u22ef")
        self.preset_menu_btn.setProperty("role", "menu")
        self.preset_menu_btn.setToolTip("Manage presets")
        self.preset_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.preset_menu = QMenu(self)
        self.preset_menu.aboutToShow.connect(self._fill_preset_menu)
        self.preset_menu_btn.setMenu(self.preset_menu)
        row.addWidget(self.preset_menu_btn)
        lay.addLayout(row)
        self.preset_desc = QLabel()
        self.preset_desc.setWordWrap(True)
        self.preset_desc.setProperty("role", "faint")
        lay.addWidget(self.preset_desc)

        self.smart = QCheckBox("Smart settings: adapt to each recording")
        self.smart.setChecked(smart)
        self.smart.setToolTip("Analyzes each recording and sets the cleanup amounts for it: more for noisy files,\n"
                              "less for clean ones, room reduction when there is reverb, hum filter when needed.\n"
                              "While on, the controls it sets are locked and marked AUTO.")
        self.smart.toggled.connect(self._smart_toggled)
        lay.addWidget(self.smart)
        self.smart_note = QLabel()
        self.smart_note.setWordWrap(True)
        self.smart_note.setProperty("role", "smartnote")
        lay.addWidget(self.smart_note)

        lay.addWidget(_divider())

        self.noise = ValueSlider("Noise Reduction", 0, 1, 0.01, pct, large=True,
                                 tooltip="Removes background noise such as fans, hiss, hum and room noise.")
        self.speech = ValueSlider("Speech Enhancement", 0, 1, 0.01, pct, large=True,
                                  tooltip="Improves clarity: balances the tone of the voice and brings out presence.")
        self.room = ValueSlider("Room / Echo Reduction", 0, 1, 0.01, pct, large=True,
                                tooltip="Reduces the sound of the room (reverb) and echoes.")
        for key, s in (("noise_reduction", self.noise), ("speech_enhancement", self.speech),
                       ("room_reduction", self.room)):
            s.valueChanged.connect(self._simple_changed)
            s.resetRequested.connect(lambda k=key, w=s: w.set_value(getattr(self._reference(), k), emit=True))
            lay.addWidget(s)

        lay.addWidget(_divider())
        self._build_resynthesis(lay)
        self._build_tone(lay)
        self._build_dynamics(lay)

        # --- advanced ------------------------------------------------------------------
        self.adv_button = QToolButton()
        self.adv_button.setText("Advanced")
        self.adv_button.setCheckable(True)
        self.adv_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_button.setArrowType(Qt.RightArrow)
        self.adv_button.setProperty("role", "disclosure-muted")
        self.adv_button.toggled.connect(self._toggle_advanced)
        lay.addWidget(self.adv_button)
        self.advanced = QWidget()
        self.advanced.setVisible(False)
        adv = QVBoxLayout(self.advanced)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(10)
        self._build_advanced(adv)
        lay.addWidget(self.advanced)
        lay.addStretch(1)

        self._populate_presets(initial.preset)
        self.set_settings(initial)
        self._update_preset_desc()
        self._sync_smart_ui()
        if advanced_open:
            self.adv_button.setChecked(True)

    # --- advanced section -----------------------------------------------------------------
    def _group(self, layout: QVBoxLayout, title: str) -> QVBoxLayout:
        box = QFrame()
        box.setProperty("role", "card")
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)
        t = QLabel(title.upper())
        t.setProperty("role", "section")
        v.addWidget(t)
        layout.addWidget(box)
        return v

    def _adv(self, layout, key: str, label: str, lo, hi, step, fmt, tip=""):
        w = ValueSlider(label, lo, hi, step, fmt, tooltip=tip)
        w.valueChanged.connect(lambda v, k=key: self._advanced_changed(k, v))
        w.resetRequested.connect(lambda k=key, w=w: w.set_value(float(getattr(self._reference(), k)), emit=True))
        layout.addWidget(w)
        self._advanced[key] = w
        return w

    def _build_advanced(self, adv: QVBoxLayout):
        g = self._group(adv, "Noise reduction")
        self.use_ai = QCheckBox("AI speech enhancement")
        self.use_ai.setToolTip("Use the neural network (DeepFilterNet3) to separate speech from noise.\n"
                               "When off, only traditional spectral noise reduction is used.")
        self.use_ai.toggled.connect(lambda v: self._advanced_changed("use_ai", v))
        g.addWidget(self.use_ai)
        self._adv(g, "noise_reduction", "Noise reduction amount", 0, 1, 0.01, pct)
        self._adv(g, "noise_floor_offset_db", "Noise floor estimate", -6, 6, 0.5, db_fmt,
                  "Shifts the estimated background level. Raise it if noise remains, lower it if speech sounds thin.")
        self._adv(g, "spectral_subtraction", "Spectral subtraction strength", 1.0, 3.0, 0.05, lambda v: f"{v:.2f}×",
                  "How aggressively the learned noise profile is subtracted.")
        self._adv(g, "noise_gate_db", "Noise gate threshold", -80, -30, 1,
                  lambda v: "Off" if v <= -79.5 else f"{v:.0f} dBFS",
                  "Gently turns down passages quieter than this level (soft expander, never full silence).")
        self._adv(g, "max_attenuation_db", "Residual noise floor", 6, 60, 1, lambda v: f"−{v:.0f} dB",
                  "The deepest the background may be reduced. Keeping some room tone sounds more natural.")

        g = self._group(adv, "Speech enhancement")
        self._adv(g, "speech_enhancement", "Enhancement strength", 0, 1, 0.01, pct,
                  "Depth of the adaptive tone correction toward a natural voice balance.")
        self._adv(g, "voice_presence", "Voice presence", -1, 1, 0.01, signed_pct, "Forwardness of the voice (around 3 kHz).")
        self._adv(g, "hf_clarity", "High-frequency clarity", -1, 1, 0.01, signed_pct, "Air and crispness above 7 kHz.")
        self._adv(g, "lf_cleanup", "Low-frequency cleanup", 0, 1, 0.01, pct, "Tightens boomy low end and rumble.")

        g = self._group(adv, "Room and echo")
        self._adv(g, "reverb_reduction", "Reverb reduction", 0, 1, 0.01, pct)
        self._adv(g, "echo_reduction", "Echo reduction", 0, 1, 0.01, pct)

        g = self._group(adv, "Loudness")
        self.normalize = QCheckBox("Normalize loudness")
        self.normalize.toggled.connect(lambda v: self._advanced_changed("normalize_loudness", v))
        g.addWidget(self.normalize)
        self._adv(g, "target_lufs", "Target loudness", -30, -10, 0.5, lambda v: f"{v:.1f} LUFS",
                  "-16 LUFS suits podcasts and online video; -19 to -23 LUFS for broadcast or quieter delivery.")
        self._adv(g, "peak_ceiling_dbtp", "Peak ceiling", -6, 0, 0.1, lambda v: f"{v:.1f} dBTP")

        g = self._group(adv, "Detected automatically")
        self.detected = QLabel("Load a recording to see what was detected.")
        self.detected.setWordWrap(True)
        self.detected.setProperty("role", "faint")
        g.addWidget(self.detected)
        reset = QPushButton("Reset to preset")
        reset.clicked.connect(self.reset_to_preset)
        g.addWidget(reset)

    # --- re-synthesis / tone / dynamics sections -------------------------------------
    def _build_resynthesis(self, lay: QVBoxLayout):
        sec = CollapsibleSection("Voice Re-synthesis")
        self.resynth_section = sec
        b = sec.body_layout
        info = QLabel("Re-generates the cleaned voice with a neural vocoder (NVIDIA BigVGAN). Smooths away "
                      "leftover processing artifacts and restores natural, coherent harmonics. "
                      "Use gently: high amounts can change the voice's character.")
        info.setWordWrap(True)
        info.setProperty("role", "faint")
        b.addWidget(info)
        self._adv(b, "resynthesis", "Re-synthesis amount", 0, 1, 0.01, lambda v: "Off" if v < 0.005 else pct(v),
                  "0% = off. 20-40% polishes the voice; 100% replaces it entirely with the re-synthesised voice.")
        self.resynth_status = QLabel("")
        self.resynth_status.setWordWrap(True)
        self.resynth_status.setProperty("role", "faint")
        b.addWidget(self.resynth_status)
        self.resynth_download = QPushButton("Download voice model (490 MB)")
        self.resynth_download.clicked.connect(self.downloadResynthRequested)
        self.resynth_download.setVisible(False)
        b.addWidget(self.resynth_download)
        lay.addWidget(sec)

    def set_resynth_status(self, installed: bool, device_kind: str = "cpu", message: str = ""):
        w = self._advanced["resynthesis"]
        w.setEnabled(installed)
        self.resynth_download.setVisible(not installed)
        if message:
            self.resynth_status.setText(message)
        elif not installed:
            self.resynth_status.setText("The re-synthesis model is not installed yet. It is downloaded once from "
                                        "NVIDIA's official repository and then works offline.")
        elif device_kind != "cuda":
            self.resynth_status.setText("Runs on the CPU here: about real-time speed, so long recordings take a while.")
        else:
            self.resynth_status.setText("")
        self.resynth_section.summary.setText("" if installed else "not installed")

    def _build_tone(self, lay: QVBoxLayout):
        sec = CollapsibleSection("EQ && Tone")  # "&&" = literal ampersand in Qt
        self.tone_section = sec
        b = sec.body_layout
        self.eq_curve = EQCurve()
        b.addWidget(self.eq_curve)
        legend = QLabel(f"<span style='color:{theme.ACCENT}'>\u2014</span> total &nbsp; "
                        f"<span style='color:{theme.ORIGINAL}'>- -</span> automatic balance (after Enhance)")
        legend.setProperty("role", "faint")
        b.addWidget(legend)
        self._adv(b, "tonal_balance", "Tonal balancing", 0, 1, 0.01, pct,
                  "Automatically corrects muddiness, boominess and missing presence by comparing the voice "
                  "with the average spectrum of natural speech.")
        self._adv(b, "eq_tilt", "Tilt (darker \u2194 brighter)", -1, 1, 0.01, signed_pct,
                  "Tilts the whole tone around 1 kHz: left for warmer/darker, right for brighter.")
        self.graphic_eq = GraphicEQ()
        self.graphic_eq.changed.connect(self._eq_changed)
        b.addWidget(self.graphic_eq)
        reset = QPushButton("Reset EQ")
        reset.setProperty("role", "ghost")
        reset.clicked.connect(self._reset_eq)
        b.addWidget(reset, alignment=Qt.AlignRight)
        lay.addWidget(sec)

    def _build_dynamics(self, lay: QVBoxLayout):
        sec = CollapsibleSection("Dynamics")
        self.dynamics_section = sec
        b = sec.body_layout
        t = QLabel("LEVELING")
        t.setProperty("role", "section")
        b.addWidget(t)
        self._adv(b, "leveler_amount", "Leveling", 0, 1, 0.01, lambda v: "Off" if v < 0.005 else pct(v),
                  "Evens out loud and quiet sentences and speakers over several seconds, like riding a fader. "
                  "Pauses are left alone, so background noise is not pumped up.")
        self._adv(b, "leveler_range_db", "Maximum correction", 2, 20, 0.5, lambda v: f"\u00b1{v:.1f} dB")
        self._adv(b, "leveler_speed_s", "Speed", 0.5, 8, 0.1, lambda v: f"{v:.1f} s",
                  "How quickly the level follows changes. Shorter reacts faster; longer is smoother.")
        t = QLabel("PAUSES")
        t.setProperty("role", "section")
        b.addWidget(t)
        self.pause_box = QCheckBox("Shorten long pauses on export")
        self.pause_box.setToolTip("Shortens long silences inside the recording to a natural pause when exporting.\n"
                                  "The preview keeps the original timing so A/B comparison stays aligned.")
        self.pause_box.toggled.connect(self._pause_toggled)
        b.addWidget(self.pause_box)
        self._adv(b, "pause_min_s", "Pauses longer than", 0.3, 5.0, 0.1, lambda v: f"{v:.1f} s")
        self._adv(b, "pause_keep_ms", "Shorten to", 0, 2000, 50,
                  lambda v: "Remove completely" if v < 1 else f"{v:.0f} ms",
                  "Length each long pause is shortened to. Slide to the left end to remove long pauses completely.")
        t = QLabel("COMPRESSION")
        t.setProperty("role", "section")
        b.addWidget(t)
        self._adv(b, "compressor_amount", "Compression", 0, 1, 0.01, lambda v: "Off" if v < 0.005 else pct(v),
                  "Controls the peaks of individual words and syllables for a steadier, more present voice.")
        self._adv(b, "comp_threshold_db", "Threshold", -40, -6, 0.5, lambda v: f"{v:.1f} dBFS")
        self._adv(b, "comp_ratio", "Ratio", 1.0, 8.0, 0.1, lambda v: f"{v:.1f}:1")
        self._adv(b, "comp_attack_ms", "Attack", 1, 100, 1, lambda v: f"{v:.0f} ms")
        self._adv(b, "comp_release_ms", "Release", 20, 1000, 5, lambda v: f"{v:.0f} ms")
        lay.addWidget(sec)

    def _pause_toggled(self, on: bool):
        self._sync_pause_controls(on)
        self._advanced_changed("pause_shorten", on)

    def _sync_pause_controls(self, on: bool | None = None):
        on = self.settings.pause_shorten if on is None else on
        for key in ("pause_min_s", "pause_keep_ms"):
            self._advanced[key].setEnabled(on)

    def _eq_changed(self, gains: list):
        self.settings = self.settings.copy(eq_gains=list(gains))
        self._update_eq_curve()
        self._update_modified()
        self.settingsChanged.emit(self.current())

    def _reset_eq(self):
        self.settings = self.settings.copy(eq_gains=[0.0] * len(self.settings.eq_gains), eq_tilt=0.0)
        self.set_settings(self.settings)
        self.settingsChanged.emit(self.current())

    def _update_eq_curve(self, auto=None):
        s = self.settings
        self.eq_curve.set_bands(manual_bands(VoiceEQParams(tilt=s.eq_tilt, manual_gains=list(s.eq_gains))), auto)

    def set_eq_result(self, auto_bands):
        """Show the automatic tonal-balance curve chosen during the last Enhance."""
        self._update_eq_curve(list(auto_bands))

    def section_states(self) -> dict:
        return {"resynth": self.resynth_section.is_open, "tone": self.tone_section.is_open,
                "dynamics": self.dynamics_section.is_open}

    def restore_section_states(self, states: dict):
        self.resynth_section.set_open(bool(states.get("resynth", False)))
        self.tone_section.set_open(bool(states.get("tone", True)))
        self.dynamics_section.set_open(bool(states.get("dynamics", False)))

    def _toggle_advanced(self, on: bool):
        self.advanced.setVisible(on)
        self.adv_button.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    @property
    def advanced_open(self) -> bool:
        return self.adv_button.isChecked()

    # --- state ---------------------------------------------------------------------------
    def set_analysis(self, analysis: AnalysisResult | None):
        """A recording was loaded (or closed). Smart on: it sets its controls for this
        recording. Smart off: the user's settings stay exactly as they are."""
        self.analysis = analysis
        self._apply_smart()
        self._after_change()
        if analysis is not None:
            bits = []
            hp = choose_highpass(self.settings.lf_cleanup, analysis.lf_rumble_db, analysis.f0_low_hz)
            bits.append(f"Background noise: {analysis.noise_floor_dbfs:.0f} dBFS (speech-to-noise {analysis.snr_db:.0f} dB)")
            bits.append(f"Hum: {analysis.hum.fundamental:.0f} Hz" if analysis.hum.detected else "Hum: none")
            bits.append(f"Room reverb: about {analysis.rt60_s:.1f} s" if analysis.rt60_s else "Room reverb: not measurable")
            if analysis.echo_delay_s:
                bits.append(f"Echo: {analysis.echo_delay_s * 1000:.0f} ms")
            if analysis.f0_median_hz:
                bits.append(f"Voice pitch: ~{analysis.f0_median_hz:.0f} Hz")
            bits.append(f"High-pass filter: {hp:.0f} Hz")
            bits.append(analysis.stereo.description)
            self.detected.setText("\n".join(bits))

    def set_settings(self, s: ProcessingSettings):
        self.settings = s
        self.noise.set_value(s.noise_reduction)
        self.speech.set_value(s.speech_enhancement)
        self.room.set_value(s.room_reduction)
        for key, w in self._advanced.items():
            w.set_value(float(getattr(s, key)))
        for box, val in ((self.use_ai, s.use_ai), (self.normalize, s.normalize_loudness),
                         (self.pause_box, s.pause_shorten)):
            box.blockSignals(True)
            box.setChecked(val)
            box.blockSignals(False)
        self.graphic_eq.set_values(list(s.eq_gains))
        self._sync_pause_controls()
        self._update_eq_curve()
        self._update_modified()

    def current(self) -> ProcessingSettings:
        return self.settings.copy()

    def set_ai_available(self, available: bool):
        self.use_ai.setEnabled(available)
        if not available:
            self.use_ai.setToolTip("The AI model is not available. Traditional noise reduction is used.")

    # --- presets ---------------------------------------------------------------------------
    def _populate_presets(self, select: str):
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(list(PRESETS))
        mine = [n for n in preset_names() if n not in PRESETS]
        if mine:
            self.preset.insertSeparator(self.preset.count())
            self.preset.addItems(sorted(mine, key=str.lower))
        self.preset.setCurrentText(select if select in preset_names() else DEFAULT_PRESET)
        self.preset.blockSignals(False)

    def _preset_selected(self, name: str):
        if name:
            self._preset_changed(name)

    def _preset_changed(self, name: str):
        """Apply preset ``name`` completely (then Smart, if on, for the loaded recording)."""
        self.settings = preset_reference(name, self.settings)
        self._apply_smart()
        self._after_change()

    def reset_to_preset(self):
        self._preset_changed(self.preset.currentText())

    def _apply_smart(self):
        if self.smart.isChecked() and self.analysis is not None:
            dec = smart_adapt(self.settings, self.analysis)
            self.settings, self.auto_notes = dec.settings, dec.notes
        else:
            self.auto_notes = []

    def _after_change(self):
        self.set_settings(self.settings)
        self._update_preset_desc()
        self._sync_smart_ui()
        self.settingsChanged.emit(self.current())

    def _reference(self) -> ProcessingSettings:
        """What the current preset gives right now (with Smart, for the loaded recording)."""
        ref = preset_reference(self.settings.preset, self.settings)
        if self.smart.isChecked() and self.analysis is not None:
            ref = smart_adapt(ref, self.analysis).settings
        return ref

    def modified(self) -> list[str]:
        return modified_fields(self.settings, self._reference(), self.smart.isChecked())

    def _update_modified(self):
        if not hasattr(self, "modified_tag"):
            return
        changed = self.modified()
        self.modified_tag.setVisible(bool(changed))
        self.reset_link.setVisible(bool(changed))
        if changed:
            self.modified_tag.setToolTip(f"{len(changed)} control{'s' if len(changed) != 1 else ''} differ "
                                         f"from the preset \u201c{self.settings.preset}\u201d.")

    def _smart_toggled(self, on: bool):
        # on: Smart takes over its controls for the loaded recording; off: values stay, unlocked
        self._apply_smart()
        self._after_change()

    def _sync_smart_ui(self):
        on = self.smart.isChecked()
        for w in (self.noise, self.speech, self.room):
            w.set_auto(on)
        for key, w in self._advanced.items():
            if key in SMART_FIELDS:
                w.set_auto(on)
        self.smart_note.setVisible(on)
        if on:
            when = "this recording" if self.analysis is not None else "each recording when it is loaded"
            self.smart_note.setText(
                f"<b>Smart is on.</b> Noise, Speech and Room (and the controls tied to them, marked "
                f"<span style='color:{theme.ACCENT}'>AUTO</span>) are set for {when}. "
                "Turn Smart off to adjust them yourself.")

    def _update_preset_desc(self):
        name = self.preset.currentText()
        p = PRESETS.get(name)
        self.preset_desc.setText(p.description if p else "Your own preset." if is_user_preset(name) else "")

    def _fill_preset_menu(self):
        m = self.preset_menu
        m.clear()
        name = self.preset.currentText()
        m.addAction("Save as New Preset\u2026", self.save_preset_as)
        if is_user_preset(name):
            a = m.addAction(f"Update \u201c{name}\u201d", self.update_preset)
            a.setEnabled(bool(self.modified()))
            m.addAction(f"Rename \u201c{name}\u201d\u2026", self.rename_preset)
            m.addAction(f"Delete \u201c{name}\u201d\u2026", self.delete_preset)
        m.addSeparator()
        a = m.addAction("Reset to Preset", self.reset_to_preset)
        a.setEnabled(bool(self.modified()))
        m.addAction(f"Show Presets in {FILE_MANAGER}", lambda: open_folder(user_presets.presets_dir()))

    def _settings_to_save(self) -> ProcessingSettings:
        s = self.current()
        if self.smart.isChecked():
            # the values Smart chose for this recording are not the preset's: keep the preset's own
            ref = preset_reference(s.preset, s)
            s = s.copy(**{k: getattr(ref, k) for k in SMART_FIELDS})
        return s

    def _ask_name(self, title: str, label: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, label, QLineEdit.Normal, text)
        return name if ok else None

    def save_preset_as(self):
        cur = self.preset.currentText()
        name = self._ask_name("Save as Preset",
                              f"Name for the new preset (up to {user_presets.MAX_NAME} characters):",
                              cur if is_user_preset(cur) else "")
        if name is None:
            return
        try:
            clean = user_presets.validate_name(name)
            existing = user_presets.find(clean)
            if existing and QMessageBox.question(
                    self, "Replace Preset", f"A preset called \u201c{existing}\u201d already exists. Replace it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            saved = user_presets.save(clean, self._settings_to_save())
        except user_presets.PresetError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message)
            return
        self._select_saved(saved)

    def update_preset(self):
        try:
            self._select_saved(user_presets.save(self.preset.currentText(), self._settings_to_save()))
        except user_presets.PresetError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message)

    def rename_preset(self):
        old = self.preset.currentText()
        name = self._ask_name("Rename Preset", "New name:", old)
        if name is None or name == old:
            return
        try:
            self._select_saved(user_presets.rename(old, name))
        except user_presets.PresetError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message)

    def delete_preset(self):
        name = self.preset.currentText()
        if QMessageBox.question(self, "Delete Preset", f"Delete the preset \u201c{name}\u201d?\n"
                                "Your current settings stay as they are.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            user_presets.delete(name)
        except user_presets.PresetError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message)
            return
        # keep the sound; it just no longer has a preset of its own
        self.settings = self.settings.copy(preset=DEFAULT_PRESET)
        self._populate_presets(DEFAULT_PRESET)
        self._after_change()

    def _select_saved(self, name: str):
        """After saving, the current settings are this preset (nothing changes audibly)."""
        self.settings = self.settings.copy(preset=name)
        self._apply_smart()
        self._populate_presets(name)
        self._after_change()

    def _simple_changed(self, _):
        s = apply_simple(self.settings, self.noise.value(), self.speech.value(), self.room.value())
        self.set_settings(s)
        self.settingsChanged.emit(self.current())

    def _advanced_changed(self, key: str, value):
        self.settings = self.settings.copy(**{key: value})
        # keep the simple sliders in step with their advanced counterparts
        if key == "eq_tilt":
            self._update_eq_curve()
        mirror = {"noise_reduction": self.noise, "speech_enhancement": self.speech, "reverb_reduction": self.room}
        if key in mirror:
            mirror[key].set_value(float(value))
            if key == "reverb_reduction":
                self.settings = self.settings.copy(room_reduction=float(value))
        self._update_modified()
        self.settingsChanged.emit(self.current())


def _divider() -> QFrame:
    f = QFrame()
    f.setProperty("role", "divider")
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return f
