"""Start Fel's Voice Slate: python -m recorder

    python -m recorder               open the window
    pythonw -m recorder              the same with no console window (what the desktop shortcut runs)
    python -m recorder --check       say whether what the program needs is installed
    python -m recorder --shortcut    put a shortcut on the desktop (or: folder, start-menu, or a path ending in .lnk)
    python -m recorder --setup-transcripts   download a speech recognizer and model for draft transcripts (asks first)
    python -m recorder --version
"""
import argparse
import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from . import __version__, launch, settings

REQUIRED = (('PySide6', 'the window'), ('numpy', 'audio maths'), ('scipy', 'filters and resampling'),
            ('soundfile', 'reading and writing WAV files'), ('sounddevice', 'the microphone and playback'),
            ('send2trash', 'Discard, which uses the Recycle Bin'))
OPTIONAL = (('pyaudiowpatch', 'recording what the computer plays'),)
NEEDS_PYSIDE = (f'{settings.APP_NAME} needs PySide6 and the other requirements. Install them with:\n'
                '    python -m pip install -r requirements.txt\n'
                'or run setup.bat in the program folder.')


def parse(argv):
    parser = argparse.ArgumentParser(prog='python -m recorder', description=settings.APP_NAME)
    parser.add_argument('--version', action='store_true', help='print the version and exit')
    parser.add_argument('--check', action='store_true', help='check that what the program needs is installed')
    parser.add_argument('--shortcut', nargs='?', const='desktop', metavar='WHERE',
                        help='make a shortcut that starts the program with no console window: desktop (the default), '
                             'folder (the program folder), start-menu, or a path ending in .lnk')
    parser.add_argument('--setup-transcripts', nargs='?', const='', metavar='SIZE',
                        help='download a speech recognizer and a speech model for draft transcripts, after asking: '
                             'SIZE is tiny, base or small (asked if left out)')
    parser.add_argument('--language', metavar='CODE',
                        help='with --setup-transcripts: the language you speak, such as en or de, or auto')
    parser.add_argument('--yes', action='store_true', help='with --setup-transcripts: do not ask before downloading')
    return parser.parse_args(argv)


def check():
    """Print what is installed and what is missing. Returns 0 if the program can run."""
    from . import clean

    data = settings.load()
    missing = False
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
        print(f'  {"ok     " if ok else "PROBLEM" if required else "absent "}  {what:40s} {state}')
    ffmpeg = clean.locate(data.get('ffmpeg'))
    print(f'  {"ok     " if ffmpeg else "absent "}  {"noise cleaning (FFmpeg, optional)":40s} {ffmpeg or "not found"}')
    try:
        from . import transcript
        drafts = transcript.available(data.get('asr_python'), data.get('asr_model'))
    except ImportError:                                 # soundfile is missing, which the lines above already say
        drafts = False
    print(f'  {"ok     " if drafts else "absent "}  {"draft transcripts (optional)":40s} '
          f'{"ready" if drafts else "not set up: run setup-transcripts.bat"}')
    root = data.get('root') or f'{settings.program_dir() / settings.CLIPS_FOLDER}  (the default)'
    print(f'\nClips are saved in: {root}\nSettings file: {settings.settings_path()}\nLog file: {launch.log_path()}')
    print('\n' + ('Everything the program needs is installed.' if not missing else 'Something it needs is missing: see above.'))
    return 1 if missing else 0


def make_shortcut(where):
    from . import shortcut

    if where in ('desktop', 'folder', 'start-menu'):
        link = shortcut.default_path(where)
    elif where.lower().endswith('.lnk'):
        link = Path(where)
    else:
        print(f'Not a place I know: {where}. Use desktop, folder, start-menu or a path ending in .lnk.', file=sys.stderr)
        return 2
    try:
        made = shortcut.create(link)
    except OSError as e:
        print(f'Could not make the shortcut: {e}', file=sys.stderr)
        return 1
    print(f'Made {made}')
    print(f'Double-click it to start {settings.APP_NAME}.')
    return 0


def setup_transcripts(size, language, yes):
    from . import asr_setup

    return asr_setup.run(size or None, language, yes)


def start():
    """Open the window, with a log file and a plain message if it cannot start (there may be no console to read)."""
    launch.start_logging()
    if importlib.util.find_spec('PySide6') is None:
        launch.tell(NEEDS_PYSIDE)
        return 1
    if launch.already_running():
        if not launch.bring_forward():
            launch.tell(f'{settings.APP_NAME} is already running. Look for its window on the taskbar.', error=False)
        return 0
    try:
        from .ui import run
        return run()
    except Exception:
        logging.getLogger('recorder').exception('The window could not start')
        launch.tell(f'{settings.APP_NAME} could not start. The reason is in the log file:\n{launch.log_path()}')
        return 1


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f'{settings.APP_NAME} {__version__}')
        return 0
    if args.check:
        return check()
    if args.shortcut:
        return make_shortcut(args.shortcut)
    if args.setup_transcripts is not None:
        return setup_transcripts(args.setup_transcripts, args.language, args.yes)
    return start()


if __name__ == '__main__':
    sys.exit(main())
