# Changelog

## 1.1.10

- **Smooth Compile preview.** With many takes in the Compile list, the preview could stutter.
  The time readout under the waveform changes ten times a second, and each change redrew
  the whole tab, every take in the list included, leaving too little time for the audio.
  The readout now keeps its size, so only the readout itself is redrawn, and the waveform
  is redrawn only when the playhead moves. Playback in the Enhance and Playback tabs
  benefits too.

## 1.1.9

- **Noises are no longer mistaken for speech.** On recordings with little dynamic range (a
  noisy tape, a quiet room mic), a rumble, knock or handling noise as loud as the voice
  could count as speech, so noise reduction, pause clean-up and the quality check all left it
  alone (and the quality check read its removal as lost speech, retrying for nothing). The
  speech detector now also listens for pitch: vowels repeat at the speaker's pitch, most
  noises do not, so a low-sounding stretch of 0.3 s or more without pitch is background.
  Short unpitched sounds inside words (an "s", a "t") stay speech, and so do breathy or
  whispered words, which carry their sound higher up (breath, "h", "s"); on a whispered
  recording, which has little pitch anywhere, the check stays out of it entirely. The
  quality check's "speech loss" measure now looks at the core of each phrase only, not the
  short margin kept beside words: next to a loud noise that margin holds the noise, and
  removing it was counted as lost speech, so strong presets (Extreme Noise) retried for nothing. On a test tape, three such noises came
  out 7-30 dB quieter than before, and Enhance needed one pass instead of three.
- **Pauses never louder than the original.** Turning the voice up to the loudness target
  turned up whatever background was left between words too: hiss that grew in the highs
  while the rumble below was removed, or a rustle or click in a pause, could come out louder
  than in the recording. A last step now compares the finished result with the original in
  the pauses, band by band, and turns down anything that came out louder (fading in the
  guard zones beside the words, so speech is never touched). Live preview does the same.
  The quality check also reports pauses that got louder ("background between words ... dB
  louder than the original") and, if it happens, retries with firmer pause clean-up.
- **Export after Enhance Again, fixed.** *Enhance and Export* now exports the newly enhanced
  recording instead of asking again. The "Your settings changed since the last Enhance" prompt
  only appears when they really changed: before, whenever the quality check adjusted a setting
  during Enhance, VoiceCleaner treated the settings as changed and asked on every export.

## 1.1.8

- **Plays at once.** On some PCs the first Play of a session took a few seconds and showed a
  window: VoiceCleaner picked up another program's audio library (PortAudio from Csound or
  similar software) that opens every ASIO driver while starting, and Windows briefly showed
  a console window while VoiceCleaner looked up the Windows version. It now always uses its
  own audio library, starts no other program, and gets audio ready in the background as soon
  as the window opens. The first Play went from 2.8 s to about 0.3 s.
- **Audio output, organized:** *System default* (naming the current device), then the
  **Active** outputs, each once by its full name, the default first. Outputs that are not
  connected or are disabled are under *Inactive* (greyed out), and the same outputs through
  other audio systems under *Other connections*. Raw Windows driver names are shown readably
  ("Headset (Mpow Flame Solo Hands-Free)"). On a Mac, where every listed output is connected,
  the menu is just the list.
- **Refined logo artwork** in the app, the Windows icon and the installer.
- **About:** the VoiceCleaner name in the brand typeface, and a description of what it does now.

## 1.1.7

- **New VoiceCleaner logo:** the Steamburger burger now has a sound wave between the buns. It
  is in the app, the Windows icon (every size from 16 to 256 px) and the installer, drawn
  from vector artwork so it stays crisp at every size, with a soft edge that keeps it visible
  on light backgrounds as well as dark.

## 1.1.6

- **Edit Take in the Enhance tab.** Click *Edit Take…* above the Original track (or right-click
  either track, or press E) to cut out a pop, a click or a stumble, or silence it, with the
  same editor as Compile. The edits are the same for both tracks: the cut parts are marked on
  both waveforms, both tracks skip them while playing (and play silenced parts silent), so
  A/B still compares the same audio. Export writes the enhanced audio with the edits. The
  original file is never changed; the edits are kept with the session and are the same ones
  Compile uses for the take, so a take edited in one tab is edited in the other. *Clear
  Edits* in the same menu removes them.
- **The playhead goes to the start of a selection.** After dragging across a waveform in the
  Enhance tab, the playhead is at the start of the selection, so Play starts right there.
- The Original track's header no longer has its own Teleprompter button, making room for
  Edit Take. The teleprompter opens from the button in the top bar, the start page, the menu
  or Ctrl+T, as before.
- The grey line drawn along the top of the tabs is gone; the header now meets the tabs in the
  theme's own colours, dark and light.

## 1.1.5

