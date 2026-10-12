"""Teleprompter scripts: the format of the original HTML prompter, and the take units.

Script format (plain text or Markdown):

    ## Heading | Clip name     a section; the clip name (optional) names its takes
    [text in brackets]         a stage direction (shown, never read, never in a take name)
    blank line                 a new paragraph

A *unit* is what one take covers: a whole section, or (line-by-line mode) one paragraph.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CUE = re.compile(r"\[([^\]]+)\]")


@dataclass
class Block:
    kind: str  # "para" | "cue"
    text: str

    @property
    def words(self) -> int:
        return len(CUE.sub("", self.text).split()) if self.kind == "para" else 0


@dataclass
class Section:
    index: int
    title: str
    clip: str = ""
    blocks: list[Block] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(b.words for b in self.blocks)

    @property
    def name(self) -> str:
        """What its takes are called: the clip name if given, else the title."""
        return self.clip or self.title


@dataclass(frozen=True)
class Unit:
    """What one take covers."""
    id: str  # "03" (section) or "03.02" (paragraph): stable while the script keeps its shape
    section: int
    paragraph: int | None  # None: the whole section
    title: str  # shown in the prompter and in Compile
    label: str  # used in file names
    words: int


@dataclass
class Script:
    sections: list[Section]
    text: str = ""

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)

    def units(self, mode: str = "section") -> list[Unit]:
        """Take units in script order. ``mode``: "section" or "paragraph"."""
        out = []
        for s in self.sections:
            if mode == "paragraph":
                paras = [b for b in s.blocks if b.kind == "para"]
                for i, b in enumerate(paras, 1):
                    first = " ".join(CUE.sub("", b.text).split()[:6])
                    out.append(Unit(f"{s.index + 1:02d}.{i:02d}", s.index, i - 1,
                                    f"{s.title} · {i}/{len(paras)}", f"{s.name} - {i:02d} {first}", b.words))
            else:
                out.append(Unit(f"{s.index + 1:02d}", s.index, None, s.title, s.name, s.words))
        return out


def parse(text: str) -> Script:
    """Parse a script. Text before the first ``##`` heading becomes an untitled section."""
    sections: list[Section] = []
    para: list[str] = []

    def current() -> Section:
        if not sections:
            sections.append(Section(0, "Script"))
        return sections[-1]

    def flush():
        if para:
            current().blocks.append(Block("para", " ".join(para)))
            para.clear()

    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("## "):
            flush()
            title, _, clip = line[3:].partition("|")
            sections.append(Section(len(sections), title.strip() or f"Section {len(sections) + 1}", clip.strip()))
        elif re.fullmatch(r"\[.*\]", line):
            flush()
            current().blocks.append(Block("cue", line))
        else:
            para.append(line)
    flush()
    # a heading with nothing to read is still a section (it may hold only directions)
    return Script(sections, text)


def file_label(text: str, limit: int = 48) -> str:
    """``text`` made safe for a file name, shortened."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", CUE.sub("", text))
    clean = " ".join(clean.replace("·", "-").split())
    return clean[:limit].rstrip(" .-") or "Take"


SAMPLE = """## 1 · Welcome | Intro
[Look into the camera, smile]
Hi, and welcome. This is the VoiceCleaner teleprompter. Press Space to start: after a short countdown it records, and every section becomes its own take.

## 2 · How it works | Takes
When the next section reaches the reading line, a new take starts by itself. Made a mistake? Press Backspace to record this section again. Every take is kept, and the newest one goes into the compilation.

## 3 · Wrap up | Outro
Press Escape or Stop when you are done. Your takes are enhanced in the background and wait in the Compile tab, in script order.
"""
