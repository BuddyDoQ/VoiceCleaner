# Changelog

## 1.0.8

- **Mac: voice re-synthesis on the Apple GPU.** On Apple Silicon Macs, Voice Re-synthesis now
  runs on the GPU, about 5x faster (a 20 s recording with re-synthesis: 9 s instead of 36 s).
  The result is the same as on the CPU. Noise reduction stays on the CPU, where it is faster.
  - The header shows *CPU + GPU*; *CPU only* in the menu (⋯ > Processing device) turns the
    GPU off. If the GPU ever fails, that part is redone on the CPU automatically.
- **Mac: live preview is on by default on Apple Silicon** (under half a second per preview,
  about 5 s with Voice Re-synthesis).
- **Fixed:** in light mode the playhead on the track being played was white, so it could not be
  seen. It is now dark in light mode (still white in dark mode).
- **Mac: the brand typeface and Windows-matched text sizes.** Labels, buttons, checkboxes and
  menus now use Syne like the rest of the app (macOS had swapped in its system font), and all
  text is a third larger, as on Windows: small text such as the info chips, section headings
  and slider values was hard to read.
- **Fixed:** the start screen's description was cut off at the top and bottom (Mac).
- **Fixed:** EQ band names and the 1.25k label were cut off; band names now alternate between
  two lines under the sliders.
- **Fixed:** the Record button's label was cut off while the recorder was open.
- **Fixed (Mac):** the Export dialog's file name and folder fields were too narrow.

## 1.0.7

- **Batch tab shows the settings:** the settings sidebar (preset, Smart, sliders, EQ,
  dynamics) is now on the right of the Batch tab too, so you no longer go back to Enhance to
  change them. It is the same panel: a change in either tab applies to both.
- **Batch results:** *Open Output Folder* when the batch is done, and double-click a finished
  file to compare it in the Enhance tab.
- **Clearer layout:** in dark mode the panels stand out more from the background, and the
  section tabs (Enhance, Playback, Compile, Batch) are now real tab buttons with the current
  one highlighted.
- **Fixed:** slider knobs were cut off at the top and bottom, most visibly with Windows display
  scaling above 100 %.
- **Fixed:** the start screen's description could lose its second line.
- **Quitting while working asks first** (enhancing, exporting, a batch or a recording).
- **Recent recordings on the start screen**, and VoiceCleaner reopens on the tab you used last.
- The Smart explanation can be hidden with its ×; *Show Tips Again* in the menu brings it back.

## 1.0.6

- **Splash screen:** VoiceCleaner shows a splash immediately when it starts, with what it
  is loading (audio engine, interface, AI model on the GPU). Before, nothing appeared for
  several seconds. The window opens ready to work.
- **See what is happening:**
  - the step and percentage are shown on the waveform being processed;
  - elapsed time and an estimate of the time left under the progress bar;
  - a header chip shows the running job from any tab (click it to go there);
  - busy tabs carry a badge, for example *BATCH · 3/10*;
  - the Windows taskbar button shows the progress, and flashes when a long job finishes
    while you are in another app.
- **Compile: automatic preview.** The compilation updates by itself a moment after each
  change, so *Play* starts at once. Changing the order while it plays keeps playing and
  continues at the same take.
- **Live preview of changes:** after the first Enhance, changing a setting re-processes about
  10 seconds around the playhead (or your selection) within a second or two on a GPU, so you
  hear the change right away. *Enhance Again* applies it to the whole recording; exporting
  with changed settings asks first. On by default with an NVIDIA GPU.
- **Loop** (`L`): repeat a selected passage (or the whole recording) while comparing A/B or
  adjusting settings. Selections can now be made on either waveform.
- **Undo / redo** for settings (`Ctrl+Z` / `Ctrl+Y`, or ↶ ↷ next to the preset).
- macOS release notes: first-launch steps updated for macOS 15 Sequoia (*Open Anyway*).

## 1.0.5

- **Update check on every start:** VoiceCleaner now checks for a newer version each time it
  opens and shows it right away. Before, it checked at most once a day, so opening the app
  usually showed nothing even when an update was out.
  - *Later* (or closing the update window) hides that version until the next day;
    *Skip Version* hides it for good. A newer release is always shown.
  - Turn automatic checks off with the checkbox in the update window.

## 1.0.4

- **Sharper Steamburger logo and app icon:** the logo is now true vector artwork, so it stays
  crisp at every size instead of looking ragged when small.
- The Windows icon now includes every size the taskbar, Explorer and shortcuts use (16-256 px),
  each drawn from the vector, instead of one large image that Windows had to shrink.
- On displays scaled above 100 %, the logos in the app are drawn at full resolution instead
  of being enlarged.

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
