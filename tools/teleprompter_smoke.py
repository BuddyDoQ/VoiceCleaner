"""Drive the teleprompter end to end with a fake microphone that "speaks" test speech in
real time: start, next section, retake, next section, stop. Checks the takes, Compile's
script order and selected takes, and background enhancing. Saves screenshots.

Usage: python tools/teleprompter_smoke.py <screenshot_dir>
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="vc_appdata_")
os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="vc_profile_")
os.environ["HOME"] = os.environ["USERPROFILE"]
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.audio.recorder import InputSource  # noqa: E402
from app.teleprompter import window as tw  # noqa: E402
from app.teleprompter.takes import TakeBook, TakeRecorder  # noqa: E402
from app.ui import theme  # noqa: E402
from app.utils.config import UserConfig  # noqa: E402
from app.utils.logging import setup_logging  # noqa: E402

SR = 48_000
SCRIPT = """## 1 · Cold Open | The Hook
[Look into the camera]
Howdy, Brian with Steamburger Studios here. Listen to this.

## 2 · The Why | Why I Built It
Here's the problem. I record a lot of voice work, and every tool wanted a subscription.

## 3 · Close | Outro
Grab it, break it, improve it. Y'all take care.
"""


def speech_stream():
    """0.6 s pause, A, pause, B, pause, B again (the retake), pause, C, pause."""
    x, sr = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")
    assert sr == SR
    a, b, c = x[int(1.6 * SR): int(4.4 * SR)], x[int(4.6 * SR): int(7.4 * SR)], x[int(7.6 * SR): int(10.6 * SR)]
    gap = lambda s: np.random.default_rng(1).standard_normal(int(s * SR)).astype(np.float32) * 2e-4  # noqa: E731
    parts = [gap(0.6), a, gap(1.0), b, gap(1.0), b, gap(1.0), c, gap(1.4)]
    marks, t = [], 0.0
    for p in parts:
        t += len(p) / SR
        marks.append(t)
    return np.concatenate(parts), marks


AUDIO, MARKS = speech_stream()


def fake_blocks(stop):
    """Real-time playback of AUDIO as if from a microphone, then silence."""
    n, block = 0, 2400
    t0 = time.perf_counter()
    while not stop.is_set():
        due = int((time.perf_counter() - t0) * SR)
        while n + block <= due:
            chunk = AUDIO[n: n + block] if n < len(AUDIO) else np.zeros(block, np.float32)
            if len(chunk) < block:
                chunk = np.pad(chunk, (0, block - len(chunk)))
            yield chunk[:, None]
            n += block
        time.sleep(0.005)


def pump(app, cond, timeout=60):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(10):
        app.processEvents()


def wait(app, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "night")
    # the fake microphone, offered in the prompter's list
    fake_mic = InputSource("fake", "Test speech (fake microphone)", "microphone", 1, True)
    tw.list_sources = lambda: ([fake_mic], [])
    tw.TeleprompterWindow._make_recorder = lambda self, raw, src: TakeRecorder(raw, src, blocks=fake_blocks)

    from app.ui.main_window import MainWindow

    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    cfg = UserConfig.load()
    cfg.extra["teleprompter"] = {"countdown": False, "advance": "step", "last_script": SCRIPT, "help_hidden": False}
    w = MainWindow(cfg)
    w.resize(1360, 880)
    w.show()
    pump(app, lambda: w._mm_ready.is_set(), 120)
    check("top bar button: closed", w.prompter_btn.property("state") == "" and "TELEPROMPTER" in w.prompter_btn.text())
    w.prompter_btn.click()
    p = w.prompter
    check("top bar button opens it", p is not None and w.prompter_btn.property("state") == "open")
    w.prompter_btn.click()
    check("clicking again keeps one window", w.prompter is p)
    p.resize(1200, 760)
    pump(app, lambda: p.isVisible())
    p.cd_btn.setChecked(False)
    p.mode_btn.setChecked(True)
    p._toggle_advance()
    check("script loaded with 3 sections", [u.id for u in p.stage.units] == ["01", "02", "03"])
    p.grab().save(str(out / "1_prompter_ready.png"))

    p.toggle_play()  # start: records from the fake microphone
    pump(app, lambda: p.playing, 20)
    t0 = time.perf_counter() - p.recorder.frames / SR  # wall time of the stream's start
    check("recording with REC shown", p.recording and "REC" in p.rec_label.text())
    check("top bar shows REC", w.prompter_btn.property("state") == "rec" and "REC" in w.prompter_btn.text())
    w.grab().save(str(out / "0_top_bar_rec.png"))

    def at(i, extra=0.5):  # wait until the stream is past mark i
        while time.perf_counter() - t0 < MARKS[i] - extra:
            app.processEvents()
            time.sleep(0.01)

    at(2)  # A said, in the pause after it
    p.next_unit()
    wait(app, 0.3)
    p.grab().save(str(out / "2_prompter_recording.png"))
    at(4)  # B said once, in the pause: fluffed it -> retake
    p.retake()
    at(6)  # B said again
    p.next_unit()
    at(8, extra=0.3)  # C said
    p.stop()
    pump(app, lambda: not p.recording and len(TakeBook.load(w.session.path).takes) >= 4, 15)
    book = TakeBook.load(w.session.path)
    got = [(t.unit, t.number, round(t.duration, 1)) for t in book.takes]
    print("     takes:", got)
    check("4 takes: 01, 02 (twice), 03", [(u, n) for u, n, _ in got] == [("01", 1), ("02", 1), ("02", 2), ("03", 1)])
    for t in book.takes:
        x, _ = sf.read(w.session.recordings_dir / t.file)
        print(f"     {t.file}: {len(x) / SR:.2f} s")
    check("the retake is the selected one", book.selected("02").number == 2)
    durations = {(t.unit, t.number): t.duration for t in book.takes}
    # each take: its speech (2.8-3 s) plus about half of the pauses around it
    check("takes match what was said (speech plus half the pauses)", all(3.0 < d < 5.3 for d in durations.values()))

    # Compile: script order, selected takes, enhanced in the background
    pump(app, lambda: w.take_enhancer.pending == 0, 180)
    book = TakeBook.load(w.session.path)
    check("every take was enhanced", all(t.enhanced for t in book.takes))
    w.tabs.setCurrentWidget(w.compile_panel)
    cp = w.compile_panel
    pump(app, lambda: cp.script_mode and cp.list.count() == 3, 20)
    titles = [cp.list.item(i).data(cp_title_role(cp)) for i in range(cp.list.count())]
    print("     compile rows:", titles)
    check("Compile lists one row per section in script order",
          [t.split(" · ")[0] for t in titles] == ["01", "02", "03"])
    check("section 2 uses take 2 of 2", "Take 2 of 2" in titles[1])
    check("rows use the enhanced takes", all(Path(cp.paths()[i]).parent == w.session.exports_dir for i in range(3)))
    pump(app, lambda: cp.preview_ready, 30)
    w.grab().save(str(out / "3_compile_script.png"))
    cp._use_take("02", 1)
    pump(app, lambda: "Take 1 of 2" in cp.list.item(1).data(cp_title_role(cp)), 10)
    check("choosing take 1 for section 2 updates the row", True)
    p.close()

    # ---- a microphone that sends only silence, and closing the window mid-recording
    errors = []
    import traceback

    def hook(*exc):
        errors.append(exc)
        traceback.print_exception(*exc)

    sys.excepthook = hook
    silent = lambda stop: (np.zeros((2400, 1), np.float32) for _ in iter(lambda: stop.wait(0.05) or True, False))  # noqa: E731
    tw.TeleprompterWindow._make_recorder = lambda self, raw, src: TakeRecorder(raw, src, blocks=silent)
    before = len(TakeBook.shared(w.session.path).takes)
    w.open_teleprompter()
    p = w.prompter
    p.cd_btn.setChecked(False)
    p.toggle_play()
    wait(app, 1.0)
    pump(app, lambda: p.playing, 20)
    wait(app, 2.6)
    check("a silent microphone is reported", "NO SOUND" in p.rec_label.text() and p.status.isVisible()
          and "No sound from" in p.status.text())
    p.grab().save(str(out / "4_no_sound.png"))
    p.close()  # while recording
    wait(app, 1.0)
    check("closing mid-recording saves the take", len(TakeBook.shared(w.session.path).takes) == before + 1)
    check("closing mid-recording raises no error", not errors)
    check("the window is gone", w.prompter is None)
    check("top bar button back to closed", w.prompter_btn.property("state") == "")
    w.close()
    print("ALL PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


def cp_title_role(cp):
    from app.ui.compile_panel import TITLE_ROLE

    return TITLE_ROLE


if __name__ == "__main__":
    main()
