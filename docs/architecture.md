# Architecture

A map of the code for people who want to change it. Read [CONTRIBUTING.md](../CONTRIBUTING.md) for how to set up and send
a change, and [Clip format](clip-format.md) for what the program writes.

## The layers

```text
recorder/
  ui/            the PySide6 window: draws what the session says changed, and forwards what the person does
  session/       the controller. No toolkit. Session.tick() drives it, and a Bus tells the window what changed
  core/          pure functions on arrays, recipes and text: edits, chain, render, analysis, autofix, reading
  store.py       the files: speakers and their releases, originals, kept clips and their sidecars, the spool files of takes
                 being recorded, the autosave files of takes not kept yet, moving a take to another speaker, and moving a
                 whole output folder (Relocation). A take reads its audio only when it is asked for (_Resident)
  share.py       a copy of a kept clip as WAV, FLAC, MP3 or Ogg at another sample rate (SoundFile's libsndfile, no FFmpeg)
  manifest.py    a list of the kept clips, as JSON Lines or CSV, for programs that read a whole set
  dataset.py     the kept clips gathered in one new folder, as clips with sidecars or in the LJSpeech layout
  audio_core.py  small pure helpers, the purposes, and the rules for what can be kept
  i18n.py        the window in other languages: tr() and the tables in resources/lang
  capture.py     microphone (sounddevice) and system audio (PyAudioWPatch), behind one interface
  audio_io.py    playback through a sounddevice output stream
  clean.py       noise reduction by running the user's own FFmpeg
  audio_import.py reading a recording to import: SoundFile for its own formats, the user's FFmpeg for the rest (M4A)
  transcript.py  draft transcripts by running a faster-whisper of the user's (asr_worker.py does the work); a session
                 keeps one worker running (Worker), so the model loads once
  asr_setup.py   setup-transcripts.bat: installs that faster-whisper (.venv-asr, requirements-asr.txt) and a model
                 (models/) when the person runs it and agrees, then fills in the settings. The one code path that goes
                 online, and the program never calls it by itself. It can use another Python than the program's, because
                 the recognizer's packages are built for fewer versions (PYTHONS). It only ever lists what the Python
                 launcher has: told to run a version it lacks, the launcher can download that version
  jobs.py        a small pool of worker threads
  settings.py    settings.json, and where the clips go by default
  __main__.py    starting up: --check, --shortcut, --setup-transcripts, and the window
  diagnostics.py what is installed (--check), and the zip of Export diagnostics, with names in it replaced
  launch.py      what a windowless start needs: a log file, one copy at a time, and an error window instead of a console
  shortcut.py    the icon in the program folder, on the desktop or in the Start menu (a Windows .lnk file made through the shell's own COM interfaces)
  hotkey.py      F9 while another program is in front: Windows' RegisterHotKey, heard in Qt's event loop
  resources/     the program's icon, drawn by tools/make_icon.py, and the language files
installer/       the Inno Setup script of the Windows installer
tools/           shoot.py draws the screenshots; synthetic.py makes test audio and a fake capture; make_icon.py draws the icon;
                 build_installer.py builds the installer; strings.py lists the text there is to translate
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
resampling to the rate of the take's purpose for export, and the limiter once more at that rate, because resampling can push a
peak over the ceiling (`render.finish`). Each stage is cached by what decides its output, so moving the level slider does not run
FFmpeg again.

`clean.denoise` keeps the clip in step: afftdn holds the audio back and its output is only as long as its input, so the
delay is measured (once for each FFmpeg and sample rate, on a burst of noise) and taken out, and the input is padded so the
end of the clip gets through.

Playback, the Check tab, the transcript draft and Keep all use this one pipeline, so **what you hear and measure is what
Keep writes**. Clipping is the one thing the finished clip cannot show, because gain and the limiter hide it, so
`render.recorded_clipping` measures it on the original, on the loudest channel and leaving out what was cut or silenced.
`core/analysis.py` holds the measurements (peak pyramid for drawing, spectrogram, loudness, noise floor, hum, bandwidth,
silence) and `quality_report()` which builds the Check tab's rows.

## Auto analyze and Auto fix

`core/autofix.py` turns the measurements into a plan. `diagnose()` looks at the take as it is now, then works out a
sequence of steps (a rumble filter, a hum notch, noise reduction, trimming the ends, shortening long pauses, a level) in
the order that lets each one see the effect of the last. Each step is tried on a **copy of the recipe and rendered**, so
the plan is measured rather than guessed. It returns the findings in plain words, the operations and chain settings that
would fix them, and a before-and-after for each number that changes. It is a pure function of the recipe and a render
function, so it is tested on generated audio with known faults. The rules are conservative on purpose: noise reduction
can change a voice, so it is only used on a clearly noisy clip, set from that clip's own noise floor, and is kept only if
it measurably lowers the noise and `analysis.voice_loss_db` finds the voice still there (the speech above the noise goes up
even when the voice is taken along with the noise, so that measure cannot judge it alone); a clip is never shortened below
the shortest length its purpose can keep; and a second look at a fixed clip finds nothing more to do.

## Purposes

`audio_core.PURPOSES` holds a `Purpose` for each of Reference, Sentence and Audition: its length rules, the longest recording, and
the format it is kept in. A take's purpose is `take.meta['purpose']`, stamped from `Workspace.purpose` when the take is made, and
`take.purpose` and `take.export_rate` read it. Everything that judges a length or writes an export asks the take: `take_warnings`,
`Workspace.readiness`, `quality_report`, `autofix.diagnose`, `Library.keep`, and the drawing of the length guide. A recording stops at
`record_limit`, which is well past `max_seconds` on purpose, so that it never stops in the middle of a sentence. A sidecar with no
`purpose` is a Reference. A measurement depends on the purpose as well as the recipe, so caches are keyed by `measure.take_key`.

## A big library

`Take.samples` is a property, set after the class is defined, because a dataclass cannot have a field and a property of one name.
A take loaded from disk knows its length from `sf.info` and has no audio until something asks. `store._Resident` counts what is held,
in order of use, by weak reference so that a discarded take does not stay in it, and past 256 MB the oldest takes let go of their
audio and of what was worked out from it. The take in front (`in_use`) and a take whose original is not on disk (`unsaved`) never let
go. Another thread can let a take go at any moment, so code that reads `take.cache` for something made from the audio reads it once.

The list of takes (`ui/library.py`) is a `QListView` over a model, drawn by a delegate, so only the rows on screen cost anything.
`Measurements.thumbnail` works out the outline of a row in the background the first time it is drawn.

`session/advisor.py` runs it. It works out the advice on a worker thread, caches it per take under the recipe it was
made for (a result for an older recipe is dropped), follows the clip as it is edited, and applies a fix through
`Editor.apply_auto_fix`, which adds **all** of its steps to the undo history as one. The Check tab's card (`AutoCard` in
`ui/inspector.py`) only shows what the advisor holds.

## Threads, the tick and the clock

- **Device threads.** The microphone and loopback callbacks copy audio into buffers and nothing else.
- **The main thread** runs `Session.tick()` about twenty times a second from a `QTimer`. `tick()` reads the meter, writes
  what has been recorded to the spool file, notices a device that has stalled, ends a recording when its stop tail is over,
  moves the playhead, writes the autosave files that are due, and delivers finished jobs.
- **Worker threads** (`jobs.py`) do the slow work: measuring, rendering for playback, FFmpeg, and the speech recognizer. A
  job's result waits in a queue until `tick()` hands it to the main thread, so no code outside `jobs.py` needs a lock.
  Jobs are given a frozen copy of the recipe, so edits made while they run cannot change what they render.
- **The clock** is injected, so the recording state machine and the stop tail run in tests without sleeping.

## Starting up

The desktop icon runs `pythonw.exe -m recorder`, which has no console, so `launch.py` supplies what a console would have
been used for. It writes `voice-slate.log` (and, if a native library crashes, `voice-slate-crash.log`) beside the
settings file, replaces the missing `stdout` and `stderr` with the log, and shows an error window for a failure that
would otherwise be silent. An error that nothing caught, on any thread, is logged and then passed to the window through its
`unexpected` signal, so it is shown rather than lost; the window shows each message once, because a fault in something that
repeats would otherwise open a dialog twenty times a second. A named mutex allows one copy at a time: a second start brings
the first window forward and exits. `shortcut.py` makes the `.lnk` through Windows' own COM interfaces with `ctypes` and gives it the same
application id as the running program, so the taskbar groups the window with the icon it was started from. Neither module
imports Qt, and `python -m recorder --check` reports what is installed without opening a window.

`hotkey.py` is what lets the record key work while another program has the focus, which a Qt shortcut cannot do. It asks Windows
(`RegisterHotKey`, through `ctypes`) to tell the program when that one key is pressed, so it never sees any other key, and
it hears the answer in Qt's event loop through a native event filter. `ui.run()` gives the window a `GlobalKey`; the window
runs the same Record command a click does, and gives the key back when it closes or when Settings turns it off. It is off unless
the settings turn it on. `parse_key` says which keys can be the record key: a function key, or a letter or digit held with Ctrl or
Alt, so that it cannot be typed by accident. While a recording runs and the window is not the active one, `ui/badge.py` shows a small
sign on top of everything, which takes no focus. A window built without a `GlobalKey` (every test and `tools/shoot.py`) keeps the
key as an ordinary shortcut, so nothing but the real program ever takes a key from the machine it runs on.

`ui.run()` sets the look before the window is built: the language (`i18n.use`), the theme (`palette.use`) and the size, which is
Qt's `QT_SCALE_FACTOR`, read as the application starts. That is why Settings says they apply at the next start.

The installed program is not frozen into an `.exe`. `tools/build_installer.py` puts a relocatable CPython (fetched by `uv`) and the
pinned packages in a folder, removes what the window does not use from Qt and Python, and opens the real window from that folder as
a test, before Inno Setup packs it (`installer/voice-slate.iss`). A real Python is what lets `setup-transcripts.bat` make its
environment there too, Qt stays a plain library that can be replaced, and virus scanners flag it less often than a frozen program.
It installs per user, so the program folder is writable.

## Keeping the person's audio safe

- While a take runs, `Capturing.tick` writes what has arrived to a `store.Spool` in `masters/`, and forces it to the disk
  every two seconds. The audio callbacks stay out of this: they only fill buffers. A failure here never touches the
  recording, which is in memory anyway. `Library.load_takes` makes a take of any spool it finds (`_recover_spools`), flagged
  *interrupted*, unless a recording is running, because its spool is in the same folder.
- The original is written (to a temporary file, then renamed) when recording stops. If it cannot be written, the take
  stays in memory flagged *Not saved*, its spool stays on disk, and the window refuses to close until it has been written.
- Exports, sidecars and settings are written atomically (temporary file, then rename), and recordings and sidecars are forced
  to the disk first (`store.sync_file`), because a file renamed while it is still in Windows' cache can be empty after a power
  cut. Names are never overwritten: a clash gets a numeric suffix. A damaged `speakers.json` or `settings.json` is moved aside
  and rebuilt, never overwritten.
- `Library.keep` finishes both the WAV and the sidecar under temporary names before either goes in place, and only then sets
  `export_path`, which is what makes a take count as kept. A Keep that fails leaves the take as it was and still being
  autosaved. SoundFile reports failures as `RuntimeError`, so `_write_audio` turns them into `OSError`, which is what the
  window catches. Tests of this must make the real write fail (a folder standing where the file has to go), not raise a
  stand-in error.
- A sidecar is a text file that can be edited or arrive from elsewhere, so `Library._check_fields` checks the kind of every
  value it uses. One bad value costs that clip, which is reported, and not the whole list.
- Discard goes through `send2trash`, so it is recoverable. If the Recycle Bin is not available the person is asked
  before anything is deleted for good.
- Each export's SHA-256 goes in its sidecar.
- The work on a take that Keep has not written (its recipe, its text and its label) is saved to `masters/<name>.autosave.json`.
  `session/autosave.py` marks a take when the bus says `edits` changed, or `meta` with that take as its payload, writes it
  once the take has been still for a second (or after five seconds of steady change, such as typing), and writes everything
  waiting when another take is opened and when the session closes. `Library.autosave` writes the file and
  `Library.load_unkept` reads it back, checking it against the original it sits beside (name, sample rate and length) and
  its recipe by the same rules as a sidecar's (`Library._recipe_from`). A file that fails is left alone, and the take loads
  as it was recorded, with the reason in `take.autosave_problem`. The permission tick is never in it, as it is never
  remembered. Keep deletes it, and Discard sends it to the Recycle Bin with the other files. A failed write is reported
  once and tried again; closing with a write still failing refuses once, and then lets go, so a broken folder cannot trap
  the window.
- A kept take is compared with what its sidecar holds (`Take.changed`). While they differ it has an autosave file too, read
  back by `Library.load_kept`, and its card says *Changed*. The file carries the SHA-256 of the kept WAV it builds on, so one
  left behind by a Keep that could not delete it is ignored. `Library.revert` drops the changes. A label goes straight into
  the sidecar, as it changes nothing about the clip, and new words for the origin or allowed use clear the permission tick.

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
- **Export dataset** is `dataset.Export`, fed the rows of `manifest.collect` one at a time by `session/batch.py`. Made from the
  original, a clip goes through `render.export_samples` again at the rate asked for, with the recipe in its sidecar.
- **Moving an output folder** is `store.Relocation`: every file is copied and compared, and the old files go only when all of them
  have arrived. Stopped or failed on the way, the copies are removed and the old folder stays in use.
- **Drag out** is in `ui/library.py`. A kept card pulled past `startDragDistance` emits `dragRequested`, and the panel
  starts a `QDrag` carrying the file URL of the WAV, offered `CopyAction` only. `run_drag` is the seam the tests use,
  because `QDrag.exec` waits for a real mouse; the drop itself is the system's, and is not tested.

## Long sessions

- **The script** (`session/script.py`) is remembered in `script.json` beside the settings. What has been done of it is never stored:
  `Script.progress` counts `take.meta['script_line']` over the takes of the speaker in front, so it cannot go stale.
- **Many takes at once** (`session/batch.py`) run in `Session.tick`, a slice of 40 ms at a time, on the main thread, because each
  step changes files and lists the window shows. `Batch.keep` takes a statement the window collected. It goes on each take for its
  own Keep with `consent_batch`, and comes off again if that Keep fails: the permission is still never remembered.
- **The reading check** (`core/reading.py`) compares words with `difflib`, without capitals or punctuation, and letter by letter in
  languages written without spaces. `workspace.reading_of` gives the check for a take, or None when the draft was made before the
  latest cuts, and `Drafts` asks for a new draft when that happens.
- **The words** come from the recognizer in seconds of the edited clip, and `Drafts` stores them in seconds of the master, as the
  edits are. `analysis.quietest_near` moves the edges of a word to the quiet beside it, because the recognizer's times are rough.
  `ui/words.py` shows them as links in a text view, which gives the keyboard and a screen reader what they need.
- **Splitting** (`analysis.split_ranges`, `Workspace.split_take`) makes ordinary takes with their own originals, cut in the middle of
  pauses, and leaves the long take alone.

## The window

- **`session/bus.py`** carries topics (`takes`, `current`, `meta` (with the take whose text changed, when that is worth
  saving), `edits`, `selection`, `playhead`, `recording`, `analysis` and so on). The window marks the parts that heard a
  topic and redraws them once per event-loop turn (`MainWindow.flush`).
- **`ui/actions.py`** defines every command once (`SPEC`: name, icon, shortcuts, tooltip). Toolbar buttons, menus and
  shortcuts all use the same `QAction`, so a command is enabled, disabled and described in one place.
- **`ui/dialogs.py`** is the only place that opens a modal dialog or a context menu, behind a small `Dialogs` class that
  tests replace with canned answers, so no test can block on a dialog.
- **`ui/timeline.py`** draws the waveform into a cached pixmap and only redraws the cursor, selection and edits when
  they change. The waveform, spectrogram and overview share one `ViewRange`.
- **`ui/theme.py`, `palette.py`, `icons.py`** hold the themes (a palette for each, one stylesheet, pixel-sized fonts so the
  window scales as a whole) and the icons, which are SVG geometry drawn in code. No colour is written anywhere else, and
  `tests/test_ui_access.py` measures the contrast of each theme.
- **`i18n.py`, `ui/translate.py`** translate the window. The English text is the key. The helpers in `ui/widgets.py` translate what
  they are given, and `translate_tree` goes over a finished window or dialog, so most code simply writes English. A text built from
  values stays in English until it is written with `{}` fields and `tr(...).format(...)`. `tools/strings.py` lists what a language
  lacks, and the tests refuse an entry whose fields differ from the English.
- **`ui/toolbar.py`** collapses labels, then icons, then buttons into a "more" menu as the window narrows, so nothing is
  clipped at the minimum size of 1100 by 600.

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

**A new purpose.** Add it to `audio_core.PURPOSES`. Everything else reads the rules from there.

**A new language.** Add it to `i18n.LANGUAGES` and `WINDOWS_LANGUAGES`, and put a file in `resources/lang`. See
[CONTRIBUTING.md](../CONTRIBUTING.md#making-a-change).

Each of these needs tests alongside it.
