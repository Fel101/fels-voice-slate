"""Choosing the hardware: one input of a device, the device to play through, the record key, and the sign while recording.

The sound library is replaced by a stand-in here. None of this has been tried on a real two-input interface: see the
changelog."""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402

import ui_helpers  # noqa: E402
from helpers import FakeCapture, speech  # noqa: E402
from recorder import audio_io, capture as cap  # noqa: E402
from recorder import hotkey, settings  # noqa: E402
from recorder.ui import dialogs as dialog_mod  # noqa: E402
from recorder.ui.actions import SPEC, key_problem  # noqa: E402


class FakeStream:
    def __init__(self, **options):
        self.options = options
        self.callback = options.get('callback')
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass


def sound_library(monkeypatch, inputs=2, outputs=('Speakers', 'Headphones')):
    """A stand-in for sounddevice with one input device and some outputs. Returns the streams it was asked for."""
    streams = []
    devices = [{'name': 'Interface', 'hostapi': 0, 'max_input_channels': inputs, 'max_output_channels': 0,
                'default_samplerate': 48000.0}]
    devices += [{'name': name, 'hostapi': 0, 'max_input_channels': 0, 'max_output_channels': 2, 'default_samplerate': 48000.0}
                for name in outputs]

    def stream(**options):
        streams.append(FakeStream(**options))
        return streams[-1]

    def query_devices(device=None, kind=None):
        return devices if device is None else devices[device]

    fake = SimpleNamespace(query_devices=query_devices, InputStream=stream, OutputStream=stream,
                           query_hostapis=lambda index=None: [{'name': 'WASAPI'}] if index is None else {'name': 'WASAPI'})
    monkeypatch.setitem(__import__('sys').modules, 'sounddevice', fake)
    return streams


def no_flags():
    return SimpleNamespace(input_overflow=False, input_underflow=False)


# ---- one input of a device ------------------------------------------------------------------------------------------------

def test_with_no_choice_a_two_input_device_records_both(monkeypatch):
    streams = sound_library(monkeypatch)
    capture = cap.open_capture('mic', 0)
    capture.start()
    assert capture.channels == 2 and streams[0].options['channels'] == 2


def test_one_input_is_recorded_as_a_mono_take_at_its_full_level(monkeypatch):
    """Mixed, a voice on input 1 of two comes out 6 dB low."""
    streams = sound_library(monkeypatch)
    capture = cap.open_capture('mic', 0, channel=0)
    capture.start()
    voice = 0.5 * np.ones(480, np.float32)
    streams[0].options['callback'](np.column_stack([voice, 0 * voice]), 480, None, no_flags())
    took = capture.stop()
    assert capture.channels == 1 and took.ndim == 1 and took.max() == pytest.approx(0.5)


def test_the_second_input_can_be_chosen(monkeypatch):
    streams = sound_library(monkeypatch)
    capture = cap.open_capture('mic', 0, channel=1)
    capture.start()
    voice = 0.5 * np.ones(480, np.float32)
    streams[0].options['callback'](np.column_stack([0 * voice, voice]), 480, None, no_flags())
    assert capture.stop().max() == pytest.approx(0.5)


def test_the_device_is_opened_with_all_its_inputs_to_read_one(monkeypatch):
    streams = sound_library(monkeypatch, inputs=8)
    capture = cap.open_capture('mic', 0, channel=5)
    capture.start()
    assert streams[0].options['channels'] == 8 and capture.channels == 1


def test_an_input_the_device_does_not_have_is_ignored(monkeypatch):
    sound_library(monkeypatch)
    assert cap.open_capture('mic', 0, channel=7).backend.channel is None


def only_input_of(left, right, seconds=1.2):
    backend = SimpleNamespace(kind='mic', rate=1000, channels=2, open=lambda on_block: None, close=lambda: None)
    capture = cap.Capture(backend)
    capture.arm()
    frames = int(1000 * seconds)
    capture._on_block(np.column_stack([np.full(frames, left, np.float32), np.full(frames, right, np.float32)]))
    return capture.only_input()


def test_a_lone_input_is_noticed():
    assert only_input_of(0.3, 0.0) == 1 and only_input_of(0.0, 0.3) == 2


def test_two_live_inputs_are_not_a_lone_input():
    assert only_input_of(0.3, 0.2) is None


def test_silence_on_both_says_nothing():
    assert only_input_of(0.0, 0.0) is None


def test_it_takes_a_second_to_say_so():
    assert only_input_of(0.3, 0.0, seconds=0.2) is None


def test_the_session_offers_a_choice_only_for_a_device_with_several_inputs(make_session, monkeypatch):
    session = make_session()
    assert session.capturing.inputs == 1
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session.capturing.refresh_devices()
    assert session.capturing.inputs == 2


