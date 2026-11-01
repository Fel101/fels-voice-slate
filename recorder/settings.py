"""User settings: one small JSON file.

The file is the first of these that applies:

1. the file named by the VOICE_SLATE_SETTINGS environment variable;
2. `settings.json` next to the program (portable mode, convenient when running from a clone);
3. `%APPDATA%\\Fels Voice Slate\\settings.json`.

Keys, all optional:

- `root`: the output folder for speakers and clips. Without it, `default_root()` decides.
- `ffmpeg`: full path of ffmpeg.exe, when it is not on PATH. Noise cleaning needs it.
- `asr_python` and `asr_model`: the optional speech recognizer used for draft transcripts (see the README).
- `asr_language`: a language code such as `en` for the recognizer. Without it the language is detected for each clip.
- `tail_seconds`: how long recording continues after Stop, 0 to 1.
- `purpose`: what new takes are for: `reference`, `sentence` or `audition`.
- `global_hotkey`: `true` lets the record key start and stop a recording while another program is in front (off by default).
- `record_key`: the record key, such as `F9` or `Ctrl+Shift+R`.
- `input_device`: the device last chosen for each source, by the name shown in the list.
- `input_channels`: which input of a device to record, by the device's name in the list, counted from 0. Without an entry
  the first two inputs are mixed.
- `output_device`: the device to play through, by the name shown in Settings. Without it, Windows' default.
- `theme`: `dark`, `light`, `contrast` or `system` (the mode Windows is in).
- `text_scale`: the size of the whole window, from 1 to 2. Without it, Windows' own Text size setting.
- `language`: the language of the window, such as `de`. Without it, the one Windows is in, if there is a translation.
"""
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

APP_NAME = "Fel's Voice Slate"           # what the person sees: the window title, the About box, messages, the shortcut
APP_TAGLINE = 'Voice and Clip Recorder'
APP_DIR = 'Fels Voice Slate'            # the settings folder under %APPDATA%: no apostrophe, which scripts and shells trip over
APP_ID = 'FelsVoiceSlate.App'           # what Windows groups the taskbar button and a shortcut to the program by
ENV_VAR = 'VOICE_SLATE_SETTINGS'
CLIPS_FOLDER = 'voice-clips'
# Under the user's profile rather than Documents, which OneDrive often syncs: these are voice recordings.
USER_ROOT = Path.home() / 'Recordings' / CLIPS_FOLDER
SYNCED_PREFIXES = ('onedrive', 'dropbox', 'google drive', 'googledrive', 'icloud')
DEFAULT_TAIL_SECONDS = 0.5
DEFAULT_RECORD_KEY = 'F9'
THEMES = ('dark', 'light', 'contrast', 'system')
TEXT_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)


def program_dir():
    return Path(__file__).resolve().parent.parent


def _is_inside(path, base):
    try:
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return False
    return True


def in_synced_folder(path):
    """True when `path` lies in a folder that OneDrive, Dropbox, Google Drive or iCloud keeps in the cloud."""
    path = Path(path)
    if any(part.lower().startswith(SYNCED_PREFIXES) for part in path.parts):
        return True
    for name in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        base = os.environ.get(name)
        if base and _is_inside(path, Path(base)):
            return True
    return False


def _writable(folder):
    """True when `folder` exists (it is made if needed) and a file can be created in it."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return False
    return True


def _keep_out_of_git(folder):
    """A .gitignore inside the folder that ignores everything in it, so a recording cannot be committed by accident,
    even from a copy of the program that has lost its own .gitignore."""
    marker = folder / '.gitignore'
    if not marker.exists():
        try:
            marker.write_text('# Recordings of voices, and their sidecars. Never commit them.\n*\n', encoding='utf-8')
        except OSError:
            pass


def holds_recordings(folder):
    """True when `folder` is an output folder with speakers in it."""
    folder = Path(folder)
    if any(folder.glob('*/masters')):
        return True
    try:
        return bool(json.loads((folder / 'speakers.json').read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return False


def default_root():
    """The output folder when the settings do not name one.

    That is USER_ROOT in the person's own folder, where updating or removing the program cannot touch it. Copies of the
    program before 0.5 kept the recordings in `voice-clips` inside the program folder, so one with recordings in it stays in
    use, unless OneDrive, Dropbox or the like syncs it or it cannot be written to. Settings can move it.
    """
    folder = program_dir() / CLIPS_FOLDER
    if holds_recordings(folder) and not in_synced_folder(folder) and _writable(folder):
        _keep_out_of_git(folder)
        return folder
    return USER_ROOT


def settings_path():
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    portable = program_dir() / 'settings.json'
    if portable.exists():
        return portable
    base = os.environ.get('APPDATA')
    return (Path(base) if base else Path.home() / '.config') / APP_DIR / 'settings.json'


def load(path=None):
    """The settings as a dict. A missing or unreadable file gives an empty dict."""
    try:
        data = json.loads(Path(path or settings_path()).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data, path=None):
    """Write the settings atomically. A file that cannot be read is moved aside first, so it is never overwritten."""
    path = Path(path or settings_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
            os.replace(path, path.with_name(f'{path.name}.damaged-{stamp}'))
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def update(changes, path=None):
    """Change some keys and keep the rest. A value of None removes the key."""
    data = load(path)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    save(data, path)
    return data


def tail_seconds(data=None):
    """The stop tail from the settings, kept between 0 and 1. Anything that is not a number gives the default."""
    try:
        value = float((data if data is not None else load()).get('tail_seconds', DEFAULT_TAIL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_TAIL_SECONDS
    if value != value or value in (float('inf'), float('-inf')):
        return DEFAULT_TAIL_SECONDS
    return min(max(value, 0.0), 1.0)


def global_hotkey(data=None):
    """Whether the record key also works while another program is in front. Off, unless the settings say `true`: while it is
    on no other program gets the key, and a stray press would open the microphone."""
    return (data if data is not None else load()).get('global_hotkey') is True


def theme(data=None):
    """The look of the window."""
    value = (data if data is not None else load()).get('theme')
    return value if value in THEMES else THEMES[0]


def text_scale(data=None):
    """The size chosen for the window, or None to follow Windows."""
    value = (data if data is not None else load()).get('text_scale')
    return value if value in TEXT_SCALES else None


def language(data=None):
    """The language chosen for the window, or None to follow Windows."""
    value = (data if data is not None else load()).get('language')
    return value if isinstance(value, str) and value else None


def record_key(data=None):
    """The key that starts and stops a recording, as text such as F9 or Ctrl+Shift+R."""
    value = (data if data is not None else load()).get('record_key')
    return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_RECORD_KEY
