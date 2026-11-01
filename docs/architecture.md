# Architecture

A map of the code for people who want to change it. Read [CONTRIBUTING.md](../CONTRIBUTING.md) for how to set up and send
a change, and [Clip format](clip-format.md) for what the program writes.

## The layers

```text
recorder/
  ui/            the PySide6 window: draws what the session says changed, and forwards what the person does
  session/       the controller. No toolkit. Session.tick() drives it, and a Bus tells the window what changed
  core/          pure functions on arrays and recipes: edits, chain, render, analysis, autofix
  store.py       the files: speakers, originals, kept clips and their sidecars, the autosave files of takes not kept yet,
                 and moving a take to another speaker
  share.py       a copy of a kept clip as WAV, FLAC, MP3 or Ogg at another sample rate (SoundFile's libsndfile, no FFmpeg)
  manifest.py    a list of the kept clips, as JSON Lines or CSV, for programs that read a whole set
  audio_core.py  small pure helpers, and the rules for what can be kept
  capture.py     microphone (sounddevice) and system audio (PyAudioWPatch), behind one interface
  audio_io.py    playback through a sounddevice output stream
  clean.py       noise reduction by running the user's own FFmpeg
  transcript.py  draft transcripts by running a faster-whisper of the user's (asr_worker.py does the work)
  asr_setup.py   setup-transcripts.bat: installs that faster-whisper (.venv-asr, requirements-asr.txt) and a model
                 (models/) when the person runs it and agrees, then fills in the settings. The one code path that goes
                 online, and the program never calls it by itself
  jobs.py        a small pool of worker threads
  settings.py    settings.json, and where the clips go by default
  __main__.py    starting up: --check, --shortcut, --setup-transcripts, and the window
  launch.py      what a windowless start needs: a log file, one copy at a time, and an error window instead of a console
  shortcut.py    the icon in the program folder, on the desktop or in the Start menu (a Windows .lnk file made through the shell's own COM interfaces)
  hotkey.py      F9 while another program is in front: Windows' RegisterHotKey, heard in Qt's event loop
  resources/     the program's icon, drawn by tools/make_icon.py
tools/           shoot.py draws the screenshots; synthetic.py makes test audio and a fake capture; make_icon.py draws the icon
tests/           the test suite
```

The dependencies point downward. `core/` and `session/` never import PySide6, so almost everything the program does is
tested without a display, a microphone or a speaker. The window is a thin view over the session.

## A take and its recipe

A **take** (`store.Take`) holds the original samples, which are never changed, and a **recipe** (`core/edits.History`):
the list of operations (cut, silence, fade, region gain), the processing chain settings, and the join fade. Every change
to the recipe is one step in an undo history; Undo, Redo and "go back to step N" all move along it. Times in the recipe
are seconds on the original's timeline, so they survive undo and redo.

Because the original is written to disk the moment recording stops and the recipe is separate, editing is
non-destructive: **Keep** renders the recipe to a new file, and the sidecar stores the recipe, so a kept clip can be
opened again and its edits are still editable.

## Rendering

`core/render.py` turns an original and a recipe into a clip, in a fixed order: mix to mono; silence, fades and region
gain; cuts and joins; high-pass and hum notch (`core/chain.py`); noise reduction (`clean.py`); gain and limiter; then
resampling to 24 kHz for export, and the limiter once more at that rate, because resampling can push a peak over the
ceiling (`render.finish`). Each stage is cached by what decides its output, so moving the level slider does not run
FFmpeg again.

Playback, the Check tab, the transcript draft and Keep all use this one pipeline, so **what you hear and measure is what
Keep writes**. `core/analysis.py` holds the measurements (peak pyramid for drawing, spectrogram, loudness, noise floor,
hum, bandwidth, silence) and `quality_report()` which builds the Check tab's rows.

## Auto analyze and Auto fix

`core/autofix.py` turns the measurements into a plan. `diagnose()` looks at the take as it is now, then works out a
sequence of steps (a rumble filter, a hum notch, noise reduction, trimming the ends, shortening long pauses, a level) in
the order that lets each one see the effect of the last. Each step is tried on a **copy of the recipe and rendered**, so
the plan is measured rather than guessed. It returns the findings in plain words, the operations and chain settings that
would fix them, and a before-and-after for each number that changes. It is a pure function of the recipe and a render
function, so it is tested on generated audio with known faults. The rules are conservative on purpose: noise reduction
can change a voice, so it is only used on a clearly noisy clip and is kept only if it measurably lowers the noise; a
clip is never shortened below the 6 s the Keep rules need; and a second look at a fixed clip finds nothing more to do.

