# Models

VoiceCleaner keeps AI models in this folder, next to the application, so they can be
updated or replaced without rebuilding the program. Normal processing never needs an
internet connection; models are installed once with:

```
python tools/download_models.py
```

| Folder | Model | License | Size | Source |
|---|---|---|---|---|
| `DeepFilterNet3/` | DeepFilterNet3 (epoch 120) | MIT **or** Apache-2.0 (dual) | 8.7 MB | <https://github.com/Rikorose/DeepFilterNet> (`models/DeepFilterNet3.zip`, SHA-256 `49c52edc…22284d2`) |
| `BigVGAN-v2-44k/` | NVIDIA BigVGAN-v2, 44.1 kHz, 128-band, 512x (generator only) | MIT | 489 MB | <https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x> (`bigvgan_generator.pt` SHA-256 `d9fe7ec6…ccd357`) |

If the folder is missing or damaged, VoiceCleaner still works: it falls back to its own
spectral noise reduction and says so in the interface.

## Why DeepFilterNet3

Candidates were judged on speech quality, noise reduction, speed, CPU-only operation,
GPU support, model size, license and how easy they are to ship.

| Model | Rate | Quality for this use | CPU speed | Size | License | Verdict |
|---|---|---|---|---|---|---|
| **DeepFilterNet3** | **48 kHz full band** | Strong noise suppression, very natural voice, few artifacts; attenuation limit allows a gentle "strength" control | ~60x realtime on one desktop CPU | 8.7 MB | MIT / Apache-2.0 | **Selected** |
| Meta Demucs denoiser (`facebook/denoiser`) | 16 kHz | Good, but band-limited to 8 kHz (dull for podcasts) | ~realtime | 33-130 MB | **CC-BY-NC 4.0** (non-commercial) | Rejected: license and bandwidth |
| SpeechBrain MetricGAN+ / SepFormer | 16 / 8 kHz | Good scores, band-limited, MetricGAN can sound processed | moderate | 2-110 MB | Apache-2.0 | Rejected: 16 kHz output |
| RNNoise | 48 kHz | Lightweight but noticeably weaker on non-stationary noise | very fast | 85 KB | BSD-3 | Rejected: quality |
| VoiceFixer | 44.1 kHz | Generative restoration; can hallucinate or alter the voice, heavy | slow on CPU | ~500 MB | MIT | Rejected: artifact risk, size |

DeepFilterNet3 was the only option that is full-band (48 kHz), fast enough on any CPU,
tiny, permissively licensed for redistribution, and controllable (its attenuation limit
lets VoiceCleaner remove *some* noise instead of all of it, which is the main protection
against the "underwater" sound of over-processed speech).

It does not do heavy dereverberation, so VoiceCleaner adds its own statistical
late-reverb suppression, plus profile-based noise reduction, click suppression, hum
notches, adaptive EQ, dynamics and loudness processing around it.

## Why BigVGAN-v2 for voice re-synthesis

Re-synthesis analyses the cleaned voice into a log-mel spectrogram and regenerates it
with a neural vocoder. The vocoder can only produce natural voice sounds, so leftover
artifacts are not carried over.

* **BigVGAN-v2 (selected):** a universal vocoder trained on speech, singing and
  environmental audio across many recording conditions, so it generalizes to unseen
  speakers. 44.1 kHz full band, MIT license for code and weights, from NVIDIA's
  official repositories.
* **Vocos:** 24 kHz only, which would discard the top octave of 48 kHz recordings.
* **HiFi-GAN:** 22 kHz, trained mostly on single speakers.
* **VoiceFixer:** generative "restoration" that predicts a new spectrum and can
  alter the voice.

Measured on the synthetic test set, a full re-synthesis of clean speech keeps
intelligibility (STOI 0.989) and level (-0.4 dB). In the pipeline, 30 % strength costs
at most about 0.004 STOI, and 100 % up to 0.03 on reverberant input. So presets use
it sparingly (Noisy 20 %, Podcast 25 %, Extreme Noise 40 %); it is off otherwise.

It runs about 22x realtime on an RTX 5070 Ti and about 1.3x realtime on a desktop CPU.

Code: the generator and anti-aliased activations are vendored in
`app/ai/vendor/bigvgan/` (MIT, license included). The Hugging Face Hub loader was
removed. The mel analysis uses VoiceCleaner's own NumPy filterbank, verified identical
to librosa's.

## License and redistribution notes

* DeepFilterNet (code and pretrained weights) is dual-licensed MIT or Apache-2.0. Both
  allow commercial use and redistribution. Keep the copyright notice and license text
  with redistributed copies (the build copies this file into `LICENSES/`).
* The Python package `deepfilternet` 0.5.6 imports a torchaudio module that newer
  torchaudio versions removed. VoiceCleaner installs a harmless placeholder for that
  import at runtime (`app/ai/enhancement_model.py`); no DeepFilterNet code is modified.
* Model weights are not modified and are loaded only from this folder.

## Adding another model

1. Subclass `EnhancementModel` in `app/ai/enhancement_model.py` (implement
   `is_installed`, `load`, `unload`, `enhance`).
2. Register it in `REGISTRY` in `app/ai/model_manager.py` with its folder name.
3. Put its files in `models/<folder>/` and document the license here.

The pipeline, chunking, resampling, GPU/CPU fallback and the UI need no changes.
