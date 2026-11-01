"""Set up draft transcripts: a speech recognizer and a speech model, both kept in the program folder.

    python -m recorder --setup-transcripts                       asks which model and language, then asks to go ahead
    python -m recorder --setup-transcripts small --language de   the same with those two answers given

This is the one part of Fel's Voice Slate that goes online, and only when a person runs it and agrees: pip fetches
faster-whisper and what it needs from PyPI into an environment of its own (`.venv-asr`, so the program's environment
stays small), and faster-whisper fetches the chosen model from Hugging Face into `models/`. Nothing about the person or
their recordings is sent, and the program itself never goes online. Once the recognizer has run on a second of quiet
audio, the settings that name it are written (`asr_python`, `asr_model`, `asr_language`), so draft transcripts work from
then on. Nothing is written when a step fails. See "Draft transcripts" in the README.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from . import audio_core as ac
from . import settings, transcript

REQUIREMENTS = 'requirements-asr.txt'
VENV = '.venv-asr'
MODELS = 'models'
# Megabytes, rounded up from the wheels on PyPI and from the files on Hugging Face (the model, tokenizer and vocabulary).
RECOGNIZER_MB = 90
RECOGNIZER_INSTALLED_MB = 300           # what the recognizer takes on disk once it is unpacked (291 MB measured)
PROGRESS_SECONDS = 4                    # how often the model download says how far it has come
SIZES = {'tiny': (80, 'the fastest, and the roughest drafts'),
         'base': (150, 'a good middle (recommended)'),
         'small': (490, 'the most accurate, and the slowest')}
DEFAULT_SIZE = 'base'
ENGLISH = 'en'
SPARE_MB = 200                          # free space to leave over, besides what the setup itself needs

# The Pythons that every package in requirements-asr.txt is built for, newest first. The program can run on a newer one,
# and then the recognizer needs one of these beside it: for anything else pip finds nothing to install.
PYTHONS = ('3.14', '3.13', '3.12')
THIS_PYTHON = f'{sys.version_info.major}.{sys.version_info.minor}'
LISTED_PYTHON = re.compile(r'^\s*-V:(3\.\d+)\s', re.MULTILINE)          # in `py -0p`: leaves out 3.14-32, 3.14t and the like
# Microsoft's C++ runtime. ctranslate2 and onnxruntime need it and do not bring it. The window's Qt brings its own copy, so
# a computer without it runs the whole program and fails only here.
RUNTIME = ('msvcp140.dll', 'msvcp140_1.dll')
RUNTIME_PAGE = 'https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist'
SYSTEM_FOLDER = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'

# The files of a faster-whisper model, and where each model lives on Hugging Face (SYSTRAN's conversions of OpenAI's
# Whisper). faster-whisper's own download_model fetches exactly these, but switches the progress bars off, which would
# leave the window looking frozen for minutes on the larger models; huggingface_hub's snapshot_download shows them.
MODEL_PATTERNS = ['config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*']
REPOSITORY = 'Systran/faster-whisper-{name}'
FETCH = ('import sys; from huggingface_hub import snapshot_download; '
         f'snapshot_download(sys.argv[1], local_dir=sys.argv[2], allow_patterns={MODEL_PATTERNS!r})')

INTRO = f"""
Draft transcripts: set up the speech recognizer

Fel's Voice Slate can suggest a transcript from the audio. That needs two things, which this setup keeps in this
program folder and nowhere else:

  1. a speech recognizer (faster-whisper and what it needs), from PyPI: about {RECOGNIZER_MB} MB, in {VENV}
  2. a speech model, from Hugging Face: {SIZES['tiny'][0]} to {SIZES['small'][0]} MB, the one you choose, in {MODELS}

