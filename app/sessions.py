"""Sessions: a named project that groups related recordings and exports.

Each session is a folder under ``Documents\\VoiceCleaner Sessions``::

    <Session Name>/
        session.json         name, creation time
        Recordings/          <Session Name> - Rec 001.wav, Rec 002 ...
        Exports/             <Session Name> - <source> enhanced.wav ...

Session names are limited to :data:`MAX_NAME_LENGTH` characters and cleaned of
characters Windows does not allow in file names, so every file name that
carries the session name stays short and valid.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .utils.logging import get_logger

log = get_logger("sessions")

MAX_NAME_LENGTH = 32
MAX_STEM_LENGTH = 40  # source part of export names
SESSION_FILE = "session.json"
RECORDINGS = "Recordings"
EXPORTS = "Exports"
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_REC = re.compile(r" - Rec (\d{3,})")


class SessionError(Exception):
    pass


def sessions_root() -> Path:
    base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "VoiceCleaner Sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sanitize_name(name: str) -> str:
    """Make ``name`` safe as a Windows folder/file-name part, max 32 characters."""
    name = _INVALID.sub("", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:MAX_NAME_LENGTH].rstrip(" .")
    if name.upper().split(".")[0] in _RESERVED:
        name = f"{name}_"[:MAX_NAME_LENGTH]
    return name


def validate_name(name: str) -> str | None:
    """User-facing problem with ``name``, or None when it is acceptable."""
    if not (name or "").strip():
        return "Enter a session name."
    if len(name.strip()) > MAX_NAME_LENGTH:
        return f"Use at most {MAX_NAME_LENGTH} characters."
    if _INVALID.search(name):
        return 'Names cannot contain < > : " / \\ | ? *'
    if not sanitize_name(name):
        return "Use letters or numbers in the name."
    return None


def default_name(root: Path | None = None) -> str:
    base = f"Session {datetime.now():%Y-%m-%d}"
    return _unique_name(base, root or sessions_root())


def _unique_name(name: str, root: Path) -> str:
    candidate, i = name, 2
    while (root / candidate).exists():
        suffix = f" ({i})"
        candidate = name[: MAX_NAME_LENGTH - len(suffix)].rstrip() + suffix
        i += 1
    return candidate


@dataclass
class Session:
    name: str
    path: Path
    created: str = ""

    @property
    def recordings_dir(self) -> Path:
        d = self.path / RECORDINGS
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def exports_dir(self) -> Path:
        d = self.path / EXPORTS
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- file naming ------------------------------------------------------------
    def next_recording_number(self) -> int:
        nums = [int(m.group(1)) for p in self.recordings_dir.glob("*.wav") if (m := _REC.search(p.stem))]
        return max(nums, default=0) + 1

    def recording_path(self, label: str = "") -> Path:
        """``<Session> - Rec 007.wav`` (or ``... - Rec 007 append.wav`` with a label)."""
        n = self.next_recording_number()
        extra = f" {label}" if label else ""
        return self.recordings_dir / f"{self.name} - Rec {n:03d}{extra}.wav"

    def strip_prefix(self, stem: str) -> str:
        prefix = f"{self.name} - "
        return stem[len(prefix):] if stem.startswith(prefix) else stem

    def export_path(self, source: Path, ext: str = ".wav", suffix: str = "enhanced", directory: Path | None = None) -> Path:
        """``<Session> - <source, shortened> enhanced.wav``, unique in the folder."""
        stem = self.strip_prefix(Path(source).stem)
        if len(stem) > MAX_STEM_LENGTH:
            stem = stem[:MAX_STEM_LENGTH].rstrip(" -_.")
        directory = directory or self.exports_dir
        base = f"{self.name} - {stem} {suffix}".strip()
        candidate, i = directory / f"{base}{ext}", 2
        while candidate.exists():
            candidate = directory / f"{base} ({i}){ext}"
            i += 1
        return candidate

    # --- persistence ------------------------------------------------------------------
    def save(self):
        self.path.mkdir(parents=True, exist_ok=True)
        data = {"name": self.name, "created": self.created, "app": "VoiceCleaner"}
        (self.path / SESSION_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def audio_files(self) -> list[Path]:
        files = list(self.path.glob(f"{RECORDINGS}/*.wav")) + list(self.path.glob(f"{EXPORTS}/*.wav"))
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def create(name: str, root: Path | None = None) -> Session:
    problem = validate_name(name)
    if problem:
        raise SessionError(problem)
    root = root or sessions_root()
    clean = sanitize_name(name)
    if (root / clean).exists():
        raise SessionError(f"A session called “{clean}” already exists. Open it or choose another name.")
    s = Session(clean, root / clean, datetime.now().isoformat(timespec="seconds"))
    s.save()
    s.recordings_dir, s.exports_dir  # noqa: B018 - create folders
    log.info("Created session %s at %s", clean, s.path)
    return s


def open_session(path: Path) -> Session:
    path = Path(path)
    if path.name in (RECORDINGS, EXPORTS) and (path.parent / SESSION_FILE).exists():
        path = path.parent
    meta = path / SESSION_FILE
    if not path.is_dir():
        raise SessionError(f"“{path}” is not a folder.")
    name, created = sanitize_name(path.name), ""
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            name = sanitize_name(data.get("name", name)) or name
            created = data.get("created", "")
        except (OSError, ValueError):
            pass
    if not name:
        raise SessionError("This folder cannot be used as a session.")
    s = Session(name, path, created)
    if not meta.exists():
        s.save()  # adopt an existing folder as a session
    return s


def rename(session: Session, new_name: str) -> Session:
    """Rename the session folder and the session prefix of its files."""
    problem = validate_name(new_name)
    if problem:
        raise SessionError(problem)
    clean = sanitize_name(new_name)
    if clean == session.name:
        return session
    target = session.path.parent / clean
    if target.exists():
        raise SessionError(f"A session called “{clean}” already exists.")
    old_prefix = f"{session.name} - "
    for f in session.audio_files():
        if f.name.startswith(old_prefix):
            f.rename(f.with_name(f"{clean} - {f.name[len(old_prefix):]}"))
    session.path.rename(target)
    s = Session(clean, target, session.created)
    s.save()
    log.info("Renamed session %s -> %s", session.name, clean)
    return s


def list_sessions(root: Path | None = None) -> list[Session]:
    root = root or sessions_root()
    out = []
    for d in root.iterdir():
        if d.is_dir() and (d / SESSION_FILE).exists():
            try:
                out.append(open_session(d))
            except SessionError:
                continue
    return sorted(out, key=lambda s: s.path.stat().st_mtime, reverse=True)


def current_or_new(saved_path: str | None) -> Session:
    """Resume the last session, or start a new dated one."""
    if saved_path:
        try:
            p = Path(saved_path)
            if p.is_dir():
                return open_session(p)
        except SessionError:
            pass
    return create(default_name())
