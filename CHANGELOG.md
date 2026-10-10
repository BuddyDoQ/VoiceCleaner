# Changelog

## 1.0.3

- **Settings are remembered:** every control, not just a few, is restored at the next start.
- **Smart settings now clearly win, and only where they apply:** while *Smart settings* is on,
  Noise, Speech and Room (and the controls tied to them) are locked and marked **AUTO**, with a
  note explaining why. Your other settings (re-synthesis, leveling, EQ, ...) are no longer reset
  when you open another recording. With Smart off, opening a recording changes nothing.
  Batch processing follows the same rules.
- **Your own presets:** *Save as…* next to the preset list stores all current settings as a
  preset. The `⋯` menu updates, renames or deletes it, or shows the presets folder.
- **MODIFIED** appears next to the preset when you change a control; *Reset* returns to the
  preset. Double-click a slider to reset just that one.
- **Open Recent** and **Keyboard Shortcuts** (`Ctrl+/`) in the app menu.
- **Export:** optional *Show the file in Explorer when done* (remembered).
- **Fixed:** the *Enhance* and *Export* buttons could be squeezed and their labels clipped
  when the status text wrapped. Status lines now stay on one line (full text in the tooltip).
- **Fixed:** in a narrow window the sidebar covered the right end of the playback controls;
  they now move to a second row.

## 1.0.2

- **macOS:** VoiceCleaner now runs natively on Apple Silicon Macs (DMG and zip).
  - Finder integration: *Show in Folder* reveals files in Finder, and WAV files can be
    opened from Finder or by dropping them on the Dock icon.
  - Native locations: `~/Library/Application Support/VoiceCleaner` and
    `~/Library/Logs/VoiceCleaner`.
  - Smooth, click-free playback and stable processing on macOS.
  - Desktop-audio recording is not available on macOS (no system loopback device);
    microphone recording works.
- **Check for Updates** (app menu): shows what's new in a newer release and opens the
  right download for your computer. Checks automatically once a day (can be turned off;
  only GitHub's public release information is read, nothing is sent or installed).
- Error and permission messages no longer assume Windows.

## 1.0.1

- **Compile tab:** join enhanced takes into one WAV.
  - It starts with the session's enhanced exports. Add more by drag and drop from
    Explorer, with *Add Files*, or with *Add to Compilation* in the Playback tab.
  - Drag-and-drop re-ordering (plus ▲/▼, `Ctrl+Up`/`Ctrl+Down`, `Delete`). Double-click a
    take to hear it.
  - Leading and trailing silence of every take is trimmed, keeping up to 100 ms at each
    end (adjustable).
  - Optional gap between takes and loudness matching. Preview, then export.
- **Shorten long pauses (optional, off by default):** pauses inside a recording longer
  than 1.0 s (adjustable, 0.3–5 s) are shortened to 250 ms (adjustable; "Remove
  completely" is an explicit choice).
  - Available in the Compile tab, and on export in the Enhance tab (Dynamics → Pauses,
    Batch too).
  - Your choices are remembered between sessions.
- Silence detection now follows each recording's own background level, so raw recordings
  with audible room noise are trimmed correctly.
- Number fields use the DM Mono typeface for legible digits.

## 1.0.0

First public release: speech enhancement (DeepFilterNet3 + DSP), neural voice
re-synthesis (BigVGAN-v2, downloaded on demand), EQ and tonal balancing, dynamic
leveling, recording (microphone and desktop audio), sessions, playback, batch processing,
Steamburger Studios branding with day/night themes. Standard (CPU) and NVIDIA GPU
editions.
