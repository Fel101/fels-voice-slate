"""What is installed, and a zip for a bug report.

`python -m recorder --check` prints what is installed. **Export diagnostics...** in the gear menu saves the same with the
settings and the end of the log in a zip, where the person chooses. Nothing is sent anywhere.

Before any text goes in the zip, what would name the person or the people they record is replaced: the recordings
folder, the home folder and the user name in paths, and each speaker's name and folder. Paths are matched however they
are written (C:\\Users\\..., C:/Users/..., or with the doubled backslashes of an error message) and in any case. The
person can open the zip and read every word of it before they share it.
"""
import datetime as dt
import importlib
import importlib.util
import json
import platform
import re
import sys
import zipfile
from pathlib import Path

from . import __version__, launch, settings

REQUIRED = (('PySide6', 'the window'), ('numpy', 'audio maths'), ('scipy', 'filters and resampling'),
            ('soundfile', 'reading and writing WAV files'), ('sounddevice', 'the microphone and playback'),
            ('send2trash', 'Discard, which uses the Recycle Bin'))
OPTIONAL = (('pyaudiowpatch', 'recording what the computer plays'),)
LOG_LINES = 3000                    # the end of the log, where the problem is
LEFT_OUT = ('window',)              # settings that tell nothing: where the window was on the screen


def installed(data):
    """(lines, missing): a line for each package and optional tool, saying whether it is there; and whether something
    the program needs is missing."""
    from . import clean

    lines, missing = [], False
    for name, what in REQUIRED + OPTIONAL:
        required = (name, what) in REQUIRED
        if importlib.util.find_spec(name) is None:
            ok, state = False, 'MISSING' if required else 'not installed'
        else:
            try:
                module = importlib.import_module(name)
            except Exception as e:                      # there, but it will not load: as bad as missing
                ok, state = False, f'does not load ({e})'
            else:
                ok, state = True, f'{name} {getattr(module, "__version__", "")}'.strip()
        missing = missing or (required and not ok)
        lines.append(f'  {"ok     " if ok else "PROBLEM" if required else "absent "}  {what:40s} {state}')
    ffmpeg = clean.locate(data.get('ffmpeg'))
    lines.append(f'  {"ok     " if ffmpeg else "absent "}  {"noise cleaning (FFmpeg, optional)":40s} {ffmpeg or "not found"}')
    try:
        from . import transcript
        drafts = transcript.available(data.get('asr_python'), data.get('asr_model'))
    except ImportError:                                 # soundfile is missing, which the lines above already say
        drafts = False
    lines.append(f'  {"ok     " if drafts else "absent "}  {"draft transcripts (optional)":40s} '
                 f'{"ready" if drafts else "not set up: run setup-transcripts.bat"}')
    return lines, missing


# ---- hiding names -----------------------------------------------------------------------------------------------------

AFTER_SEPARATOR = r'(?<=[\\/])'     # a name that is one part of a path
ENDS = r'(?![\w.\-])'               # where a name in a path ends: not in the middle of a longer one


def _path(path):
    """A pattern for `path` in a text, whatever its separators: one backslash, two, or a slash."""
    parts = [re.escape(part) for part in re.split(r'[\\/]+', str(path)) if part]
    return r'[\\/]+'.join(parts) + ENDS if parts else None


def _speakers(root):
    """{text: (number, as a word too)} for each speaker in the recordings folder: their folder, which is hidden where it
    is part of a path, and their name, which is hidden wherever it stands as a word. Nothing in the folder is changed."""
    found = []
    try:
        registry = json.loads((root / 'speakers.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        registry = {}
    if isinstance(registry, dict):
        for slug, speaker in registry.items():
            found.append((str(slug), speaker.get('name') if isinstance(speaker, dict) else None))
    try:
        found += [(folder.name, None) for folder in sorted(root.iterdir()) if (folder / 'masters').is_dir()]
    except OSError:
        pass
    numbers, speakers = {}, {}
    for slug, name in found:
        number = numbers.setdefault(slug, len(numbers) + 1)
        speakers.setdefault(slug, (number, False))
        if isinstance(name, str) and len(name.strip()) >= 2:
            speakers[name.strip()] = (number, True)
    return speakers


def hider(home=None, root=None):
    """A function that takes a text and returns it with the recordings folder, the home folder, the user name and the
    speakers replaced by <recordings>, <home>, <user> and <speaker 1>, <speaker 2> and so on."""
    home = Path(home) if home is not None else Path.home()
    root = Path(root) if root is not None else Path(settings.load().get('root') or settings.default_root())
    in_paths = [(pattern, label) for pattern, label in ((_path(root), '<recordings>'), (_path(home), '<home>')) if pattern]
    if home.name:
        in_paths.append((AFTER_SEPARATOR + re.escape(home.name) + ENDS, '<user>'))
    as_words = []
    speakers = _speakers(root)
    for text in sorted(speakers, key=len, reverse=True):     # "Ann Lee" before "Ann"
        number, word = speakers[text]
        in_paths.append((AFTER_SEPARATOR + re.escape(text) + ENDS, f'<speaker {number}>'))
        if word:
            as_words.append((r'(?<![\w<])' + re.escape(text) + r'(?!\w)', f'<speaker {number}>'))
    # Paths in any case, as Windows reads them; a name only as it is written, so that a word like "will" stays.
    rules = [(re.compile(pattern, re.IGNORECASE), label) for pattern, label in in_paths]
    rules += [(re.compile(pattern), label) for pattern, label in as_words]

    def hide(text):
        for pattern, label in rules:
            text = pattern.sub(label, text)
        return text

    return hide


# ---- the zip ----------------------------------------------------------------------------------------------------------

def _tail(path, lines=LOG_LINES):
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    return '\n'.join(text.splitlines()[-lines:]) + '\n'


def summary(data, now=None):
    """The text of summary.txt, before the names in it are hidden."""
    now = now or dt.datetime.now()
    lines = [f'{settings.APP_NAME} {__version__}: diagnostics made {now:%Y-%m-%d %H:%M}', '',
             'Made by Export diagnostics, to attach to a bug report. The recordings folder, your home folder and user '
             'name, and the names and folders of your speakers are replaced by <recordings>, <home>, <user> and <speaker '
             '1>, <speaker 2> and so on. Read it all before you share it.', '',
             f'Windows: {platform.platform()}', f'Python: {sys.version.split()[0]} ({sys.executable})',
             f'Program folder: {settings.program_dir()}', f'Settings file: {settings.settings_path()}',
             f'Log file: {launch.log_path()}', '', 'What is installed:']
    lines += installed(data)[0]
    lines += ['', 'Settings:']
    lines += [f'  {key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}'
              for key, value in sorted(data.items()) if key not in LEFT_OUT] or ['  (none)']
    return '\n'.join(lines) + '\n'


def collect(destination, home=None, root=None):
    """Write the zip: summary.txt, and the end of the log and of the crash log. Returns where it went."""
    destination = Path(destination)
    data = settings.load()
    hide = hider(home, root if root is not None else data.get('root') or settings.default_root())
    log = Path(launch.log_path())
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('summary.txt', hide(summary(data)))
        for path in (log, log.with_name(launch.CRASH_NAME)):
            text = _tail(path)
            if text is not None:
                bundle.writestr(path.name, hide(text))
    return destination