Fel's Voice Slate itself never goes online; only this setup does, now that you ran it. Nothing about you or your
recordings is sent. A draft is a guess: you listen and correct it before a clip is kept.
"""

OFFER = (f'Draft transcripts need a speech recognizer, and this computer does not have one yet.\n\n'
         f'The setup downloads one once, from PyPI and Hugging Face, and keeps it in this program\'s folder: about '
         f'{RECOGNIZER_MB} MB for the recognizer and {SIZES["tiny"][0]} to {SIZES["small"][0]} MB for the speech model '
         f'you choose. A window asks which model before anything is downloaded. Nothing you record is sent anywhere, '
         f'and the program itself never goes online.\n\nOpen the setup now?')

DONE = """
Done. Draft transcripts are set up with {what}.

Start Fel's Voice Slate, or restart it if it is open. The bottom of the window then says "Draft transcripts: ready",
and Suggest from audio fills in a draft for you to listen to and correct.

The recognizer is in {venv} and the model in {model}. To undo all this, delete those two folders and clear the speech
recognizer lines in Settings.
"""

TOO_NEW = """Draft transcripts need Python {oldest} to {newest}, and Fel's Voice Slate is running on Python {version}.

The speech recognizer is not built for Python {version} yet, and no other Python was found on this computer. Install
Python {newest} beside it, which changes nothing else: type  py install {newest}  in a terminal, or download it from
https://www.python.org/downloads/. The program can stay on the Python it has."""

NO_RUNTIME = """The speech recognizer needs Microsoft's Visual C++ runtime, and this computer does not have it ({missing}).

The rest of Fel's Voice Slate brings its own copy, which is why everything else works. Install the X64 "Visual C++
Redistributable" from Microsoft: {page}"""


class SetupError(Exception):
    """A step of the setup failed. The message says which, and nothing was written to the settings."""


# ---- the answers ------------------------------------------------------------------------------------------------------

def size_choice(text):
    """'tiny', 'base' or 'small' from what was typed: the name, or its number in the list. Empty is the default."""
    text = (text or '').strip().lower()
    names = list(SIZES)
    if not text:
        return DEFAULT_SIZE
    if text in names:
        return text
    if text.isdigit() and 1 <= int(text) <= len(names):
        return names[int(text) - 1]
    raise ValueError(f'Choose one of {", ".join(names)}, or its number in the list ({text!r} is not one).')


def language_code(text):
    """The language that will be spoken: 'en' for an empty answer, a code such as de or fr, or None for 'auto'."""
    text = (text or '').strip().lower()
    if text in ('', ENGLISH, 'english'):
        return ENGLISH
    if text == 'auto':
        return None
    if not re.fullmatch(r'[a-z]{2,3}', text):
        raise ValueError(f'{text!r} is not a language code. Use two or three letters such as en, de, es or fr, or auto.')
    return text


def model_name(size, language):
    """The faster-whisper model: the English-only one suits English best, the multilingual one every other language."""
    return f'{size}.en' if language == ENGLISH else size


def describe(size, language):
    """The model in words for the person: 'the base model for English' or 'the small multilingual model'."""
    return f'the {size} model for English' if language == ENGLISH else f'the {size} multilingual model'


def _ask(prompt, convert):
    """Ask until `convert` accepts the answer."""
    while True:
        try:
            return convert(input(prompt))
        except ValueError as e:
            print(f'  {e}')


def _ask_size():
    print('Which speech model?')
    for number, (name, (megabytes, about)) in enumerate(SIZES.items(), 1):
        print(f'  {number}  {name:<6} about {megabytes:>3} MB   {about}')
    return _ask(f'Number or name [{DEFAULT_SIZE}]: ', size_choice)


def _ask_language():
    print()
    return _ask('Which language will you speak? Press Enter for English, type a code such as de, es, fr or pt, or\n'
                'auto to let the model find out each time [English]: ', language_code)


def _confirm(question):
    return input(f'{question} [Y/n] ').strip().lower() in ('', 'y', 'yes')


# ---- what is already there --------------------------------------------------------------------------------------------

