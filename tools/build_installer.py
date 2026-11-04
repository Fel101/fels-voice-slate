"""Build the Windows installer: a private Python with the program's packages, the program, and an Inno Setup installer of them.

    python tools/build_installer.py                       everything, into build/
    python tools/build_installer.py --iscc PATH           name Inno Setup's compiler, when it is not found by itself
    python tools/build_installer.py --no-installer        stop before the installer: build/app can be started as it is

The installed program is not frozen into an .exe. It is the program as it is here, run by a private copy of Python, for three
reasons: the transcript setup needs a real Python to make its environment with, virus scanners often flag frozen programs
that have no signature, and Qt stays a plain library that a person can replace, which its licence (LGPL-3.0) asks for.

It needs `uv` (pip install uv), which fetches a Python made to be moved, and Inno Setup 6 for the last step. Both are only
needed here. Nothing about the person building it goes in: the Python is a download and the packages are the pinned ones.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = '3.13'
ISS = ROOT / 'installer' / 'voice-slate.iss'
ISCC_PLACES = (Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6' / 'ISCC.exe',
               Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Inno Setup 6' / 'ISCC.exe',
               Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe')
PROGRAM_FILES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'requirements-asr.txt', 'requirements.txt',
                 'setup-transcripts.bat')
# What the window does not use. All of this is Qt's own tooling and modules, and Python's tests and its window toolkit: the
# smoke test at the end of the build opens the real window, so a mistake here stops the build.
PRUNE_PYTHON = ('Lib/test', 'Lib/idlelib', 'Lib/tkinter', 'Lib/turtledemo', 'tcl', 'include', 'Scripts', 'Lib/site-packages/pip')
PRUNE_QT_FOLDERS = ('qml', 'translations', 'typesystems', 'include', 'glue', 'metatypes', 'scripts', 'support', 'doc',
                    'resources', 'plugins/qmllint', 'plugins/qmltooling', 'plugins/designer', 'plugins/sqldrivers',
                    'plugins/multimedia', 'plugins/networkinformation', 'plugins/tls', 'plugins/generic',
                    'plugins/platforminputcontexts')
PRUNE_QT_FILES = ('*.exe', '*.pyi', 'Qt6Quick*', 'Qt6Qml*', 'Qt6Pdf*', 'Qt6Designer*', 'Qt6Help*', 'Qt6Sql*', 'Qt6Test*',
                  'Qt6OpenGL*', 'Qt6Network*', 'Qt6Concurrent*', 'Qt6Xml*', 'Qt6Labs*', 'Qt6ShaderTools*', 'opengl32sw.dll',
                  'QtQuick*', 'QtQml*', 'QtPdf*', 'QtDesigner*', 'QtHelp*', 'QtSql*', 'QtTest*', 'QtOpenGL*', 'QtNetwork*',
                  'QtConcurrent*', 'QtXml*', 'QtUiTools*', 'pyside6qml*', 'libEGL*', 'libGLESv2*', 'd3dcompiler*')

SMOKE = """
import os, sys
os.environ['VOICE_SLATE_SETTINGS'] = sys.argv[1]
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from recorder.ui import theme
from recorder.ui.main_window import MainWindow
app = QApplication(['smoke'])
theme.install(app)
assert app.platformName() == 'windows'
window = MainWindow()
window.timer.stop()
window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
window.show()
app.processEvents()
assert not window.grab().isNull()
window.close()
import numpy, scipy.signal, scipy.ndimage, soundfile, sounddevice, send2trash, pyaudiowpatch
print('smoke test passed')
"""


def run(command, **options):
    print('>', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def version():
    text = (ROOT / 'recorder' / '__init__.py').read_text(encoding='utf-8')
    return text.split("__version__ = '")[1].split("'")[0]


def fetch_python(work):
    """A Python made to be moved, fetched by uv. Returns its folder, copied to `work`."""
    store = work / 'python-download'
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(store))
    run(['uv', 'python', 'install', PYTHON_VERSION, '--no-bin', '--no-registry'], env=env)
    found = sorted(path for path in store.iterdir() if path.is_dir() and path.name.startswith('cpython-') and
                   (path / 'python.exe').is_file())
    if not found:
        sys.exit(f'uv put no Python in {store}')
    target = work / 'app' / 'python'
    shutil.copytree(found[-1], target)
    return target


def install_packages(python):
    (python / 'Lib' / 'EXTERNALLY-MANAGED').unlink(missing_ok=True)      # uv's marker: this copy is ours to install into
    run([python / 'python.exe', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location',
         '--only-binary=:all:', '-r', ROOT / 'requirements.txt'])


def remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def prune(python):
    for name in PRUNE_PYTHON:
        remove(python / name)
    site = python / 'Lib' / 'site-packages'
    for folder in [path for path in site.rglob('*') if path.is_dir() and path.name in ('tests', '__pycache__')]:
        remove(folder)
    qt = site / 'PySide6'
    for name in PRUNE_QT_FOLDERS:
        remove(qt / name)
    for pattern in PRUNE_QT_FILES:
        for path in qt.glob(pattern):
            remove(path)


def copy_program(app):
    shutil.copytree(ROOT / 'recorder', app / 'recorder', ignore=shutil.ignore_patterns('__pycache__'))
    for name in PROGRAM_FILES:
        shutil.copy2(ROOT / name, app / name)


def compile_program(python, app):
    """Compile ahead of time, because a first start that takes half a minute looks like a program that does not work.

    They are checked by hash, because Python's usual check is the file's time to the second, and Inno Setup rounds the times
    it installs down to two seconds: half of what was compiled here would look out of date on the person's computer."""
    subprocess.run([str(part) for part in (python / 'python.exe', '-E', '-s', '-m', 'compileall', '-q', '-f', '-j', '0',
                                           '--invalidation-mode', 'checked-hash', app / 'recorder', python / 'Lib')], check=False)


