"""One teleprompter run: which unit is being read, and where each take starts and ends.

The prompter tells the run what happens (start, next unit, retake, pause, stop); the run
turns that into takes cut from the continuous recording. A cut is placed only once the
audio around it exists (``poll`` is called a few times a second), so it can move to the
quietest moment nearby.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..utils.logging import get_logger
from .script import Unit
from .takes import TakeBook, TakeInfo, TakeRecorder, render_take, snap_cut, take_file_name, write_take

log = get_logger("teleprompter")

MIN_TAKE_S = 0.4  # shorter takes (a stray key press) are not kept
ADVANCE_WINDOW = (0.8, 0.8)  # seconds searched before/after a section change for the cut
RETAKE_WINDOW = (0.4, 0.3)


@dataclass
class _Take:
    unit: Unit
    parts: list[list[int]] = field(default_factory=list)  # [start, end or None]
    number: int = 0
    keeper: bool = False  # marked during recording (G)


@dataclass
class _Cut:
    frame: int
    window: tuple[float, float]
    closing: _Take
    opening: _Take | None


class PrompterRun:
    def __init__(self, recorder: TakeRecorder, book: TakeBook, session_name: str, recordings: Path,
                 on_take: Callable[[TakeInfo, Path], None] | None = None):
        self.rec, self.book, self.session_name, self.recordings = recorder, book, session_name, recordings
        self.on_take = on_take
        self.current: _Take | None = None
        self.paused = False
        self._cuts: list[_Cut] = []
        self.takes_written: list[TakeInfo] = []

    # --- what the prompter reports ------------------------------------------------------
    def begin(self, unit: Unit):
        """Recording reached the first unit (after the countdown)."""
        self.current = self._new(unit, self.rec.frames)

    def next_unit(self, unit: Unit):
        """The prompter moved on to ``unit`` (scrolled past, stepped, or jumped)."""
        self._cut(unit, ADVANCE_WINDOW)

    def retake(self):
        """Record the current unit again: what was said so far becomes its own take."""
        if self.current is not None:
            self._cut(self.current.unit, RETAKE_WINDOW)

    def pause(self):
        if self.current is not None and not self.paused:
            self.current.parts[-1][1] = self.rec.frames
        self.paused = True

    def resume(self):
        if self.current is not None and self.paused:
            self.current.parts.append([self.rec.frames, None])
        self.paused = False

    def stop(self):
        """End the run: the current take ends now. Call after the recorder has caught up."""
        if self.current is not None:
            if not self.paused:
                self.current.parts[-1][1] = self.rec.frames
            self._cuts = [c for c in self._cuts if c.opening is not self.current]
            pending = list(self._cuts)
            self._cuts.clear()
            for c in pending:  # close whatever is waiting, with the audio there is
                self._finish_cut(c, force=True)
            self._write(self.current)
            self.current = None

    def poll(self):
        """Place cuts whose surroundings have been recorded; write finished takes."""
        for c in list(self._cuts):
            if self.rec.frames >= c.frame + int(c.window[1] * self.rec.sr):
                self._cuts.remove(c)
                self._finish_cut(c)

    # --- internals ------------------------------------------------------------------------
    def _new(self, unit: Unit, frame: int) -> _Take:
        t = _Take(unit, [[frame, None]])
        return t

    def _cut(self, unit: Unit, window):
        if self.current is None:
            self.begin(unit)
            return
        frame = self.rec.frames
        closing = self.current
        if not self.paused:
            closing.parts[-1][1] = frame  # provisional; the cut may move
        opening = self._new(unit, frame)
        if self.paused:
            opening.parts = []  # starts at resume
        self.current = opening
        self._cuts.append(_Cut(frame, window, closing, opening))

    def _finish_cut(self, c: _Cut, force: bool = False):
        at = c.frame
        if not self.paused or force:
            at = snap_cut(self.rec, c.frame, *c.window)
        if c.closing.parts and c.closing.parts[-1][1] == c.frame:
            c.closing.parts[-1][1] = at
        if c.opening is not None and c.opening.parts and c.opening.parts[0][0] == c.frame:
            c.opening.parts[0][0] = at
        self._write(c.closing)

    def _write(self, t: _Take):
        parts = [(a, b if b is not None else self.rec.frames) for a, b in t.parts]
        parts = [(a, b) for a, b in parts if b > a]
        frames = sum(b - a for a, b in parts)
        if frames < MIN_TAKE_S * self.rec.sr:
            return
        number = self.book.next_number(t.unit.id)
        name = take_file_name(self.session_name, t.unit, number)
        path = self.recordings / name
        write_take(path, render_take(self.rec, parts), self.rec.sr)
        info = TakeInfo(file=name, unit=t.unit.id, number=number, created=time.time(), duration=frames / self.rec.sr,
                        keeper=t.keeper)
        self.book.add(info)
        self.takes_written.append(info)
        log.info("Take %s of unit %s: %.1f s -> %s", number, t.unit.id, info.duration, name)
        if self.on_take:
            self.on_take(info, path)
