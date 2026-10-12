"""Teleprompter: script parsing, cutting takes from the continuous recording, retakes,
keepers, pauses, and the session's take book."""
import numpy as np
import pytest
import soundfile as sf

from app.teleprompter import script as S
from app.teleprompter.run import PrompterRun
from app.teleprompter.takes import TakeBook, TakeRecorder, snap_cut

SR = 48_000


class FakeRecorder(TakeRecorder):
    """The recorder, fed by the test instead of a microphone."""

    def __init__(self, path):
        super().__init__(path, blocks=lambda stop: iter(()))
        self.raw_path.write_bytes(b"")

    def feed(self, x):
        with open(self.raw_path, "ab") as f:
            f.write(np.asarray(x, "<f4").tobytes())
        self.frames += len(x)


def tone(seconds, f=220.0, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def silence(seconds):
    return (np.random.default_rng(0).standard_normal(int(seconds * SR)) * 1e-4).astype(np.float32)


SCRIPT = """## 1 · Cold Open | The Hook
[B-roll: noisy waveform]
Howdy, this is the first line.

Second paragraph of the opening.

## 2 · The Why
Here's the problem. [beat] I record a lot.
"""


def test_parse_sections_cues_and_units():
    sc = S.parse(SCRIPT)
    assert [s.title for s in sc.sections] == ["1 · Cold Open", "2 · The Why"]
    assert sc.sections[0].clip == "The Hook" and sc.sections[0].name == "The Hook"
    kinds = [b.kind for b in sc.sections[0].blocks]
    assert kinds == ["cue", "para", "para"]
    assert sc.sections[1].words == 7  # the inline [beat] is not read
    units = sc.units()
    assert [(u.id, u.label) for u in units] == [("01", "The Hook"), ("02", "2 · The Why")]
    paras = sc.units("paragraph")
    assert [u.id for u in paras] == ["01.01", "01.02", "02.01"]
    assert S.file_label('Bad: name? <x> [cue] 1 · 2') == "Bad name x 1 - 2"
    assert S.parse("No heading at all.\n\nSecond.").sections[0].title == "Script"


@pytest.fixture
def setup(tmp_path):
    rec = FakeRecorder(tmp_path / "run.f32")
    book = TakeBook(tmp_path)
    units = S.parse(SCRIPT).units()
    book.set_script(SCRIPT, "section", units)
    written = []
    run = PrompterRun(rec, book, "Ep 1", tmp_path, on_take=lambda info, path: written.append((info, path)))
    return rec, book, units, run, written


def test_cut_snaps_to_the_pause_near_a_section_change(setup):
    rec, book, units, run, written = setup
    run.begin(units[0])
    rec.feed(tone(2.0))
    rec.feed(silence(0.5))  # the pause between sections: 2.0 .. 2.5 s
    rec.feed(tone(0.3))
    # the prompter moved on a bit late, while the next section had already started
    run.next_unit(units[1])
    run.poll()
    assert not written  # waits until the audio after the change exists
    rec.feed(tone(1.5))
    run.poll()
    assert len(written) == 1
    info, path = written[0]
    x, sr = sf.read(path)
    assert 2.1 < len(x) / sr < 2.45  # ends inside the pause, not in the next section's words
    assert info.unit == "01" and info.number == 1 and path.name == "Ep 1 - 01 The Hook - Take 1.wav"
    rec.feed(silence(0.2))
    run.stop()
    assert len(written) == 2 and written[1][0].unit == "02"
    y, _ = sf.read(written[1][1])
    assert abs((len(x) + len(y)) / SR - rec.frames / SR) < 0.01  # nothing lost, nothing doubled


def test_retake_keeps_every_take_and_selects_the_newest(setup):
    rec, book, units, run, written = setup
    run.begin(units[0])
    rec.feed(tone(1.0))
    rec.feed(silence(0.3))
    run.retake()  # fluffed it
    rec.feed(silence(0.5))
    run.poll()
    rec.feed(tone(1.5))
    run.stop()
    takes = book.takes_of("01")
    assert [t.number for t in takes] == [1, 2]
    assert book.selected("01").number == 2  # the newest
    book.mark_keeper("01", 1)
    assert book.selected("01").number == 1  # a keeper wins over later takes
    book.select("01", 2)
    assert book.selected("01").number == 2


def test_pause_leaves_the_paused_audio_out(setup):
    rec, book, units, run, written = setup
    run.begin(units[0])
    rec.feed(tone(1.0))
    run.pause()
    rec.feed(tone(3.0, f=900))  # talking to someone while paused
    run.resume()
    rec.feed(tone(1.0))
    run.stop()
    x, _ = sf.read(written[0][1])
    assert abs(len(x) / SR - 2.0) < 0.01


def test_stray_tiny_takes_are_not_kept(setup):
    rec, book, units, run, written = setup
    run.begin(units[0])
    rec.feed(tone(0.1))
    run.stop()
    assert not written and not book.takes


def test_book_round_trip_and_compilation_order(setup, tmp_path):
    rec, book, units, run, written = setup
    run.begin(units[1])  # read section 2 first
    rec.feed(tone(1.0))
    rec.feed(silence(0.6))
    run.next_unit(units[0])
    rec.feed(silence(0.3))
    rec.feed(tone(1.0))
    run.poll()
    run.stop()
    exports = tmp_path / "Exports"
    exports.mkdir()
    enhanced = exports / "enh.wav"
    sf.write(enhanced, tone(0.5), SR)
    book.set_enhanced(book.selected("02").file, "enh.wav")
    again = TakeBook.load(tmp_path)
    assert again.script == SCRIPT and [u["id"] for u in again.units] == ["01", "02"]
    order = again.compilation(tmp_path, exports)
    assert [u["id"] for u, t, p in order] == ["01", "02"]  # script order, not recording order
    assert order[1][2] == enhanced  # the enhanced take when there is one
    assert order[0][2].name.endswith("Take 1.wav")


def test_snap_cut_without_a_pause_picks_the_quietest_point(tmp_path):
    rec = FakeRecorder(tmp_path / "r.f32")
    x = tone(2.0)
    x[int(1.2 * SR): int(1.21 * SR)] *= 0.01  # a 10 ms dip only
    rec.feed(x)
    c = snap_cut(rec, int(1.0 * SR))
    assert abs(c / SR - 1.205) < 0.02
