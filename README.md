# VoiceCleaner

A desktop application that turns noisy, poorly recorded speech into clean, clear,
professional-sounding audio. Drop a WAV file, listen to before/after with an instant
A/B switch, and export.

**Workflow:** Drop WAV → automatic analysis → Enhance → Listen (A/B) → Export.

It works fully offline. It runs on any CPU and uses an NVIDIA GPU automatically when one
is present.

## Quick start (from source)

Requires Windows and **Python 3.11** (DeepFilterNet's native library ships Windows
wheels only up to 3.11).

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128   # NVIDIA GPU
#   ...or: --index-url https://download.pytorch.org/whl/cpu                                              # CPU only
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python tools\download_models.py      # DeepFilterNet3, 8.7 MB, checksum-verified
.venv\Scripts\python -m app.main
```

Run the tests with `.venv\Scripts\python -m pytest`. To build the standalone app, run
`.venv\Scripts\python tools\build_windows.py`; the result is `dist\VoiceCleaner\VoiceCleaner.exe`.

## What it does

| Problem | How VoiceCleaner handles it |
|---|---|
| Steady noise (fans, HVAC, computer, hiss, mic self-noise) | Noise profile learned from pauses (or a region you select), then a decision-directed Wiener filter, followed by the AI model |
| Changing noise (traffic, rustle, babble) | DeepFilterNet3 neural speech enhancement |
| Clicks, taps, keyboard in pauses | Transient suppressor (never touches speech) |
| Electrical hum | Automatic 50/60 Hz and harmonic detection, narrow zero-phase notches |
| Rumble | Adaptive high-pass, always below the speaker's lowest pitch |
| Room reverb, mild echo | Statistical late-reverb suppression using the measured RT60; cepstral echo detection |
| Dull, muddy, boomy voice | Adaptive EQ comparing the voice with a natural long-term speech spectrum; gentle, limited corrections |
| Uneven levels | Gentle compressor (soft knee, linked stereo) and an optional soft expander |
| Loudness | ITU-R BS.1770-4 integrated loudness to -16 LUFS by default, true-peak limiter at -1 dBTP |

Every result goes through a **quality check**. VoiceCleaner compares the enhanced audio
with the original: lost syllables, changed voice tone, high-frequency artifacts,
over-compression, clipping. If it finds a problem, it reprocesses with gentler settings.
The guiding rule is *better speech, not obviously processed speech*.

## Using it

* **Simple controls:** a preset plus three sliders (Noise Reduction, Speech Enhancement,
  Room/Echo Reduction). With *Adapt to this recording* on, analysis tunes the preset:
  clean recordings get light processing, noisy ones get more.
* **Advanced:** noise floor bias, spectral subtraction strength, noise gate, residual
  noise floor, presence, clarity, low-end cleanup, reverb/echo, compressor
  (amount/threshold/ratio/attack/release), target loudness and peak ceiling.
* **Learn Noise Profile:** drag across a background-only part of the *Original*
  waveform, then click *Learn Noise Profile*.
* **A/B:** press `A` / `B` (or the buttons) during playback; the switch is instant and
  click-free at the same position. *Equal-loudness comparison* plays the original at
  the enhanced loudness so you judge quality, not volume.
* **Shortcuts:** `Space` play/pause, `A`/`B` switch, `Home` to start, `Ctrl+O` open,
  `Ctrl+Enter` enhance, `Ctrl+E` export, `Esc` cancel. `Ctrl+wheel` zooms the waveform.
* **Batch:** drop several files (or a folder). Each file is analyzed and enhanced on its
  own; a damaged file is reported and the rest continue.
* **Export:** WAV 16-bit (dithered), 24-bit (default) or 32-bit float, original or
  converted sample rate; optional MP3. The original file is never overwritten.

## Supported input

WAV/WAVE (including WAVE_FORMAT_EXTENSIBLE, RF64, W64). Mono or stereo. 8/16/24/32-bit
PCM or 32/64-bit float. 8 kHz to 384 kHz. Up to 6 hours per file. Recordings too large
for memory are processed from disk automatically.

Stereo is handled according to what it contains:

| Stereo content | Processing |
|---|---|
| Identical channels | Processed once as mono, written back as stereo |
| Speech on one side only | That channel is processed, then centred on both |
| Genuine stereo | Each channel processed, with linked dynamics |

## Architecture

```
app/
  main.py                 entry point, global error handler
  ui/                     PySide6 interface (main window, waveform, player, settings, batch, export)
  audio/
    loader.py             validation, float32 conversion, disk-backed buffers for long files
    analyzer.py           level/LUFS/peak/LRA, VAD, noise floor, SNR, hum, RT60, echo, pitch, clipping, stereo
    noise_profile.py      noise spectrum from pauses or a user selection
    denoise.py            Wiener denoiser, click suppressor
    speech_enhancer.py    AI stage (wraps app/ai)
    dereverb.py           late-reverb and echo suppression
    eq.py                 high-pass, hum notches, adaptive voice EQ (zero-phase)
    compressor.py         compressor, soft expander
    loudness.py           BS.1770-4 meter (streaming)
    limiter.py            true-peak lookahead limiter
    quality.py            before/after metrics, QC checks, clarity estimate
    settings.py           settings, presets, automatic configuration
    pipeline.py           orchestration, chunking, QC retry loop
  ai/
    enhancement_model.py  model interface + DeepFilterNet3
    model_manager.py      registry, device selection, lazy loading
    inference.py          resampling, chunked inference, GPU to CPU fallback
  export/                 WAV and MP3 writers
  workers/                background threads (single file, batch)
  utils/                  config, logging, hardware detection, user-facing errors
