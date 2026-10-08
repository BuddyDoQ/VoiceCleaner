"""Enhancement controls: preset, three simple sliders, collapsible advanced section."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio.analyzer import AnalysisResult
from ..audio.eq import choose_highpass
from ..audio.settings import PRESETS, ProcessingSettings, apply_simple, auto_configure, from_preset
from . import theme


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
        return self.lo + self.slider.value() * self.step

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

        # --- advanced ------------------------------------------------------------------
        self.adv_button = QToolButton()
        self.adv_button.setText("Advanced")
        self.adv_button.setCheckable(True)
        self.adv_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_button.setArrowType(Qt.RightArrow)
        self.adv_button.setStyleSheet(f"QToolButton {{ border: none; color: {theme.MUTED}; font-weight: 600; padding: 4px 0; }}"
                                      f"QToolButton:hover {{ color: {theme.TEXT}; }}")
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

        g = self._group(adv, "Dynamics")
        self._adv(g, "compressor_amount", "Compressor amount", 0, 1, 0.01, pct)
        self._adv(g, "comp_threshold_db", "Threshold", -40, -6, 0.5, lambda v: f"{v:.1f} dBFS")
        self._adv(g, "comp_ratio", "Ratio", 1.0, 8.0, 0.1, lambda v: f"{v:.1f}:1")
        self._adv(g, "comp_attack_ms", "Attack", 1, 100, 1, lambda v: f"{v:.0f} ms")
        self._adv(g, "comp_release_ms", "Release", 20, 1000, 5, lambda v: f"{v:.0f} ms")

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
        for box, val in ((self.use_ai, s.use_ai), (self.normalize, s.normalize_loudness)):
            box.blockSignals(True)
            box.setChecked(val)
            box.blockSignals(False)

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
