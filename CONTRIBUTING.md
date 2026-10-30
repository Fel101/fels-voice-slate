# Contributing

Thank you for wanting to help. This is a small project, and clear bug reports, fixes, documentation and tests are all
welcome.

## What this project is, and is not

Voice Slate records, tidies and documents clean voice clips on Windows, and keeps a record of where each clip came
from and whether the person had the right to use the voice. That last part is a requirement, not a feature to trade away.

- **Changes that weaken the consent step will not be merged.** That includes skipping or pre-filling the origin,
  allowed-use or permission fields, or making them optional. See [Responsible use](docs/responsible-use.md).
- **No network access and no telemetry.** The program does not connect to anything. Optional tools (FFmpeg, a speech
  recognizer) run as separate programs. The user installs FFmpeg; the recognizer is installed by
  `setup-transcripts.bat` (`recorder/asr_setup.py`), which downloads only when the user runs it and agrees, and says what
  it fetches first. Nothing else may go online, and that script must not start by itself.
- Nothing aimed at deceiving people or imitating a voice without permission.

For anything bigger than a small fix, please open an issue first so we can agree on the approach before you spend time on it.

## Reporting a problem

Search the existing issues first, then use the bug-report form. It helps to include:

- the Voice Slate version (the gear menu, then **About**), your Windows version and your Python version;
- what you did, what you expected, and what happened, including any message in the status line or an error window;
- whether it involved a microphone, system audio, or an imported file.

Errors that do not reach the window are written to `voice-slate.log` (the [user guide](docs/user-guide.md) says
where it is). To watch them as they happen, start the program with `run.bat` or from a terminal with
`.venv\Scripts\python -m recorder`, and run `.venv\Scripts\python -m recorder --check` to see what is installed. Please
remove personal folder names from anything you paste from the log.

**Do not attach recordings or sidecar files of real people, your `settings.json`, or anything private**, and remove
personal folder names from anything you paste. To show a problem with audio, describe it, or use generated audio:
`tools/synthetic.py` makes test takes.

To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Setting up

You need Windows (11 is the tested version) and Python 3.12 or newer.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pyflakes recorder tests tools
```

The tests need no microphone, speakers or display, and make no network connections. The window tests run on Qt's
offscreen platform. If a test appears to hang, `faulthandler_timeout` in `pyproject.toml` prints where it is stuck.

Start the program with `run.bat` (it keeps a console open to show errors), or `.venv\Scripts\python -m recorder`. The
desktop icon, which starts it with no console, is made by `.venv\Scripts\python -m recorder --shortcut`.

## Making a change

1. Fork the repository and make a branch.
2. Keep the change focused: one fix or feature per pull request.
3. Add or update tests with the change. Bug fixes should come with a test that fails without the fix.
4. Follow the code around yours: its naming, how much it comments, and how it is laid out. Comments say why, not what.
5. Read [docs/architecture.md](docs/architecture.md). In short: `core/` and `session/` never import PySide6, `store.py`
   keeps old sidecars loading, and the window is a thin view over the session.
6. If the window changed, redraw the screenshots with `python tools/shoot.py` and include the new ones. If the icon
   changed, redraw it with `python tools/make_icon.py`.
7. Update the documentation and add a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).
8. Run the tests and pyflakes (above) before you open the pull request.

**Things the tests cannot cover.** A real microphone, system-audio capture and playback depend on the machine. If your
change touches them, try it by hand and say in the pull request what you tried and on which hardware.

**New dependencies.** Please ask first. A dependency must have a licence compatible with MIT (permissive, or LGPL used as
an installed library), must be needed by the program, and must be added to `requirements.txt` and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Never commit** recordings, `settings.json`, the `.venv`, or a path or name from your own computer. `.gitignore` keeps
audio and settings out, but check your change before you push it.

## Licence of your contribution

By sending a contribution you agree that it is released under the project's [MIT License](LICENSE), and that you have the
right to give it. There is no contributor agreement to sign.

## Project layout

See [docs/architecture.md](docs/architecture.md).