def test_system_audio_offers_no_choice_of_inputs(make_session, monkeypatch):
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session = make_session()
    session.capturing.set_source('system')
    assert session.capturing.inputs == 0


def test_the_chosen_input_reaches_the_capture_and_is_remembered_for_that_device(make_session, monkeypatch, open_fake):
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session = make_session()
    session.capturing.select_channel(1)
    session.capturing.start()
    assert open_fake[0].channel == 1
    session.capturing.close_capture()
    assert make_session().capturing.channel == 1


def test_going_back_to_both_inputs_forgets_the_choice(make_session, monkeypatch):
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session = make_session()
    session.capturing.select_channel(1)
    session.capturing.select_channel(None)
    assert make_session().capturing.channel is None
    assert 'input_channels' not in settings.load(session.settings_file)


def test_a_saved_input_the_device_no_longer_has_is_dropped(make_session, monkeypatch):
    session = make_session()
    settings.update({'input_channels': {session.capturing.device_label(): 5}}, session.settings_file)
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session.capturing.refresh_devices()
    assert session.capturing.channel is None


def test_a_device_that_will_not_say_gives_no_choice(make_session, monkeypatch):
    def broken(device):
        raise OSError('it has gone')

    monkeypatch.setattr(cap, 'input_channels', broken)
    assert make_session().capturing.inputs == 0


def test_the_status_line_says_when_only_one_input_has_sound(session):
    fake = FakeCapture(speech(3.0))
    fake.lone_input = 1
    session.capturing.capture = fake
    session.tick()
    assert 'Only input 1 has sound' in session.status_text and '6 dB' in session.status_text


def test_the_hint_is_given_once(session):
    fake = FakeCapture(speech(3.0))
    fake.lone_input = 2
    session.capturing.capture = fake
    session.tick()
    session.bus.status('Something else.')
    session.tick()
    assert session.status_text == 'Something else.'


def test_no_hint_once_an_input_has_been_chosen(session):
    fake = FakeCapture(speech(3.0))
    fake.lone_input = 1
    session.capturing.capture = fake
    session.capturing.channel = 0
    session.tick()
    assert 'Only input' not in session.status_text


# ---- the device chosen is remembered -------------------------------------------------------------------------------------------

def test_the_device_chosen_comes_back_after_a_restart(make_session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'First'), (2, 'Second')])
    session = make_session()
    session.capturing.select_device(2)
    assert make_session().capturing.device == 2


def test_the_choice_follows_the_name_when_the_numbers_change(make_session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'First'), (2, 'Second')])
    session = make_session()
    session.capturing.select_device(2)
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(7, 'Second'), (1, 'First')])
    assert make_session().capturing.device == 7


def test_a_device_that_has_gone_falls_back_to_the_default(make_session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'First'), (2, 'Second')])
    session = make_session()
    session.capturing.select_device(2)
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'First')])
    assert make_session().capturing.device == 1


# ---- the device to play through --------------------------------------------------------------------------------------------------

def test_the_outputs_are_listed_with_their_interface(monkeypatch):
    sound_library(monkeypatch)
    assert audio_io.list_outputs() == ['Speakers  [WASAPI]', 'Headphones  [WASAPI]']


def test_an_output_is_found_by_its_name(monkeypatch):
    sound_library(monkeypatch)
    assert audio_io.find_output('Headphones  [WASAPI]') == 2


@pytest.mark.parametrize('name', [None, '', 'A device that has gone  [WASAPI]'])
def test_no_name_or_a_lost_device_is_the_default(monkeypatch, name):
    sound_library(monkeypatch)
    assert audio_io.find_output(name) is None


def test_playing_uses_the_device_chosen(monkeypatch):
    streams = sound_library(monkeypatch)
    player = audio_io.Player()
    player.device = 2
    player.load(np.zeros(4800, np.float32), 48000)
    player.play()
    assert streams[0].options['device'] == 2
    player.stop()


def test_the_default_needs_no_choice(monkeypatch):
    streams = sound_library(monkeypatch)
    player = audio_io.Player()
    player.load(np.zeros(4800, np.float32), 48000)
    player.play()
    assert streams[0].options['device'] is None
    player.stop()


def test_the_session_starts_on_the_saved_output(make_session, monkeypatch, player):
    sound_library(monkeypatch)
    session = make_session()
    settings.update({'output_device': 'Headphones  [WASAPI]'}, session.settings_file)
    make_session()
    assert player.device == 2