def _ask_quietly(command):
    """(True when the command ran without an error, what it printed), with nothing on the console."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors='replace', check=False,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        return False, ''
    return done.returncode == 0, done.stdout or ''


def recognizer_ready(python):
    """True when the recognizer's environment exists and can load faster-whisper."""
    return Path(python).is_file() and _ask_quietly([str(python), '-I', '-c', 'import faster_whisper'])[0]


def python_for_recognizer():
    """The command that starts a Python the recognizer is built for: this one, or one the Python launcher has.

    The launcher is only asked for its list. Told to run a version, it can download that version, and nothing may be
    downloaded before the person has agreed.
    """
    if THIS_PYTHON in PYTHONS:
        return [sys.executable]
    listed = LISTED_PYTHON.findall(_ask_quietly(['py', '-0p'])[1])
    for version in PYTHONS:
        if version in listed:
            return ['py', f'-{version}']
    raise SetupError(TOO_NEW.format(oldest=PYTHONS[-1], newest=PYTHONS[0], version=THIS_PYTHON))


def check_runtime():
    """Stop before any download on a computer that does not have Microsoft's C++ runtime."""
    missing = [name for name in RUNTIME if not (SYSTEM_FOLDER / name).is_file()]
    if missing:
        raise SetupError(NO_RUNTIME.format(missing=', '.join(missing), page=RUNTIME_PAGE))


def model_complete(folder):
    """True when a faster-whisper model is all in the folder: its weights, settings, tokenizer and vocabulary."""
    folder = Path(folder)
    return all((folder / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')) \
        and any(folder.glob('vocabulary.*'))


def _enough_space(folder, megabytes):
    free = shutil.disk_usage(folder).free // (1024 * 1024)
    if free < megabytes + SPARE_MB:
        raise SetupError(f'There is not enough free space on the drive of {folder}: about {megabytes + SPARE_MB} MB are '
                         f'needed and {free} MB are free.')


# ---- the steps --------------------------------------------------------------------------------------------------------

def _run(command, doing, env=None):
    """Run a command with its output on the console, so progress shows. Raises SetupError if it fails."""
    try:
        done = subprocess.run(command, env=env, check=False)
    except OSError as e:
        raise SetupError(f'Setup could not start what it needed for {doing}: {e}') from None
    if done.returncode:
        raise SetupError(f'Setup stopped while {doing}. The messages above say why.')


def make_environment(folder, starter):
    """Make the recognizer's environment with the Python that `starter` runs, and install the pinned packages.

    Returns its python.exe. One that is there already goes first: it cannot load the recognizer, or this would not be
    called, and if a Python too new for the recognizer made it, installing into it again would fail the same way.
    """
    requirements = folder / REQUIREMENTS
    if not requirements.is_file():
        raise SetupError(f'{REQUIREMENTS} is missing from {folder}, so there is nothing to install.')
    python = folder / VENV / 'Scripts' / 'python.exe'
    print(f'Creating the recognizer environment in {VENV} ...')
    _run([*starter, '-m', 'venv', '--clear', str(folder / VENV)], 'creating the environment')
    print('Installing the recognizer (faster-whisper and what it needs) from PyPI ...')
    _run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
          '-r', str(requirements)], 'installing the recognizer')
    return python


def _megabytes_in(folder):
    """How much is in the folder so far, partial downloads included, in millions of bytes."""
    total = 0
    for path in Path(folder).rglob('*'):
        try:
            total += path.stat().st_size if path.is_file() else 0
        except OSError:                                 # a file that was renamed into place a moment ago
            pass
    return total // 1_000_000


def _report_progress(folder, megabytes, finished):
    """Say how much of the model has arrived every few seconds until `finished` is set. The bars the download draws
    count files, not bytes, so on a slow connection the largest file would sit at the same bar for minutes."""
    shown = 0
    while not finished.wait(PROGRESS_SECONDS):
        arrived = _megabytes_in(folder)
        if arrived > shown:
            print(f'  {arrived} of about {megabytes} MB downloaded ...', flush=True)
            shown = arrived


