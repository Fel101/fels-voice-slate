# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Phone recordings can be imported.** With FFmpeg installed, Import reads the M4A that phones and Windows' Sound Recorder
  make, AAC, AMR, WMA and WebM, and the sound of a video (MP4, MOV, 3GP). WAV, FLAC, Ogg, MP3 and AIFF are read as before,
  and a damaged one is still refused, not handed to FFmpeg. Without FFmpeg, the message says how to get it.
- **Several recordings can be chosen at once** in the Import dialog, as dragging several onto the window already allowed.
  One that cannot be read is named, and the rest come in.
- **Export diagnostics...** in the gear menu saves a zip to attach to a bug report: what `--check` says, the settings and the
  end of the log. The recordings folder, the home folder, the user name and the speakers' names and folders are replaced
  in it, however a path is written, and it is saved only where the person chooses.

### Changed

- **Draft transcripts are quicker after the first.** The speech recognizer is kept running between drafts, so the model
  loads once: a draft made again after a cut took 0.4 s instead of about 2 s with the tiny model. A recognizer that stops still
  says why, as before (a Python that cannot start, say), and it stops when the window closes.
- The release workflow no longer replaces an installer already on the Release, such as one built and tried by hand. Its own
  build is then kept with the run.
- The release workflow stops when installing its tools fails, instead of building with what it has.
- The tests run in parallel with `pytest -n auto` (pytest-xdist, for development only).

### Fixed

- Auto-trim, Auto fix and the Check tab find the pauses of a take that is mostly pause, such as one short word in ten
  seconds. The quiet before and after the word counted as speech, so nothing was trimmed. A take with a quiet voice, and one
  with no speech at all, are measured as they were.
- A window that has closed no longer has its last status line read out by a screen reader a moment later.

## [0.5.0] - 2026-11-02

### Added

- **Purposes.** A take is a *Reference* (6 to 40 s, 24 kHz, as before), a *Sentence* (1 to 15 s, for a dataset or voice
  banking, one sentence at a time) or an *Audition* (3 s to 10 minutes, kept at up to 48 kHz and 24-bit). Each has its own
  length rules, and the checklist, the Check tab, Auto fix and Keep all follow them. Choose what new takes are for under the
  speaker, and change it for one take on the Clip tab. The sidecar and the manifest say which it is.
- **A session mode for long scripts.** The script and your place in it come back when the program starts again. A new
  **Script** tab lists every line and whether it has a kept clip, for the speaker in front, and clicking a line goes to it.
  *Next line to record* (`Ctrl+Shift+Down`) skips what is done, and Keep does too. `Alt+Down` and `Alt+Up` walk the list of
  takes.
- **Several takes at once.** Hold `Ctrl` or `Shift` to select takes, or `Ctrl+A` in the list, then keep, discard or move them
  together, from the right-click menu or the gear menu. Keeping several still needs the statement of permission: a dialog
  shows what the takes say about their origin and allowed use, and Keep stays off until its box is ticked, every time. Each
  clip's sidecar records the statement with how many takes it covered (`consent_batch`). A take that fails a Keep rule is left
  out and named. A long job runs a little at a time, with a Stop button in the status bar.
- **A reading check.** A take read from a script line is compared with what the speech recognizer heard. The Clip tab shows
  the places that differ, *Ready to keep?* mentions them, and one link makes what was heard the transcript. It never stops
  Keep: the recognizer can be wrong too.
- **Editing by clicking words.** A take with a draft transcript shows its words under the waveform. Click a word to select it
  (the edges go to the quiet just outside it), Shift+click to run on to another, and press `Delete` to cut. A word that was cut
  stands struck through, and clicking it brings it back. The words and their times are saved with the take and in the sidecar
  (`words`).
- **Split at pauses...** makes a new take of each part of a long take, cut in the middle of its pauses, with its share of the
  draft transcript. The long take stays. A part's sidecar says where it came from (`split_from`).
