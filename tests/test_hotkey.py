"""F9 in other programs: asking Windows for the key, hearing it, giving it back, and what the window does with it.

Windows' RegisterHotKey is replaced by a stand-in everywhere but one test, so no test takes F9 from the machine it runs
on. The messages are real: they are posted to this thread and taken through Qt's event loop, which is the path a real key
press follows.
"""
import ctypes
import sys
from ctypes import wintypes

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

import ui_helpers  # noqa: E402
from recorder import hotkey, settings  # noqa: E402
from recorder.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='the hotkey is a Windows feature')

WM_USER = 0x0400
VK_F24 = 0x87
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x1, 0x2, 0x4


class FakeWindows:
    """Stands in for RegisterHotKey: remembers what was asked, and can refuse with a Windows error code."""

    def __init__(self, error=0):
        self.error = error
        self.registered = []
        self.released = 0

    def register(self, key_id, modifiers, vk):
        if self.error:
            return self.error
        self.registered.append((key_id, modifiers, vk))
        return 0

    def unregister(self, key_id):
        self.released += 1


def post(message, wparam=0, lparam=0):
    """Post a message to this thread's queue, as the system does for a hotkey."""
    user32 = ctypes.WinDLL('user32')
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    assert user32.PostThreadMessageW(ctypes.windll.kernel32.GetCurrentThreadId(), message, wparam, lparam)


@pytest.fixture
def windows():
    return FakeWindows()


@pytest.fixture
def key(qapp, windows):
    made = hotkey.GlobalKey(api=windows)
    yield made
    made.set_enabled(False)


# ---- the key ------------------------------------------------------------------------------------------------------------

def test_f9_is_registered_once_and_a_held_key_does_not_repeat(key, windows):
    assert key.set_enabled(True) is True
    assert windows.registered == [(hotkey.KEY_ID, hotkey.MOD_NOREPEAT, hotkey.VK_F9)]
    assert key.active and key.problem == ''
    assert key.set_enabled(True) is True and len(windows.registered) == 1          # asking again changes nothing


def test_a_key_another_program_holds_is_reported_not_taken(key, windows):
    windows.error = hotkey.ERROR_HOTKEY_ALREADY_REGISTERED
    assert key.set_enabled(True) is False
    assert not key.active and key.problem == 'another program already uses F9'


def test_any_other_refusal_names_the_windows_error(key, windows):
    windows.error = 5
    assert key.set_enabled(True) is False
    assert 'error 5' in key.problem


def test_turning_it_off_gives_the_key_back_and_forgets_the_problem(key, windows):
    key.set_enabled(True)
    assert key.set_enabled(False) is False
    assert not key.active and windows.released == 1
    key.set_enabled(False)
    assert windows.released == 1                                                    # nothing more to give back
    windows.error = hotkey.ERROR_HOTKEY_ALREADY_REGISTERED
    key.set_enabled(True)
    assert key.problem
    key.set_enabled(False)
    assert key.problem == ''


def test_a_press_is_heard_through_the_event_loop(key, qapp):
    heard = []
    key.pressed.connect(lambda: heard.append(1))
    key.set_enabled(True)
    post(hotkey.WM_HOTKEY, hotkey.KEY_ID, hotkey.VK_F9 << 16)
    ui_helpers.pump(qapp)
    assert heard == [1]


def test_other_keys_and_other_messages_are_ignored(key, qapp):
    heard = []
    key.pressed.connect(lambda: heard.append(1))
    key.set_enabled(True)
    post(hotkey.WM_HOTKEY, hotkey.KEY_ID + 1, hotkey.VK_F9 << 16)                  # another key of this thread's
    post(WM_USER, hotkey.KEY_ID)                                                    # not a hotkey at all
    ui_helpers.pump(qapp)
    assert heard == []


def test_nothing_is_heard_once_the_key_is_given_back(key, qapp):
    heard = []
    key.pressed.connect(lambda: heard.append(1))
    key.set_enabled(True)
    key.set_enabled(False)
    post(hotkey.WM_HOTKEY, hotkey.KEY_ID, hotkey.VK_F9 << 16)
    ui_helpers.pump(qapp)
    assert heard == []


def test_windows_itself_takes_a_key_once_and_gives_it_back():
    api = hotkey.Win32()
    modifiers = MOD_CONTROL | MOD_ALT | MOD_SHIFT | hotkey.MOD_NOREPEAT            # a combination no one uses
    first = api.register(hotkey.KEY_ID, modifiers, VK_F24)
    if first:                                                                       # another program has it, or no desktop
        pytest.skip(f'Windows would not register Ctrl+Alt+Shift+F24 here (error {first})')
    try:
        assert api.register(hotkey.KEY_ID, modifiers, VK_F24) == hotkey.ERROR_HOTKEY_ALREADY_REGISTERED
    finally:
        api.unregister(hotkey.KEY_ID)
    assert api.register(hotkey.KEY_ID, modifiers, VK_F24) == 0                      # it was given back
    api.unregister(hotkey.KEY_ID)


