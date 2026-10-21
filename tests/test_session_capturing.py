"""Recording: devices, start and stop with a tail, and what happens when a device fails or a take reaches 40 s."""
import json
from types import SimpleNamespace

import pytest

from helpers import RATE, FakeCapture, speech
from recorder import capture as cap
from recorder.capture import Capture


# ---- starting and stopping ----------------------------------------------------------------------------------------------

def test_start_records_and_stop_ends_the_take_after_the_tail(session, open_fake, clock):
    assert session.capturing.start() is True
    assert session.capturing.state == 'recording' and session.capturing.recording
    assert open_fake[0].opened_with == ('mic', 1, 0.5)          # the microphone keeps a half-second pre-roll
    session.capturing.stop()
    assert session.capturing.state == 'stopping' and session.capturing.finishing
    clock.advance(0.2)
    session.tick()
    assert session.capturing.state == 'stopping' and not open_fake[0].stopped
    clock.advance(0.4)
    session.tick()
    assert open_fake[0].stopped and open_fake[0].closed
    assert session.capturing.state == 'stopped' and session.capturing.capture is None
    assert len(session.workspace.takes) == 1 and 'Take saved' in session.status_text


def test_toggle_starts_then_stops(session, open_fake):
    session.capturing.toggle()
    assert session.capturing.recording
    session.capturing.toggle()
    assert session.capturing.finishing


def test_the_first_recording_creates_the_speaker_my_voice(make_session, open_fake):
    s = make_session()
    assert s.workspace.speaker is None
    s.capturing.start()
    assert s.workspace.speaker == 'my-voice'


def test_a_device_that_cannot_be_opened_is_reported_and_leaves_things_idle(session, monkeypatch):
    def broken(source, device, pre_roll):
        raise OSError('Invalid device')

    monkeypatch.setattr(cap, 'open_capture', broken)
    assert session.capturing.start() is False
    assert session.capturing.state == 'idle' and session.capturing.capture is None
    assert session.bus.notices[-1].level == 'error' and 'Invalid device' in session.bus.notices[-1].text


def test_starting_with_no_device_asks_for_one(session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [])
    monkeypatch.setattr(cap, 'default_microphone', lambda: None)
    session.capturing.refresh_devices()
    assert session.capturing.device is None
    assert session.capturing.start() is False
    assert 'Choose a device' in session.bus.notices[-1].text


def test_arming_opens_the_device_and_disarming_releases_it(session, open_fake):
    session.capturing.toggle_arm()
    assert session.capturing.state == 'armed' and session.capturing.armed and open_fake[0].armed
    session.capturing.toggle_arm()
    assert session.capturing.state == 'idle' and open_fake[0].closed and session.capturing.capture is None


def test_a_system_audio_capture_has_no_pre_roll(session, open_fake):
    session.capturing.set_source('system')
    assert session.capturing.device == 9
    session.capturing.start()
    assert open_fake[0].opened_with == ('system', 9, 0.0)


def test_the_meter_level_is_read_on_every_tick(session, open_fake):
    session.capturing.start()
    session.tick()
    assert session.capturing.level == (-20.0, -30.0)


# ---- the stop tail ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value, expected', [('', 0.5), ('abc', 0.5), ('0.3', 0.3), ('2', 1.0), ('-1', 0.0), (None, 0.5)])
def test_the_tail_is_read_safely(session, value, expected):
    session.capturing.set_tail(value)
    assert session.capturing.tail_seconds == pytest.approx(expected)


def test_the_tail_is_remembered(session, tmp_path):
    session.capturing.set_tail(0.25)
    assert json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))['tail_seconds'] == 0.25


def test_stop_with_a_bad_tail_still_stops_and_the_state_resets(session, clock):
    fake = FakeCapture(speech(2.0))
    session.capturing.capture = fake
    session.capturing.set_tail('abc')
    session.capturing.stop()
    assert session.capturing.finishing is True
    clock.advance(0.6)
    session.tick()
    assert session.capturing.finishing is False
    assert fake.stopped and session.capturing.capture is None
    assert len(session.workspace.takes) == 1


def test_a_stop_with_no_tail_ends_on_the_next_tick(session, clock):
    fake = FakeCapture(speech(2.0))
    session.capturing.capture = fake
    session.capturing.set_tail(0)
    session.capturing.stop()
    session.tick()
    assert fake.stopped


def test_stop_does_nothing_when_not_recording(session):
    session.capturing.stop()
    assert session.capturing.state == 'idle' and not session.capturing.finishing