- **Consent records for each speaker.** *Speaker details...* keeps the signed release in a `release` folder in the speaker's
  folder, with when it was signed and when it runs out. Once it has run out, no clip of that speaker can be kept until it is
  renewed, and a manifest or dataset leaves their clips out and says why. A release about to run out is mentioned. Each clip's
  sidecar names the release it rests on.
- **Delete speaker...** moves a speaker, their clips and their release to the Recycle Bin, after saying how much that is.
- **Export dataset...** gathers kept clips in one new folder: each clip with its sidecar, or the LJSpeech layout (`wavs` and
  `metadata.csv`), at 16 to 48 kHz, with a `manifest.jsonl` that carries the origin and allowed use. Clips can be made again
  from their original recordings, which keeps detail a 24 kHz clip has lost.
- **A Windows installer that needs no Python.** It brings its own private Python, installs for one person with no
  administrator rights, and adds a Start menu icon. `tools/build_installer.py` builds it, and a new workflow attaches it to a
  GitHub Release.
- **Choosing the hardware.** On a device with several inputs, a picker beside the device chooses which one to record, as a
  mono take; the status line says so when only one of two mixed inputs has sound, which lowers a voice by 6 dB. Settings chooses
  the playback device. The input device chosen is remembered, by name.
- **The record key can be changed** in Settings, to a function key or a letter or number held with Ctrl or Alt.
- **Set from this clip** on the Noise reduction card measures the clip's own noise and sets the noise floor about 5 dB above
  it, which is what Auto fix does.
- **A light theme and a high-contrast theme**, or whatever Windows is set to, and a **size** setting from 100% to 200% for the
  whole window. Without it the program follows Windows' own *Text size* setting.
- **German, Spanish and French.** The window follows the language of Windows, or the one chosen in Settings. Buttons, labels,
  tooltips and dialogs are translated, and so are most messages. Messages built from numbers, and the explanations on the Check
  tab, are still in English, and so are the documents. These translations are new and were not checked by native speakers.

### Changed

- **Recordings now go in your own folder**, `C:\Users\<you>\Recordings\voice-clips`, so that updating or removing the
  program cannot touch them. A copy that already has recordings in its `voice-clips` folder goes on using it, and Settings can
  move them: choose a new output folder, and each file is copied and checked before the old ones go.
- **A recording no longer stops at 40 s.** It runs on to 2 minutes for a Reference, and the take can then be trimmed or split.
  What can be kept has not changed.
- **`F9` in other programs is off until you turn it on** in Settings, because while it is on no other program gets the key,
  and a stray press opens the microphone. If you had it on, turn it on again.
- **While a recording runs with the window out of sight, a small sign stays on top of everything**, with the time and the key
  that stops it. A click on it stops the take.
- **The permission box is now in *Ready to keep?*, beside the Keep button**, so it is always in view, at any window size.
- **The window can be smaller:** 1100 by 600, which fits a 1080p screen at 150%. In a short window the notes fold away with the
  passed checks.
- **The list of takes is a real list.** It can be reached with `Tab` and walked with the arrow keys, a screen reader can read
  its rows, and it draws only the rows on screen.
- **Big libraries open quickly.** A take reads its audio when it is opened, and the takes not used lately let go of it. With
  1,000 kept clips of 13 s, the window opens in about 2.5 s, where it froze for 16, and the program stays between 200 and 450 MB,
  where 500 takes held 1.3 GB.
- **Every control has a name for a screen reader**, focus shows on every control, and the waveform can be reached with `Tab`.
  What the status line says is read out as it changes, so *Kept* or *Take saved* is heard, and the marks in the checklist and
  the script are read as words.
- Draft transcripts run with word times, and a line read from a script is drafted again after a cut, for the reading check.
- The manifest has three more columns at the end: `purpose`, `release_signed` and `release_expires`.
- The dimmest text of the dark theme is a little lighter: it fell just short of the 4.5 to 1 contrast the theme aims for.
- CI now tests Python 3.15 as well.
- The version is 0.5.0.

## [0.4.0] - 2026-11-01

### Added