def smoke_test(python, app, work):
    """Open the real window with the Python that will ship, from the folder that will ship."""
    run([python / 'python.exe', '-E', '-s', '-c', SMOKE, work / 'smoke-settings.json'], cwd=app)
    run([python / 'python.exe', '-E', '-s', '-m', 'recorder', '--version'], cwd=app)


def compiler(named):
    for place in ([Path(named)] if named else [*ISCC_PLACES]):
        if place.is_file():
            return place
    found = shutil.which('iscc')
    if found:
        return Path(found)
    sys.exit('Inno Setup 6 was not found. Install it, or name its ISCC.exe with --iscc.')


def size_of(folder):
    return sum(path.stat().st_size for path in folder.rglob('*') if path.is_file()) // (1 << 20)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--iscc', help="Inno Setup's ISCC.exe")
    parser.add_argument('--work', default=str(ROOT / 'build'), help='the folder to build in')
    parser.add_argument('--no-installer', action='store_true', help='stop after the program folder')
    args = parser.parse_args()
    work = Path(args.work).resolve()        # in full: Inno Setup reads a relative folder from installer/, beside its script
    if work.exists():
        shutil.rmtree(work)
    (work / 'app').mkdir(parents=True)
    python = fetch_python(work)
    install_packages(python)
    prune(python)
    copy_program(work / 'app')
    compile_program(python, work / 'app')
    smoke_test(python, work / 'app', work)
    print(f'The program folder is {work / "app"}, {size_of(work / "app")} MB.')
    if args.no_installer:
        return 0
    run([compiler(args.iscc), f'/DAppVersion={version()}', f'/DAppFolder={work / "app"}', f'/O{work}', ISS])
    made = work / f'Fels-Voice-Slate-{version()}-Setup.exe'
    print(f'The installer is {made}, {made.stat().st_size // (1 << 20)} MB.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