def fetch_model(python, name, folder, megabytes=0):
    """Download the model into `folder` with the recognizer's own Python. `megabytes` is its size, for the progress."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    print(f'Downloading the {name} model from Hugging Face ...\n'
          '(A notice about unauthenticated requests is normal: no account or token is needed.)')
    env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_SYMLINKS_WARNING='1')
    finished = threading.Event()
    watcher = threading.Thread(target=_report_progress, args=(folder, megabytes, finished), daemon=True)
    watcher.start()
    try:
        _run([str(python), '-I', '-c', FETCH, REPOSITORY.format(name=name), str(folder)], 'downloading the model',
             env=env)
    finally:
        finished.set()
        watcher.join()
    if not model_complete(folder):
        raise SetupError(f'The model was downloaded but {folder} is missing some of its files.')


def check_recognizer(python, model, language):
    """Run the recognizer on a second of quiet noise: the packages load, the model opens and the worker answers."""
    noise = np.random.default_rng(1).normal(0.0, 0.002, ac.EXPORT_RATE).astype(np.float32)
    try:
        transcript.suggest(noise, python, model, language)
    except transcript.DraftUnavailable as e:
        raise SetupError(f'The recognizer was installed but did not run: {e}') from None


def save_settings(python, model, language):
    """Point the settings at what was installed. `None` for the language removes it, so each clip's is detected."""
    settings.update({'asr_python': str(python), 'asr_model': str(model), 'asr_language': language})


# ---- the whole setup --------------------------------------------------------------------------------------------------

def _set_up(folder, size, language, yes):
    print(INTRO)
    python = folder / VENV / 'Scripts' / 'python.exe'
    get_recognizer = not recognizer_ready(python)
    starter = None
    if get_recognizer:                                  # what cannot work is said now, before any question or download
        check_runtime()
        starter = python_for_recognizer()
    try:
        size = size_choice(size) if size else _ask_size()
        language = language_code(language) if language is not None else _ask_language()
    except ValueError as e:
        print(e)
        return 2
    name = model_name(size, language)
    model = folder / MODELS / f'faster-whisper-{name}'
    get_model = not model_complete(model)
    if get_recognizer or get_model:
        print('\nThis will download:')
        if get_recognizer:
            print(f'  - the speech recognizer from PyPI, about {RECOGNIZER_MB} MB, into {VENV}')
        if get_model:
            print(f'  - {describe(size, language)} from Hugging Face, about {SIZES[size][0]} MB, into {MODELS}')
        _enough_space(folder, (RECOGNIZER_INSTALLED_MB if get_recognizer else 0) + (SIZES[size][0] if get_model else 0))
        if not yes and not _confirm('Go ahead?'):
            print('Nothing was downloaded or changed.')
            return 0
    else:
        print(f'\nThe recognizer and {describe(size, language)} are already here; they only need checking and connecting.')
    if get_recognizer:
        python = make_environment(folder, starter)
    if get_model:
        fetch_model(python, name, model, SIZES[size][0])
    print('Checking that the recognizer works ...')
    check_recognizer(python, model, language)
    save_settings(python, model, language)
    print(DONE.format(what=describe(size, language), venv=VENV, model=f'{MODELS}\\{model.name}'))
    return 0


def run(size=None, language=None, yes=False, folder=None):
    """Set up draft transcripts. `size` and `language` are what was typed, or None to ask. Returns the exit code:
    0 when it is done or the person said no, 1 when a step failed or it was cancelled, 2 for an answer it cannot use."""
    folder = Path(folder) if folder else settings.program_dir()
    try:
        return _set_up(folder, size, language, yes)
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled. Nothing in your settings was changed.')
        return 1
    except SetupError as e:
        print(f'\n{e}\nNothing in your settings was changed. Run {transcript.SETUP_SCRIPT} again to try again.')
        return 1
