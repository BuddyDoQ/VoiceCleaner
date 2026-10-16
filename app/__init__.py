"""VoiceCleaner application package."""
import os as _os
import sys as _sys

if _sys.platform == "darwin":
    # Must be set before PyTorch is imported: an operation the Apple GPU (MPS) does not
    # support then runs on the CPU instead of failing the whole re-synthesis.
    _os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


def _use_bundled_portaudio() -> None:
    """Play and record through the PortAudio that ships with sounddevice.

    sounddevice first looks for *any* PortAudio on the system (``portaudio.dll`` on the
    PATH, e.g. from Csound or other audio software) and only then uses its own. Such a
    build may include ASIO: PortAudio then opens every ASIO driver while starting, which
    takes seconds and makes drivers such as FL Studio ASIO or ASIO4ALL show their own
    window the first time something plays. VoiceCleaner never uses ASIO, so the bundled
    library (no ASIO; WASAPI, MME, DirectSound) is always the right one. This must run
    before sounddevice is imported.
    """
    import ctypes.util as cu

    if getattr(cu.find_library, "_voicecleaner", False):
        return
    original = cu.find_library

    def find_library(name):
        if "portaudio" in str(name).lower():
            return None  # never a system PortAudio: sounddevice falls back to its own
        return original(name)

    find_library._voicecleaner = True
    cu.find_library = find_library


if _sys.platform in ("win32", "darwin") and not _os.environ.get("VOICECLEANER_SYSTEM_PORTAUDIO"):
    _use_bundled_portaudio()


def _quiet_windows_version() -> None:
    """Python 3.11's ``platform`` module runs ``cmd /c ver`` to learn the Windows version
    (sounddevice asks for it when it loads). From the windowed app that flashes a console
    window. Windows reports the same version directly, without starting anything."""
    import platform

    def syscmd_ver(system="", release="", version="", supported_platforms=("win32", "win16", "dos")):
        v = _sys.getwindowsversion()
        return system or "Microsoft Windows", release, f"{v.major}.{v.minor}.{v.build}"

    platform._syscmd_ver = syscmd_ver


if _sys.platform == "win32" and _sys.version_info < (3, 12):  # 3.12+ asks Windows itself
    _quiet_windows_version()