- **Quieter, even pauses.** A new *Pause clean-up* stage (Advanced → Noise reduction) turns
  down the background left between phrases and after the last word to an even, quiet room
  tone, never to digital silence. Speech is not touched, and a laugh or breath in a pause stays
  as it is. The AI model keeps its gate open for a moment after speech, so the end of a take
  used to be much noisier than its start: on a real 6-second take, the trailing pause was
  31 dB below the speech and the lead-in 41 dB. With 1.1.5 both are 55-56 dB below, and
  hiss above 8 kHz in the pauses is 15-23 dB lower. It follows the Noise Reduction slider
  (and Smart settings), like the other noise controls.
- **Noise learned from short pauses.** A third of a second of steady noise before the first
  word is now enough to learn the background automatically (it was a second). When there is
  no learned profile, about 0.4 s of steady noise across all the pauses is now trusted for the
  profile noise reduction (it was 0.75 s). Short, changing stretches (a rustle, a door) are
  still not used.
- **Batch and the command line learn the noise before the first word** too, as the Enhance
  tab does when a recording opens, so a file comes out the same either way. Batch Preview
  uses it as well.
- **EQ warning.** When the EQ cuts the body of the voice (250 Hz-1 kHz) by 9 dB or more,
  a note under the EQ says it can sound hollow or distant. The EQ is still applied as set.

## 1.1.4

- **Opens more than WAV.** Phone voice memos (M4A/AAC), MP3, FLAC, Opus/OGG, AIFF, WMA and
  AMR, and the sound of video files such as Zoom recordings (MP4, MOV, MKV, WebM, AVI): in the
  Enhance tab, Batch, Compile and Playback. The first audio track is used; surround is mixed
  down to stereo. Results are WAV (or MP3), named after the original. M4A, AAC, WMA and video
  are decoded by an LGPL build of FFmpeg that ships with VoiceCleaner (its source code is
  attached to the release).
- **Batch preview:** turn on *Preview* in the Batch tab (`P`) and click a file to hear a
  10-second sample (the part with the most speech) enhanced with the current settings, A/B
  at equal loudness. Clicking another file previews that one, and the sample follows setting
  changes, so you can tune before enhancing everything.
- **Command line:** `VoiceCleanerCLI --batch recordings out --preset Natural` enhances files
  and folders without the window (`--format mp3`, `--bits 16`, `--recursive`, `--no-smart`,
  `--list-presets`), with one progress line per file and an exit code for scripts.
- **Recording options** (Enhance tab, *Record*): a **3-second countdown** before recording
  starts, and **Record 1 s of room tone first**: a quiet second (*Stay quiet* → *Speak now*)
  that becomes the noise profile of the take. Big, colour-coded cues show what to do: *GET
  READY · 3*, *STAY QUIET*, *SPEAK NOW*. Both options are remembered.
- **Clipping repair.** Recordings made too loud have their cut-off peaks rebuilt (sparse
  declipping): every unclipped sample stays as recorded, and the lost peaks are filled in
  from the surrounding sound. It runs automatically when clipping is detected (*Advanced →
  Repair clipping*). On clipped test speech, intelligibility (STOI) after enhancing rose
  from 0.88-0.97 to 0.94-0.99.
- **Enhance pauses playback** (whichever track is playing) until it is done; Play waits until
  then, so you never hear a half-finished state.
- **Fixed:** the installer's side panel showed the VoiceCleaner name cut off (1.1.3).
- **README:** download links and what VoiceCleaner does come first; building from source
  moved to `BUILDING.md`.
- **Mac:** the same formats open on a Mac. Its FFmpeg is compiled from the same source as the
  Windows one (LGPL, no external libraries); on a Mac, `VoiceCleaner.app/Contents/MacOS/VoiceCleaner
  --batch ...` is the command line.

## 1.1.3

- **Noise learned automatically.** When a recording starts with at least a second of
  background noise before the first word, VoiceCleaner learns that noise when the recording
  opens (shown on the waveform; *Clear Noise Profile* undoes it).
- **A learned noise profile now makes a real difference.** VoiceCleaner trusts a profile you
  (or the automatic lead-in) provide: steady noise is removed more firmly and the AI keeps
  its full range, about 4-8 dB less background noise in pauses. It also works on recordings
  without clear pauses.
- **Scrolling never changes a setting.** The mouse wheel scrolls the settings, even over a
  slider, the preset box or a number box. Click a control first to adjust it with the wheel
  (its knob turns blue).
- **System fonts by default** for easier reading; the Steamburger Studios fonts are one click
  away (⋯ menu → Appearance → Brand Fonts). The logo keeps its brand face.
- **Cleaner look:** the file's details sit in one quiet line under its name; the processor and
  AI model in the header are plain text, not buttons; a smooth day/night button; the selected
  tab now sits in front, joined to its page.
- **Fixed:** learning or clearing a noise profile could push the playback row off the side of
  the window or onto a second line; the selection stayed visible on the Enhanced waveform
  after learning, and learning again then asked for a selection.
- **Fixed:** with system fonts the first tab read "ENHAN..." (the tab bar measured its tabs
  in a different font from the one they are drawn in).

## 1.1.2

