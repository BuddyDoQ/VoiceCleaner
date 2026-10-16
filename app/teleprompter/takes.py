"""Recording takes while the teleprompter runs.

* :class:`TakeRecorder` records one input without a break into a raw float32 file for the
  whole run, so no syllable is lost at a section change and a crash keeps what was said.
* A take is one or more frame ranges of that recording (pausing the prompter pauses the
  take). :func:`snap_cut` moves each cut to the quietest moment near it, so a last word
  that runs past the section change stays in its take and is never repeated in the next.
* :class:`TakeBook` is the session's record (``teleprompter.json``): the script, every
  take of every unit, which take is selected (a "keeper" if marked, else the newest),
  and each take's enhanced version once the background enhancer has made it.
"""
from __future__ import annotations

import json
import threading
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import soundfile as sf

from ..audio.recorder import BLOCK_FRAMES, RECORD_SR, InputSource, RecordingError, _co_initialize, _soundcard, _to_db
from ..audio.playback_routing import audio_lifecycle
from ..utils.logging import get_logger
from .script import Unit, file_label

log = get_logger("teleprompter")

FADE_S = 0.005  # at take ends and between the parts of a paused take
BOOK_FILE = "teleprompter.json"
_SHARED: dict = {}


# ------------------------------------------------------------------------- capture
class TakeRecorder:
    """Records one input (mono, 48 kHz) into ``raw_path`` until stopped.

    ``blocks`` replaces the device for tests: a callable that, given a stop event, yields
    float32 blocks (frames x channels) in real time."""

    def __init__(self, raw_path: Path, source: InputSource | None = None, gain: float = 1.0,
                 blocks: Callable[[threading.Event], Iterator[np.ndarray]] | None = None):
        if source is None and blocks is None:
            raise RecordingError("Choose a microphone to record from.")
        self.raw_path = Path(raw_path)
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self.source, self.gain, self._blocks = source, gain, blocks
        self.sr = RECORD_SR
        self.frames = 0
        self.error = ""
        self._peak = self._rms = self.max_peak = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.start_time: float | None = None

    def start(self):
        self.raw_path.write_bytes(b"")
        self._thread = threading.Thread(target=self._run, name="teleprompter-rec", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def capturing(self) -> bool:
        return self.frames > 0

    def levels(self) -> tuple[float, float]:
        """(rms_db, peak_db) since the previous call."""
        with self._lock:
            out = (_to_db(self._rms), _to_db(self._peak))
            self._peak = 0.0
        return out

    def _device_blocks(self, stop: threading.Event) -> Iterator[np.ndarray]:
        sc = _soundcard()
        _co_initialize()
        mic = sc.get_microphone(self.source.id, include_loopback=self.source.kind == "desktop")
        channels = max(1, min(2, self.source.channels))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            recorder = mic.recorder(samplerate=RECORD_SR, channels=channels, blocksize=BLOCK_FRAMES)
            with audio_lifecycle.stream(recorder) as rec:
                while not stop.is_set():
                    yield np.asarray(rec.record(numframes=BLOCK_FRAMES * 5), dtype=np.float32)

    def _run(self):
        with audio_lifecycle.recording():
            self._run_guarded()

    def _run_guarded(self):
        gen = None
        try:
            gen = (self._blocks or self._device_blocks)(self._stop)
            with open(self.raw_path, "ab") as f:
                for block in gen:
                    if self._stop.is_set():
                        break
                    block = np.asarray(block, np.float32)
                    mono = (block.mean(axis=1) if block.ndim == 2 else block) * self.gain
                    if self.start_time is None:
                        self.start_time = time.perf_counter() - mono.shape[0] / self.sr
                    f.write(np.ascontiguousarray(mono, dtype="<f4").tobytes())
                    f.flush()
                    peak = float(np.abs(mono).max(initial=0.0))
                    with self._lock:
                        self.frames += mono.shape[0]
                        self._peak = max(self._peak, peak)
                        self.max_peak = max(self.max_peak, peak)
                        self._rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        except Exception as exc:  # noqa: BLE001 - reported in the prompter
            log.warning("Teleprompter capture stopped: %s", exc)
            name = self.source.name if self.source else "the input"
            self.error = (f"Recording from “{name}” stopped unexpectedly. The device may have been "
                          "disconnected or is in use by another program.")
        finally:
            # Breaking a generator loop must close its native recorder before the
            # lifecycle guard is released, even when the capture thread failed.
            close = getattr(gen, "close", None)
            if close is not None:
                close()

    def read(self, a: int, b: int) -> np.ndarray:
        """Frames ``a``..``b`` (clamped to what has been recorded)."""
        a, b = max(0, int(a)), min(int(b), self.frames)
        if b <= a:
            return np.zeros(0, np.float32)
        with open(self.raw_path, "rb") as f:
            f.seek(a * 4)
            return np.frombuffer(f.read((b - a) * 4), dtype="<f4").astype(np.float32)


def snap_cut(rec: TakeRecorder, frame: int, before_s: float = 0.8, after_s: float = 0.8) -> int:
    """The quietest moment within ``before_s`` / ``after_s`` of ``frame``: the middle of the
    longest pause there, else the quietest 10 ms."""
    sr = rec.sr
    a = max(0, frame - int(before_s * sr))
    b = min(rec.frames, frame + int(after_s * sr))
    x = rec.read(a, b)
    hop = sr // 100
    n = x.shape[0] // hop
    if n < 3:
        return min(frame, rec.frames)
    lv = 10 * np.log10(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    # a pause: near the quietest level here, and clearly below the speech around it
    quiet = lv <= min(max(np.percentile(lv, 15) + 6.0, lv.max() - 40.0), lv.max() - 20.0)
    best, run_start, best_len = None, None, 0
    for i, q in enumerate(np.append(quiet, False)):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            if i - run_start > best_len:
                best, best_len = (run_start + i) // 2, i - run_start
            run_start = None
    if best is None:
        best = int(np.argmin(lv))
    return a + best * hop + hop // 2


def render_take(rec: TakeRecorder, parts: list[tuple[int, int]]) -> np.ndarray:
    """The take's audio: its parts joined with short fades (no clicks at pauses)."""
    k = max(1, int(FADE_S * rec.sr))
    pieces = []
    for a, b in parts:
        x = rec.read(a, b).copy()
        if x.shape[0] > 2 * k:
            ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
            x[:k] *= ramp
            x[-k:] *= ramp[::-1]
        pieces.append(x)
    return np.concatenate(pieces) if pieces else np.zeros(0, np.float32)


# ------------------------------------------------------------------------- the session's record
@dataclass
class TakeInfo:
    file: str  # take file name in the session's Recordings folder
    unit: str
    number: int
    created: float = 0.0
    duration: float = 0.0
    keeper: bool = False
    enhanced: str = ""  # file name in the session's Exports folder, once enhanced


@dataclass
class TakeBook:
    """Everything the teleprompter recorded in one session, saved as teleprompter.json."""
    session_dir: Path
    script: str = ""
    mode: str = "section"  # "section" | "paragraph"
    units: list[dict] = field(default_factory=list)  # [{"id", "title"}] in script order
    takes: list[TakeInfo] = field(default_factory=list)
    # the prompter adds takes while the background enhancer records results
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    @classmethod
    def shared(cls, session_dir: Path) -> "TakeBook":
        """The one book of this session that everything writes to (the prompter, the
        background enhancer, Compile), so no one overwrites another's changes."""
        key = str(Path(session_dir).resolve()).lower()
        book = _SHARED.get(key)
        if book is None:
            book = _SHARED[key] = cls.load(session_dir)
        return book

    @classmethod
    def load(cls, session_dir: Path) -> "TakeBook":
        book = cls(Path(session_dir))
        f = book.path
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                book.script = str(data.get("script", ""))
                book.mode = data.get("mode", "section") if data.get("mode") in ("section", "paragraph") else "section"
                book.units = [u for u in data.get("units", []) if isinstance(u, dict) and "id" in u]
                book.takes = [TakeInfo(**{k: v for k, v in t.items() if k in TakeInfo.__dataclass_fields__})
                              for t in data.get("takes", []) if isinstance(t, dict)]
            except (ValueError, TypeError) as exc:
                log.warning("Could not read %s: %s", f, exc)
        return book

    @property
    def path(self) -> Path:
        return self.session_dir / BOOK_FILE

    def save(self):
        with self._lock:
            self._save()

    def _save(self):
        data = {"app": "VoiceCleaner", "script": self.script, "mode": self.mode, "units": self.units,
                "takes": [asdict(t) for t in self.takes]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set_script(self, script: str, mode: str, units: list[Unit]):
        self.script, self.mode = script, mode
        self.units = [{"id": u.id, "title": u.title, "label": u.label} for u in units]

    # --- takes ---------------------------------------------------------------------
    def takes_of(self, unit_id: str) -> list[TakeInfo]:
        return [t for t in self.takes if t.unit == unit_id]

    def next_number(self, unit_id: str) -> int:
        return max((t.number for t in self.takes_of(unit_id)), default=0) + 1

    def add(self, take: TakeInfo):
        with self._lock:
            self.takes.append(take)
            self._save()

    def selected(self, unit_id: str) -> TakeInfo | None:
        """The take used for the compilation: the newest keeper, else the newest take."""
        ts = self.takes_of(unit_id)
        keepers = [t for t in ts if t.keeper]
        pool = keepers or ts
        return max(pool, key=lambda t: (t.number, t.created), default=None)

    def select(self, unit_id: str, number: int):
        """Use take ``number`` for this unit (it becomes its keeper)."""
        for t in self.takes_of(unit_id):
            t.keeper = t.number == number
        self.save()

    def mark_keeper(self, unit_id: str, number: int | None = None):
        """Mark a take (default: the newest) as a keeper: later retakes don't replace it."""
        ts = self.takes_of(unit_id)
        if not ts:
            return
        target = number if number is not None else max(t.number for t in ts)
        for t in ts:
            t.keeper = t.number == target
        self.save()

    def set_enhanced(self, take_file: str, enhanced_file: str):
        with self._lock:
            for t in self.takes:
                if t.file == take_file:
                    t.enhanced = enhanced_file
            self._save()

    def compilation(self, recordings: Path, exports: Path) -> list[tuple[dict, TakeInfo, Path]]:
        """``(unit, selected take, file)`` in script order: the enhanced take when it exists,
        else the recording. Units without takes are left out."""
        out = []
        for u in self.units:
            t = self.selected(u["id"])
            if t is None:
                continue
            enhanced = exports / t.enhanced if t.enhanced else None
            path = enhanced if enhanced is not None and enhanced.exists() else recordings / t.file
            if path.exists():
                out.append((u, t, path))
        return out


def take_file_name(session_name: str, unit: Unit, number: int) -> str:
    return f"{session_name} - {unit.id} {file_label(unit.label)} - Take {number}.wav"


def write_take(path: Path, audio: np.ndarray, sr: int = RECORD_SR):
    sf.write(str(path), audio, sr, subtype="PCM_24")
