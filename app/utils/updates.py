"""Checking GitHub for a newer VoiceCleaner release.

One HTTPS GET of the repository's public "latest release" (drafts and pre-releases
are excluded by GitHub). Nothing about the user, the computer or any audio is sent,
and nothing is downloaded or installed automatically: the app only offers a link to
the installer that fits this computer.
"""
from __future__ import annotations

import json
import platform
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import APP_NAME, APP_VERSION, edition

REPO = "BuddyDoQ/VoiceCleaner"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
REMIND_LATER_S = 24 * 3600  # "Later" hides that version from automatic checks this long
TIMEOUT_S = 10


class UpdateError(Exception):
    """A check that could not be completed; the message is shown to the user."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class ReleaseInfo:
    version: str  # "1.0.2"
    tag: str  # "v1.0.2"
    page_url: str
    notes: str = ""
    published: str = ""
    assets: tuple[Asset, ...] = field(default_factory=tuple)


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``. Suffixes such as ``-beta`` are ignored;
    text without a leading number gives ``()``."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not m:
        return ()
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.0 == 1.0.0
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    c = parse_version(candidate)
    return bool(c) and c > parse_version(current)


def parse_release(data: dict) -> ReleaseInfo:
    try:
        tag = str(data["tag_name"])
    except (KeyError, TypeError) as exc:
        raise UpdateError("GitHub returned an unexpected answer.") from exc
    assets = tuple(Asset(str(a.get("name", "")), str(a.get("browser_download_url", "")), int(a.get("size") or 0))
                   for a in data.get("assets") or [] if isinstance(a, dict))
    return ReleaseInfo(version=tag.lstrip("vV"), tag=tag, page_url=str(data.get("html_url") or RELEASES_PAGE),
                       notes=str(data.get("body") or ""), published=str(data.get("published_at") or "")[:10],
                       assets=assets)


def fetch_latest(timeout: float = TIMEOUT_S) -> ReleaseInfo:
    req = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",  # GitHub rejects requests without one
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published release was found.") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub is limiting requests right now. Try again in an hour.") from exc
        raise UpdateError(f"GitHub answered with an error (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Could not reach GitHub. Check your internet connection.") from exc
    except ValueError as exc:
        raise UpdateError("GitHub returned an unexpected answer.") from exc
    return parse_release(data)


def pick_asset(release: ReleaseInfo, plat: str | None = None, arch: str | None = None,
               ed: str | None = None) -> Asset | None:
    """The download that fits this computer, or None (then offer the release page)."""
    plat = plat or sys.platform
    arch = arch or platform.machine()
    ed = ed or edition()
    names = {a.name.lower(): a for a in release.assets}

    def find(pattern: str) -> Asset | None:
        return next((a for n, a in names.items() if re.fullmatch(pattern, n)), None)

    if plat == "darwin":
        mac_arch = "arm64" if arch == "arm64" else "x86_64"
        return find(rf"voicecleaner-[\d.]+-macos-{mac_arch}\.dmg") or find(rf"voicecleaner-[\d.]+-macos-{mac_arch}\.zip")
    if plat == "win32":
        if ed == "gpu":
            return find(r"voicecleaner-gpu-[\d.]+-web\.exe")
        return find(r"voicecleaner-[\d.]+-win64\.msi")
    return None


def snooze(version: str, now: float | None = None) -> dict:
    """The stored reminder state after the user chose "Later" for ``version``."""
    now = time.time() if now is None else now
    return {"version": version, "until": now + REMIND_LATER_S}


def is_snoozed(state, version: str, now: float | None = None) -> bool:
    """True while ``version`` is postponed by "Later". A newer release, or a clock moved
    back past the snooze start, ends it."""
    if not isinstance(state, dict) or state.get("version") != version:
        return False
    now = time.time() if now is None else now
    try:
        until = float(state.get("until", 0))
    except (TypeError, ValueError):
        return False
    return 0 < until - now <= REMIND_LATER_S


def whats_new(notes: str, max_chars: int = 4000) -> str:
    """The "What's new" section of the release notes (Markdown), without the
    per-platform download and install details."""
    text = notes.replace("\r\n", "\n").strip()
    m = re.search(r"^#{2,4}\s*What.s new.*$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        body = text[m.end():]
        end = re.search(r"^(#{1,4}\s|---)", body, re.MULTILINE)
        text = body[: end.start()] if end else body
    else:
        end = re.search(r"^---", text, re.MULTILINE)
        text = text[: end.start()] if end else text
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n\n…"
    return text
