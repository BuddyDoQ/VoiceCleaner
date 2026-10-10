"""User presets: the complete Enhance settings saved under a name.

Each preset is a small JSON file in ``<user data>/presets`` (``%APPDATA%\\VoiceCleaner\\presets``
on Windows, ``~/Library/Application Support/VoiceCleaner/presets`` on macOS), so presets can
be backed up, shared or copied between computers. Loaded presets are registered in
:data:`app.audio.settings.USER_PRESETS`, which is how every part of the app resolves them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..audio.settings import PRESETS, USER_PRESETS, ProcessingSettings
from .config import APP_VERSION, user_data_dir
from .errors import VoiceCleanerError
from .logging import get_logger

log = get_logger("presets")

MAX_NAME = 40
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PresetError(VoiceCleanerError):
    title = "Preset problem"


def presets_dir() -> Path:
    path = user_data_dir() / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_name(name: str) -> str:
    return " ".join(_BAD.sub("", name).split())[:MAX_NAME].strip(" .")


def validate_name(name: str, renaming: str | None = None) -> str:
    """The cleaned name, or PresetError explaining why it cannot be used."""
    n = clean_name(name)
    if not n:
        raise PresetError("Please enter a name for the preset.")
    if n.lower() in (p.lower() for p in PRESETS):
        raise PresetError(f"“{n}” is a built-in preset. Please choose another name.")
    return n


def _file_for(name: str) -> Path:
    return presets_dir() / f"{name}.json"


def load_all() -> list[str]:
    """(Re)load every preset file and register them. Returns the names, sorted."""
    USER_PRESETS.clear()
    for f in sorted(presets_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = clean_name(str(data.get("name") or f.stem))
            settings = data["settings"]
            if not name or not isinstance(settings, dict) or name in PRESETS:
                raise ValueError("missing name or settings")
            ProcessingSettings.from_dict(settings)  # must be readable
        except Exception as exc:  # noqa: BLE001 - one bad file must not hide the others
            log.warning("Skipping preset file %s: %s", f.name, exc)
            continue
        USER_PRESETS[name] = settings
    return sorted(USER_PRESETS, key=str.lower)


def find(name: str) -> str | None:
    """The existing user preset with this name, ignoring case."""
    return next((n for n in USER_PRESETS if n.lower() == name.lower()), None)


def save(name: str, settings: ProcessingSettings) -> str:
    name = validate_name(name)
    old = find(name)
    if old and old != name:  # same name, different case: replace it
        _file_for(old).unlink(missing_ok=True)
        USER_PRESETS.pop(old, None)
    data = settings.copy(preset=name).to_dict()
    payload = {"name": name, "app": f"VoiceCleaner {APP_VERSION}", "settings": data}
    try:
        _file_for(name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        raise PresetError(f"The preset could not be saved: {exc.strerror or exc}") from exc
    USER_PRESETS[name] = data
    log.info("Saved preset %s", name)
    return name


def rename(old: str, new: str) -> str:
    new = validate_name(new)
    if old not in USER_PRESETS:
        raise PresetError(f"There is no preset called “{old}”.")
    clash = find(new)
    if clash and clash != old:
        raise PresetError(f"A preset called “{clash}” already exists.")
    settings = ProcessingSettings.from_dict(USER_PRESETS[old])
    delete(old)
    return save(new, settings)


def delete(name: str) -> None:
    try:
        _file_for(name).unlink(missing_ok=True)
    except OSError as exc:
        raise PresetError(f"The preset could not be deleted: {exc.strerror or exc}") from exc
    USER_PRESETS.pop(name, None)
    log.info("Deleted preset %s", name)
