"""Put the current screenshots into the user manual.

The manual (docs/guide/VoiceCleaner-User-Manual.html) is one self-contained file: every
picture is embedded, in a light and a dark version (class im-light / im-dark). This replaces
each embedded picture whose caption (alt text) starts with a known phrase with the matching
file from tools/screenshots.py, so the manual shows the app as it is now:

    python tools/screenshots.py                 (writes docs/screenshots and docs/screenshots/manual)
    python tools/update_manual_images.py [screenshots_dir]

Pictures with no match (the session menu, the New Session window) are left as they are.
"""
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "guide" / "VoiceCleaner-User-Manual.html"

# caption starts with -> screenshot name (in the screenshots folder, or its manual/ subfolder)
PICTURES = {
    "The Getting Started tour": "manual/tour",
    "The start screen": "manual/start",
    "A recording opened and analyzed": "manual/opened",
    "After Enhance:": "enhance",
    "The export window": "manual/export",
    "The app menu.": "manual/menu",
    "The Enhance tab after enhancing.": "enhance",
    "The top of the settings sidebar.": "settings",
    "The Settings window": "manual/settings-window",
    "The record panel": "manual/record",
    "Recording from the teleprompter": "teleprompter",
    "Compile with the teleprompter": "compile",
    "Batch processing of three files": "batch",
    "The Playback tab": "manual/playback",
    "An update downloaded": "update",
}

IMG = re.compile(r'<img(?P<attrs>[^>]*?)src="data:image/png;base64,(?P<data>[A-Za-z0-9+/=]*)"(?P<rest>[^>]*)>')


def main():
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    html = MANUAL.read_text(encoding="utf-8")
    replaced, kept = 0, 0

    def swap(m):
        nonlocal replaced, kept
        attrs = m.group("attrs") + m.group("rest")
        alt = re.search(r'alt="([^"]*)"', attrs)
        mode = "dark" if "im-dark" in attrs else "light"
        name = next((v for k, v in PICTURES.items() if alt and alt.group(1).startswith(k)), None)
        path = shots / f"{name}-{mode}.png" if name else None
        if path is None or not path.exists():
            kept += 1
            return m.group(0)
        replaced += 1
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'<img{m.group("attrs")}src="data:image/png;base64,{data}"{m.group("rest")}>'

    html = IMG.sub(swap, html)
    MANUAL.write_text(html, encoding="utf-8", newline="\n")
    print(f"{replaced} pictures replaced, {kept} kept")


if __name__ == "__main__":
    main()