def test_settings_offers_the_outputs_and_keeps_a_lost_one_visible(qapp, monkeypatch):
    dialog = dialog_mod.SettingsDialog({'outputs': ['Speakers  [WASAPI]'], 'output_device': 'Old headset  [WASAPI]'})
    names = [dialog.output.itemText(i) for i in range(dialog.output.count())]
    assert names == ["Windows' default", 'Speakers  [WASAPI]', 'Old headset  [WASAPI] (not connected now)']
    assert dialog.values()['output_device'] == 'Old headset  [WASAPI]'
    dialog.output.setCurrentIndex(0)
    assert dialog.values()['output_device'] is None


def test_the_window_applies_the_output_chosen(window, session, dialogs, player, monkeypatch):
    sound_library(monkeypatch)
    dialogs.settings_answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None,
                               'output_device': 'Headphones  [WASAPI]'}
    window.open_settings()
    assert player.device == 2 and dialogs.settings_seen['outputs'] == ['Speakers  [WASAPI]', 'Headphones  [WASAPI]']
    assert settings.load(session.settings_file)['output_device'] == 'Headphones  [WASAPI]'


# ---- the record key ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('F9', (0x78, 0)), ('F1', (0x70, 0)), ('F24', (0x87, 0)), ('Ctrl+F9', (0x78, 2)), ('Shift+F2', (0x71, 4)),
    ('Ctrl+Shift+R', (ord('R'), 6)), ('Alt+7', (ord('7'), 1)), ('ctrl+r', (ord('R'), 2))])
def test_keys_that_can_be_the_record_key(text, expected):
    assert hotkey.parse_key(text) == expected


@pytest.mark.parametrize('text', ['', 'R', '7', 'Shift+R', 'F25', 'Space', 'Ctrl+Space', 'Meta+F9', 'Ctrl+', None, 'F0'])
def test_keys_that_cannot_be_it_because_they_would_be_typed_by_accident(text):
    assert hotkey.parse_key(text) is None


def test_no_other_command_can_have_the_key():
    assert 'Undo' in key_problem('Ctrl+Z') and 'Settings' not in key_problem('Ctrl+Shift+R')
    assert key_problem('Ctrl+Shift+R') == '' and key_problem('F9') == ''


def test_a_key_of_another_command_is_refused_in_settings(qapp):
    dialog = dialog_mod.SettingsDialog({})
    dialog.record_key.setKeySequence(QKeySequence('Ctrl+Z'))
    assert 'Undo' in dialog.key_note.text() and not dialog.ok.isEnabled()
    dialog.record_key.setKeySequence(QKeySequence('F8'))
    assert dialog.ok.isEnabled() and dialog.values()['record_key'] == 'F8'


def test_a_key_that_could_be_typed_is_refused_in_settings(qapp):
    dialog = dialog_mod.SettingsDialog({})
    dialog.record_key.setKeySequence(QKeySequence('R'))
    assert 'function key' in dialog.key_note.text() and not dialog.ok.isEnabled()


def test_the_default_key_is_not_stored(qapp):
    assert dialog_mod.SettingsDialog({}).values()['record_key'] is None


def test_a_new_key_takes_over_the_command_and_every_word_that_names_it(window, session, dialogs):
    dialogs.settings_answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None,
                               'record_key': 'F8'}
    window.open_settings()
    assert window.actions.record.shortcut() == QKeySequence('F8')
    assert 'F8' in window.topbar.record.text() and 'F8' in window.editor_panel.empty.record.text()
    assert settings.load(session.settings_file)['record_key'] == 'F8'


def test_the_list_of_shortcuts_shows_the_key_in_use(window, dialogs, qapp):
    from PySide6.QtWidgets import QLabel
    window.actions.set_record_key('F7')
    text = ' '.join(label.text() for label in dialog_mod.ShortcutsDialog(window).findChildren(QLabel))
    assert 'F7' in text and 'F9' not in text


def test_the_status_line_names_the_key(session, open_fake):
    settings.update({'record_key': 'Ctrl+Shift+R'}, session.settings_file)
    session.capturing.start()
    assert 'Press Ctrl+Shift+R or Stop' in session.status_text


def test_the_saved_key_is_in_place_when_the_window_opens(qapp, session, dialogs):
    from recorder.ui.main_window import MainWindow
    settings.update({'record_key': 'F7'}, session.settings_file)
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    assert window.actions.record.shortcut() == QKeySequence('F7')
    window.close()


def test_a_saved_key_that_is_not_text_is_the_default():
    assert settings.record_key({'record_key': 7}) == 'F9' and settings.record_key({'record_key': ' '}) == 'F9'
    assert settings.record_key({}) == 'F9'


def test_the_command_table_still_says_f9_for_the_shortcuts_list():
    assert SPEC['record'][2] == ['F9']