models/                   AI models (separate from the app, see models/README.md)
tests/                    pytest suite (70 tests)
tools/                    test-set generator, evaluation, model download, build
```

Each stage is a separate class with its own parameters and can be tested on its own.
Long recordings are processed in 20 s chunks with 1 s raised-cosine crossfades.

### Technology choices

* **Python 3.11 + NumPy/SciPy:** all DSP. Filters run zero-phase (forward-backward),
  because processing is offline and this leaves the voice's phase untouched.
* **PySide6 (Qt 6):** native Windows look, drag-and-drop, fast custom-painted
  waveforms, threads, and good PyInstaller support. Under the LGPL it is used as
  dynamically linked libraries.
* **sounddevice (PortAudio):** one output stream that reads either buffer at a shared
  playhead, which is what makes the A/B switch instant.
* **DeepFilterNet3 on PyTorch:** see [models/README.md](models/README.md) for the model
  comparison and licenses.

## Evaluation

`tools/make_test_recordings.py` builds a test set with real synthesized speech (Windows
speech synthesizer). It covers clean speech, white noise, HVAC, hum, reverb, a mix of
noises, and format variants. `tools/evaluate.py` scores the results against the clean
reference.

Results with *Clean Voice* + smart settings (STOI = short-time objective intelligibility):

| File | STOI before → after | Noise floor before → after |
|---|---|---|
| speech + HVAC (6 dB SNR) | 0.963 → 0.944 | -34 → -54 dBFS |
| speech + white noise | 0.959 → 0.964 | -35 → -54 dBFS |
| speech + reverb (RT60 0.9 s) | 0.713 → 0.752 | n/a |
| speech + HVAC + hum + clicks + reverb | 0.615 → 0.658 | -40 → -53 dBFS |

Noise floors are measured after loudness normalization, so they are relative to
speech at -16 LUFS. Processing speed was 10-28x realtime on an RTX 5070 Ti, and about
the same on a Ryzen 7 9800X3D CPU, because the model is very small.

The **speech clarity score** in the interface is an internal estimate. It combines
the speech-to-background ratio with the depth of syllable modulation, and is not a
standardized intelligibility measure.

## Logs and settings

* Preferences: `%APPDATA%\VoiceCleaner\config.json`
* Logs: `%LOCALAPPDATA%\VoiceCleaner\logs\voicecleaner.log`. The log records file
  names, settings, devices, timings and errors, never audio content. It is also
  reachable from the app menu (*Open Log Folder*).

## Known limitations

* De-reverberation is statistical (spectral). It clearly reduces room tails but cannot
  make a very reverberant room sound fully dry. A neural dereverberation model can be
  added through the model interface.
* Clipping is detected and reported but not reconstructed (no declipper yet).
* Speech buried under very loud noise cannot be fully recovered; the quality check
  favors keeping speech over removing the last bit of noise.
