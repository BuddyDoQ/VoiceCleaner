"""User preferences and application paths.

Preferences are stored as JSON in the per-user application data folder so
that the application directory itself can stay read-only (important once it
is installed under Program Files as a packaged executable).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "VoiceCleaner"
APP_VERSION = "1.0.1"


def app_root() -> Path:
    """Directory that contains the application (and the ``models`` folder).

    Works both from source and from a PyInstaller bundle, where the models
    folder is shipped next to ``VoiceCleaner.exe`` rather than inside it so
    that models can be updated independently of the application.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    """Models shipped with the application (read-only when installed in Program Files)."""
    override = os.environ.get("VOICECLEANER_MODELS")
    return Path(override) if override else app_root() / "models"


def user_models_dir() -> Path:
    """Models downloaded on demand (always writable, survives app updates)."""
    override = os.environ.get("VOICECLEANER_USER_MODELS")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
        path = Path(base) / APP_NAME / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_path(folder: str, marker: str) -> Path:
    """Where model ``folder`` lives: the bundled copy if it has ``marker``, else the
    user download location (which is also where a download would put it)."""
    bundled = models_dir() / folder
    if (bundled / marker).exists():
        return bundled
    return user_models_dir() / folder


def edition() -> str:
    """Build edition written by the release script: "standard" (CPU) or "gpu"."""
    try:
        return (app_root() / "edition.txt").read_text(encoding="utf-8").strip() or "source"
    except OSError:
        return "source"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / APP_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class UserConfig:
    last_open_dir: str = ""
    last_export_dir: str = ""
    preset: str = "Clean Voice"
    auto_settings: bool = True
    noise_reduction: float = 0.5
    speech_enhancement: float = 0.5
    room_reduction: float = 0.3
    target_lufs: float = -16.0
    peak_ceiling_dbtp: float = -1.0
    output_format: str = "wav"
    output_bit_depth: str = "24"
    output_sample_rate: int = 0  # 0 = keep original
    device_preference: str = "auto"  # auto | cpu | cuda
    match_loudness_ab: bool = True
    window_geometry: str = ""
    advanced_open: bool = False
    batch_output_dir: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def path(cls) -> Path:
        return user_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "UserConfig":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        cfg = cls()
        for key, value in data.items():
            if key in known:
                expected = type(getattr(cfg, key))
                try:
                    setattr(cfg, key, expected(value) if expected is not dict else dict(value))
                except (TypeError, ValueError):
                    pass  # ignore corrupt individual values, keep defaults
        return cfg

    def save(self) -> None:
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, self.path())