# ---- in other programs -------------------------------------------------------------------------------------------------------------

class FakeKey(QObject):
    pressed = Signal()

    def __init__(self):
        super().__init__()
        self.name, self.active, self.problem = 'F9', False, ''

    def set_enabled(self, enabled):
        self.active = bool(enabled)
        return self.active

    def set_key(self, name):
        self.name = name
        return True


@pytest.mark.parametrize('saved, on', [({}, False), ({'global_hotkey': False}, False), ({'global_hotkey': True}, True),
                                       ({'global_hotkey': 'yes'}, False)])
def test_the_key_works_in_other_programs_only_when_someone_turned_that_on(saved, on):
    assert settings.global_hotkey(saved) is on


def test_a_new_install_takes_no_key_from_other_programs(qapp, session, dialogs):
    from recorder.ui.main_window import MainWindow
    key = FakeKey()
    window = MainWindow(session=session, dialogs=dialogs, hotkey=key)
    window.timer.stop()
    assert not key.active and 'also while another program' not in window.topbar.record.toolTip()
    window.close()
    ui_helpers.pump(qapp)


def test_a_new_key_is_asked_for_in_place_of_the_old_one(qapp, session, dialogs):
    from recorder.ui.main_window import MainWindow
    settings.update({'global_hotkey': True}, session.settings_file)
    key = FakeKey()
    window = MainWindow(session=session, dialogs=dialogs, hotkey=key)
    window.timer.stop()
    dialogs.settings_answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None,
                               'record_key': 'F7', 'global_hotkey': True}
    window.open_settings()
    assert key.name == 'F7' and key.active
    window.close()
    ui_helpers.pump(qapp)


def test_the_real_key_can_be_changed_while_it_is_not_held():
    made = hotkey.GlobalKey('Ctrl+Shift+R', api=SimpleNamespace(register=lambda *a: 0, unregister=lambda *a: None))
    assert made.name == 'Ctrl+Shift+R' and made.vk == ord('R') and made.modifiers & hotkey.MOD_NOREPEAT
    assert made.set_key('nonsense') is False and made.name == 'Ctrl+Shift+R'


# ---- the sign on top ---------------------------------------------------------------------------------------------------------------

def test_a_recording_with_the_window_out_of_sight_shows_the_sign(window, session, open_fake, monkeypatch):
    monkeypatch.setattr(window, 'isActiveWindow', lambda: False)
    session.capturing.start()
    window.flush()
    window._tick()
    assert window.badge.isVisible() and 'Recording' in window.badge.text.text() and 'F9 stops' in window.badge.text.text()


def test_no_sign_while_the_window_is_in_front(window, session, open_fake, monkeypatch):
    monkeypatch.setattr(window, 'isActiveWindow', lambda: True)
    session.capturing.start()
    window._tick()
    assert not window.badge.isVisible()


def test_the_sign_goes_when_the_recording_ends(window, session, open_fake, monkeypatch, clock):
    monkeypatch.setattr(window, 'isActiveWindow', lambda: False)
    session.capturing.start()
    window._tick()
    session.capturing.stop()
    clock.advance(1.0)
    window._tick()
    window._tick()
    assert not window.badge.isVisible()


def test_a_click_on_the_sign_stops_the_take(window, session, open_fake, monkeypatch):
    monkeypatch.setattr(window, 'isActiveWindow', lambda: False)
    session.capturing.start()
    window._tick()
    window.badge.stopRequested.emit()
    assert session.capturing.finishing


def test_the_sign_never_takes_the_keyboard(window):
    from PySide6.QtCore import Qt
    assert window.badge.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.badge.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


# ---- the top bar ------------------------------------------------------------------------------------------------------------------

def test_the_input_picker_shows_only_for_a_device_with_several_inputs(window, session, monkeypatch):
    bar = window.topbar
    window._refresh({'devices'})
    assert bar.channel.isHidden()
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session.capturing.refresh_devices()
    window.flush()
    assert not bar.channel.isHidden()
    assert [bar.channel.itemText(i) for i in range(bar.channel.count())] == ['Mix 1+2', 'Input 1', 'Input 2']


def test_picking_an_input_reaches_the_session(window, session, monkeypatch):
    monkeypatch.setattr(cap, 'input_channels', lambda device: 2)
    session.capturing.refresh_devices()
    window.flush()
    bar = window.topbar
    bar.channel.setCurrentIndex(2)
    bar.channel.activated.emit(2)
    assert session.capturing.channel == 1


def test_the_input_picker_is_off_while_recording(window, session, open_fake):
    session.capturing.start()
    window.flush()
    assert not window.topbar.channel.isEnabled()
