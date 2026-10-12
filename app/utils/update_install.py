"""Download, verify and install an update (only when the user chooses *Install Update*).

* **Windows, installed copy** (the MSI records its folder in the registry): the installer
  is downloaded into ``%LOCALAPPDATA%\\VoiceCleaner\\updates``, checked against the SHA-256
  that GitHub publishes for the file, and run after VoiceCleaner has closed, by a small
  helper script that then starts VoiceCleaner again. The standard edition runs the MSI;
  the GPU edition runs its web installer, which downloads and checks its own files.
  Both install with a progress bar and no questions (``/passive``); Windows asks for
  permission because VoiceCleaner is installed for all users.
* **Portable copies and macOS**: the file is downloaded and verified the same way, then
  shown, to be installed by hand (``mode == "show"``).

The outcome of an install is written to ``last_result.txt`` and reported at the next start.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import APP_NAME, APP_VERSION, _local_data_base, app_root
from .logging import get_logger
from .updates import Asset, ReleaseInfo, UpdateError, pick_asset

log = get_logger("update-install")

REG_KEY = r"Software\Steamburger Studios\VoiceCleaner"
CHUNK = 256 * 1024
TIMEOUT_S = 30
# release files are served from GitHub; a redirect anywhere else is refused
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")
OK_CODES = (0, 3010)  # 3010: installed, Windows wants a restart later
CANCELLED_CODES = (1602, 1223)  # the user declined the permission prompt or cancelled


@dataclass(frozen=True)
class UpdatePlan:
    asset: Asset | None
    mode: str  # "msi" | "bundle" (installed on Windows), "show" (download, then show), "browser"
    install_dir: Path | None = None

    @property
    def installs(self) -> bool:
        return self.mode in ("msi", "bundle")


def updates_dir() -> Path:
    path = _local_data_base() / APP_NAME / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------------- where we are
def install_dir() -> Path | None:
    """The folder of an installed (MSI) VoiceCleaner that is the one running, else None
    (running from source, or a portable copy)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    here = app_root().resolve()
    registered = _registered_install_folder()
    if registered is not None:
        return here if _same_dir(registered, here) else None
    # installers before 1.0.9 did not record the folder: Program Files means installed
    pf = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or ""
    return here if pf and _is_within(here, Path(pf)) else None


def _registered_install_folder() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            value, _ = winreg.QueryValueEx(k, "InstallFolder")
        return Path(str(value)) if value else None
    except OSError:
        return None


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())).rstrip("\\/") == os.path.normcase(str(b)).rstrip("\\/")
    except OSError:
        return False


def _is_within(child: Path, parent: Path) -> bool:
    c, p = os.path.normcase(str(child)), os.path.normcase(str(parent.resolve())).rstrip("\\/") + os.sep
    return c.startswith(p)


def plan(release: ReleaseInfo, plat: str | None = None, installed_dir: Path | None | str = "auto") -> UpdatePlan:
    """How this computer gets ``release``: installed in place, downloaded and shown, or
    (no fitting file) the release page in the browser."""
    plat = plat or sys.platform
    asset = pick_asset(release, plat=plat)
    if asset is None:
        return UpdatePlan(None, "browser")
    folder = install_dir() if installed_dir == "auto" else installed_dir
    if plat == "win32" and folder is not None:
        name = asset.name.lower()
        if name.endswith(".msi"):
            return UpdatePlan(asset, "msi", Path(folder))
        if name.endswith(".exe"):
            return UpdatePlan(asset, "bundle", Path(folder))
    if plat == "win32":
        return UpdatePlan(asset, "browser")  # portable: the user picks the portable download
    return UpdatePlan(asset, "show")


# ------------------------------------------------------------------------- download
def expected_sha256(release: ReleaseInfo, asset: Asset, timeout: float = TIMEOUT_S) -> str:
    """The checksum to verify ``asset`` against: GitHub's digest of the file, or the
    release's SHA256SUMS list. Empty when neither is available (then nothing is installed)."""
    if re.fullmatch(r"[0-9a-f]{64}", asset.sha256 or ""):
        return asset.sha256
    for sums in release.assets:
        if not re.fullmatch(r"sha256sums(-[\w]+)?\.txt", sums.name.lower()):
            continue
        try:
            text = _get(sums.url, timeout).decode("utf-8", "replace")
        except UpdateError:
            continue
        for line in text.splitlines():
            m = re.match(r"([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
            if m and m.group(2).strip() == asset.name:
                return m.group(1).lower()
    return ""


def _check_host(url: str):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if os.environ.get("VOICECLEANER_UPDATE_FEED") and host in ("127.0.0.1", "localhost"):
        return  # local test feed
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise UpdateError(f"The download was redirected to an unexpected site ({host}); it was stopped.")


def _open(url: str, timeout: float, start: int = 0):
    _check_host(url)
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    if start:
        headers["Range"] = f"bytes={start}-"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"The download failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("The download failed. Check your internet connection and try again.") from exc
    _check_host(r.geturl())  # after redirects
    return r


def _get(url: str, timeout: float) -> bytes:
    with _open(url, timeout) as r:
        return r.read(4 * 1024 * 1024)


def download(release: ReleaseInfo, asset: Asset, progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] = lambda: False, folder: Path | None = None) -> Path:
    """Download ``asset`` (resuming a partial download) and verify its SHA-256.
    Returns the verified file; raises UpdateError (the file is then deleted if it was bad)."""
    sha = expected_sha256(release, asset)
    if not sha:
        raise UpdateError("The release does not publish a checksum for this file, so it cannot be "
                          "verified. Use the release page instead.")
    folder = folder or (updates_dir() / release.version)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / asset.name
    if dest.exists() and _sha256(dest) == sha:
        if progress:
            progress(dest.stat().st_size, dest.stat().st_size)
        return dest  # downloaded before
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    total = asset.size
    free = shutil.disk_usage(folder).free
    if total and free < (total - have) * 1.1 + 50e6:
        raise UpdateError(f"Not enough free disk space for the download ({total / 1e6:.0f} MB).")
    with _open(asset.url, TIMEOUT_S, start=have) as r:
        if have and r.status != 206:  # the server did not resume: start over
            have = 0
        total = int(r.headers.get("Content-Length") or 0) + have if r.headers.get("Content-Length") else total
        with open(part, "ab" if have else "wb") as f:
            done = have
            while True:
                if cancelled():
                    raise UpdateError("Download cancelled.")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total or done)
    if _sha256(part) != sha:
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded file does not match its published checksum, so it was "
                          "deleted and not installed. Please try again.")
    part.replace(dest)
    log.info("Downloaded and verified %s (%s)", dest, sha)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------- install
