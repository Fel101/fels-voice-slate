"""Windows shortcuts (.lnk) that start the recorder without a console window.

A shortcut runs `pythonw -m recorder` from the program folder, shows the program's icon, and carries the program's
AppUserModelID, so a copy pinned to the taskbar and the running window are one button. Only ctypes is used: no extra
package is needed to make one.

    python -m recorder --shortcut              a shortcut on the desktop
    python -m recorder --shortcut start-menu   one in the Start menu's program list
    python -m recorder --shortcut folder       one in the program folder, beside the code

A shortcut file holds the paths of the computer it was made on (and Windows adds that computer's name), so one made here
cannot be passed on: Git ignores `*.lnk`, and each person makes their own, which `setup.bat` does.
"""
import ctypes
import sys
import uuid
from contextlib import contextmanager
from ctypes import POINTER, Structure, byref, c_int, c_ulong, c_ushort, c_void_p, c_wchar_p, wintypes
from functools import lru_cache
from pathlib import Path

from . import settings

ICON = Path('recorder') / 'resources' / 'voice-slate.ico'
ARGUMENTS = '-m recorder'
DESCRIPTION = 'Record, edit and check short voice reference clips'
FOLDERS = {'desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641', 'start-menu': 'A77F5D77-2E2B-44C3-A6A2-ABA601054A51'}
FALLBACK_FOLDERS = {'desktop': Path('Desktop'), 'start-menu': Path('AppData/Roaming/Microsoft/Windows/Start Menu/Programs')}
VT_LPWSTR = 31
CLSID_SHELL_LINK = '00021401-0000-0000-C000-000000000046'
IID_SHELL_LINK = '000214F9-0000-0000-C000-000000000046'
IID_PERSIST_FILE = '0000010B-0000-0000-C000-000000000046'
IID_PROPERTY_STORE = '886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99'
APP_USER_MODEL_ID = ('9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3', 5)         # System.AppUserModel.ID

# Slots in the method tables of the COM interfaces used below (IUnknown takes the first three).
QUERY_INTERFACE, RELEASE = 0, 2
GET_PATH, GET_DESCRIPTION, SET_DESCRIPTION, GET_WORKING_DIRECTORY, SET_WORKING_DIRECTORY = 3, 6, 7, 8, 9
GET_ARGUMENTS, SET_ARGUMENTS, GET_ICON_LOCATION, SET_ICON_LOCATION, SET_PATH = 10, 11, 16, 17, 20
LOAD, SAVE = 5, 6                                                         # IPersistFile
GET_VALUE, SET_VALUE, COMMIT = 5, 6, 7                                    # IPropertyStore


class GUID(Structure):
    _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD), ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class PropertyKey(Structure):
    _fields_ = [('fmtid', GUID), ('pid', wintypes.DWORD)]


class PropVariant(Structure):
    _fields_ = [('vt', c_ushort), ('reserved1', c_ushort), ('reserved2', c_ushort), ('reserved3', c_ushort),
                ('value', c_void_p), ('extra', c_void_p)]


