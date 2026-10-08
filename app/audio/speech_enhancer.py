"""AI speech enhancement stage.

Wraps :class:`app.ai.inference.ModelInference` as a pipeline stage. The
strength setting maps to the model's attenuation limit rather than a
wet/dry mix: limiting how far noise may be pushed down keeps the voice
natural, while a plain mix would bring back the noise *and* phase smear.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ai.inference import ModelInference


@dataclass
class SpeechEnhancerParams:
    strength: float = 0.5  # 0..1
    max_attenuation_db: float | None = None


class SpeechEnhancer:
    def __init__(self, inference: ModelInference, params: SpeechEnhancerParams):
        self.inference = inference
        self.params = params

    @property
    def active(self) -> bool:
        return self.params.strength > 0.01

    def process(self, x: np.ndarray, sr: int, out: np.ndarray, progress=None, check_cancel=None) -> np.ndarray:
        channels = x.shape[1]
        for c in range(channels):
            def ch_progress(f, c=c):
                if progress:
                    progress((c + f) / channels)

            out[:, c] = self.inference.enhance(x[:, c], sr, self.params.strength, progress=ch_progress,
                                               check_cancel=check_cancel,
                                               max_attenuation_db=self.params.max_attenuation_db)
        return out
