# Security policy

## Supported versions

Only the latest release is supported. Please update before you report a problem, in case it is already fixed.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include
what you found, the steps to reproduce it, the version, and what you think the impact is. If that option is not
available, open a public issue that says only that you have a security report and asks for a private way to send it. Do
not put the details in it.

This is a volunteer project. You will get a reply as soon as someone can, a fix when one is ready, and credit in the
release notes if you want it.

## What the program does that matters for security

Voice Slate is a local desktop program. It makes no network connections. The two setup scripts do: `setup.bat` runs
`pip` to fetch the packages in `requirements.txt`, and the optional `setup-transcripts.bat` fetches the packages in
`requirements-asr.txt` and one speech model, only when you run it and say yes. The packages named in those two files are pinned to exact versions. The
program does read and write files, and it starts other programs, so these are the places to look:

- **Files it opens.** Audio files you import are read by the SoundFile library (libsndfile). Sidecar JSON and
  `speakers.json` files in the output folder are read and checked: a sidecar that names a file outside its own folder, and
  a speaker list whose entries are not plain folder names, are refused, because a clip folder can come from someone else.
  Keep, which writes files, and Discard, which removes them, act only inside the speaker's folder.
- **Programs it runs.** Noise reduction runs `ffmpeg.exe`, and draft transcripts run a `python.exe` with a worker script
  (`recorder/asr_worker.py`, started in isolated mode with a local model folder). Their locations come from your
  `settings.json`, or are found on `PATH`. **Treat `settings.json` as trusted**, like any configuration that names a
  program: anyone who can change it can make the recorder run a program of their choosing. It is not meant to be taken from
  strangers.
- **What it stores.** Recordings and sidecars are personal data. They are written where you choose, and are not encrypted
  by the program. See [Responsible use](docs/responsible-use.md) for how to look after them.
- **What else it creates.** `voice-slate.log` (and `voice-slate-crash.log`, only after a crash) beside the settings
  file, which record start-ups and errors, including the names of the files and folders involved, so read the log before
  you paste it anywhere. And, only when you ask for it with `--shortcut` or at setup, a shortcut (`.lnk` file) on the
  desktop or in the Start menu that starts the program from the folder it is in.

## Reports that are in scope

- A crafted audio file, sidecar, `speakers.json` or settings file that makes the program read, overwrite, move or delete
  files outside the folders it is meant to use, or run something it should not.
- A crash or hang that a crafted file can cause, and that loses a person's recordings.
- A way for recordings or personal data to leave the computer.

Problems in a library the program uses (Qt, NumPy, SciPy, SoundFile, sounddevice, PyAudioWPatch) are best reported to
that library. If this program makes one worse, tell us as well.
