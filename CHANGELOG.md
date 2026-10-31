# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Labels.** Give a take a name in the new field at the top of the Clip tab, with `F2`, or from the take's right-click
  menu. The label shows in the list in place of the take's number, and is saved in the sidecar as an optional `label`
  field (a take with no label has no such field). Changing the label of a kept clip is saved at once, without Keep.
- **Rename speaker...** in the gear menu, and on the right-click menu of the speaker list, changes the name a speaker is
  shown under. Their folder keeps its name, so no clip moves and nothing already kept is touched.
- **A filter box above the list of takes** shows only the takes whose label, transcript or notes contain all the words
  typed, whatever the capitals. The take numbers do not change while it is on.

### Changed

- The README and the user guide said that edits were "kept beside" the original. That was true only after Keep; it is now
  true of every take, by the autosave file described below.
- The message that appears when the window will not close because something could not be saved now says "everything",
  not "every recording", since it can be the edits of a take as well as its audio.

### Fixed

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

[Unreleased]: ../../compare/v0.3.0...HEAD
[0.3.0]: ../../compare/v0.2.0...v0.3.0
[0.2.0]: ../../compare/v0.1.0...v0.2.0
[0.1.0]: ../../releases/tag/v0.1.0
