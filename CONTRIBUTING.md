# Contributing

Thank you for wanting to help. This is a small project, and clear bug reports, fixes, documentation and tests are all
welcome.

## What this project is, and is not

Voice Slate records, tidies and documents clean voice clips on Windows, and keeps a record of where each clip came
from and whether the person had the right to use the voice. That last part is a requirement, not a feature to trade away.

- **Changes that weaken the consent step will not be merged.** That includes skipping or remembering the permission tick,
  making the origin, allowed-use or permission fields optional, filling in the origin or allowed use with anything but
  the person's own words for that speaker, which are shown for them to check, or letting several takes be kept without the
  statement being made for what is listed. See [Responsible use](docs/responsible-use.md).
- **No network access and no telemetry.** The program does not connect to anything. Optional tools (FFmpeg, a speech
  recognizer) run as separate programs. The user installs FFmpeg; the recognizer is installed by
  `setup-transcripts.bat` (`recorder/asr_setup.py`), which downloads only when the user runs it and agrees, and says what
  it fetches first. Nothing else may go online, and that script must not start by itself.
- Nothing aimed at deceiving people or imitating a voice without permission.

For anything bigger than a small fix, please open an issue first so we can agree on the approach before you spend time on it.

## Reporting a problem

Search the existing issues first, then use the bug-report form. It helps to include:

- the Voice Slate version (the gear menu, then **Help → About**), your Windows version and your Python version;
- what you did, what you expected, and what happened, including any message in the status line or an error window;
- whether it involved a microphone, system audio, or an imported file.

Errors that do not reach the window are written to `voice-slate.log` (the [user guide](docs/user-guide.md) says
where it is). To watch them as they happen, start the program with `run.bat` or from a terminal with
`.venv\Scripts\python -m recorder`, and run `.venv\Scripts\python -m recorder --check` to see what is installed. Please
remove personal folder names from anything you paste from the log.

**Export diagnostics...** in the gear menu does that for you: it saves a zip with what `--check` says, the settings and the
end of the log, with your folders, user name and speaker names replaced, for you to read and then attach.

**Do not attach recordings or sidecar files of real people, your `settings.json`, or anything private**, and remove
personal folder names from anything you paste. To show a problem with audio, describe it, or use generated audio:
`tools/synthetic.py` makes test takes.

To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Setting up

You need Windows (11 is the tested version) and Python 3.12 or newer.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m pyflakes recorder tests tools
```

The tests need no microphone, speakers or display, and make no network connections. The window tests run on Qt's
offscreen platform. `-n auto` runs them in parallel; leave it out to run them one after another, which is easier to
follow when one fails. If a test appears to hang, `faulthandler_timeout` in `pyproject.toml` prints where it is stuck.
Tests that use a real FFmpeg are skipped when it is not installed.

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

**Translating.** The window's text is in JSON files in `recorder/resources/lang`, with the English as the key. To correct a
translation, edit the file. To see what a language lacks, run `python tools/strings.py --missing de`. To add a language, add it to
`LANGUAGES` and `WINDOWS_LANGUAGES` in `recorder/i18n.py` and add its file. A translation must keep the `{fields}` of its English, and
the tests check that. The first translations (German, Spanish, French) have not been checked by native speakers, so corrections
are very welcome, above all to the statement of permission, which has to mean exactly what the English means.

**The installer.** `python -m pip install -r requirements-build.txt`, then `python tools/build_installer.py`, with
[Inno Setup 6](https://jrsoftware.org/isinfo.php) installed. It builds in `build/`, opens the real window from what it built as a
test, and fails if that does not work. `--no-installer` stops before Inno Setup. Beside the installer it writes `SHA256SUMS`, which holds the installer's
SHA-256.

**Making a release.** Only the release workflow (`.github/workflows/release.yml`) puts files on a release, because only what it
built from the tag is attested.

1. Set `__version__` in `recorder/__init__.py`, and move the lines under *Unreleased* in [CHANGELOG.md](CHANGELOG.md) under
   `## [<version>] - <YYYY-MM-DD>`.
2. If you want notes written by hand, with pictures or a video, write them on GitHub as a **draft** release for the tag
   `v<version>`, and leave it unpublished with no files on it. Without a draft, the notes are the changelog's section.
3. Push the commit, then the tag `v<version>`. The workflow runs the tests on the tagged commit, builds the installer and
   `SHA256SUMS`, has GitHub attest both, and puts them on the draft. Then, on another runner, it downloads the draft's files and
   checks their names, the hash and the attestations (`tools/check_release_files.py`). Only then does it publish the draft.
4. When anything fails, nothing is published. If the failure was not the code's, such as GitHub being down, run the workflow
   again for the same tag (*Run workflow*, with the tag). Otherwise fix it and release the next patch number. A published
   release is never changed.

`python tools/release_notes.py --tag v<version>` checks the tag against the version and prints the notes, and
`python tools/check_release_files.py --dir build --tag v<version> --no-attestation` checks a local build's files, if you want to
try them before you push.

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

## Fel's Studio ports

Voice Slate sends clips through the shared inbox and has no listening port. When a documentation example needs an
unassigned port, use `8775`.

| What | Port |
|---|---|
| Voice Lab | 8765 |
| NPC Forge | 8766 (was 8765) |
| Fel's Altera | 8767 for new installs (was 5001) |
| A second copy of NPC Forge | 8768 |
| Voice Lab's MCP server over HTTP | 8769 |
| Altera's own voice service | 8770 |
| ComfyUI | 8188 |
| LM Studio | 1234 |
| Ollama | 11434 |
