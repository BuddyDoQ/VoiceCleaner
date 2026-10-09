"""Enhancement controls: preset, three simple sliders, collapsible advanced section."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio.analyzer import AnalysisResult
from ..audio.eq import VoiceEQParams, choose_highpass, manual_bands
from ..audio.settings import PRESETS, ProcessingSettings, apply_simple, auto_configure, from_preset
from . import theme
from .tone_widgets import CollapsibleSection, EQCurve, GraphicEQ


class ValueSlider(QWidget):
    """Slider mapping an integer track onto a float range, with a value label."""

    valueChanged = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, step: float, fmt: Callable[[float], str],
                 tooltip: str = "", large: bool = False, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
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
        lay.addWidget(self.slider)
        if tooltip:
            self.setToolTip(tooltip)
        self._update_label()

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
        self.value_label.setText(self.fmt(self.value()))


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

        sec = QLabel("PRESET")
        sec.setProperty("role", "section")
        lay.addWidget(sec)
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.setCurrentText(initial.preset if initial.preset in PRESETS else "Clean Voice")
        self.preset.currentTextChanged.connect(self._preset_changed)
        lay.addWidget(self.preset)
        self.preset_desc = QLabel()
        self.preset_desc.setWordWrap(True)
        self.preset_desc.setProperty("role", "faint")
        lay.addWidget(self.preset_desc)

        self.smart = QCheckBox("Adapt to this recording")
        self.smart.setChecked(smart)
        self.smart.setToolTip("Analyzes the recording and adjusts the preset: more cleanup for noisy files,\n"
                              "less for clean ones, room reduction when there is reverb, hum filter when needed.")
        self.smart.toggled.connect(lambda _: self._preset_changed(self.preset.currentText()))
        lay.addWidget(self.smart)

        lay.addWidget(_divider())

        self.noise = ValueSlider("Noise Reduction", 0, 1, 0.01, pct, large=True,
                                 tooltip="Removes background noise such as fans, hiss, hum and room noise.")
        self.speech = ValueSlider("Speech Enhancement", 0, 1, 0.01, pct, large=True,
                                  tooltip="Improves clarity: balances the tone of the voice and brings out presence.")
        self.room = ValueSlider("Room / Echo Reduction", 0, 1, 0.01, pct, large=True,
                                tooltip="Reduces the sound of the room (reverb) and echoes.")
        for s in (self.noise, self.speech, self.room):
            s.valueChanged.connect(self._simple_changed)
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

        self.set_settings(initial)
        self._update_preset_desc()
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
        reset = QPushButton("Reset to automatic settings")
        reset.clicked.connect(lambda: self._preset_changed(self.preset.currentText()))
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
        self.analysis = analysis
        self._preset_changed(self.preset.currentText())
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

    def current(self) -> ProcessingSettings:
        return self.settings.copy()

    def set_ai_available(self, available: bool):
        self.use_ai.setEnabled(available)
        if not available:
            self.use_ai.setToolTip("The AI model is not available. Traditional noise reduction is used.")

    def _preset_changed(self, name: str):
        base = self.settings.copy(preset=name)
        if self.smart.isChecked() and self.analysis is not None:
            dec = auto_configure(name, self.analysis, base)
            s, self.auto_notes = dec.settings, dec.notes
        else:
            s, self.auto_notes = from_preset(name, base), []
        # loudness preferences are the user's, not the preset's
        s.target_lufs, s.peak_ceiling_dbtp = self.settings.target_lufs, self.settings.peak_ceiling_dbtp
        s.normalize_loudness, s.use_ai = self.settings.normalize_loudness, self.settings.use_ai
        s.eq_gains, s.eq_tilt = list(self.settings.eq_gains), self.settings.eq_tilt  # the user's EQ is not a preset
        s.pause_shorten, s.pause_min_s, s.pause_keep_ms = (self.settings.pause_shorten, self.settings.pause_min_s,
                                                           self.settings.pause_keep_ms)
        self.set_settings(s)
        self._update_preset_desc()
        self.settingsChanged.emit(self.current())

    def _update_preset_desc(self):
        p = PRESETS.get(self.preset.currentText())
        self.preset_desc.setText(p.description if p else "")

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
        self.settingsChanged.emit(self.current())


def _divider() -> QFrame:
    f = QFrame()
    f.setProperty("role", "divider")
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return f
