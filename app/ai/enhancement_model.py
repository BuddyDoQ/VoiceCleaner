"""Speech enhancement model interface and implementations.

Every model implements :class:`EnhancementModel`. The rest of the
application only talks to this interface, so another network (a future
DeepFilterNet release, a dereverberation model, ...) can be added by
writing one subclass and registering it in ``model_manager``.

Current implementation: DeepFilterNet3 (Schröter et al., 2023), MIT /
Apache-2.0 dual-licensed. See ``models/README.md`` for the selection
rationale and license notes.
"""
from __future__ import annotations

import sys
import types
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from ..utils.errors import ModelUnavailableError
from ..utils.logging import get_logger

log = get_logger("model")


class EnhancementModel(ABC):
    """A neural speech enhancer operating on mono float32 audio."""

    key: str = ""
    display_name: str = ""
    sample_rate: int = 48_000
    license: str = ""
    homepage: str = ""
    # capabilities, used to decide which DSP stages are still needed
    denoises: bool = True
    dereverbs: bool = False

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.device = "cpu"

    @abstractmethod
    def is_installed(self) -> bool: ...

    @abstractmethod
    def load(self, device: str) -> None: ...

    @abstractmethod
    def unload(self) -> None: ...

    @property
    @abstractmethod
    def loaded(self) -> bool: ...

    @abstractmethod
    def enhance(self, audio: np.ndarray, attenuation_limit_db: float | None) -> np.ndarray:
        """Enhance ``audio`` (1-D, at ``sample_rate``). Output has the same length.

        ``attenuation_limit_db`` caps how much noise is removed; ``None``
        means unlimited. Lower limits keep more of the original ambience and
        are the main defence against over-processing.
        """


# ---------------------------------------------------------------------------------------

def _install_torchaudio_compat() -> None:
    """DeepFilterNet 0.5.6 imports ``torchaudio.backend.common.AudioMetaData``,
    which was removed in torchaudio 2.1+. VoiceCleaner never uses DeepFilterNet's
    file I/O, so an inert placeholder is enough for the import to succeed."""
    if "torchaudio.backend.common" in sys.modules:
        return
    try:
        import torchaudio.backend.common  # noqa: F401
        return
    except Exception:
        pass
    backend = sys.modules.get("torchaudio.backend") or types.ModuleType("torchaudio.backend")
    common = types.ModuleType("torchaudio.backend.common")

    class AudioMetaData:  # pragma: no cover - placeholder only
        pass

    common.AudioMetaData = AudioMetaData
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common


class DeepFilterNet3(EnhancementModel):
    key = "deepfilternet3"
    display_name = "DeepFilterNet3"
    sample_rate = 48_000
    license = "MIT / Apache-2.0"
    homepage = "https://github.com/Rikorose/DeepFilterNet"
    denoises = True
    dereverbs = False  # trained with mild reverb augmentation only

    def __init__(self, model_dir: Path):
        super().__init__(model_dir)
        self._model = None
        self._df_state = None
        self._torch = None

    @property
    def checkpoint_dir(self) -> Path:
        return self.model_dir / "checkpoints"

    def is_installed(self) -> bool:
        return (self.model_dir / "config.ini").is_file() and any(self.checkpoint_dir.glob("model_*.ckpt*"))

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self, device: str) -> None:
        if not self.is_installed():
            raise ModelUnavailableError(
                "The DeepFilterNet3 model files were not found. Reinstall VoiceCleaner or run "
                "tools/download_models.py. Processing will continue with traditional noise reduction."
            )
        if self.loaded and device == self.device:
            return
        _install_torchaudio_compat()
        try:
            import torch
            from df.checkpoint import load_model as load_checkpoint
            from df.config import config
            from df.model import ModelParams
            from libdf import DF
        except Exception as exc:
            log.exception("DeepFilterNet import failed")
            raise ModelUnavailableError(
                "The AI enhancement library could not be loaded. Processing will continue "
                "with traditional noise reduction."
            ) from exc

        config.load(str(self.model_dir / "config.ini"), config_must_exist=True, allow_defaults=True,
                    allow_reload=True)
        # DeepFilterNet resolves its device through this config key
        config.set("DEVICE", device, str, "train")
        p = ModelParams()
        self._df_state = DF(sr=p.sr, fft_size=p.fft_size, hop_size=p.hop_size, nb_bands=p.nb_erb,
                            min_nb_erb_freqs=p.min_nb_freqs)
        model, epoch = load_checkpoint(str(self.checkpoint_dir), self._df_state, epoch="best")
        if not epoch:
            raise ModelUnavailableError("The DeepFilterNet3 checkpoint could not be read.")
        self._model = model.to(device).eval()
        self._torch = torch
        self.device = device
        log.info("Loaded %s (epoch %s) on %s", self.display_name, epoch, device)

    def unload(self) -> None:
        self._model = None
        self._df_state = None
        if self._torch is not None and self.device.startswith("cuda"):
            self._torch.cuda.empty_cache()

    def enhance(self, audio: np.ndarray, attenuation_limit_db: float | None) -> np.ndarray:
        if not self.loaded:
            self.load(self.device)
        from df.enhance import enhance as df_enhance

        torch = self._torch
        x = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None]
        with torch.inference_mode():
            y = df_enhance(self._model, self._df_state, x, pad=True, atten_lim_db=attenuation_limit_db)
        return y[0].cpu().numpy().astype(np.float32)
