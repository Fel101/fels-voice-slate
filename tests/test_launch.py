"""Starting from an icon: the log file, plain messages instead of a console, one running copy, and the command line."""
import faulthandler
import logging
import sys
import threading
import uuid
from pathlib import Path

import pytest

from recorder import __main__ as entry
from recorder import __version__, launch, settings, shortcut

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='the launcher is for Windows')


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """The settings (and so the log) in a temporary folder, and the root logger and process-wide hooks put back after."""
    monkeypatch.setenv(settings.ENV_VAR, str(tmp_path / 'settings.json'))
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = (sys.excepthook, threading.excepthook)
    yield tmp_path / launch.LOG_NAME
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    sys.excepthook, threading.excepthook = hooks
    faulthandler.disable()
    launch.release()


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


# ---- the log ----------------------------------------------------------------------------------------------------------

def test_the_log_sits_beside_the_settings_file(isolated_log):
    assert launch.log_path() == isolated_log


def test_a_process_with_no_console_logs_what_it_prints(isolated_log, monkeypatch):
    monkeypatch.setattr(sys, 'stdout', None)                    # what pythonw.exe starts with
    monkeypatch.setattr(sys, 'stderr', None)
    launch.start_logging(isolated_log)
    print('a line printed by a library')
    sys.stderr.write('a warning from somewhere\n')
    flush()
    text = isolated_log.read_text(encoding='utf-8')
    assert 'INFO stdout: a line printed by a library' in text and 'ERROR stderr: a warning from somewhere' in text
    assert f"Fel's Voice Slate {__version__} starting" in text
    assert f'Settings: {settings.settings_path()}' in text                  # which file it reads is the first thing to know


def test_a_console_is_left_as_it_is(isolated_log, capsys):
    launch.start_logging(isolated_log)
    print('still on the console')
    assert 'still on the console' in capsys.readouterr().out


def test_an_uncaught_error_is_logged_with_its_traceback(isolated_log):
    launch.start_logging(isolated_log)
    try:
        raise ValueError('boom')
    except ValueError:
        sys.excepthook(*sys.exc_info())
    flush()
    text = isolated_log.read_text(encoding='utf-8')
    assert 'Uncaught error' in text and 'ValueError: boom' in text and 'Traceback' in text


def test_an_uncaught_error_reaches_the_window_as_well_as_the_log(isolated_log):
    heard = []
    launch.start_logging(isolated_log)
    launch.report_errors_to(heard.append)
    try:
        try:
            raise ValueError('boom')
        except ValueError as error:
            sys.excepthook(type(error), error, error.__traceback__)
        thread = threading.Thread(target=lambda: 1 / 0)
        thread.start()
        thread.join()
    finally:
        launch.report_errors_to(None)
    assert [type(error) for error in heard] == [ValueError, ZeroDivisionError]
    flush()
    assert 'ValueError: boom' in isolated_log.read_text(encoding='utf-8')


def test_a_window_that_cannot_be_told_does_not_make_things_worse(isolated_log):
    launch.start_logging(isolated_log)
    launch.report_errors_to(lambda error: 1 / 0)
    try:
        try:
            raise ValueError('boom')
        except ValueError as error:
            sys.excepthook(type(error), error, error.__traceback__)
    finally:
        launch.report_errors_to(None)
    flush()
    text = isolated_log.read_text(encoding='utf-8')
    assert 'ValueError: boom' in text and 'could not be told' in text


def test_an_error_in_a_worker_thread_is_logged_too(isolated_log):
    launch.start_logging(isolated_log)

    def work():
        raise RuntimeError('in a thread')

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    flush()
    assert 'RuntimeError: in a thread' in isolated_log.read_text(encoding='utf-8')


# ---- one copy at a time -----------------------------------------------------------------------------------------------

def test_a_second_copy_finds_that_one_is_running(isolated_log):
    name = f'Local\\VoiceSlate.Test.{uuid.uuid4().hex}'     # never the real one: a recorder may be open on this computer
    assert launch.already_running(name) is False
    assert launch.already_running(name) is True
    launch.release()
    assert launch.already_running(name) is False


def test_a_window_that_is_not_there_is_not_brought_forward():
    assert launch.bring_forward(f'No such window {uuid.uuid4().hex}') is False


def test_a_second_start_brings_the_first_window_forward_and_opens_nothing(isolated_log, monkeypatch):
    opened = []
    monkeypatch.setattr(launch, 'already_running', lambda: True)
    monkeypatch.setattr(launch, 'bring_forward', lambda: True)
    monkeypatch.setattr('recorder.ui.run', lambda: opened.append(1) or 0)
    assert entry.start() == 0 and opened == []


def test_a_second_start_says_so_when_it_cannot_find_the_first_window(isolated_log, monkeypatch):
    said = []
    monkeypatch.setattr(launch, 'already_running', lambda: True)
    monkeypatch.setattr(launch, 'bring_forward', lambda: False)
    monkeypatch.setattr(launch, 'tell', lambda text, **kw: said.append((text, kw)))
    assert entry.start() == 0
    assert 'already running' in said[0][0] and said[0][1] == {'error': False}


# ---- start-up that fails ----------------------------------------------------------------------------------------------

