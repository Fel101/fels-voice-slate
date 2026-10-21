"""What starting from an icon needs.

A shortcut runs the program with `pythonw.exe`, so there is no console window and nothing to read a traceback in. This
module gives the program what the console would have: a log file, a plain message when it cannot start, and one running
copy at a time (a second double-click on the icon brings the first window forward instead of opening another).
"""
import ctypes
import faulthandler
import logging
import logging.handlers
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from . import __version__, settings

LOG_NAME = 'voice-slate.log'
CRASH_NAME = 'voice-slate-crash.log'
LOG_BYTES = 500_000
MUTEX_NAME = 'Local\\' + settings.APP_ID + '.SingleInstance'
ERROR_ALREADY_EXISTS = 183
HAS_CONSOLE = sys.stderr is not None            # pythonw.exe has no stdout or stderr; read before start_logging gives it some
_held = []                                      # what must last as long as the process: the instance mutex, the crash log


def log_path():
    """The log file: beside the settings file, so a copy that keeps its settings next to the program keeps its log there."""
    return settings.settings_path().with_name(LOG_NAME)


class _Stream:
    """Stands in for stdout and stderr when there are none: each line printed is written to the log."""

    def __init__(self, logger, level):
        self.logger, self.level, self.pending = logger, level, ''

    def write(self, text):
        self.pending += str(text)
        *lines, self.pending = self.pending.split('\n')
        for line in lines:
            if line.strip():
                self.logger.log(self.level, line.rstrip())
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def start_logging(path=None):
    """Log to a file (500 kB, then one older copy), send what is printed there when there is no console, and note a crash
    of a native library (the audio drivers) in a second file. Returns the log's path."""
    path = Path(path) if path else log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stdout is None:
        sys.stdout = _Stream(logging.getLogger('stdout'), logging.INFO)
    if sys.stderr is None:
        sys.stderr = _Stream(logging.getLogger('stderr'), logging.ERROR)
    try:
        crash = open(path.with_name(CRASH_NAME), 'a', encoding='utf-8')
        faulthandler.enable(crash)
        _held.append(crash)
    except (OSError, RuntimeError):
        pass
    sys.excepthook = _log_uncaught
    threading.excepthook = lambda args: _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    logging.getLogger('recorder').info('%s %s starting. Python %s', settings.APP_NAME, __version__, sys.version.split()[0])
    return path


def release():
    """Let go of what start_logging and already_running keep for the life of the process (a test does; the program need not)."""
    kernel32 = None
    while _held:
        item = _held.pop()
        if hasattr(item, 'close'):
            item.close()
        else:
            kernel32 = kernel32 or ctypes.WinDLL('kernel32')
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(item)


def _log_uncaught(kind, error, trace):
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, error, trace)
        return
    logging.getLogger('recorder').error('Uncaught error', exc_info=(kind, error, trace))


def tell(text, title=settings.APP_NAME, error=True):
    """Say something to the person: on the console if there is one, else in a message box."""
    if HAS_CONSOLE and sys.stderr is not None:
        sys.stderr.write(f'{text}\n')
        return
    try:
        user32 = ctypes.WinDLL('user32')
        user32.MessageBoxW(None, str(text), str(title), 0x10 if error else 0x40)       # OK button; error or information icon
    except (AttributeError, OSError):
        pass


def already_running(name=MUTEX_NAME):
    """True if another copy of the program (or an earlier call in this process) holds the instance mutex. The first
    caller keeps it until the process ends, so a second copy sees it."""
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return False                                                # cannot tell: let this copy run
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return True
        _held.append(handle)
        return False
    except (AttributeError, OSError):
        return False


def bring_forward(title=settings.APP_NAME):
    """Show and raise the window of the copy that is already running. True if it was found."""
    try:
        user32 = ctypes.WinDLL('user32')
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        window = user32.FindWindowW(None, title)
        if not window:
            return False
        if user32.IsIconic(window):
            user32.ShowWindow(window, 9)                                # SW_RESTORE
        user32.SetForegroundWindow(window)
        return True
    except (AttributeError, OSError):
        return False