`session/advisor.py` runs it. It works out the advice on a worker thread, caches it per take under the recipe it was
made for (a result for an older recipe is dropped), follows the clip as it is edited, and applies a fix through
`Editor.apply_auto_fix`, which adds **all** of its steps to the undo history as one. The Check tab's card (`AutoCard` in
`ui/inspector.py`) only shows what the advisor holds.

## Threads, the tick and the clock

- **Device threads.** The microphone and loopback callbacks copy audio into buffers and nothing else.
- **The main thread** runs `Session.tick()` about twenty times a second from a `QTimer`. `tick()` reads the meter, notices
  a device that has stalled, ends a recording when its stop tail is over, moves the playhead, writes the autosave files
  that are due, and delivers finished jobs.
- **Worker threads** (`jobs.py`) do the slow work: measuring, rendering for playback, FFmpeg, and the speech recognizer. A
  job's result waits in a queue until `tick()` hands it to the main thread, so no code outside `jobs.py` needs a lock.
  Jobs are given a frozen copy of the recipe, so edits made while they run cannot change what they render.
- **The clock** is injected, so the recording state machine and the stop tail run in tests without sleeping.

## Starting up

The desktop icon runs `pythonw.exe -m recorder`, which has no console, so `launch.py` supplies what a console would have
been used for. It writes `voice-slate.log` (and, if a native library crashes, `voice-slate-crash.log`) beside the
settings file, replaces the missing `stdout` and `stderr` with the log, and shows an error window for a failure that
would otherwise be silent. A named mutex allows one copy at a time: a second start brings the first window forward and
exits. `shortcut.py` makes the `.lnk` through Windows' own COM interfaces with `ctypes` and gives it the same
application id as the running program, so the taskbar groups the window with the icon it was started from. Neither module
imports Qt, and `python -m recorder --check` reports what is installed without opening a window.

`hotkey.py` is what lets `F9` work while another program has the focus, which a Qt shortcut cannot do. It asks Windows
(`RegisterHotKey`, through `ctypes`) to tell the program when that one key is pressed, so it never sees any other key, and
it hears the answer in Qt's event loop through a native event filter. `ui.run()` gives the window a `GlobalKey`; the window
runs the same Record command a click does, and gives the key back when it closes or when Settings turns it off. A window
built without one (every test and `tools/shoot.py`) keeps `F9` as an ordinary shortcut, so nothing but the real program ever
takes the key from the machine it runs on.

## Keeping the person's audio safe

- The original is written (to a temporary file, then renamed) when recording stops. If it cannot be written, the take
  stays in memory flagged *Not saved*, and the window refuses to close until it has been written.
- Exports, sidecars and settings are written atomically (temporary file, then rename). Names are never overwritten: a clash
  gets a numeric suffix. A damaged `speakers.json` or `settings.json` is moved aside and rebuilt, never overwritten.
- Discard goes through `send2trash`, so it is recoverable. If the Recycle Bin is not available the person is asked
  before anything is deleted for good.
- Each export's SHA-256 goes in its sidecar.
- The work on a take that is not kept yet (its recipe, its text and its label) is saved to `masters/<name>.autosave.json`.
  `session/autosave.py` marks a take when the bus says `edits` changed, or `meta` with that take as its payload, writes it
  once the take has been still for a second (or after five seconds of steady change, such as typing), and writes everything
  waiting when another take is opened and when the session closes. `Library.autosave` writes the file and
  `Library.load_unkept` reads it back, checking it against the original it sits beside (name, sample rate and length) and
  its recipe by the same rules as a sidecar's (`Library._recipe_from`). A file that fails is left alone, and the take loads
  as it was recorded, with the reason in `take.autosave_problem`. The permission tick is never in it, as it is never
  remembered. Keep deletes it, and Discard sends it to the Recycle Bin with the other files. A failed write is reported
  once and tried again; closing with a write still failing refuses once, and then lets go, so a broken folder cannot trap
  the window. A kept take has no autosave file: only a changed label is written, into its sidecar.

## Moving, copying and listing clips

None of these changes a kept clip, and none goes online.

- **Moving a take** is `Library.move_take`, called through `Workspace.move_take`, which keeps the lists and the open take
  right. The order is what makes it safe. The files are copied to the new speaker's folder under a temporary name and
  compared by SHA-256 (`_copy_checked`); the new sidecar is written, with `speaker`, the new file names and a `moves`
  entry; and only then are the old files removed, the sidecar first (`_remove_old`). A whole copy of the take exists at
  every moment. A failure before the removal removes what was made and leaves the take where it was, and one during it
  comes back as the list of files left behind. A take that is not kept moves its original, writes its autosave at the new
  place, and takes the new speaker's remembered origin and allowed use only if it still had the old speaker's. The tests
  in `tests/test_store_move.py` make every file operation fail in turn.