def test_a_window_that_cannot_open_is_reported_in_a_message_and_in_the_log(isolated_log, monkeypatch):
    said = []

    def fail():
        raise RuntimeError('no display')

    monkeypatch.setattr(launch, 'already_running', lambda: False)
    monkeypatch.setattr(launch, 'tell', lambda text, **kw: said.append(text))
    monkeypatch.setattr('recorder.ui.run', fail)
    assert entry.start() == 1
    assert str(isolated_log) in said[0]
    flush()
    assert 'The window could not start' in isolated_log.read_text(encoding='utf-8')
    assert 'RuntimeError: no display' in isolated_log.read_text(encoding='utf-8')


def test_missing_requirements_get_a_plain_message(isolated_log, monkeypatch):
    said = []
    monkeypatch.setattr(launch, 'tell', lambda text, **kw: said.append(text))
    monkeypatch.setattr(entry.importlib.util, 'find_spec', lambda name: None if name == 'PySide6' else object())
    assert entry.start() == 1
    assert 'requirements.txt' in said[0] and 'setup.bat' in said[0]


def test_with_no_console_a_message_goes_in_a_box(monkeypatch):
    boxes = []

    class User32:
        def MessageBoxW(self, *args):
            boxes.append(args)

    monkeypatch.setattr(launch, 'HAS_CONSOLE', False)
    monkeypatch.setattr(launch.ctypes, 'WinDLL', lambda name: User32())
    launch.tell('it broke')
    assert boxes == [(None, 'it broke', "Fel's Voice Slate", 0x10)]
    launch.tell('just so you know', error=False)
    assert boxes[1][3] == 0x40


def test_with_a_console_a_message_goes_to_it(monkeypatch, capsys):
    monkeypatch.setattr(launch, 'HAS_CONSOLE', True)
    launch.tell('it broke')
    assert 'it broke' in capsys.readouterr().err


# ---- the command line -------------------------------------------------------------------------------------------------

def test_version_prints_the_name_and_number(capsys):
    assert entry.main(['--version']) == 0
    assert capsys.readouterr().out.strip() == f"Fel's Voice Slate {__version__}"


def test_check_lists_what_is_installed(isolated_log, capsys):
    assert entry.main(['--check']) == 0
    out = capsys.readouterr().out
    assert 'PySide6' in out and 'Everything the program needs is installed' in out
    assert str(isolated_log) in out and 'Clips are saved in' in out


def test_check_says_what_is_missing_and_fails(isolated_log, capsys, monkeypatch):
    real = entry.importlib.util.find_spec
    monkeypatch.setattr(entry.importlib.util, 'find_spec', lambda name: None if name == 'scipy' else real(name))
    assert entry.main(['--check']) == 1
    out = capsys.readouterr().out
    assert 'MISSING' in out and 'Something it needs is missing' in out


def test_check_treats_a_package_that_will_not_load_as_a_problem(isolated_log, capsys, monkeypatch):
    real = entry.importlib.import_module

    def broken(name, *args):
        if name == 'soundfile':
            raise OSError('libsndfile not found')
        return real(name, *args)

    monkeypatch.setattr(entry.importlib, 'import_module', broken)
    assert entry.main(['--check']) == 1
    assert 'does not load (libsndfile not found)' in capsys.readouterr().out


def test_the_shortcut_option_writes_a_shortcut_that_starts_the_program(tmp_path, capsys):
    link = tmp_path / 'Recorder.lnk'
    assert entry.main(['--shortcut', str(link)]) == 0
    info = shortcut.read(link)
    assert info['arguments'] == '-m recorder' and Path(info['folder']) == settings.program_dir()
    assert 'Made' in capsys.readouterr().out


def test_the_shortcut_option_refuses_a_place_it_does_not_know(capsys):
    assert entry.main(['--shortcut', 'nowhere']) == 2
    assert 'Not a place I know' in capsys.readouterr().err


def test_the_folder_shortcut_goes_in_the_program_folder(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, 'program_dir', lambda: tmp_path)
    assert entry.main(['--shortcut', 'folder']) == 0
    link = tmp_path / "Fel's Voice Slate.lnk"
    assert link.is_file() and Path(shortcut.read(link)['folder']) == tmp_path


def test_setup_makes_the_icon_in_the_program_folder():
    """setup.bat is the one way most people make the icon, and a shortcut file cannot be shared, so it must still make it."""
    setup = (settings.program_dir() / 'setup.bat').read_text(encoding='utf-8')
    assert '-m recorder --shortcut folder' in setup


def test_setup_finds_a_python_newer_than_the_ones_it_names():
    """The README says 3.12 or newer, and a new Python is often only known to the launcher."""
    setup = (settings.program_dir() / 'setup.bat').read_text(encoding='utf-8')
    assert 'py -3 -c' in setup and setup.index('(3.14 3.13 3.12)') < setup.index('py -3 -c') < setup.index('python -c')


def test_the_desktop_shortcut_goes_where_windows_keeps_the_desktop(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(shortcut, 'known_folder', lambda name: tmp_path / name)
    assert entry.main(['--shortcut']) == 0
    assert (tmp_path / 'desktop' / "Fel's Voice Slate.lnk").is_file()
    assert entry.main(['--shortcut', 'start-menu']) == 0
    assert (tmp_path / 'start-menu' / "Fel's Voice Slate.lnk").is_file()
