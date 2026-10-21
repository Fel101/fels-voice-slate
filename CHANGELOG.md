# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: ../../compare/v0.1.0...HEAD
[0.1.0]: ../../releases/tag/v0.1.0
