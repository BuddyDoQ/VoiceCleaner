"""Generate the README screenshots (dark and light) and the GitHub social preview image.

Runs the real app with a clean, temporary profile and demo content (the test speech, a
demo session called "Episode 12", a teleprompter script), so no personal folders, session
or device names appear. Re-run it for each release:

    python tools/screenshots.py [output_dir]      (default: docs/screenshots)

Writes <name>-dark.png / <name>-light.png for: enhance, teleprompter, compile, batch,
settings, update; plus social-preview.png (1280x640) and teleprompter.gif.
"""
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
    os.environ[var] = tempfile.mkdtemp(prefix=f"vc_shots_{var.lower()}_")
os.environ["HOME"] = os.environ["USERPROFILE"]
os.environ["VOICECLEANER_NO_UPDATE_CHECK"] = "1"
os.environ["VOICECLEANER_NO_TOUR"] = "1"  # the Getting Started tour would cover the window

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SR = 48_000
SIZE = (1440, 900)
PROMPTER_SIZE = (1440, 860)
SCRIPT = """## 1 · Cold Open | The Hook
[B-roll: A/B toggle, noisy waveform snapping clean]
Listen to this. Now listen to this. Same recording, one click. No subscription, no upload: that's VoiceCleaner.

## 2 · The Why | Why I Built It
[Talking head]
I record a lot of voice work: narration, devlogs, character lines. Every good enhancement tool wanted a monthly subscription, or my audio in somebody's cloud.

## 3 · Close | Free & Open Source
VoiceCleaner is free and open source. It runs on your own computer, on the CPU or an NVIDIA GPU. Grab it, break it, improve it.
"""


