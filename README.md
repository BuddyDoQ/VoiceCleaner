# VoiceCleaner

**Clean, clear speech from noisy recordings: free, offline, for Windows and Mac.**

**[Download the latest version](https://github.com/BuddyDoQ/VoiceCleaner/releases/latest)** ·
by [Steamburger Studios](https://www.steamburgerstudios.com/)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/enhance-dark.png">
  <img alt="VoiceCleaner: the Enhance tab after enhancing, with the original and enhanced waveforms, A/B switch and measurements" src="docs/screenshots/enhance-light.png">
</picture>

Open a recording (a phone memo, a Zoom call, a podcast take, the sound of a video) and
VoiceCleaner removes background noise, room echo and hum, evens out the level and makes
the voice clear. Compare before and after with an instant A/B switch, then export.

## Download

| Your computer | Download |
|---|---|
| **Windows 10/11**, any PC | `VoiceCleaner-<version>-win64.msi` |
| **Windows with an NVIDIA graphics card** (much faster) | `VoiceCleaner-GPU-<version>-web.exe` |
| **Mac with Apple Silicon** (M1 or newer), macOS 12+ | `VoiceCleaner-<version>-macos-arm64.dmg` |

All on the **[latest release page](https://github.com/BuddyDoQ/VoiceCleaner/releases/latest)**,
with portable versions that need no installation. VoiceCleaner keeps itself up to date
(*Check for Updates* in the ⋯ menu). The downloads are not code-signed yet: Windows may show
*Windows protected your PC* (choose *More info* → *Run anyway*), and macOS may ask you to
confirm opening it the first time.

Needs 8 GB of memory. Everything runs on your computer: no account, no upload, no
internet needed after installing.

## What it does

* **Cleans up speech:** background noise (fans, traffic, hiss), room echo, hum, rumble,
  clicks, clipping (recorded too loud) and harsh "s" sounds. An AI model and classic
  signal processing work together, and a quality check makes sure the voice still sounds
  natural.
* **Opens what you have:** WAV, MP3, M4A (phone voice memos), FLAC, Opus/OGG, AIFF and WMA,
  and the sound of video files such as Zoom recordings (MP4, MOV, MKV, WebM).
* **Records:** a microphone, the computer's own sound, or both at once (both sides of a call).
* **Teleprompter:** read your script while it records, one take per section,
  [see below](#teleprompter).
* **Compile:** join takes into one file: trims the silence at the ends, lets you cut out a
  pop or a stumble, and re-enhance a take.
* **Batch:** enhance a whole folder of recordings with the same settings; turn on *Preview*
  to hear a sample of any file first. Also from the [command line](#command-line).
* **Evens out loudness** to podcast level (-16 LUFS) without clipping.

## Teleprompter

Read your script while VoiceCleaner records, one take per script section. Retake any
section; every take is kept and enhanced automatically, and Compile joins them in script
order.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/teleprompter-dark.png">
  <img alt="The teleprompter recording a script, with the reading line, REC indicator and take number" src="docs/screenshots/teleprompter-light.png">
</picture>

<img alt="The teleprompter moving on to the next section" src="docs/screenshots/teleprompter.gif" width="720">

Open it with `Ctrl+T`, the *TELEPROMPTER* button in the header, or *Teleprompter* on the
start screen and next to *Record*. The header button shows when it is open, and REC while
it records.

* Script format: `## Heading | Clip name` starts a section (the clip name names its
  takes), `[brackets]` are stage directions, a blank line starts a paragraph. Paste or
  edit it in the prompter (*Edit*, `E`) or open a .txt / .md file.
* *Start* (`Space`) counts down 3-2-1 and records from the chosen microphone without a
  break. When the next section reaches the reading line (or you press `→` / `PageDown`),
  a new take starts by itself. The cut is placed in the pause between the sections, so
  a word that runs over is never cut or repeated.
* **Retake** (`Backspace` or `PageUp`) goes back to the start of the section and records
  it again. Every take is kept; the newest is used, unless you marked one with
  **Keep** (`G`). **Last take** (`P`) plays back the most recent take.
* *Step* mode holds each section (or line) until you move on; *Lines* records one take
  per paragraph instead of per section, for line-by-line voice work. Presentation
  clickers and foot pedals that send PageUp/PageDown work hands-free.
* Pausing (`Space`) pauses the take too. Speed (wpm, `↑`/`↓`), text size (`+`/`-`),
  mirror for teleprompter glass (`M`), stage directions (`D`), fullscreen (`F`), and a
  REC indicator with take number, level meter and recorded time. If the microphone sends
  only silence, a NO SOUND warning names it.
* Colours follow the app's day/night setting; the ◐ button keeps the prompter always
  Dark or Light instead.
* Takes are saved in the session's Recordings folder as
  `<Session> - 03 What It Does - Take 2.wav`, enhanced in the background with the
  current settings (into Exports), and listed in Compile.

## More screenshots

**Compile:** the teleprompter's takes in script order, ready to join into one file.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/compile-dark.png">
  <img alt="The Compile tab listing teleprompter takes in script order with a preview of the compilation" src="docs/screenshots/compile-light.png">
</picture>

<table>
<tr>
<td width="62%">

**Batch:** enhance many files at once, with the same settings sidebar as Enhance.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/batch-dark.png">
  <img alt="Batch processing of three files with the settings sidebar" src="docs/screenshots/batch-light.png">
</picture>

</td>
<td>

**Smart settings and presets:** analysis sets the cleanup for each recording.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/settings-dark.png">
  <img alt="The settings sidebar with preset, Smart settings note and AUTO sliders" src="docs/screenshots/settings-light.png" width="300">
</picture>

</td>
</tr>
</table>

**Updates:** VoiceCleaner downloads, checks and installs new versions itself (Windows).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/update-dark.png">
  <img alt="The update window: downloaded and verified, ready to install and restart" src="docs/screenshots/update-light.png" width="560">
</picture>

## Using it

1. **Open** a recording: drop it on the window, use *Choose a Recording*, or press
   *Record*. VoiceCleaner analyzes it and picks settings for it.
2. **Enhance.**
3. **Listen:** press `Space`, and `A` / `B` to switch between the original and the
   enhanced version.
4. **Export.** The first start shows a short tour of the basics (*Getting Started* in the
   ⋯ menu shows it again).

The details:

* **Simple controls:** a preset plus three sliders (Noise Reduction, Speech Enhancement,
  Room/Echo Reduction). Every control is remembered between sessions.
* **Smart settings** (*Smart settings: adapt to each recording*, on by default): analysis
  sets Noise, Speech and Room, and the controls tied to them, for each recording. Clean
  recordings get light processing and noisy ones get more. While Smart is on, those
  controls are locked and marked **AUTO**, and a note explains why. Turn Smart off to set
  them yourself; your values then stay as they are when you open another recording.
  Everything else (re-synthesis, leveling, EQ, ...) is always yours.
* **Presets:** *Save as…* stores all current settings as your own preset (the `⋯` menu
  next to it updates, renames or deletes it, or shows the presets folder). Presets are
  JSON files in `%APPDATA%\VoiceCleaner\presets` (macOS:
  `~/Library/Application Support/VoiceCleaner/presets`), easy to back up or share.
  **MODIFIED** appears when you change a control; *Reset* goes back to the preset.
  Double-click any slider to reset just that one.
* **Voice Re-synthesis:** set the amount (off by default). The model (490 MB) can be
  downloaded from inside the app the first time.
* **EQ & Tone:** a live response curve, *Tonal balancing* (automatic correction
  amount), *Tilt*, and eight band sliders (double-click a band to reset it).
* **Dynamics:**
  * *Leveling* (amount, maximum correction, speed) evens out sentences and speakers.
  * *Compression* (amount, threshold, ratio, attack, release) controls word-level peaks.
  * *De-esser* (50% by default) softens harsh "s", "sh" and "t" sounds only at the
    moments they stick out, so the voice stays bright without sounding sharp.
* **Scrolling:** the mouse wheel scrolls the settings, even over a slider or the preset box.
  Click a control first to adjust it with the wheel.
* **Fonts:** system fonts by default; *Appearance → Brand Fonts* in the ⋯ menu switches to
  the Steamburger Studios fonts.
* **Advanced:** noise floor bias, spectral subtraction strength, noise gate, residual
  noise floor, presence, clarity, low-end cleanup, reverb/echo, target loudness and
  peak ceiling.
* **Learn Noise Profile:** drag across a background-only part of either waveform, then
  click *Learn Noise Profile*. A learned profile is trusted: that noise is removed more
  firmly. When a recording starts with a second or more of background noise before the
  first word, it is learned automatically on opening (*Clear Noise Profile* undoes it).
* **A/B:** press `A` / `B` (or the buttons) during playback; the switch is instant and
  click-free at the same position. *Equal-loudness comparison* plays the original at
  the enhanced loudness so you judge quality, not volume.
* **Loop** (`L`): drag across either waveform and turn on *Loop* to repeat just that
  passage while you switch A/B or change settings. Without a selection the whole
  recording loops.
* **Live preview of changes:** after the first Enhance, changing a setting re-processes
  about 10 seconds around the playhead (or your selection, up to 30 s) a moment after
  you stop adjusting. The previewed part is marked *LIVE PREVIEW* on the Enhanced track
  and plays at the level a full Enhance would give it. *Enhance Again* applies the
  settings to the whole recording; exporting first asks whether to do that. On by
  default with an NVIDIA GPU (about a second per preview) and on Apple Silicon Macs (under
  half a second, or about 5 s with Voice Re-synthesis); it can be turned on for other CPUs
  too, where it is slower.
* **Undo / redo** settings changes with `Ctrl+Z` / `Ctrl+Y` or the ↶ ↷ buttons next to
  the preset. A slider drag is one step; the tooltip names the change.
* **Smart note:** the × hides the explanation under *Smart settings* (the AUTO labels stay);
  *Show Tips Again* in the menu brings it back.
* **While working:** the step and percentage show on the waveform being processed, the
  sidebar shows elapsed time and an estimate of the time left, a header chip shows the
  running job from any tab (click it to go there), and the busy tab carries a badge
  (for example *BATCH · 3/10*). On Windows the taskbar button shows the progress, and a
  long job that finishes while VoiceCleaner is in the background flashes it.
* **Start-up:** a splash screen appears immediately and shows what is loading; the
  window opens once the AI engine is ready.
* **Shortcuts:** `Space` play/pause, `A`/`B` switch, `Home` to start, `Ctrl+O` open,
  `Ctrl+Enter` enhance, `Ctrl+E` export, `R` record/stop, `Ctrl+Shift+N` new session,
  `Esc` cancel. `Space` plays whichever tab is in front (Enhance or Playback). `Ctrl+wheel` zooms the waveform.
  The menu's *Keyboard Shortcuts* (`Ctrl+/`) lists them all.
* **Getting Started:** the first start of a new install takes a short tour of the basics,
  one stop per tab. After an update, only what is new is shown. The ⋯ menu has both
  (*Getting Started*, *What's New*).
* **Open Recent** (app menu): the last ten recordings you opened. The start screen lists
  the last five for one-click access, and next to them, under *THIS SESSION*, the
  session's five newest recordings. VoiceCleaner reopens on the tab you used last.
* **Quitting while working** (enhancing, exporting, a batch or a recording) asks first.
* **Export:** tick *Show the file in Explorer when done* to go straight to the exported
  file instead of the confirmation box; the choice is remembered.
* **Record:** click **● Record** on the Original track (or *Record Audio* on the start
  screen, or press `R`). Pick a microphone (USB, headset, line-in) and/or **desktop
  audio**: anything playing through a chosen speaker or headphone output, captured via
  WASAPI loopback, so no "Stereo Mix" is needed (on a Mac: *System audio*, macOS 14.2+, or
  a loopback driver such as BlackHole). Recording both at once captures both
  sides of a call; the two are aligned and clock-drift corrected. Per-source level
  meters and trims are shown, and the take is drawn live on the Original waveform.
  Two options, remembered between sessions: a **3-second countdown** before recording
  starts (click Record again to cancel it), and **Record 1 s of room tone first**: recording
  starts with a quiet second (*Stay quiet*, then *Speak now*), and that second becomes the
  noise profile, so the background noise is measured exactly (new recordings only).
  With a file already open, choose how the take relates to it:
  * *New recording*: the take becomes the Original.
  * *Append*: added to the end.
  * *Overdub*: layered onto the Original from the playhead, optionally while the
    Original plays (use headphones). Playback and capture latency are compensated.

  Takes are saved to the session's `Recordings` folder as 48 kHz 24-bit WAV and
  streamed to disk while recording, so an unplugged USB microphone or a crash keeps
  what was captured. Combined results are written as new files; the original is never
  changed.
* **Sessions:** a session is a project. *New Session* (header button, or `Ctrl+Shift+N`)
  creates `Documents\VoiceCleaner Sessions\<name>\` with `Recordings` and `Exports`
  folders. Every file it creates carries the session name, for example
  `Episode 12 - Rec 003.wav`, `Episode 12 - Rec 003 enhanced.wav` and
  `Episode 12 - Rec 004 append.wav`. Names are limited to 32 characters and cleaned of
  characters Windows does not allow, so file names stay short. Sessions can be opened
  or renamed (renaming also renames the session's files). The app resumes the last
  session on start. A new session starts on the Enhance tab with nothing open: if the
  open recording was enhanced but not exported, VoiceCleaner offers to export it first.
* **Playback tab:** starts with the current session's recordings and exports.
  * *All Recordings* lists every session in the default recordings folder.
  * *Browse Folder…* scans any folder, including subfolders. Recordings (and videos) that
    can actually be played are listed; damaged ones are counted and skipped.
  * Search, sort, play with a waveform and seek, then *Open in Enhancer*, *Add to
    Batch* or *Show in Folder*.
* **Compile tab:** joins enhanced takes into a single WAV.
  * It starts with the current session's enhanced exports. Add more with *Add Files*,
    by dropping recordings from Explorer, or with *Add to Compilation* in the Playback tab.
  * Drag rows to set the order, or use the ▲/▼ buttons or `Ctrl+Up`/`Ctrl+Down`.
    Double-click a row to hear that take on its own.
  * Leading and trailing silence of every take is trimmed, keeping up to 100 ms at each
    end (adjustable) so breaths and soft onsets survive; 5 ms fades prevent clicks.
  * Optional gap between takes, and optional loudness matching between takes. A
    true-peak limiter keeps the result from clipping.
  * The preview updates by itself a moment after each change, so *Play* starts at once.
    Changing the order while it plays keeps playing and continues at the same take.
  * Export it to the session's Exports folder as `<Session> - Compilation.wav`.
  * **Teleprompter takes:** with *TELEPROMPTER TAKES* the list is the script, in order: one
    row per section with its selected take (enhanced when ready). Right-click a row to use
    another take; it then counts as that section's keeper.
  * **Edit a take** (`E`, *✂ Edit Take…* or right-click): select a pop, click or a pause the
    trimming missed and **Cut** it (`X` / `Delete`, crossfaded) or **Silence** it (`S`,
    keeps the timing). `Ctrl`+wheel zooms to a few milliseconds; *Hear Result* plays the
    take as it will sound. Edits are saved in the session (`compile_edits.json`); the file
    itself is never changed.
  * **Re-enhance** (*↻ Re-enhance*, or right-click; select several rows to do them all):
    enhances takes again from their original recordings, with the current settings or a
    preset, in the background. The replaced version is kept in `Exports\Previous Versions`
    (*Revert to Previous Version* swaps it back), and take edits stay in place. *Open in
    Enhance Tab* loads one take's recording for fine-tuning; *Update Take in Compile*
    puts the result back.
* **Shorten long pauses (optional, off by default):** pauses inside a recording that are
  longer than a threshold (1.0 s by default, adjustable from 0.3 to 5 s) are shortened to a natural pause (250 ms by
  default, adjustable up to 2 s). Both values are remembered between sessions. The kept pause is made of the start and end of the original silence, so
  breaths and lead-ins survive, joined by a short crossfade so the background tone
  continues without clicks. Setting the length to 0 ("Remove completely") removes long
  pauses entirely, an explicit choice. Silence before the first word and after the last
  is left to trimming. Two places:
  * **Compile tab:** applied to every take before joining. Rows mark the shortened
    pauses, and the preview includes them.
  * **Enhance tab** (Dynamics → Pauses): applied when exporting, including Batch. The
    preview keeps the original timing, so the A/B comparison stays aligned. The notes
    say how many pauses will be shortened and the time saved. Presets never enable it.
* **Day and night themes:** use the sun/moon button in the header. *Appearance* in the
  menu also offers *Automatic*, which follows the system's light/dark setting. Colours
  follow the Steamburger Studios website; *Appearance → Brand Fonts* also uses its type
  (Bebas Neue, Syne, DM Mono) instead of the system fonts.
* **Batch:** drop several files (or a folder): recordings in any supported format, or videos.
  Turn on **Preview** (`P`) and click a file to hear a 10-second sample of it (the part with
  the most speech) with the current settings; compare it A/B at equal loudness (`Space`, `A`,
  `B`). Clicking another file previews that one, and the sample follows setting changes, so
  you can tune the settings before enhancing everything. Each file is analyzed and enhanced on its
  own; a damaged file is reported and the rest continue. The settings sidebar is shown on
  the right of the Batch tab (the same settings as Enhance). When done, *Open Output Folder*
  shows the results, and double-clicking a finished file opens it in Enhance to compare.
* **Export:** WAV 16-bit (dithered), 24-bit (default) or 32-bit float, original or
  converted sample rate; optional MP3. Exports are named after the original (also for
  phone memos and videos), which is never overwritten.

## Command line

Enhance files and folders without opening the window, with the same processing as the
Batch tab. On Windows, `VoiceCleanerCLI.exe` is next to `VoiceCleaner.exe`
(`C:\Program Files\Steamburger Studios\VoiceCleaner`):

```
VoiceCleanerCLI --batch recordings out --preset Natural
VoiceCleanerCLI --batch "Voice Memo 14.m4a" zoom_0.mp4 out --format mp3
VoiceCleanerCLI --batch D:\Podcast --recursive D:\Podcast\clean --preset Podcast --no-smart
VoiceCleanerCLI --list-presets
```

* Inputs are files and/or folders (`--recursive` includes subfolders); the last path is the
  output folder. Results are named `<name>_enhanced.wav` and never overwrite anything.
* `--preset` takes a built-in preset or one of your own; Smart settings adapt it to each
  file unless `--no-smart`. `--format wav|mp3`, `--bits 16|24|32` (32 = float),
  `--device auto|cpu|cuda`.
* One line per file with progress, a summary at the end, and the exit status for scripts:
  0 all enhanced, 1 some failed (the others are still done), 2 nothing to do or a mistake in
  the command. `--help` lists everything.

## How it cleans up speech

| Problem | How VoiceCleaner handles it |
|---|---|
| Steady noise (fans, HVAC, computer, hiss, mic self-noise) | Noise profile learned from pauses (or a region you select), then a decision-directed Wiener filter, followed by the AI model |
| Changing noise (traffic, rustle, babble) | DeepFilterNet3 neural speech enhancement |
| Clipping (recorded too loud) | The cut-off peaks are rebuilt by sparse declipping (consistent iterative hard thresholding): every unclipped sample is kept, and the lost peaks are filled in beyond the clipping level |
| Clicks, taps, keyboard in pauses | Transient suppressor (never touches speech) |
| Electrical hum | Automatic 50/60 Hz and harmonic detection, narrow zero-phase notches |
| Rumble | Adaptive high-pass, always below the speaker's lowest pitch |
| Room reverb, mild echo | Statistical late-reverb suppression using the measured RT60; cepstral echo detection |
| Dull, muddy, boomy voice | Adaptive EQ comparing the voice with a natural long-term speech spectrum; gentle, limited corrections |
| Leftover processing artifacts, thin or smeared voice | Optional neural voice re-synthesis (NVIDIA BigVGAN-v2): the cleaned voice is regenerated by a vocoder, blended coherently by a strength slider |
| Tone | Automatic tonal balancing toward a natural voice spectrum, plus an 8-band graphic EQ (80 Hz-10 kHz, +-12 dB) and a darker/brighter tilt, all zero-phase |
| Uneven levels | Dynamic leveling (speech-aware gain riding over seconds, frozen in pauses), gentle compressor (soft knee, linked stereo) and an optional soft expander |
| Loudness | ITU-R BS.1770-4 integrated loudness to -16 LUFS by default, true-peak limiter at -1 dBTP |

Every result goes through a **quality check**. VoiceCleaner compares the enhanced audio
with the original: lost syllables, changed voice tone, high-frequency artifacts,
over-compression, clipping. If it finds a problem, it reprocesses with gentler settings.
The guiding rule is *better speech, not obviously processed speech*.

## Supported input

* **Opened directly:** WAV/WAVE (including WAVE_FORMAT_EXTENSIBLE, RF64, W64), MP3, FLAC,
  Opus and Vorbis (OGG), AIFF and CAF.
* **Opened through the included FFmpeg:** M4A and AAC (phone voice memos), WMA, AMR, and
  the sound of video files (MP4, MOV, M4V, MKV, WebM, AVI, WMV, 3GP). The first audio track
  is used; surround sound is mixed down to stereo. FFmpeg is included in the Windows
  downloads. On a Mac, M4A and video files open once the Mac download includes FFmpeg as
  well; the formats above open there already.
* Mono or stereo, 8 kHz to 384 kHz, up to 6 hours per file. Recordings too large for
  memory are processed from disk automatically. Results are saved as WAV (or MP3).

Stereo is handled according to what it contains:

| Stereo content | Processing |
|---|---|
| Identical channels | Processed once as mono, written back as stereo |
| Speech on one side only | That channel is processed, then centred on both |
| Genuine stereo | Each channel processed, with linked dynamics |

## Logs and settings

* Sessions (recordings and exports): `Documents\VoiceCleaner Sessions\<session>\`
* Preferences: `%APPDATA%\VoiceCleaner\config.json`
* Logs: `%LOCALAPPDATA%\VoiceCleaner\logs\voicecleaner.log`. The log records file
  names, settings, devices, timings and errors, never audio content. It is also
  reachable from the app menu (*Open Log Folder*).
* On a Mac: preferences and downloaded models are in
  `~/Library/Application Support/VoiceCleaner`, logs in `~/Library/Logs/VoiceCleaner`.

## Updates

*Check for Updates…* in the app menu asks GitHub for the latest published release. If
there is a newer version, the app shows what is new and how to get it. Nothing is
downloaded or installed until you choose to, and nothing about you, your computer or
your audio is sent.

* **Installed on Windows (1.0.9 and later):** *Install Update* downloads the installer
  inside the app (with progress; an interrupted download continues where it stopped),
  checks it against the SHA-256 checksum GitHub publishes for the file, and then offers
  *Install and Restart* or *Install When I Quit*. VoiceCleaner closes, the installer runs
  with a progress bar (Windows asks for permission, as VoiceCleaner is installed for all
  users), and VoiceCleaner opens again in the same folder and confirms the update. The GPU
  edition runs its web installer, which downloads the ~2 GB update with its own progress
  window. Downloads are kept in `%LOCALAPPDATA%\VoiceCleaner\updates` until the update is
  installed.
* **Portable copies:** *Download* opens the release page to pick the portable download.
* **macOS:** *Download* fetches and checks the DMG, then shows it in Finder; drag the new
  VoiceCleaner to Applications.

By default the app also checks automatically every time it starts (one small request,
a few seconds after starting) and only speaks up when there is something new. *Later*
hides that version until the next day, *Skip Version* hides it for good, and a newer
release is shown right away. Turn automatic checks off with the checkbox in the update
window. To check from a terminal (useful for diagnosing network problems):
`VoiceCleaner --check-updates`. Set `VOICECLEANER_NO_UPDATE_CHECK=1` to disable automatic
checks entirely, for example on managed computers.

## Known limitations

* De-reverberation is statistical (spectral). It clearly reduces room tails but cannot
  make a very reverberant room sound fully dry. A neural dereverberation model can be
  added through the model interface.
* Clipping repair rebuilds peaks that were cut off by a few dB at a time; recordings clipped
  very hard (a large share of every word flattened) come out much better but not like an
  undistorted recording.
* Speech buried under very loud noise cannot be fully recovered; the quality check
  favors keeping speech over removing the last bit of noise.

## Building from source

Running from source, tests, the architecture and how releases are built:
see [BUILDING.md](BUILDING.md).

## License

VoiceCleaner's source code is released under the [MIT License](LICENSE).

The Steamburger Studios name and burger logo are trademarks of Steamburger Studios and
are not covered by the MIT License. Bundled third-party components (DeepFilterNet,
NVIDIA BigVGAN, PyTorch, Qt and others) keep their own licenses; see
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) and [models/README.md](models/README.md).
The AI model weights are not stored in this repository. Fetch them with
`tools/download_models.py`.
