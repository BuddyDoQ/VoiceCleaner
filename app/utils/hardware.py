"""Hardware detection: processing device and available memory."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from functools import lru_cache

import psutil

from .logging import get_logger

log = get_logger("hardware")


@dataclass(frozen=True)
class DeviceInfo:
    kind: str  # "cpu" or "cuda"
    torch_device: str  # e.g. "cpu", "cuda:0"
    name: str  # human readable

    @property
    def label(self) -> str:
        if self.kind == "cuda":
            return f"NVIDIA GPU ({self.name.removeprefix('NVIDIA ').strip()})"
        return f"CPU ({self.name})"


@lru_cache(maxsize=1)
def cpu_name() -> str:
    import platform

    name = platform.processor() or "CPU"
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        pass
    return name


@lru_cache(maxsize=1)
def cuda_device() -> DeviceInfo | None:
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            # Make sure kernels for this architecture actually exist in the
            # installed torch build (e.g. new GPUs with an old wheel).
            torch.zeros(1, device="cuda:0").add_(1).item()
            return DeviceInfo("cuda", "cuda:0", name)
    except Exception as exc:  # broken driver, unsupported arch, ...
        log.warning("CUDA present but unusable: %s", exc)
    return None


def select_device(preference: str = "auto") -> DeviceInfo:
    """Pick the processing device. ``preference`` is auto, cpu or cuda."""
    cpu = DeviceInfo("cpu", "cpu", cpu_name())
    if preference == "cpu":
        return cpu
    gpu = cuda_device()
    if gpu is None:
        if preference == "cuda":
            log.warning("GPU requested but not available, using CPU")
        return cpu
    return gpu


def available_ram_bytes() -> int:
    return psutil.virtual_memory().available


_torch_lock = threading.Lock()


def ensure_torch_imported() -> None:
    """Import PyTorch exactly once, under a lock, before concurrent work starts.

    SciPy's array-API helpers check ``sys.modules['torch']``; if another thread is
    halfway through importing torch at that moment, SciPy sees a partially
    initialised module and fails. Every worker calls this first, so a second
    thread simply waits until the import has finished.
    """
    with _torch_lock:
        try:
            import torch  # noqa: F401
        except Exception as exc:  # torch missing/broken: DSP-only mode still works
            log.warning("PyTorch unavailable: %s", exc)