- **Labels.** Give a take a name in the new field at the top of the Clip tab, with `F2`, or from the take's right-click
  menu. The label shows in the list in place of the take's number, and is saved in the sidecar as an optional `label`
  field (a take with no label has no such field). Changing the label of a kept clip is saved at once, without Keep.
- **Rename speaker...** in the gear menu, and on the right-click menu of the speaker list, changes the name a speaker is
  shown under. Their folder keeps its name, so no clip moves and nothing already kept is touched.
- **A filter box above the list of takes** shows only the takes whose label, transcript or notes contain all the words
  typed, whatever the capitals. The take numbers do not change while it is on.
- **Move to speaker.** A take recorded under the wrong speaker can be filed under the right one: right-click it and
  choose *Move to speaker*, or use *Move take to speaker...* in the gear menu. The files are copied to the new speaker's
  folder and checked, the new description is written, and only then are the old files removed, so a failure leaves the take
  where it was. A kept clip asks first, keeps its permission, origin and allowed use as they were written, and gets an
  optional `moves` list in its sidecar (where it came from, and when); its `speaker` field is the new folder, as programs
  that read the format expect. A take that is not kept moves with its edits and text.
- **Drag a kept clip out of the window.** Pull a kept take's card onto Explorer, a chat window or an audio program to
  copy the clip's WAV there. Only copying is offered, so a drop cannot move a clip out of the library.
