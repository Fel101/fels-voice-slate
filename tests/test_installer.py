"""The installer's script and its build. Building it takes minutes and Inno Setup, so that is left to the release workflow and
tools/build_installer.py, which opens the real window from what it built. These keep the two in step with the program."""
import fnmatch
import re
import sys
from pathlib import Path

import pytest

from recorder import launch, settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import build_installer  # noqa: E402

SCRIPT = (settings.program_dir() / 'installer' / 'voice-slate.iss').read_text(encoding='utf-8')
NEEDED = ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll', 'Qt6Svg.dll', 'QtCore.pyd', 'QtGui.pyd', 'QtWidgets.pyd', 'QtSvg.pyd',
          'pyside6.abi3.dll')


def test_the_installer_waits_for_the_running_program_by_the_programs_own_lock():
    assert f'AppMutex={launch.MUTEX_NAME}' in SCRIPT


def test_the_icons_group_with_the_window_on_the_taskbar():
    assert SCRIPT.count(f'AppUserModelID: "{settings.APP_ID}"') == 2


def test_it_installs_for_one_person_so_the_program_folder_stays_writable():
    """The transcript setup makes its environment and fetches its model there."""
    assert 'PrivilegesRequired=lowest' in SCRIPT and 'DefaultDirName={localappdata}' in SCRIPT


def test_it_starts_the_program_the_way_a_shortcut_does():
    from recorder import shortcut
    assert f'#define Start "-E -s {shortcut.ARGUMENTS}"' in SCRIPT


def test_it_starts_a_python_that_is_in_the_folder_it_built():
    assert '{app}\\python\\pythonw.exe' in SCRIPT and build_installer.ISS.name == 'voice-slate.iss'


def test_an_update_replaces_the_program_but_keeps_the_recognizer():
    update = SCRIPT.split('[InstallDelete]')[1].split('[Files]')[0]
    assert '{app}\\python' in update and '{app}\\recorder' in update
    assert '.venv-asr' not in update and 'models' not in update


def test_uninstalling_says_the_recordings_stay():
    assert 'Your recordings and settings' in SCRIPT and 'voice-clips' not in SCRIPT.split('[UninstallDelete]')[1]


def test_the_file_name_the_build_expects_is_the_one_the_script_writes():
    assert 'OutputBaseFilename=Fels-Voice-Slate-{#AppVersion}-Setup' in SCRIPT


@pytest.mark.parametrize('name', NEEDED)
def test_nothing_the_window_needs_is_pruned(name):
    assert not any(fnmatch.fnmatch(name, pattern) for pattern in build_installer.PRUNE_QT_FILES)


def test_what_the_transcript_setup_needs_is_kept():
    """It makes an environment with the private Python, which takes ensurepip and venv."""
    assert not any(part in entry for entry in build_installer.PRUNE_PYTHON for part in ('ensurepip', 'venv'))


def test_the_version_is_read_from_the_program():
    assert re.fullmatch(r'\d+\.\d+\.\d+', build_installer.version())


def test_what_is_compiled_ahead_does_not_depend_on_the_times_inno_setup_rounds(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(build_installer.subprocess, 'run', lambda command, **options: seen.append(command))
    build_installer.compile_program(tmp_path / 'python', tmp_path / 'app')
    assert seen[0][seen[0].index('--invalidation-mode') + 1] == 'checked-hash'


def test_the_build_copies_what_the_program_refers_to():
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'setup-transcripts.bat', 'requirements-asr.txt'):
        assert name in build_installer.PROGRAM_FILES and (settings.program_dir() / name).is_file()


def test_the_transcript_setup_finds_the_python_it_comes_with():
    script = (settings.program_dir() / 'setup-transcripts.bat').read_text(encoding='utf-8')
    assert 'python\\python.exe' in script and '.venv\\Scripts\\python.exe' in script