def test_a_leftover_stop_cannot_finish_a_new_take_or_reset_its_stop(session, clock):
    first = FakeCapture(speech(2.0))
    session.capturing.capture = first
    session.capturing.stop()
    session.capturing.finish_take()
    assert not session.capturing.finishing and len(session.workspace.takes) == 1
    second = FakeCapture(speech(2.0))
    session.capturing.capture = second
    session.capturing.stop()
    clock.advance(0.2)
    session.tick()
    assert second.recording and session.capturing.finishing and len(session.workspace.takes) == 1
    clock.advance(0.4)
    session.tick()
    assert not second.recording and not session.capturing.finishing and len(session.workspace.takes) == 2


def test_a_stop_left_over_from_another_capture_is_dropped_by_the_next_tick(session, clock):
    old, new = FakeCapture(speech(1.0)), FakeCapture(speech(1.0))
    session.capturing.capture = new
    session.capturing._stop = (old, clock() - 1)                 # its deadline passed long ago
    session.capturing.finishing = True
    session.tick()
    assert new.recording and not new.stopped and not session.capturing.finishing


def test_pressing_stop_twice_makes_one_take(session, clock):
    session.capturing.capture = FakeCapture(speech(2.0))
    session.capturing.stop()
    clock.advance(0.3)
    session.capturing.stop()                                    # F9 again during the tail: the deadline is not pushed back
    clock.advance(0.3)
    session.tick()
    session.tick()
    assert len(session.workspace.takes) == 1


def test_closing_during_the_stop_tail_saves_the_audio_and_cancels_the_stop(session):
    backend = SimpleNamespace(kind='mic', name='synthetic', hostapi='test', rate=RATE, channels=1,
                              open=lambda callback: None, close=lambda: None)
    capture = Capture(backend)
    capture.start()
    capture._on_block(speech(2.0).reshape(-1, 1))
    session.capturing.capture = capture
    session.capturing.stop()
    assert session.close() == []
    assert session.closed and session.workspace.takes[0].master_path.is_file()
    assert not capture.recording and not capture._blocks and session.capturing._stop is None


# ---- failures ---------------------------------------------------------------------------------------------------------

def test_a_meter_error_mid_take_still_saves_the_audio(session):
    fake = FakeCapture(speech(2.0), level_error=RuntimeError('meter fell over'))
    session.capturing.capture = fake
    session.tick()
    assert fake.stopped and fake.closed and session.capturing.capture is None
    assert len(session.workspace.takes) == 1 and session.workspace.takes[0].seconds == pytest.approx(2.0, abs=0.01)
    assert 'saved' in session.status_text and 'interrupted' in session.workspace.takes[0].flags


def test_an_error_on_an_armed_device_releases_the_device(session):
    fake = FakeCapture(level_error=RuntimeError('meter fell over'))
    fake.recording = False
    session.capturing.capture = fake
    session.tick()
    assert fake.closed and session.capturing.capture is None
    assert session.workspace.takes == [] and session.capturing.state == 'error'


def test_a_playback_error_does_not_end_a_recording(session, monkeypatch):
    fake = FakeCapture()
    session.capturing.capture = fake

    def broken():
        raise RuntimeError('playback broke')

    monkeypatch.setattr(session.transport, 'tick', broken)
    session.tick()
    assert session.capturing.capture is fake and not fake.stopped and not fake.closed
    assert 'Playback error: playback broke' in session.status_text


def test_a_device_error_as_a_take_stops_is_reported_and_the_audio_is_kept(session):
    fake = FakeCapture(speech(3.0))
    fake.close_error = OSError('device unplugged')
    session.capturing.capture = fake
    session.capturing.finish_take()
    assert len(session.workspace.takes) == 1 and session.workspace.takes[0].seconds == pytest.approx(3.0, abs=0.01)
    assert 'device reported an error' in session.status_text


def test_a_device_that_stops_sending_ends_the_take_as_interrupted(session):
    fake = FakeCapture(speech(3.0))
    fake.stall = True
    session.capturing.capture = fake
    session.tick()
    assert fake.stopped and session.capturing.capture is None
    assert 'interrupted' in session.workspace.takes[0].flags and session.capturing.state == 'interrupted'


def test_a_device_that_dies_ends_the_take_as_interrupted(session):
    fake = FakeCapture(speech(3.0), alive=False)
    session.capturing.capture = fake
    session.tick()
    assert fake.stopped and 'interrupted' in session.workspace.takes[0].flags