# ---- the window ---------------------------------------------------------------------------------------------------------

class FakeKey(QObject):
    """Stands in for the real key: a test presses it, and says whether Windows would let the program have it."""

    pressed = Signal()

    def __init__(self, allowed=True):
        super().__init__()
        self.name, self.allowed = 'F9', allowed
        self.active, self.problem = False, ''
        self.asked = []

    def set_enabled(self, enabled):
        self.asked.append(bool(enabled))
        self.active = bool(enabled and self.allowed)
        self.problem = 'another program already uses F9' if enabled and not self.allowed else ''
        return self.active


def open_window(qapp, session, dialogs, fake):
    window = MainWindow(session=session, dialogs=dialogs, hotkey=fake)
    window.timer.stop()
    window.show()
    ui_helpers.pump(qapp)
    return window


@pytest.fixture
def keyed(qapp, session, dialogs):
    """A window over a stand-in for the key, and the stand-in."""
    fake = FakeKey()
    window = open_window(qapp, session, dialogs, fake)
    yield window, fake
    window.timer.stop()
    window.close()
    ui_helpers.pump(qapp)


def test_the_window_asks_for_the_key_and_says_so_in_the_tooltips(keyed):
    window, fake = keyed
    assert fake.asked == [True] and fake.active
    for tip in (window.actions.record.toolTip(), window.topbar.record.toolTip()):
        assert 'also while another program is in front' in tip


def test_the_key_pressed_elsewhere_starts_and_stops_a_recording(keyed, session, open_fake, qapp):
    window, fake = keyed
    fake.pressed.emit()
    ui_helpers.pump(qapp)
    assert session.capturing.recording and not session.capturing.finishing
    fake.pressed.emit()
    ui_helpers.pump(qapp)
    assert session.capturing.finishing                                              # the stop tail has begun


def test_a_dialog_that_is_open_is_not_interrupted_by_a_recording_starting(keyed, session, open_fake, qapp):
    window, fake = keyed
    dialog = QDialog(window)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    ui_helpers.pump(qapp)
    assert QApplication.activeModalWidget() is dialog
    fake.pressed.emit()
    ui_helpers.pump(qapp)
    assert not session.capturing.recording
    dialog.close()
    ui_helpers.pump(qapp)
    fake.pressed.emit()
    ui_helpers.pump(qapp)
    assert session.capturing.recording
    dialog.show()
    ui_helpers.pump(qapp)
    fake.pressed.emit()                                                             # but a recording can always be stopped
    ui_helpers.pump(qapp)
    assert session.capturing.finishing
    dialog.close()


def test_a_key_that_another_program_holds_is_explained(qapp, session, dialogs):
    fake = FakeKey(allowed=False)
    window = open_window(qapp, session, dialogs, fake)
    try:
        assert 'F9 only works while this window is in front' in window.status_text.full_text
        assert 'another program already uses F9' in window.status_text.full_text
        assert 'also while another program' not in window.actions.record.toolTip()
    finally:
        window.close()
        ui_helpers.pump(qapp)


def test_the_setting_gives_the_key_back_and_takes_it_again(keyed, session, dialogs):
    window, fake = keyed
    dialogs.settings_answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None,
                               'global_hotkey': False}
    window.open_settings()
    assert fake.asked[-1] is False and not fake.active
    assert settings.load(session.settings_file)['global_hotkey'] is False
    assert 'also while another program' not in window.actions.record.toolTip()
    dialogs.settings_answer = dict(dialogs.settings_answer, global_hotkey=True)
    window.open_settings()
    assert fake.asked[-1] is True and fake.active
    assert 'global_hotkey' not in settings.load(session.settings_file)              # on is the default: nothing is stored


def test_the_dialog_is_shown_the_current_choice(keyed, dialogs, session):
    window, _ = keyed
    window.open_settings()
    assert settings.global_hotkey(dialogs.settings_seen) is True
    settings.update({'global_hotkey': False}, session.settings_file)
    window.open_settings()
    assert settings.global_hotkey(dialogs.settings_seen) is False


def test_closing_the_window_gives_the_key_back(qapp, session, dialogs):
    fake = FakeKey()
    window = open_window(qapp, session, dialogs, fake)
    window.close()
    ui_helpers.pump(qapp)
    assert fake.asked[-1] is False and not fake.active


def test_a_window_without_the_key_works_as_before(window, session, open_fake):
    assert window.hotkey is None
    window.actions.record.trigger()
    assert session.capturing.recording
