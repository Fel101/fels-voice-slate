"""The room and level check in the window: the command, the steps on the screen, the findings in plain words, and the
device left alone while it listens."""
import pytest

pytest.importorskip('PySide6')

from recorder.core.analysis import Room  # noqa: E402
from recorder.session import roomcheck  # noqa: E402
from recorder.ui.roomcheck import findings, verdict  # noqa: E402
from test_room_check import STEP, Room as Heard, fit_microphone  # noqa: E402


def room(**changes):
    found = dict(heard=True, floor_db=-72.0, speech_db=-21.0, floor_at_target_db=-71.0, peak_dbfs=-12.0, clipped=0.0,
                 hum=None, room='ok', level='ok', headroom='ok')
    return Room(**{**found, **changes})


def headings(result):
    return [heading for _, heading, _ in findings(result)]


# ---- the findings, in words -----------------------------------------------------------------------------------------------

def test_all_is_well_says_so():
    rows = findings(room())
    assert [state for state, _, _ in rows] == ['ok', 'ok', 'ok'] and verdict(rows) == 'Ready to record'
    assert headings(room()) == ['The microphone level is right', 'The room is quiet enough',
                                'There is room above the loudest words']
    assert rows[1][2] == 'With your voice at the target level, the room would measure -71 dB. The Check tab asks for -55 ' \
                         'dB or less.'


def test_a_low_level_says_what_to_change():
    rows = findings(room(speech_db=-34.0, level='bad', floor_at_target_db=-58.0))
    state, heading, text = rows[0]
    assert (state, heading) == ('bad', 'The microphone level is too low')
    assert 'Your voice measured -34 dB, and the target is -20 dB.' in text and 'Raise the input level' in text
    assert 'Windows Settings > System > Sound, under Input' in text and verdict(rows) == 'Change this before you record'


def test_a_high_level_and_clipping_say_to_lower_it():
    rows = findings(room(speech_db=-14.0, level='warn', peak_dbfs=0.0, clipped=0.02, headroom='bad'))
    assert rows[0][:2] == ('advice', 'The microphone level is a little high') and 'Lower the input level' in rows[0][2]
    assert rows[2][:2] == ('bad', 'The voice clipped') and rows[2][2].startswith('2.0% of it reached full scale')


def test_a_noisy_room_says_how_to_quieten_it():
    rows = findings(room(room='bad', floor_at_target_db=-40.0))
    assert rows[1][:2] == ('bad', 'The room is too noisy')
    assert 'The Check tab flags anything above -45 dB.' in rows[1][2] and 'Turn off a fan' in rows[1][2]
    assert findings(room(room='warn', floor_at_target_db=-50.0))[1][0] == 'advice'


def test_no_voice_says_where_to_look():
    rows = findings(room(heard=False, level='bad', speech_db=-240.0, floor_at_target_db=-72.0))
    assert headings(room(heard=False, level='bad')) == ['No voice was heard', 'The room is quiet enough']
    assert 'is the one you speak into' in rows[0][2] and rows[1][2].startswith('The room measured -72 dB.')


def test_peaks_near_full_scale_and_hum_are_notes():
    rows = findings(room(peak_dbfs=-0.4, headroom='warn', hum=(50.0, 20.0)))
    assert rows[2][:2] == ('advice', 'The loudest words come close to full scale') and '-0.4 dBFS' in rows[2][2]
    assert rows[3][:2] == ('advice', 'Mains hum at 50 Hz') and verdict(rows) == 'Ready to record, and worth a look'


# ---- in the window -------------------------------------------------------------------------------------------------------

def run_until(window, session, microphone, heard, done, seconds=20.0):
    for _ in range(int(seconds / STEP)):
        if microphone.on_block is not None:
            microphone.on_block(heard.hear(STEP), False)
        window._clock.advance(STEP)
        window._tick()
        window.flush()
        if done():
            return True
    return False


@pytest.fixture
def microphone(monkeypatch):
    return fit_microphone(monkeypatch)


@pytest.fixture
def checking(window, session, microphone, clock):
    window._clock = clock
    window.actions.room_check.trigger()
    window.flush()
    return window.room_dialog


def test_the_command_opens_the_check_and_says_nothing_is_kept(window, checking):
    dialog = checking
    assert dialog.isVisible() and dialog.step.text() == 'Press Start, then stay quiet for 3 seconds.'
    assert 'Nothing it hears is saved' in dialog.privacy.text() and dialog.start_button.text() == 'Start'
    assert not dialog.meter.isVisible() and not dialog.found.isVisible()


def test_the_check_goes_through_its_steps_and_shows_what_it_found(window, session, checking, microphone):
    dialog, heard = checking, Heard(session)
    dialog.start_button.click()
    window.flush()
    assert session.room.phase == 'quiet' and dialog.step.text() == 'Stay quiet: listening to the room... 3'
    assert dialog.start_button.text() == 'Stop' and dialog.meter.isVisible()
    assert not window.topbar.device.isEnabled() and not window.topbar.arm.isEnabled()
    assert not window.actions.settings.isEnabled() and not window.actions.import_recording.isEnabled()
    assert run_until(window, session, microphone, heard, lambda: session.room.phase == 'speak')
    assert dialog.sentence.isVisible() and roomcheck.SENTENCE in dialog.sentence.text()
    assert run_until(window, session, microphone, heard, lambda: not session.room.active)
    assert dialog.step.text() == 'Ready to record' and dialog.found.isVisible()
    assert dialog.start_button.text() == 'Check again' and not dialog.sentence.isVisible()
    assert window.topbar.device.isEnabled() and window.actions.settings.isEnabled()


def test_esc_stops_the_check(window, session, checking, microphone):
    checking.start_button.click()
    window.flush()
    window.actions.escape.trigger()
    window.flush()
    assert not session.room.active and microphone.on_block is None and session.room.result is None
    assert checking.step.text() == 'Press Start, then stay quiet for 3 seconds.'


def test_closing_the_window_of_the_check_stops_it(window, session, checking, microphone):
    checking.start_button.click()
    checking.close()
    assert not session.room.active and microphone.on_block is None


def test_the_check_cannot_start_while_recording(window, session, checking):
    session.capturing.start()
    window.flush()
    assert not checking.start_button.isEnabled()
    assert checking.start_button.toolTip() == 'Stop the recording before checking the room.'
    assert not window.actions.room_check.isEnabled()


def test_a_take_playing_stops_before_the_check_listens(window, session, checking, player):
    from helpers import record
    record(session, 5.0)
    window.flush()
    session.transport.toggle()
    assert session.transport.active
    checking.start_button.click()
    assert not session.transport.active and session.room.active