@lru_cache(maxsize=None)
def _dlls():
    """The Windows libraries used here, with the types of the functions called."""
    ole32, shell32 = ctypes.WinDLL('ole32'), ctypes.WinDLL('shell32')
    ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.HRESULT
    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
    ole32.CoTaskMemAlloc.restype = c_void_p
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    ole32.CoTaskMemFree.restype = None
    ole32.PropVariantClear.argtypes = [POINTER(PropVariant)]
    ole32.PropVariantClear.restype = ctypes.HRESULT
    shell32.SHGetKnownFolderPath.argtypes = [POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, POINTER(c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT
    return ole32, shell32


class Interface:
    """A COM object seen through one interface: a pointer whose first field points at the table of its methods."""

    def __init__(self, pointer):
        self.pointer = c_void_p(pointer)

    def _function(self, slot, restype, *argtypes):
        table = ctypes.cast(self.pointer, POINTER(POINTER(c_void_p)))[0]
        return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[slot])

    def call(self, slot, argtypes, *args):
        """Call a method. A failing HRESULT raises OSError."""
        return self._function(slot, ctypes.HRESULT, *argtypes)(self.pointer, *args)

    def query(self, iid):
        out = c_void_p()
        self.call(QUERY_INTERFACE, (POINTER(GUID), POINTER(c_void_p)), byref(GUID.of(iid)), byref(out))
        return Interface(out.value)

    def release(self):
        self._function(RELEASE, c_ulong)(self.pointer)

    def put_text(self, slot, text):
        self.call(slot, (c_wchar_p,), str(text))

    def get_text(self, slot):
        """Read a string through a method of the form Get...(buffer, size)."""
        buffer = ctypes.create_unicode_buffer(1024)
        if slot == GET_PATH:                                    # also takes the file's details (not wanted) and flags
            self.call(slot, (wintypes.LPWSTR, c_int, c_void_p, wintypes.DWORD), buffer, len(buffer), None, 0)
        elif slot == GET_ICON_LOCATION:                         # also gives the icon's number inside its file
            self.call(slot, (wintypes.LPWSTR, c_int, POINTER(c_int)), buffer, len(buffer), byref(c_int()))
        else:
            self.call(slot, (wintypes.LPWSTR, c_int), buffer, len(buffer))
        return buffer.value


@contextmanager
def _holding(first):
    """Interfaces to release when done: yields a list the caller adds to, starting with `first`."""
    pieces = [first]
    try:
        yield pieces
    finally:
        for piece in reversed(pieces):
            piece.release()


@contextmanager
def _com():
    ole32 = _dlls()[0]
    started = ole32.CoInitializeEx(None, 2) >= 0                # 2: apartment threaded. False if COM is already set up
    try:                                                        # another way: carry on, it still works
        yield
    finally:
        if started:
            ole32.CoUninitialize()


def _new_link():
    out = c_void_p()
    _dlls()[0].CoCreateInstance(byref(GUID.of(CLSID_SHELL_LINK)), None, 1, byref(GUID.of(IID_SHELL_LINK)), byref(out))
    return Interface(out.value)


def _app_id_key():
    key = PropertyKey()
    key.fmtid = GUID.of(APP_USER_MODEL_ID[0])
    key.pid = APP_USER_MODEL_ID[1]
    return key


def _string_variant(text):
    """A PROPVARIANT holding a string. Free it with PropVariantClear."""
    data = (text + '\0').encode('utf-16-le')
    memory = _dlls()[0].CoTaskMemAlloc(len(data))
    if not memory:
        raise OSError('Windows could not allocate memory for the shortcut')
    ctypes.memmove(memory, data, len(data))
    value = PropVariant()
    value.vt = VT_LPWSTR
    value.value = memory
    return value


def known_folder(name):
    """The desktop or the Start menu's program list of the current user, wherever Windows has put it (OneDrive may have
    moved the desktop). `name` is 'desktop' or 'start-menu'."""
    ole32, shell32 = _dlls()
    path = c_void_p()
    try:
        shell32.SHGetKnownFolderPath(byref(GUID.of(FOLDERS[name])), 0, None, byref(path))
        return Path(ctypes.wstring_at(path.value))
    except OSError:
        return Path.home() / FALLBACK_FOLDERS[name]
    finally:
        if path.value:
            ole32.CoTaskMemFree(path)


def launch_target():
    """(program, arguments, working folder, icon file) of a shortcut that starts the recorder with no console window."""
    python = Path(sys.executable)
    windowless = python.with_name('pythonw.exe')
    here = settings.program_dir()
    return (windowless if windowless.exists() else python), ARGUMENTS, here, here / ICON


def default_path(where='desktop'):
    """Where the shortcut goes: 'desktop', 'start-menu', or 'folder' (the program folder)."""
    folder = settings.program_dir() if where == 'folder' else known_folder(where)
    return folder / f'{settings.APP_NAME}.lnk'


def create(link, program=None, arguments=None, folder=None, icon=None, description=DESCRIPTION, app_id=settings.APP_ID):
    """Write a shortcut file at `link`, replacing one that is there. By default it starts this program with no console
    window. Returns the path. Raises OSError if Windows refuses."""
    default = launch_target()
    program, arguments = program or default[0], default[1] if arguments is None else arguments
    folder, icon = folder or default[2], icon or default[3]
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    with _com(), _holding(_new_link()) as pieces:
        shell = pieces[0]
        shell.put_text(SET_PATH, program)
        shell.put_text(SET_ARGUMENTS, arguments)
        shell.put_text(SET_WORKING_DIRECTORY, folder)
        shell.put_text(SET_DESCRIPTION, description)
        if Path(icon).exists():
            shell.call(SET_ICON_LOCATION, (c_wchar_p, c_int), str(icon), 0)
        if app_id:
            store = shell.query(IID_PROPERTY_STORE)
            pieces.append(store)
            value = _string_variant(app_id)
            try:
                store.call(SET_VALUE, (POINTER(PropertyKey), POINTER(PropVariant)), byref(_app_id_key()), byref(value))
                store.call(COMMIT, ())
            finally:
                _dlls()[0].PropVariantClear(byref(value))
        saver = shell.query(IID_PERSIST_FILE)
        pieces.append(saver)
        saver.call(SAVE, (c_wchar_p, wintypes.BOOL), str(link), True)
    return link


def read(link):
    """What a shortcut file holds, as a dict: program, arguments, folder, icon, description and app_id."""
    with _com(), _holding(_new_link()) as pieces:
        shell = pieces[0]
        loader = shell.query(IID_PERSIST_FILE)
        pieces.append(loader)
        loader.call(LOAD, (c_wchar_p, wintypes.DWORD), str(link), 0)
        found = {'program': shell.get_text(GET_PATH), 'arguments': shell.get_text(GET_ARGUMENTS),
                 'folder': shell.get_text(GET_WORKING_DIRECTORY), 'icon': shell.get_text(GET_ICON_LOCATION),
                 'description': shell.get_text(GET_DESCRIPTION), 'app_id': ''}
        store = shell.query(IID_PROPERTY_STORE)
        pieces.append(store)
        value = PropVariant()
        store.call(GET_VALUE, (POINTER(PropertyKey), POINTER(PropVariant)), byref(_app_id_key()), byref(value))
        try:
            if value.vt == VT_LPWSTR and value.value:
                found['app_id'] = ctypes.wstring_at(value.value)
        finally:
            _dlls()[0].PropVariantClear(byref(value))
    return found