def pump(app, cond, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError
    for _ in range(15):
        app.processEvents()


def wait(app, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def to_pil(pixmap) -> Image.Image:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    pixmap.save(buf, "PNG")
    import io

    return Image.open(io.BytesIO(bytes(ba.data()))).convert("RGB")


def save(img: Image.Image, path: Path):
    """PNG, reduced to a palette when that keeps it sharp but much smaller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pal = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal.save(path, optimize=True)
    print(f"  {path.name}: {img.size[0]}x{img.size[1]}, {path.stat().st_size / 1024:.0f} KB")


# ------------------------------------------------------------------------- demo audio and mic
def speech():
    x, sr = sf.read(ROOT / "test_audio" / "reference" / "clean_speech.wav", dtype="float32")
    assert sr == SR
    return x


def mic_stream():
    """What the fake microphone says: three short sections with pauses (and a retake)."""
    x = speech()
    a, b, c = x[int(1.6 * SR): int(4.4 * SR)], x[int(4.6 * SR): int(7.4 * SR)], x[int(7.6 * SR): int(10.6 * SR)]
    gap = lambda s: np.random.default_rng(1).standard_normal(int(s * SR)).astype(np.float32) * 3e-4  # noqa: E731
    parts = [gap(0.6), a, gap(1.0), b, gap(1.0), b, gap(1.0), c, gap(2.0)]
    marks, t = [], 0.0
    for p in parts:
        t += len(p) / SR
        marks.append(t)
    return np.concatenate(parts), marks


AUDIO, MARKS = mic_stream()


def fake_blocks(stop):
    n, block, t0 = 0, 2400, time.perf_counter()
    while not stop.is_set():
        due = int((time.perf_counter() - t0) * SR)
        while n + block <= due:
            chunk = AUDIO[n: n + block] if n < len(AUDIO) else np.zeros(block, np.float32)
            if len(chunk) < block:
                chunk = np.pad(chunk, (0, block - len(chunk)))
            yield chunk[:, None]
            n += block
        time.sleep(0.005)


# ------------------------------------------------------------------------- a local "newer release"
class Feed:
    def __init__(self, version: str, notes: str):
        payload = os.urandom(2_000_000)
        name = f"VoiceCleaner-{version}-win64.msi"
        files = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        base = f"http://127.0.0.1:{self.httpd.server_port}"
        files["/" + name] = payload
        files["/latest.json"] = json.dumps({
            "tag_name": f"v{version}", "html_url": base + "/", "published_at": "2026-10-12T00:00:00Z",
            "body": notes, "assets": [{"name": name, "browser_download_url": f"{base}/{name}", "size": 188_000_000,
                                       "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}]}).encode()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        os.environ["VOICECLEANER_UPDATE_FEED"] = base + "/latest.json"


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index(f"## {version}")
    end = text.find("\n## ", start + 5)
    body = text[start:end if end > 0 else None].split("\n", 1)[1]
    return f"### What's new in {version}\n{body}"


# ------------------------------------------------------------------------- one pass per theme
def run_theme(app, mode: str, out: Path, shots: dict, gif: bool):
    from app import sessions
    from app.audio.recorder import InputSource
    from app.teleprompter import window as tw
    from app.teleprompter.takes import TakeRecorder
    from app.ui import theme
    from app.ui import update_dialog as ud
    from app.ui.main_window import MainWindow
    from app.utils import update_install
    from app.utils.config import APP_VERSION, UserConfig

    suffix = "dark" if mode == "night" else "light"
    print(f"{suffix}:")
    theme.apply(app, mode)
    fake_mic = InputSource("demo", "USB Microphone", "microphone", 1, True)
    tw.list_sources = lambda: ([fake_mic], [])
    tw.TeleprompterWindow._make_recorder = lambda self, raw, src: TakeRecorder(raw, src, blocks=fake_blocks)

    cfg = UserConfig.load()
    cfg.extra.update(theme=mode, recent_files=[], smart_note_hidden=False,
                     teleprompter={"countdown": False, "advance": "step", "last_script": SCRIPT, "help_hidden": True,
                                   "theme": "auto", "size": 50})
    cfg.window_geometry = ""
    w = MainWindow(cfg)
    w.resize(*SIZE)
    w.show()
    pump(app, lambda: w._mm_ready.is_set())
    session = sessions.create("Episode 12")
    w._switch_session(session)

    # 1 + 5: Enhance after enhancing; the settings sidebar close-up
    rec = session.recording_path()
    x, _ = sf.read(ROOT / "test_audio" / "speech_multiple_noises.wav", dtype="float32")
    sf.write(rec, x, SR, subtype="PCM_24")
    w.open_file(rec)
    pump(app, lambda: w.audio is not None and not w._task_running())
    w.enhance()
    pump(app, lambda: w.result is not None and not w._task_running(), 300)
    w.player.seek(7.2)
    w._on_position(7.2)
    wait(app, 0.5)
    shots.setdefault("enhance", {})[suffix] = to_pil(w.grab())
    side = to_pil(w.sidebar.grab())
    shots.setdefault("settings", {})[suffix] = side.crop((0, 0, side.width, min(side.height, 640)))

    # 2: Teleprompter while recording (+ the GIF, dark only)
    w.open_teleprompter()
    p = w.prompter
    p.resize(*PROMPTER_SIZE)
    pump(app, lambda: p.isVisible())
    p.cd_btn.setChecked(False)
    p.toggle_play()
    pump(app, lambda: p.playing, 30)
    t0 = time.perf_counter() - p.recorder.frames / SR

    def at(i, extra=0.5):
        while time.perf_counter() - t0 < MARKS[i] - extra:
            app.processEvents()
            time.sleep(0.01)

    frames = []
    at(2)
    p.next_unit()
    if gif:  # the section change, as it scrolls
        for _ in range(14):
            wait(app, 0.07)
            frames.append(to_pil(p.grab()).resize((PROMPTER_SIZE[0] // 2, PROMPTER_SIZE[1] // 2), Image.LANCZOS))
    wait(app, 1.2)
    p._poke()
    wait(app, 0.3)
    shots.setdefault("teleprompter", {})[suffix] = to_pil(p.grab())
    at(4)
    p.retake()
    at(6)
    p.next_unit()
    at(8, extra=0.3)
    p.stop()
    pump(app, lambda: not p.recording and w.take_enhancer.pending == 0, 180)
    wait(app, 1.0)
    p.close()
    if gif and frames:
        frames[0].save(out / "teleprompter.gif", save_all=True, append_images=frames[1:] + [frames[-1]] * 8,
                       duration=80, loop=0, optimize=True)
        print(f"  teleprompter.gif: {(out / 'teleprompter.gif').stat().st_size / 1024:.0f} KB")

    # 3: Compile with the teleprompter takes in script order
    w.tabs.setCurrentWidget(w.compile_panel)
    cp = w.compile_panel
    pump(app, lambda: cp.script_mode and cp.list.count() == 3, 60)
    pump(app, lambda: cp.preview_ready, 60)
    wait(app, 0.5)
    shots.setdefault("compile", {})[suffix] = to_pil(w.grab())

    # 4: Batch with the settings sidebar, after a run
    takes = []
    for i, (a, b) in enumerate(((0.0, 9.0), (9.0, 18.0), (4.0, 14.0)), 1):
        f = session.path / f"Interview part {i}.wav"
        sf.write(f, x[int(a * SR): int(b * SR)], SR, subtype="PCM_24")
        takes.append(f)
    w.tabs.setCurrentWidget(w.batch_page)
    w.batch_panel.add_files(takes)
    w.batch_panel._start()
    pump(app, lambda: w._batch is None and w.batch_panel.outputs, 300)
    wait(app, 0.5)
    shots.setdefault("batch", {})[suffix] = to_pil(w.grab())

    # 6: the update window, downloaded and verified
    w.tabs.setCurrentIndex(0)
    ud.APP_VERSION = "1.0.9"  # as seen by someone on the previous version
    update_install.install_dir = lambda: Path(tempfile.mkdtemp())
    Feed(APP_VERSION, changelog_section(APP_VERSION))
    import importlib

    from app.utils import updates

    importlib.reload(updates)  # picks up the local feed
    newer = updates.is_newer
    updates.is_newer = lambda c, current="1.0.9": newer(c, current)  # someone on the previous version
    ud.updates = updates
    w.check_for_updates(manual=True)
    pump(app, lambda: any(isinstance(t, ud.UpdateDialog) and t.isVisible() for t in app.topLevelWidgets()), 30)
    dlg = next(t for t in app.topLevelWidgets() if isinstance(t, ud.UpdateDialog))
    dlg.resize(700, 560)
    dlg.action_btn.click()
    pump(app, lambda: dlg.installer is not None, 60)
    # the stand-in file is small; show the size of the real installer
    dlg.state.setText(dlg.state.text().replace(f"({dlg.installer.stat().st_size / 1e6:.0f} MB)", "(188 MB)"))
    wait(app, 0.3)
    shots.setdefault("update", {})[suffix] = to_pil(dlg.grab())
    dlg.reject()
    w.close()


def social_preview(hero: Image.Image, out: Path):
    """1280x640 card: logo, name, tagline, and the app."""
    from app.ui import theme

    theme.apply(QApplication.instance(), "night")
    W, H = 1280, 640
    bg = Image.new("RGB", (W, H), theme.NIGHT["BG"])
    d = ImageDraw.Draw(bg)
    fonts = ROOT / "app" / "ui" / "assets" / "fonts"
    disp = ImageFont.truetype(str(fonts / "BebasNeue-Regular.ttf"), 92)
    mono = ImageFont.truetype(str(fonts / "DMMono-Regular.ttf"), 20)
    body = ImageFont.truetype(str(fonts / "Syne-Variable.ttf"), 26)
    from app.ui.brand import logo_pixmap

    pm = logo_pixmap(120, dpr=1)
    import io

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    logo = Image.open(io.BytesIO(bytes(ba.data()))).convert("RGBA")
    bg.paste(logo, (64, 70), logo)
    d.text((70, 214), "STEAMBURGER STUDIOS", font=mono, fill=theme.NIGHT["ACCENT"])
    d.text((64, 238), "VOICECLEANER", font=disp, fill=theme.NIGHT["TEXT"])
    lines = ["Clean, clear speech from noisy", "recordings. Record, enhance,", "compile. Free, open source,",
             "runs on your computer."]
    for i, line in enumerate(lines):
        d.text((70, 352 + i * 38), line, font=body, fill=theme.NIGHT["MUTED"])
    # the app, tilted out of the right edge
    shot = hero.copy()
    shot.thumbnail((760, 475), Image.LANCZOS)
    frame = Image.new("RGB", (shot.width + 8, shot.height + 8), theme.NIGHT["BORDER_STRONG"])
    frame.paste(shot, (4, 4))
    bg.paste(frame, (W - frame.width + 70, (H - frame.height) // 2 + 30))
    save(bg, out / "social-preview.png")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    from app.utils.logging import setup_logging

    setup_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    shots: dict = {}
    run_theme(app, "night", out, shots, gif=True)
    for var in ("APPDATA", "USERPROFILE"):  # a fresh profile: the same demo session name again
        os.environ[var] = tempfile.mkdtemp(prefix=f"vc_shots_{var.lower()}_")
    os.environ["HOME"] = os.environ["USERPROFILE"]
    run_theme(app, "day", out, shots, gif=False)
    for name, pair in shots.items():
        for suffix, img in pair.items():
            save(img, out / f"{name}-{suffix}.png")
    social_preview(shots["enhance"]["dark"], out)


if __name__ == "__main__":
    main()