- **Save a copy...** (`Ctrl+Shift+S`, the gear menu, or the take's right-click menu) writes a copy of a kept clip as WAV,
  FLAC, MP3 or Ogg Vorbis at 16, 22.05, 24, 44.1 or 48 kHz, made from the kept clip without FFmpeg. The kept clip is not
  touched, and a copy cannot be saved inside the output folder.
- **Export manifest...** in the gear menu lists the kept clips of the open speaker, or of everyone, in a JSON Lines or CSV
  file: `audio_filepath`, `text` and `duration` (the names speech toolkits such as NeMo read), then the speaker, origin,
  allowed use and the SHA-256 of the WAV. Paths are relative to the folder the manifest is saved in. A clip whose WAV has
  changed since it was kept, or whose sidecar does not record the permission, is left out and named, and in a CSV a text
  that a spreadsheet would run as a formula gets an apostrophe in front. See [Clip format](docs/clip-format.md#manifest-files).
- **A take is written to disk while it is recorded.** A `.spool` file in `masters` grows as you record and goes when the
  original is written. If the program or the computer stops in the middle, the next start makes a take of what reached the
  disk, marked *interrupted*: a crash costs the last moment, and a power cut at most two seconds.
- **The input stays armed from one take to the next.** It used to close after every take, so only the first take of a
  session kept its first word, and the meter went dead between takes. A device that failed is not reopened by itself.
- **Audio the device lost is noticed.** The status line says so, the take gets a `dropouts` flag, and *Ready to keep?*
  reminds you to listen for gaps.
- **Go back to the kept clip...** on a take's right-click menu drops the changes made since it was kept.
- The sidecar records when the permission box was ticked (`consent_confirmed_at`) and how much of the recording behind the
  clip is clipped (`measurements.recording_clipped_fraction`). Clips kept before have neither, and no date is made up.
- The README says how to update without losing your recordings.

### Changed

- **The default noise floor is -45 dB, not -25.** On test speech -25 left the clip further from the clean voice than no
  cleaning at all in five of six test conditions, while -45 left it closer in all six. Clips that were already kept hold
  their own setting and do not change. The Process tab now suggests setting it about 5 dB above the clip's noise floor.
- **Auto fix sets noise reduction from the clip's own noise**, about 5 dB above its noise floor, and drops it if it would take
  more than 4 dB from the voice in any band. On a quiet, noisy test take the speech used to gain 12 dB over the noise while the
  consonants lost 9 dB; they now lose under 2 dB, and Auto fix beat no cleaning in all six test conditions.
- **Changing the origin or the allowed use clears the permission tick**, on a take or on a kept clip. The tick is a statement
  about what is written above it.
- A fade, a gain or a Process setting no longer asks you to confirm the transcript again after a cut, and neither does undoing
  back to the cuts you confirmed.
- Script files saved as UTF-8 with a byte-order mark, as Unicode or in the Windows code page now load. A byte-order mark used
  to end up in the first transcript.
- `setup.bat` finds a newer Python through the launcher when 3.12 to 3.14 are not there, and the tests run on 3.14 too.
- The README and the user guide said that edits were "kept beside" the original. That was true only after Keep; it is now
  true of every take, by the autosave file described below.
- The message that appears when the window will not close because something could not be saved now says "everything",
  not "every recording", since it can be the edits of a take as well as its audio.
- The version is 0.4.0 (the previous release was 0.3.0).

### Fixed

- **A draft transcript failed as soon as it held a letter outside plain English.** The recognizer passed its text back in
  the Windows code page and the program read it as UTF-8, so *café* ended in an error about `NoneType`, and Polish, Russian
  or Japanese stopped the recognizer itself. In practice that was nearly every draft in every language but English,
  although the setup offers them. The text now travels in plain ASCII with the other letters as escapes, and an error
  message in the code page is no longer lost on the way.
- **The transcript setup could not work on Python 3.15.** Everything else in the program installs there, but two of the
  recognizer's packages, ctranslate2 4.8.0 and onnxruntime, have no build for it yet, and the most a person saw was pip's
  *No matching distribution found*. The setup now looks for a Python 3.12 to 3.14 beside the one it runs on and makes the
  recognizer's environment with that. If there is none, it says what to install before it asks anything or downloads
  anything. An environment that a failed attempt left behind is made again, or trying again would have failed the same way.
- **On a computer without Microsoft's Visual C++ runtime, only the transcript setup failed**, because the window brings its
  own copy and ctranslate2 and onnxruntime do not. The setup now says so before it downloads anything, and where to get it.
- **A Keep that could not write looked like a success.** No message appeared, the card said *Kept*, no WAV existed, autosave
  stopped, and later edits were lost at the next start. SoundFile reports a failed write with an error of its own, which
  nothing was catching, and the take was marked kept before anything was written. Both files are now finished before either
  goes in place, a message says what went wrong, and the take stays as it was. A test that faked the wrong kind of error is
  why this was not found sooner: the tests now make the real write fail.
- **Gain and the limiter hid clipping.** A clip made from a badly clipped recording measured 0.000%, Auto fix said *Nothing to
  fix*, and the sidecar had no `clipped` flag. Clipping is now counted on the recording as well, on its loudest channel.
- **Noise reduction moved the clip 25 ms late and lost its last 25 ms**, because FFmpeg's filter delays the audio. The delay is
  now measured and taken out.
- **Changes to a kept clip were lost at close, with no warning.** They are now saved like any other work and come back at the
  next start. The take shows *Changed* until *Update kept clip* writes them, and the kept clip stays as it was until then.
- **One damaged sidecar could stop the program opening.** A bad `created` value was enough, and a bad `transcript` failed when
  the clip was opened. Each value is now checked: the clip is skipped and named, and the others load.
- **Errors nothing expected were silent.** They went only to the log, so a button simply did nothing. The window now shows them,
  once for each message. Playing with no output device says *Could not play*.
- Pressing `Space` said *Playing the original* while it played the edited clip.
- *Update kept clip* skipped a line of the script, and so did keeping an older take. The script now moves on only for the line
  on screen.
- A draft that was corrected was recorded as `typed` instead of `edited`, and a clip stopped being `edited` when kept again.
- Flags stayed in the sidecar after the edit behind them was removed. They are now worked out afresh at each Keep.
- Recordings, clips and sidecars are now forced to the disk before they are renamed into place, as the user guide promised for
  a power cut.
- An audio file with no audio in it, or with samples that are not numbers, is refused at import. The second used to write
  invalid JSON into the sidecar.
- A speaker called *Nul*, *Con* or another name Windows keeps for a device could not be saved. They get a folder that works.
- **The edits and typed text of a take that was not kept were lost on a crash or a close.** The original recording was
  safe, but the cuts, the Process settings and the transcript, origin, allowed use and notes lived only in memory until
  Keep, so the take came back as it was recorded. They are now written to an `.autosave.json` file beside the original a
  moment after each change, when another take is opened and when the program closes, and come back when it starts again
  (the status line says so). The permission box is never saved, and neither is the undo history. Keep deletes the file,
  and Discard sends it to the Recycle Bin with the rest. If it cannot be written, the status line says so, and closing
  the window warns once. The file is not part of the clip format, and no program that reads the clips as described there
  sees it.

### Security

- Noise reduction no longer runs an `ffmpeg.exe` found in the current folder. On Windows the standard search of `PATH`
  looks in the current folder first, so a file of that name in whatever folder the program was started from would have run
  in place of the real one. (The icon and `run.bat` start the program in its own folder, so this took a file planted
  there, or a start from another folder by hand.) FFmpeg is now looked for only in folders that `PATH` names in full,
  after the path in Settings, and then in WinGet's folders, as before.

## [0.3.0] - 2026-10-29

### Added

- **Draft transcripts can be set up in one step.** A new copy of the program has no speech recognizer, so *Suggest from
  audio* could only say to read the README. `setup-transcripts.bat` (also offered at the end of `setup.bat`, and by
  *Suggest from audio* when nothing is set up) now asks which model you want (`tiny`, `base` or `small`, 80 to 490 MB)
  and which language you speak, says what it will download, and waits for your yes. It installs faster-whisper from
  PyPI into a folder of its own, `.venv-asr` (about 90 MB, pinned in `requirements-asr.txt`), downloads the model from
  Hugging Face into `models`, runs the recognizer once on a second of silence, and only then fills in the speech
  recognizer lines in Settings. Nothing is written if a step fails, and it can be run again. The program itself still
  never goes online: this is the one script that downloads, and only when you run it and agree.
- `python -m recorder --check` also says whether draft transcripts are ready.

### Changed

- The messages about a missing speech recognizer (the status line, the Settings dialog and its tooltips) now name
  `setup-transcripts.bat` instead of only the README. The README, the user guide, the security policy and the
  third-party notices say what the setup downloads and where from.
- The version is 0.3.0 (the previous release was 0.2.0).

### Fixed

- **The speaker list and the + button stayed switched off after the first recording on a new copy.** On a first start
  there is no speaker, and the first Record creates "My voice" as it begins. The window switched the two controls off for
  the length of that recording, and switched them on again only when the list of speakers changed, which it does not
  when a recording stops. So the list showed only "My voice" and neither it nor + would respond. They now follow the
  recording: off while one runs, on again as soon as it ends. Clicking the list during a recording, which is refused so a
  take keeps its speaker, caused the same lock and is fixed too.

## [0.2.0] - 2026-10-21

### Added

- **`F9` works while another program is in front.** Windows tells the program when `F9` is pressed, so a take can be
  started and stopped from whatever you are reading or playing, and the taskbar button flashes to show it was heard. The
  program is told about that one key and sees no other. **Record key** in Settings turns it off, a key that another
  program already holds is reported and not taken, and a dialog that is open is not interrupted by a recording starting.
- **An icon in the program folder.** `setup.bat` now makes `Fel's Voice Slate.lnk` beside the code, so there is
  something to double-click right where the program was unpacked, and still offers the desktop icon too.
  `python -m recorder --shortcut folder` makes it later. A shortcut file holds the paths of the computer it was made
  on, so it is not part of the repository (Git ignores `*.lnk`): every copy of the program makes its own.

### Changed

- The message for a missing speech recognizer now says to choose one in Settings. Hovering over *Draft transcripts* at
  the bottom of the window shows which settings file is in use, and the log names it on its first line and says at
  start-up whether draft transcripts and `F9` are ready.
- The version is 0.2.0 (the first release was 0.1.0).

## [0.1.0] - 2026-10-20

The first public release.

### Added

- **Recording** from a microphone or from what the computer plays (WASAPI loopback), with a live level meter, an Arm
  option that keeps the last half second before Record, a configurable stop tail, an automatic stop at 40 seconds, and a
  take-length guide.
- **Script mode**: one prompt per take, from a text file.
- **Import** of audio files (WAV, FLAC, Ogg, MP3, AIFF), by button, shortcut or drag and drop.
- **A new window** built with PySide6: speakers and takes on the left, the editor in the middle, and the Clip, Process
  and Check tabs on the right, in a dark theme that scales with the display and keeps everything visible down to 1100 by
  700.
- **Editing** on a waveform with a spectrogram and an overview strip: cut, trim, silence, fade in and out, gain on a
  part, auto-trim of the ends, shortening of long pauses, an Original/Edited comparison, a check of every join, and
  undo and redo with a clickable history.
- **Processing** of the whole take: high-pass filter, hum notch (50 or 60 Hz), noise reduction with the user's own
  FFmpeg, gain with a Normalize button, a peak limiter and an adjustable join fade.
- **The Check tab**: length, speech level, peak, clipping, loudness (LUFS), noise floor, bandwidth, mains hum, and silence,
  measured on the clip exactly as it will be kept.
- **Keep rules** that need a transcript, the origin, the allowed use and a confirmation of permission for every clip.
- **Auto analyze and Auto fix**: one button says in plain words what could be better about a take (long silences, rumble,
  mains hum, noise, a quiet voice, a hot peak), and what Auto fix would change, with the numbers before and after. The
  other button makes the repairs that are safe, in one step that Undo takes back. Noise reduction is used only on a clearly
  noisy clip, and only if it lowers the noise. A clip is never shortened below the 6 seconds that Keep needs.
- **Draft transcripts** with the user's own faster-whisper (optional; nothing is downloaded by the program). Stretches of
  silence are skipped, so a pause is not turned into invented words. The language can be set in Settings, a draft appears
  by itself for a new take, the Clip tab says when one is being made, and a draft that has not been edited follows the
  cuts made to the clip.
- **A desktop icon** (`setup.bat` offers it, or `python -m recorder --shortcut`) that starts the program with no console
  window and closes with it. Only one copy runs at a time: a second start brings the first window forward. Errors go to
  `voice-slate.log`, and a failure at start-up shows a message instead of vanishing. `python -m recorder --check`
  reports what is installed and what is missing.
- **Sidecar format v2**, which records the edit list and the processing settings, the transcript's status and the
  SHA-256 of the exported file. Version 1 sidecars still load.
- **Documentation**: a user guide, responsible-use guidance, the clip format, an architecture overview, and contributing
  and security policies.
- **Recordings beside the program**: the default output folder is `voice-clips` inside the program folder, which Git
  ignores, and which holds a `.gitignore` that ignores everything in it. A folder that OneDrive, Dropbox or the like
  syncs, or that cannot be written to, is not used: the recordings go in the user's own `Recordings` folder instead.
- A test suite of more than 700 tests that needs no audio hardware or display, and `tools/shoot.py` for redrawing the
  screenshots from generated audio.

### Safety and privacy

- The original recording is written to disk the moment recording stops, and is never changed by editing.
- Discard moves a take's files to the Recycle Bin instead of deleting them.
- An imported file's sidecar records the file's name but not the folder it came from.
- A sidecar that names a file outside its own folder, and a `speakers.json` that is not a plain speaker list, are refused.
- The program makes no network connections.
- The limiter's ceiling also holds for the saved 24 kHz file: resampling can push a peak over it, so the limiter runs
  once more after resampling.

[Unreleased]: ../../compare/v0.5.0...HEAD
[0.5.0]: ../../compare/v0.4.0...v0.5.0
[0.4.0]: ../../compare/v0.3.0...v0.4.0
[0.3.0]: ../../compare/v0.2.0...v0.3.0
[0.2.0]: ../../compare/v0.1.0...v0.2.0
[0.1.0]: ../../releases/tag/v0.1.0
