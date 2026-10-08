"""Processing settings, presets and automatic parameter selection.

Three layers:

* **Preset** - a starting point (Natural, Clean Voice, Podcast, ...).
* **Smart settings** - the analysis of the loaded recording adjusts the
  preset: noisy recordings get more noise reduction, clean ones get less,
  reverberant ones get room reduction, hum gets notched.
* **User** - the simple sliders and the advanced controls.

The three simple sliders drive several advanced parameters at once via
:func:`apply_simple`; advanced controls can then be fine-tuned individually.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace

# Graphic EQ bands: (centre Hz, label, filter kind). Octave spacing over the voice range.
EQ_BANDS: list[tuple[float, str, str]] = [
    (80.0, "Low end", "lowshelf"),
    (160.0, "Warmth", "peak"),
    (320.0, "Body", "peak"),
    (640.0, "Boxiness", "peak"),
    (1250.0, "Nasal", "peak"),
    (2500.0, "Clarity", "peak"),
    (5000.0, "Presence", "peak"),
    (10000.0, "Air", "highshelf"),
]
EQ_RANGE_DB = 12.0

import numpy as np

from .analyzer import AnalysisResult


@dataclass
class ProcessingSettings:
    preset: str = "Clean Voice"
    # simple controls (0..1)
    noise_reduction: float = 0.5
    speech_enhancement: float = 0.5
    room_reduction: float = 0.3
    # noise reduction
    use_ai: bool = True
    noise_floor_offset_db: float = 0.0  # bias of the learned/estimated noise floor
    spectral_subtraction: float = 1.75  # over-subtraction factor of the profile denoiser
    max_attenuation_db: float = 24.0  # cap on noise reduction; keeps natural room tone
    noise_gate_db: float = -80.0  # downward expander threshold, -80 = off
    # speech enhancement / EQ
    voice_presence: float = 0.0  # -1..1
    hf_clarity: float = 0.0  # -1..1
    lf_cleanup: float = 0.45  # 0..1
    # de-reverb
    reverb_reduction: float = 0.3
    echo_reduction: float = 0.15
    # neural voice re-synthesis (0 = off)
    resynthesis: float = 0.0
    # EQ & tonal balance
    tonal_balance: float = 0.5  # 0..1 automatic match toward a natural voice balance
    eq_tilt: float = 0.0  # -1 (darker) .. +1 (brighter)
    eq_gains: list = field(default_factory=lambda: [0.0] * len(EQ_BANDS))  # dB per band
    # dynamics
    leveler_amount: float = 0.4
    leveler_range_db: float = 10.0
    leveler_speed_s: float = 2.0
    compressor_amount: float = 0.45
    comp_threshold_db: float = -24.0
    comp_ratio: float = 2.5
    comp_attack_ms: float = 12.0
    comp_release_ms: float = 150.0
    # loudness
    normalize_loudness: bool = True
    target_lufs: float = -16.0
    peak_ceiling_dbtp: float = -1.0
    # derived automatically from analysis (shown read-only)
    hum_removal: bool = True
    highpass_hz: float = 0.0  # 0 = automatic
    quality_control: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessingSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def copy(self, **changes) -> "ProcessingSettings":
        changes.setdefault("eq_gains", list(self.eq_gains))
        return replace(self, **changes)


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    noise: float
    speech: float
    room: float
    compressor_amount: float
    comp_ratio: float
    comp_threshold_db: float
    presence: float = 0.0
    hf_clarity: float = 0.0
    gate_db: float = -80.0
    max_attenuation_db: float | None = None
    resynthesis: float = 0.0
    leveler: float = 0.4


PRESETS: dict[str, Preset] = {p.name: p for p in [
    Preset("Natural", "Minimal processing. Keeps the room and the voice as they are, just cleaner.",
           0.25, 0.25, 0.15, 0.25, 2.0, -22.0, leveler=0.2),
    Preset("Clean Voice", "Balanced cleanup for most recordings.",
           0.5, 0.5, 0.3, 0.45, 2.5, -24.0, leveler=0.4),
    Preset("Noisy Recording", "Strong noise reduction for fans, traffic and busy rooms.",
           0.8, 0.45, 0.3, 0.45, 2.5, -24.0, resynthesis=0.2, leveler=0.4),
    Preset("Interview", "Moderate noise reduction, clearer speech, even levels between speakers.",
           0.6, 0.55, 0.35, 0.55, 2.5, -24.0, presence=0.1, leveler=0.7),
    Preset("Podcast", "Broadcast-style voice: enhanced, gently compressed, consistent.",
           0.6, 0.7, 0.4, 0.7, 3.0, -26.0, presence=0.15, hf_clarity=0.1, gate_db=-60.0,
           resynthesis=0.25, leveler=0.6),
    Preset("Extreme Noise", "Maximum AI enhancement and voice re-synthesis with artifact protection.",
           1.0, 0.5, 0.5, 0.5, 2.5, -24.0, max_attenuation_db=60.0, resynthesis=0.4, leveler=0.5),
]}
DEFAULT_PRESET = "Clean Voice"


def apply_simple(s: ProcessingSettings, noise: float, speech: float, room: float) -> ProcessingSettings:
    """Map the three simple sliders onto the advanced parameters."""
    s = s.copy()
    s.noise_reduction = float(np.clip(noise, 0, 1))
    s.speech_enhancement = float(np.clip(speech, 0, 1))
    s.room_reduction = float(np.clip(room, 0, 1))
    s.spectral_subtraction = round(1.0 + 1.5 * s.noise_reduction, 2)
    preset = PRESETS.get(s.preset)
    if preset and preset.max_attenuation_db:
        s.max_attenuation_db = preset.max_attenuation_db
    else:
        s.max_attenuation_db = round(12.0 + 28.0 * s.noise_reduction, 1)
    s.lf_cleanup = round(0.2 + 0.5 * s.speech_enhancement, 2)
    s.tonal_balance = s.speech_enhancement
    s.reverb_reduction = s.room_reduction
    s.echo_reduction = round(0.5 * s.room_reduction, 2)
    return s


def from_preset(name: str, base: ProcessingSettings | None = None) -> ProcessingSettings:
    p = PRESETS.get(name, PRESETS[DEFAULT_PRESET])
    s = (base or ProcessingSettings()).copy(
        preset=p.name, compressor_amount=p.compressor_amount, comp_ratio=p.comp_ratio,
        comp_threshold_db=p.comp_threshold_db, voice_presence=p.presence, hf_clarity=p.hf_clarity,
        noise_gate_db=p.gate_db, resynthesis=p.resynthesis, leveler_amount=p.leveler,
    )
    return apply_simple(s, p.noise, p.speech, p.room)


@dataclass
class AutoDecision:
    settings: ProcessingSettings
    notes: list[str] = field(default_factory=list)


def auto_configure(preset_name: str, analysis: AnalysisResult, base: ProcessingSettings | None = None) -> AutoDecision:
    """Adapt a preset to the recording ("smart settings")."""
    p = PRESETS.get(preset_name, PRESETS[DEFAULT_PRESET])
    s = from_preset(p.name, base)
    notes: list[str] = []

    need_noise = float(np.clip((40.0 - analysis.snr_db) / 30.0, 0.0, 1.0))
    noise = p.noise + (need_noise - 0.5) * 0.6
    if analysis.speech_level_dbfs < -40:
        noise += 0.1
    room = p.room
    if analysis.rt60_s is None:
        room = p.room * 0.6
    else:
        room = p.room + (analysis.reverb_amount - 0.3) * 0.8
        if analysis.reverb_amount < 0.1:
            room = min(room, 0.15)
    speech = p.speech

    if analysis.is_clean:
        noise = min(noise, 0.15)
        speech = min(speech, 0.35)
        notes.append("The recording is already clean, so only light processing is applied.")
    elif need_noise > 0.7:
        notes.append("High background noise detected: noise reduction increased.")
    elif need_noise < 0.3:
        notes.append("Low background noise: lighter noise reduction is used.")
    if analysis.reverb_amount > 0.3:
        notes.append(f"Noticeable room reverb (about {analysis.rt60_s:.1f} s): room reduction enabled.")
    if analysis.hum.detected:
        notes.append(f"Electrical hum at {analysis.hum.fundamental:.0f} Hz detected: hum filter enabled.")
    if not analysis.speech_detected:
        noise = min(noise, 0.5)
        notes.append("No clear speech found; processing is kept moderate so nothing important is removed.")
    if analysis.is_clipped:
        notes.append("Clipping detected. Distortion can be softened but not fully repaired.")

    s = apply_simple(s, float(np.clip(noise, 0.05, 1.0)), float(np.clip(speech, 0.0, 1.0)),
                     float(np.clip(room, 0.0, 1.0)))
    s.hum_removal = analysis.hum.detected
    if analysis.reverb_amount > 0.2:
        # compression lifts reverb tails between words, which costs intelligibility
        s.compressor_amount = round(s.compressor_amount * (1.0 - 0.7 * analysis.reverb_amount), 3)
    return AutoDecision(s, notes)