- **Save a copy** is `share.save_copy`. It reads the kept WAV, resamples it with `audio_core.resample`, and writes it
  through SoundFile to a temporary name and then renames it. It writes 8192 frames at a time: libsndfile's Ogg Vorbis
  writer crashes the whole process (a stack overflow, which no `try` catches) when it is given half a minute of audio in
  one call, and the tests run every encoder on a long clip in a subprocess so that this cannot come back.
- **Export manifest** is `manifest.collect` and `manifest.write`. A row is built from each sidecar, read by the rules of
  the library (a name that leaves the sidecar's folder is refused), and the WAV is hashed again; a clip that does not
  check out is left out and named. The escaping is described in [Clip format](clip-format.md#manifest-files).
- **Drag out** is in `ui/library.py`. A kept card pulled past `startDragDistance` emits `dragRequested`, and the panel
  starts a `QDrag` carrying the file URL of the WAV, offered `CopyAction` only. `run_drag` is the seam the tests use,
  because `QDrag.exec` waits for a real mouse; the drop itself is the system's, and is not tested.

## The window

- **`session/bus.py`** carries topics (`takes`, `current`, `meta` (with the take whose text changed, when that is worth
  saving), `edits`, `selection`, `playhead`, `recording`, `analysis` and so on). The window marks the parts that heard a topic and redraws them once per event-loop turn (`MainWindow.flush`).
- **`ui/actions.py`** defines every command once (`SPEC`: name, icon, shortcuts, tooltip). Toolbar buttons, menus and
  shortcuts all use the same `QAction`, so a command is enabled, disabled and described in one place.
- **`ui/dialogs.py`** is the only place that opens a modal dialog or a context menu, behind a small `Dialogs` class that
  tests replace with canned answers, so no test can block on a dialog.
- **`ui/timeline.py`** draws the waveform into a cached pixmap and only redraws the cursor, selection and edits when
  they change. The waveform, spectrogram and overview share one `ViewRange`.
- **`ui/theme.py`, `palette.py`, `icons.py`** hold the dark theme (one palette, one stylesheet, pixel-sized fonts so the
  window scales as a whole) and the icons, which are SVG geometry drawn in code.
- **`ui/toolbar.py`** collapses labels, then icons, then buttons into a "more" menu as the window narrows, so nothing is
  clipped at the minimum size of 1100 by 700.

## Tests

`python -m pytest` runs the suite, with no device, display or network:

- The session, store, core and settings tests use fakes from `tests/helpers.py` and `tests/conftest.py`: a fake capture,
  a fake player, a controllable clock, and a stand-in Recycle Bin.
- The window tests run on Qt's **offscreen** platform with canned dialogs. They load the system's fonts, because the
  offscreen platform finds none on Windows. They deliver mouse events directly rather than through `QTest.mouseMove`,
  which proved unreliable.
- Rendering and Keep are tested against generated audio with known properties.

`tools/synthetic.py` and `tools/shoot.py` build the screenshots in `docs/images` from generated audio, with a temporary
library, so the pictures never contain a real recording.

## Extending it

**A new edit operation.** Add it to `core/edits.OPS` and `validate_edits`, give `History` a method, apply it in
`core/render.py`, add a command to `ui/actions.SPEC` and a button, and describe it in [Clip format](clip-format.md). The
sidecar stays on schema `v2` while old files still load.

**A new processing step.** Add its defaults and limits to `core/chain.py` (`normalize` fills missing keys, so older
sidecars still load), run it in `core/render.py`, add a card to the Process tab, and document it.

**A new automatic repair.** Add a finding and its step to `core/autofix.diagnose`. The step must be tried on a copy of
the recipe, leave a clip that Keep accepts, and be one that a second look at the fixed clip does not want to repeat. Add
its key to `SHOWN_AFTER`, so the card says what changes, and test it on generated audio that has the fault.

**A new keep rule.** Change `audio_core.keep_problems` and the checklist in `session/workspace.readiness`; they must agree,
because the checklist is what the person sees and `keep_problems` is what enforces it. The permission, origin and
allowed-use rules are not to be loosened: see [Responsible use](responsible-use.md).

Each of these needs tests alongside it.
