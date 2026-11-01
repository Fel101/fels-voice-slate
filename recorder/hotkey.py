"""A key that works while another program is in front.

A Qt shortcut only fires while this program's own window is the active one. Windows' RegisterHotKey asks the system to
tell this program whenever one particular key is pressed, wherever the focus is. That is not a keyboard hook: the
program is told about that one key and sees nothing else that is typed. While the key is registered no other program
gets it, so it is off unless the setting `global_hotkey` turns it on, and a key that another program already holds is
reported, not taken.
"""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000                   # holding the key down gives one press, not a stream of them
VK_F9 = 0x78
VK_F1 = 0x70
MODIFIERS = {'alt': 0x1, 'ctrl': 0x2, 'shift': 0x4}
KEY_ID = 0x5653                         # names this registration to Windows; any number from 0 to 0xBFFF will do
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
MESSAGE_TYPES = (b'windows_generic_MSG', b'windows_dispatcher_MSG')


def parse_key(text):
    """(virtual key, modifiers) of a key written as Qt writes it, such as F9 or Ctrl+Shift+R, or None when it cannot be the
    record key: a function key, or a letter or digit held with Ctrl or Alt, so that it can never be typed by accident."""
    *held, key = [part.strip() for part in str(text).split('+')] if text else ['']
    modifiers = 0
    for name in held:
        if name.lower() not in MODIFIERS:
            return None
        modifiers |= MODIFIERS[name.lower()]
    key = key.upper()
    if key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return VK_F1 + int(key[1:]) - 1, modifiers
    if len(key) == 1 and key.isascii() and key.isalnum() and modifiers & (MODIFIERS['ctrl'] | MODIFIERS['alt']):
        return ord(key), modifiers
    return None


class Win32:
    """RegisterHotKey and UnregisterHotKey for the calling thread, which must be the one that runs Qt's event loop."""

    def __init__(self):
        self.user32 = None
        if sys.platform == 'win32':
            self.user32 = ctypes.WinDLL('user32', use_last_error=True)
            self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.user32.RegisterHotKey.restype = wintypes.BOOL
            self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, key_id, modifiers, vk):
        """0 when the key is registered, else the Windows error code (-1 where there is no Windows)."""
        if self.user32 is None:
            return -1
        if self.user32.RegisterHotKey(None, key_id, modifiers, vk):
            return 0
        return ctypes.get_last_error() or -1

    def unregister(self, key_id):
        if self.user32 is not None:
            self.user32.UnregisterHotKey(None, key_id)


class _Filter(QAbstractNativeEventFilter):
    """Hears Windows' own messages as Qt's event loop takes them, and passes on the one for this key."""

    def __init__(self, key_id, handler):
        super().__init__()
        self.key_id = key_id
        self.handler = handler

    def nativeEventFilter(self, event_type, message):
        # `message` is a pointer object that tests as false (it has a length of 0), so it is compared with None, not tested
        address = int(message) if message is not None else 0
        if address and bytes(event_type) in MESSAGE_TYPES:
            msg = wintypes.MSG.from_address(address)
            if msg.message == WM_HOTKEY and msg.wParam == self.key_id:
                self.handler()
                return True, 0
        return False, 0


class GlobalKey(QObject):
    """One key that emits `pressed` wherever it is pressed. Nothing is registered until `set_enabled(True)`."""

    pressed = Signal()

    def __init__(self, name='F9', api=None, parent=None):
        super().__init__(parent)
        self.name = 'F9'
        self.vk, self.modifiers = VK_F9, MOD_NOREPEAT
        self.set_key(name)
        self.api = api or Win32()
        self.active = False             # True while Windows is telling us about the key
        self.problem = ''               # why it is not active, when it was asked for
        self._filter = None

    def set_key(self, name):
        """Use another key. Returns False, with nothing changed, for one that cannot be a record key. Give the key back
        first: this only says which key the next `set_enabled(True)` asks for."""
        parsed = parse_key(name)
        if parsed is None:
            return False
        self.name, self.vk, self.modifiers = name, parsed[0], parsed[1] | MOD_NOREPEAT
        return True

    def set_enabled(self, enabled):
        """Register the key, or give it back. Returns True when the key is registered afterwards."""
        if not enabled:
            self._release()
            self.problem = ''
            return False
        if self.active:
            return True
        app = QCoreApplication.instance()
        if app is None:
            self.problem = 'the program is not running yet'
            return False
        error = self.api.register(KEY_ID, self.modifiers, self.vk)
        if error:
            self.problem = (f'another program already uses {self.name}' if error == ERROR_HOTKEY_ALREADY_REGISTERED
                            else f'Windows would not allow it (error {error})')
            return False
        self._filter = _Filter(KEY_ID, self.pressed.emit)
        app.installNativeEventFilter(self._filter)
        self.active, self.problem = True, ''
        return True

    def _release(self):
        if not self.active:
            return
        app = QCoreApplication.instance()
        if app is not None and self._filter is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        self.api.unregister(KEY_ID)
        self.active = False