HELPER = r"""@echo off
rem VoiceCleaner update helper: waits for VoiceCleaner to close, runs the installer,
rem records the result and starts VoiceCleaner again. Values come from VC_* variables.
setlocal
set /a N=0
:wait
tasklist /FI "PID eq %VC_PID%" /NH 2>NUL | find " %VC_PID% " >NUL
if errorlevel 1 goto install
set /a N+=1
if %N% GEQ 120 goto install
ping -n 2 127.0.0.1 >NUL
goto wait
:install
if /I "%VC_MODE%"=="msi" (
  start "" /wait "%VC_MSIEXEC%" /i "%VC_INSTALLER%" /passive /norestart INSTALLFOLDER="%VC_INSTALLDIR%" /l*v "%VC_LOG%"
) else (
  start "" /wait "%VC_INSTALLER%" /passive /norestart /log "%VC_LOG%" InstallFolder="%VC_INSTALLDIR%"
)
set RC=%ERRORLEVEL%
> "%VC_RESULT%" echo %RC% %VC_VERSION%
if "%VC_RELAUNCH%"=="1" start "" "%VC_APP_EXE%"
endlocal
"""


def result_file() -> Path:
    return updates_dir() / "last_result.txt"


def launch_installer(p: UpdatePlan, installer: Path, version: str, relaunch: bool,
                     app_pid: int | None = None, msiexec: str = "msiexec.exe",
                     app_exe: Path | None = None) -> subprocess.Popen:
    """Start the helper that installs ``installer`` once this process has exited.
    Call it just before quitting."""
    if not p.installs or p.install_dir is None:
        raise UpdateError("This copy of VoiceCleaner cannot install updates itself.")
    folder = installer.parent
    helper = folder / "install_update.cmd"
    helper.write_text(HELPER, encoding="ascii")
    result_file().unlink(missing_ok=True)
    env = dict(os.environ)
    env.update({
        "VC_PID": str(app_pid or os.getpid()), "VC_MODE": p.mode, "VC_INSTALLER": str(installer),
        "VC_INSTALLDIR": str(p.install_dir).rstrip("\\/"), "VC_LOG": str(folder / "install.log"),
        "VC_RESULT": str(result_file()), "VC_VERSION": version, "VC_RELAUNCH": "1" if relaunch else "0",
        "VC_APP_EXE": str(app_exe or (p.install_dir / "VoiceCleaner.exe")), "VC_MSIEXEC": msiexec,
    })
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    log.info("Starting update helper: %s %s -> %s (relaunch=%s)", p.mode, installer, p.install_dir, relaunch)
    return subprocess.Popen(["cmd.exe", "/d", "/c", str(helper)], env=env, cwd=str(folder), creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True)


@dataclass(frozen=True)
class InstallOutcome:
    code: int
    version: str

    @property
    def ok(self) -> bool:
        return self.code in OK_CODES

    @property
    def cancelled(self) -> bool:
        return self.code in CANCELLED_CODES

    def message(self) -> str:
        if self.ok and self.version == APP_VERSION:
            return f"VoiceCleaner was updated to version {self.version}."
        if self.cancelled:
            return (f"The update to version {self.version} was not installed: Windows' permission request "
                    "was declined or the installer was cancelled. You can install it any time from "
                    "Check for Updates.")
        if self.ok:  # installer succeeded but this is not the new version (unexpected)
            return f"The installer finished, but VoiceCleaner {APP_VERSION} is still running."
        return (f"The update to version {self.version} could not be installed (installer error {self.code}). "
                f"Details are in {updates_dir() / self.version / 'install.log'}.")


def take_last_result() -> InstallOutcome | None:
    """The outcome of the last update install, once (the record is removed)."""
    f = result_file()
    try:
        text = f.read_text(encoding="ascii", errors="replace").split()
    except OSError:
        return None
    f.unlink(missing_ok=True)
    try:
        return InstallOutcome(int(text[0]), text[1] if len(text) > 1 else "")
    except (ValueError, IndexError):
        return None


def cleanup(keep: str | None = None):
    """Remove downloaded installers of other versions (and finished ones)."""
    root = updates_dir()
    for d in root.iterdir():
        if d.is_dir() and d.name != keep:
            shutil.rmtree(d, ignore_errors=True)