- **Getting Started tour.** The first start of a new install walks through the basics, one
  stop per tab: the window dims around each control and a short note explains it (Back,
  Next, Skip; arrow keys and Esc work too). After an update, VoiceCleaner shows only what
  is new since the version you used before, and nothing when there is nothing new. Both
  are in the ⋯ menu any time (*Getting Started*, *What's New*).
- The voice re-synthesis model stays where it was downloaded
  (`%LOCALAPPDATA%\VoiceCleaner\models`, on a Mac `~/Library/Application Support/VoiceCleaner/models`)
  through updates; it is only downloaded again
  when the model itself changes.

## 1.1.1

- **A warmer, smoother default sound.** The automatic voice EQ no longer thins the low end
  of every recording or brightens the upper mids by itself; it only corrects what is
  clearly off. A new **De-esser** (Dynamics, 50% by default) softens harsh "s", "sh" and
  "t" sounds only where they stick out. Measured against clean speech, the excess in the
  2-6 kHz range dropped from +1.7 dB to +0.3 dB, with unchanged intelligibility.
- **Take editor in Compile** (`E`, *✂ Edit Take…*, or right-click a take): drag across a
  pop, click, stumble or a pause the trimming missed, then **Cut** it out (`X` / `Delete`)
  or **Silence** it in place (`S`). Zoom in to a few milliseconds (`Ctrl`+wheel, `Z`),
  hear the selection or the edited result, undo. Edits are saved with the session, kept
  when a teleprompter take is enhanced, and never change the original file.
- **Re-enhance takes in Compile** (*↻ Re-enhance*, or right-click; one take or many): from
  the original recording, with the current settings or any preset, in the background. The
  version it replaces is kept (*Revert to Previous Version*), and take edits stay in place.
  **Open in Enhance Tab** fine-tunes a take with A/B and live preview; *Update Take in
  Compile* then puts the result back.
- **Teleprompter, always at hand:** a *TELEPROMPTER* button in the header (it shows when
  the prompter is open, and REC while it records) and next to *Record* on the Original
  track.
- **Sessions:** a new session starts clean, on the Enhance tab: the previous session's
  recording and Compile takes are closed. If the open recording was enhanced but not
  exported, VoiceCleaner offers to export it first. The start screen lists the session's
  newest recordings under **THIS SESSION**.
- **Mac: desktop audio recording.** *System audio (all apps)* under Desktop audio records what
  the Mac plays (the other side of a call, a video, a browser tab), alone or together with a
  microphone, aligned and drift-corrected as on Windows. It uses macOS's own system-audio
  capture (macOS 14.2 or newer; macOS asks once for permission) and leaves out VoiceCleaner's
  own playback, so an overdub never records the Original. On older macOS, a loopback driver
  such as BlackHole is listed under Desktop audio instead of Microphone.

## 1.1.0

- **Teleprompter** (`Ctrl+T`, or *Teleprompter* on the start screen and in Compile): a pop-out
  prompter that records while you read.
  - Every script section (`## Heading | Clip name`) becomes its own take, named after the
    clip: a new take starts by itself when the next section comes up. Cuts are placed in the
    pause between sections, so nothing is cut off or repeated.
  - **Retake** (`Backspace` / `PageUp`) records the section again; all takes are kept and the
    newest is used, unless one is marked **Keep** (`G`). **Last take** (`P`) plays it back.
  - Takes are **enhanced automatically** in the background and appear in **Compile in script
    order** (*TELEPROMPTER TAKES*), with a take picker per section (right-click).
  - *Step* mode (wait on each section or line), *Lines* mode (one take per paragraph),
    presentation clicker and foot pedal keys (PageUp / PageDown), REC indicator with level
    meter, open or edit the script in the prompter, mirror, stage directions, countdown,
    adjustable speed and size, fullscreen. Pausing the prompter pauses the take.
  - Uses the app's colours and follows its day/night setting (the ◐ button can keep it
    always Dark or Light). Warns when the microphone sends only silence.

- **Mac:** the teleprompter's controls and help use the app's text size and typeface (its
  control bar no longer runs past the window edge), the retake key is shown as *Delete (⌫)*,
  and the script editor uses DM Mono.

## 1.0.9

- **Install updates from inside VoiceCleaner (Windows).** When an update is available,
  *Install Update* downloads it in the app (with progress, and an interrupted download
  continues where it stopped), checks it against the checksum GitHub publishes, and then
  installs it: *Install and Restart* now, or *Install When I Quit*. VoiceCleaner closes, the
  installer runs with a progress bar (Windows asks for permission), and VoiceCleaner opens
  again and confirms the new version. The GPU edition runs its web installer, which downloads
  the update with its own progress window.
  - This works from 1.0.9 on: the installer now records where VoiceCleaner is installed, so
    updates go into the same folder. To get 1.0.9 itself, install it as before.
  - Portable copies keep opening the release page. On macOS the DMG is downloaded, checked
    and opened, showing VoiceCleaner next to the Applications folder to drag it over.
- Quitting during work still asks first; an update chosen with *Install and Restart* then
  waits until you quit.

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