def test_the_40_second_limit_is_measured_by_the_clock(session):
    fake = FakeCapture(speech(3.0))
    fake.elapsed = 1.0
    fake.wall = 41.0
    session.capturing.capture = fake
    session.tick()
    assert fake.stopped and 'Stopped at 40 s' in session.status_text


def test_a_saved_take_is_reported_without_a_pending_folder(session):
    session.capturing.capture = FakeCapture(speech(8.0))
    session.capturing.finish_take()
    assert 'Take saved' in session.status_text and 'pending' not in session.status_text


def test_a_failed_capture_that_cannot_even_be_saved_still_releases_the_device(session, monkeypatch):
    fake = FakeCapture(speech(2.0), level_error=RuntimeError('meter fell over'))
    session.capturing.capture = fake
    monkeypatch.setattr(session.workspace, 'add_take', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no memory')))
    session.tick()
    assert fake.closed and session.capturing.capture is None
    assert 'could not be saved' in session.status_text


# ---- devices ----------------------------------------------------------------------------------------------------------

def test_a_chosen_device_stays_chosen(session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(5, 'Mic A'), (7, 'Mic B')])
    monkeypatch.setattr(cap, 'default_microphone', lambda: 5)
    session.capturing.refresh_devices()
    assert session.capturing.device == 5 and session.capturing.device_label() == 'Mic A'
    session.capturing.select_device(7)
    assert session.capturing.device == 7 and session.capturing.device_label() == 'Mic B'


def test_picking_a_device_releases_an_armed_one_so_the_next_start_uses_it(session, open_fake, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(5, 'Mic A'), (7, 'Mic B')])
    monkeypatch.setattr(cap, 'default_microphone', lambda: 5)
    session.capturing.refresh_devices()
    session.capturing.toggle_arm()
    session.capturing.select_device(7)
    assert open_fake[0].closed and session.capturing.state == 'idle'
    session.capturing.start()
    assert open_fake[1].opened_with[1] == 7


def test_a_missing_default_device_falls_back_to_the_first_in_the_list(session, monkeypatch):
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(5, 'Mic A'), (7, 'Mic B')])
    monkeypatch.setattr(cap, 'default_microphone', lambda: 99)
    session.capturing.refresh_devices()
    assert session.capturing.device == 5


def test_a_device_library_error_is_shown_not_raised(session, monkeypatch):
    def broken():
        raise OSError('PortAudio is unavailable')

    monkeypatch.setattr(cap, 'list_microphones', broken)
    session.capturing.refresh_devices()
    assert session.capturing.devices == [] and 'PortAudio is unavailable' in session.status_text


def test_switching_source_lists_that_sources_devices(session):
    session.capturing.set_source('system')
    assert session.capturing.devices == [(9, 'Test speakers [Loopback]')]
    session.capturing.set_source('mic')
    assert session.capturing.devices == [(1, 'Test microphone')]


# ---- what the window shows -------------------------------------------------------------------------------------------

def test_the_window_can_read_the_live_envelope_only_while_recording(session, open_fake):
    assert session.capturing.envelope() is None
    session.capturing.start()
    rows = session.capturing.envelope()
    assert rows is not None and rows.shape[1] == 3 and len(rows) > 10
    session.capturing.stop()
    session.capturing.finish_take()
    assert session.capturing.envelope() is None


def test_the_take_info_reports_the_device_once_it_is_open(session, open_fake):
    assert session.capturing.info() == {'device': 'Test microphone', 'rate': None, 'channels': None,
                                        'pre_roll': 0.5, 'tail': 0.5}
    session.capturing.toggle_arm()
    assert session.capturing.info()['rate'] == RATE and session.capturing.info()['channels'] == 1


# ---- script mode ------------------------------------------------------------------------------------------------------

def test_a_take_recorded_against_a_script_line_starts_with_that_line_as_its_transcript(session, open_fake, clock):
    session.script.load_text('The harbour lights came on at dusk.\nA second line to read.')
    session.capturing.start()
    session.capturing.stop()
    clock.advance(1)
    session.tick()
    take = session.current
    assert take.meta['transcript'] == 'The harbour lights came on at dusk.'
    assert take.meta['transcript_status'] == 'read_from_script'


def test_the_script_does_not_move_on_until_the_take_is_kept(session, open_fake, clock):
    session.script.load_text('First line to read aloud.\nSecond line to read aloud.')
    session.capturing.start()
    session.capturing.stop()
    clock.advance(1)
    session.tick()
    assert session.script.index == 0
