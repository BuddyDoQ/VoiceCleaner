"""Model registry, discovery and lifecycle.

Models live in the ``models`` folder next to the application (not inside
the executable), so they can be updated or swapped independently.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from ..utils.config import models_dir
from ..utils.errors import ModelUnavailableError
from ..utils.hardware import DeviceInfo, select_device
from ..utils.logging import get_logger
from .enhancement_model import DeepFilterNet3, EnhancementModel
from .resynthesis import BigVGANResynthesizer

RESYNTH_FOLDER = "BigVGAN-v2-44k"

log = get_logger("models")

# key -> (class, folder name under models/)
REGISTRY: dict[str, tuple[type[EnhancementModel], str]] = {
    "deepfilternet3": (DeepFilterNet3, "DeepFilterNet3"),
}
DEFAULT_MODEL = "deepfilternet3"


@dataclass
class ModelStatus:
    key: str
    name: str
    installed: bool
    loaded: bool
    device: DeviceInfo
    error: str = ""

    @property
    def label(self) -> str:
        if self.error:
            return f"{self.name}: unavailable"
        if not self.installed:
            return f"{self.name}: not installed"
        return f"{self.name}" + (" • ready" if self.loaded else "")


class ModelManager:
    """Owns at most one loaded model; thread-safe lazy loading."""

    def __init__(self, device_preference: str = "auto", model_key: str = DEFAULT_MODEL):
        self._lock = threading.RLock()
        self.device_preference = device_preference
        self.device = select_device(device_preference)
        cls, folder = REGISTRY[model_key]
        self.model: EnhancementModel = cls(models_dir() / folder)
        self.model.device = self.device.torch_device
        self.error = ""
        self.resynth = BigVGANResynthesizer(models_dir() / RESYNTH_FOLDER)
        self.resynth_device = self.device

    def status(self) -> ModelStatus:
        return ModelStatus(self.model.key, self.model.display_name, self.model.is_installed(),
                           self.model.loaded, self.device, self.error)

    @property
    def available(self) -> bool:
        return self.model.is_installed() and not self.error

    def set_device_preference(self, preference: str):
        with self._lock:
            if preference == self.device_preference:
                return
            self.device_preference = preference
            new_device = select_device(preference)
            if new_device != self.device:
                self.model.unload()
                self.resynth.unload()
                self.device = new_device
                self.resynth_device = new_device
                self.model.device = new_device.torch_device

    def get(self) -> EnhancementModel:
        """Return the loaded model, loading it on first use."""
        with self._lock:
            if self.error:
                raise ModelUnavailableError(self.error)
            if not self.model.loaded:
                try:
                    self.model.load(self.device.torch_device)
                except ModelUnavailableError as exc:
                    self.error = exc.user_message
                    raise
                except Exception as exc:
                    log.exception("Model load failed")
                    if self.device.kind == "cuda":
                        log.warning("Retrying model load on CPU")
                        self.fallback_to_cpu()
                        return self.get()
                    self.error = "The AI model could not be loaded."
                    raise ModelUnavailableError(self.error) from exc
            return self.model

    def fallback_to_cpu(self):
        with self._lock:
            self.model.unload()
            self.device = select_device("cpu")
            self.model.device = "cpu"

    @property
    def resynth_available(self) -> bool:
        return self.resynth.is_installed()

    def get_resynthesizer(self) -> BigVGANResynthesizer:
        """The voice re-synthesis vocoder, loaded on first use (it is large)."""
        with self._lock:
            if not self.resynth.loaded:
                try:
                    self.resynth.load(self.resynth_device.torch_device)
                except ModelUnavailableError:
                    raise
                except Exception as exc:
                    log.exception("Re-synthesis model load failed")
                    if self.resynth_device.kind == "cuda":
                        self.resynth_fallback_to_cpu()
                        self.resynth.load("cpu")
                    else:
                        raise ModelUnavailableError("The voice re-synthesis model could not be loaded.") from exc
            return self.resynth

    def resynth_fallback_to_cpu(self):
        with self._lock:
            self.resynth.unload()
            self.resynth_device = select_device("cpu")

    def preload(self) -> None:
        try:
            self.get()
        except ModelUnavailableError:
            pass
