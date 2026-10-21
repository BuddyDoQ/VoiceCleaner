# Building VoiceCleaner

How to run VoiceCleaner from source, test it, and build and publish releases. To *use*
VoiceCleaner, download it from the [latest release](https://github.com/BuddyDoQ/VoiceCleaner/releases/latest)
instead; see the [README](README.md).

## Run from source

### Windows

Requires Windows 10/11 and **Python 3.11** (DeepFilterNet's native library ships Windows
wheels only up to 3.11).

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128   # NVIDIA GPU
#   ...or: --index-url https://download.pytorch.org/whl/cpu                                              # CPU only
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python tools\download_models.py      # DeepFilterNet3, 8.7 MB, checksum-verified
.venv\Scripts\python tools\fetch_ffmpeg.py ffmpeg  # LGPL FFmpeg for M4A/AAC/WMA/video (optional)
.venv\Scripts\python -m app.main
```

Without `ffmpeg\`, WAV, MP3, FLAC, Opus/OGG and AIFF still open (libsndfile reads them);
M4A, AAC, WMA and video files then ask for FFmpeg. An `ffmpeg` on the `PATH` is used too.

### macOS

Requires macOS 12+ on Apple Silicon (an Intel Mac works too if you build there) and
**Python 3.11** (for example `uv python install 3.11`).

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install torch torchaudio
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/download_models.py
.venv/bin/python -m app.main
```

On macOS:

* noise reduction runs on the CPU (about 30x realtime on Apple Silicon, faster than on the
  Apple GPU for this small model), and voice re-synthesis runs on the Apple GPU (about 3x
  realtime instead of 0.6x on the CPU; *CPU only* in the menu turns this off);
* desktop audio: *System audio (all apps)* records everything the Mac plays except
  VoiceCleaner itself, through a Core Audio process tap (macOS 14.2 or newer; the small
  `vc-systemaudio` helper in the app, built from `packaging/macos/systemaudio.swift`). On
  older macOS a loopback driver such as BlackHole is listed under Desktop audio;
* M4A, AAC, WMA and video files need an LGPL FFmpeg in the app bundle (`ffmpeg/ffmpeg`
  next to the executable) or `VOICECLEANER_FFMPEG`; see [FFmpeg](#ffmpeg) below.

## Tests

```powershell
.venv\Scripts\python -m pytest                       # unit tests
.venv\Scripts\python tools\ui_smoke.py test_audio\reference\clean_speech.wav out\ui   # one of the UI smoke scripts
```

The `tools/*_smoke.py` scripts drive the real window (with an isolated profile, so your own
settings and sessions are never touched) and save screenshots: `ui`, `settings`, `tone`,
`v106`, `v107` take a WAV and an output folder; `compile`, `teleprompter`, `take_edit`,
`session_switch`, `reenhance`, `tour`, `wheel_font`, `formats`, `batch_preview`, `countdown`, `update`, `pause`,
`brand`
take an output folder. README screenshots come from `python tools/screenshots.py`.

## Evaluation

`tools/make_test_recordings.py` builds a test set with real synthesized speech (Windows
speech synthesizer). It covers clean speech, white noise, HVAC, hum, reverb, a mix of
noises, and format variants. `tools/evaluate.py` scores the results against the clean
reference (`--learn` uses a learned noise profile, like *Learn Noise Profile*).

Results with *Clean Voice* + smart settings (STOI = short-time objective intelligibility):

| File | STOI before → after | Noise floor before → after |
|---|---|---|
| speech + HVAC (6 dB SNR) | 0.963 → 0.944 | -34 → -54 dBFS |
| speech + white noise | 0.959 → 0.964 | -35 → -54 dBFS |
| speech + reverb (RT60 0.9 s) | 0.713 → 0.752 | n/a |
| speech + HVAC + hum + clicks + reverb | 0.615 → 0.658 | -40 → -53 dBFS |

Noise floors are measured after loudness normalization, so they are relative to
speech at -16 LUFS. Processing speed, for the whole pipeline including quality checks,
was 10-28x realtime on an RTX 5070 Ti and 9-23x realtime on a Ryzen 7 9800X3D CPU
alone. The model is small, so a GPU helps less than you might expect.

To check a packaged build (or a user's machine) without the GUI:
`VoiceCleaner.exe --selftest input.wav output.wav` (the result goes to the log).

The **speech clarity score** in the interface is an internal estimate. It combines
the speech-to-background ratio with the depth of syllable modulation, and is not a
standardized intelligibility measure.

## Releasing

Releases ship in two Windows editions built from the same code:

| Edition | PyTorch | For | Packages |
|---|---|---|---|
| Standard | CPU (`.venv-cpu`) | every PC | single-file `.msi` (~0.3 GB), portable `.zip` |
| NVIDIA GPU | CUDA 12.8 (`.venv`) | NVIDIA cards, much faster | ~1 MB web installer, `.msi` + `vcgpu*.cab`, split portable zip |

Neither edition bundles the 490 MB voice re-synthesis model (BigVGAN). The app downloads
it the first time the feature is used, checksum-verified, into
`%LOCALAPPDATA%\VoiceCleaner\models`, where it stays through updates. The 9 MB
noise-reduction model is bundled, so everything else works offline. With this, every
release file fits GitHub's 2 GB per-file limit.

One-time setup:

```powershell
py -3.11 -m venv .venv-cpu
.venv-cpu\Scripts\python -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
.venv-cpu\Scripts\python -m pip install -r requirements.txt pyinstaller pytest pyloudnorm
dotnet tool restore                      # WiX Toolset 5 (pinned in .config/dotnet-tools.json)
gh auth login                            # GitHub CLI, for publishing
```

Build, and optionally publish to GitHub as release `v<version>`:

```powershell
.venv\Scripts\python tools\release.py              # both editions -> release\<version>
.venv\Scripts\python tools\release.py --publish    # ...and upload the GitHub release
.venv\Scripts\python tools\release.py --publish-only
```

How the GPU edition is delivered:

* **Web installer:** a WiX Burn bundle (`packaging/wix/Bundle.wxs`) that downloads the
  GPU `.msi` and its cabinets from the GitHub release and verifies every file against a
  hash recorded at build time. A tampered or corrupted download is rejected.
* **Portable ZIP:** split into parts under 2 GB. 7-Zip opens the `.001` part directly, or
  the parts can be joined with `copy /b` and opened in Windows.

Each Windows build contains `VoiceCleanerCLI.exe`, a console twin of `VoiceCleaner.exe` (same
`_internal`, a few MB) that runs the [command line](README.md#command-line), so a terminal
waits for it and shows its progress.

The installers:

* install per-machine into `Program Files\Steamburger Studios\VoiceCleaner`;
* add Start Menu and Desktop shortcuts and an Add/Remove Programs entry;
* share one upgrade code, so installing either edition replaces the other.

Version numbers come from `APP_VERSION` in `app/utils/config.py`. Keep the MSI and
bundle upgrade codes unchanged between releases. Builds are not code-signed, so Windows
SmartScreen warns on first run until they are signed (`signtool sign`).

To try the in-place update on a PC before publishing a version, install the locally built
MSI and run `python tools/update_e2e_local.py`: it offers that installer as a pretend newer
release to the installed app.

### macOS

`.venv/bin/python tools/build_mac.py` builds `dist/mac/VoiceCleaner.app` plus
`release/<version>/VoiceCleaner-<version>-macos-arm64.dmg` and `.zip`. Add
`--sign "Developer ID Application: …" --notarize <profile>` for a build that opens without
a Gatekeeper warning, and `--publish` to add it to the GitHub release.

### FFmpeg

VoiceCleaner opens M4A, AAC, WMA and the sound of video files through FFmpeg, run as a
separate program, so the app stays MIT-licensed. Only an **LGPL** build may be shipped
(GPL builds, such as PyAV's wheels with libx264/libx265, are not used).

* **Windows:** `tools/fetch_ffmpeg.py` downloads a pinned BtbN/FFmpeg-Builds
  *lgpl-shared* release, checks its SHA-256 and that it was configured without
  `--enable-gpl`/`--enable-nonfree`, and keeps `ffmpeg.exe` with its DLLs.
  `tools/build_windows.py` puts it in `ffmpeg\` next to `VoiceCleaner.exe` (about 160 MB
  unpacked) and copies its licence into `LICENSES\`. To update it, change `RELEASE`, `ZIP`,
  `ZIP_SHA256`, `VERSION` and `COMMIT` in the script.
* **Source code:** `tools/release.py` attaches the matching FFmpeg source
  (`ffmpeg-source-<version>.tar.gz`) to every release, as the LGPL requires.
* **macOS:** the Mac build needs its own LGPL FFmpeg in `VoiceCleaner.app/.../ffmpeg/`
  (for example built with the BtbN scripts' LGPL configuration, or from source without
  `--enable-gpl`), with its source attached the same way.

## Architecture

```
app/
  main.py                 entry point, global error handler
  cli.py                  command line (VoiceCleanerCLI --batch ...)
  sessions.py             sessions: folders, naming rules, rename
  ui/                     PySide6 interface (main window, waveform, player, settings, batch, export,
                          playback, compile, take editor, teleprompter, tour, themes, assets)
  teleprompter/           pop-out prompter, take recording and the take book
  audio/
    loader.py             validation, float32 conversion, disk-backed buffers for long files
    declip.py             clipping repair (consistent iterative hard thresholding)
    decode.py             M4A/AAC/WMA/video through FFmpeg (run as a separate program)
    recorder.py           microphone + desktop-audio (loopback) capture, alignment, append/overdub
    analyzer.py           level/LUFS/peak/LRA, VAD, noise floor, SNR, hum, RT60, echo, pitch, clipping, stereo
    noise_profile.py      noise spectrum from pauses, the lead-in or a user selection
    denoise.py            Wiener denoiser, click suppressor
    speech_enhancer.py    AI stage (wraps app/ai)
    dereverb.py           late-reverb and echo suppression
    eq.py                 high-pass, hum notches, tonal balancing, graphic EQ, tilt (zero-phase)
    leveler.py            speech-aware dynamic leveling
    compressor.py         compressor, soft expander
    deesser.py            de-esser (sibilance control)
    loudness.py           BS.1770-4 meter (streaming)
    compile.py            take trimming, edits and joining (Compile tab)
    pauses.py             long-pause detection and shortening
    limiter.py            true-peak lookahead limiter
    gap_guard.py          keeps pauses from ending up louder than in the original
    quality.py            before/after metrics, QC checks, clarity estimate
    settings.py           settings, presets, automatic configuration
    pipeline.py           orchestration, chunking, QC retry loop
  ai/
    enhancement_model.py  model interface + DeepFilterNet3
    model_manager.py      registry, device selection, lazy loading
    inference.py          resampling, chunked inference, GPU to CPU fallback
    resynthesis.py        BigVGAN-v2 voice re-synthesis, coherent strength blend
    model_download.py     checksum-verified model downloads (also used in-app)
    vendor/bigvgan/       NVIDIA BigVGAN generator code (MIT)
  export/                 WAV and MP3 writers
  workers/                background threads (single file, batch)
  utils/                  config, logging, hardware detection, updates, user-facing errors
models/                   AI models (separate from the app, see models/README.md)
tests/                    pytest suite
tools/                    builds, releases, smoke scripts, evaluation, screenshots
```

Each stage is a separate class with its own parameters and can be tested on its own.
Long recordings are processed in 20 s chunks with 1 s raised-cosine crossfades.

### Technology choices

* **Python 3.11 + NumPy/SciPy:** all DSP. Filters run zero-phase (forward-backward),
  because processing is offline and this leaves the voice's phase untouched.
* **PySide6 (Qt 6):** native look, drag-and-drop, fast custom-painted waveforms, threads,
  and good PyInstaller support. Under the LGPL it is used as dynamically linked libraries.
* **sounddevice (PortAudio):** one output stream that reads either buffer at a shared
  playhead, which is what makes the A/B switch instant.
* **libsndfile (soundfile) + FFmpeg:** libsndfile reads WAV, MP3, FLAC, Opus/OGG and AIFF
  directly; an LGPL FFmpeg, run as a separate program, decodes the rest.
* **DeepFilterNet3 on PyTorch:** see [models/README.md](models/README.md) for the model
  comparison and licenses.

### Playback routing and refresh safety

`audio/playback_routing.py` owns output selection for Enhance, Playback, Compile,
Batch, Take Editor, and the lazy teleprompter preview player. Preferences use
`UserConfig.extra["audio_output"]`: system-default mode is distinct from an explicit
device name and host API. Numeric PortAudio indexes are resolved anew, never saved.
Missing or ambiguous explicit devices produce an error without falling back to speakers.

SoundCard supplies the live OS inventory and default. A default-only change reopens
the affected player on a known output without resetting PortAudio. Device topology
changes require a global reset with the bundled PortAudio 19.7 backend; its proposed
portable hotplug API is not available. The isolated sounddevice `_terminate()` /
`_initialize()` adapter requires one initialization owner and successful closure of
every registered player. No callback lock is held during stream teardown.
The adapter assumes VoiceCleaner is the sole PortAudio client in its process;
sounddevice's initialization counter cannot detect foreign native clients. New
playback or recording entry points must participate in the shared lifecycle.

The shared lifecycle lock serializes playback admission and global refresh against
recording-thread admission. Recording guards cover device creation through native
close, including system-audio helper shutdown and teleprompter generator cleanup.
Refresh and route changes are deferred while capture is running, including slow
teardown after a UI stop, to preserve overdub monitoring latency. A native recording
open/close failure blocks global refresh until restart. Failed refreshes are not
retried by the timer; Play or Audio output > Refresh devices permits an explicit retry.

Playback retains source buffers, position, loop, edits and A/B state across reopening.
The existing macOS block size and latency remain unchanged. Output format checks
report unsupported sample rates; there is no playback resampler. Mono outputs receive
a downmix of the stereo playback mix. Same-name devices on one host API cannot be
distinguished reliably and fail explicitly.

`tests/test_audio_routing.py` exercises routing, refresh exclusion and recovery with
fake devices, real UI construction and capture threads. Live USB/Bluetooth switching,
driver teardown behavior, Windows playback and audible overdub alignment still need
hardware validation before a release.
