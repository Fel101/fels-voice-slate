"""The check of the room and the level before a session: what it judges, that it judges as the Check tab does, and that
nothing it hears is written or kept.

A real Capture listens to a stand-in microphone, and a stand-in room decides what it hears: the room, and a voice once
the person is asked to read. The clock is the test's, so nothing sleeps."""
import gc
import weakref

import numpy as np
import pytest

from helpers import speech
from recorder import audio_core as ac
from recorder import capture as cap
from recorder.capture import Capture
from recorder.core import analysis
from recorder.session import roomcheck

RATE = 48000
STEP = 0.05


def noise(seconds, db, rng, hum=0.0, rate=RATE):
    t = np.arange(int(round(seconds * rate))) / rate
    x = rng.standard_normal(t.size) * 10 ** (db / 20)
    if hum:
        x = x + hum * (np.sin(2 * np.pi * 50 * t) + 0.5 * np.sin(2 * np.pi * 100 * t) + 0.3 * np.sin(2 * np.pi * 150 * t))
    return x.astype(np.float32)


def voice(seconds, level_db, room_db, rng, hum=0.0):
    v = speech(seconds, RATE)
    v = v / 10 ** (ac.voiced_rms_db(v, RATE) / 20) * 10 ** (level_db / 20)
    return np.clip(v + noise(seconds, room_db, rng, hum), -1.0, 1.0).astype(np.float32)


def judged(level_db=-22.0, room_db=-70.0, hum=0.0, silent=False):
    rng = np.random.default_rng(7)
    spoken = noise(6.0, room_db, rng, hum) if silent else voice(6.0, level_db, room_db, rng, hum)
    return analysis.room_check(noise(3.0, room_db, rng, hum), spoken, RATE)


# ---- what it judges -----------------------------------------------------------------------------------------------------

def test_a_quiet_room_and_a_voice_at_the_target_are_ready():
    found = judged()
    assert found.heard and (found.room, found.level, found.headroom) == ('ok', 'ok', 'ok') and found.hum is None
    assert found.speech_db == pytest.approx(-22.0, abs=0.5)


def test_a_quiet_voice_is_too_low_and_raises_the_room_with_it():
    found = judged(level_db=-36.0, room_db=-70.0)
    assert found.level == 'bad' and found.speech_db < ac.TARGET_VOICED_DB
    assert found.floor_at_target_db == pytest.approx(found.floor_db + ac.TARGET_VOICED_DB - found.speech_db, abs=0.2)
    assert found.floor_at_target_db > found.floor_db + 10


def test_a_voice_a_little_low_is_worth_a_note():
    assert judged(level_db=-25.0).level == 'warn'


def test_a_voice_that_clips_is_too_high_and_has_no_headroom():
    found = judged(level_db=-5.0)
    assert found.level == 'bad' and found.speech_db > ac.TARGET_VOICED_DB
    assert found.headroom == 'bad' and found.clipped > analysis.CLIPPED_MAX


def test_a_noisy_room_is_found():
    assert judged(room_db=-48.0).room == 'warn'
    assert judged(room_db=-38.0).room == 'bad'


def test_no_voice_is_said_when_nobody_speaks():
    found = judged(silent=True)
    assert not found.heard and found.level == 'bad' and found.room == 'ok'


def test_mains_hum_in_the_quiet_is_found():
    found = judged(hum=0.003)
    assert found.hum is not None and found.hum[0] == 50.0


@pytest.mark.parametrize('level_db, room_db', [(-22.0, -70.0), (-36.0, -70.0), (-28.0, -60.0), (-22.0, -48.0),
                                               (-22.0, -38.0), (-14.0, -65.0)])
def test_the_room_check_and_the_check_tab_never_disagree(level_db, room_db):
    """Record what the check heard, as a take would be, and put it through the Check tab: the level is judged as it is,
    and the room once Normalize has brought the voice to the target, as the check says it would be."""
    rng = np.random.default_rng(3)
    quiet, spoken = noise(3.0, room_db, rng), voice(6.0, level_db, room_db, rng)
    found = analysis.room_check(quiet, spoken, RATE)
    take = ac.resample(np.concatenate([quiet, spoken]), RATE, ac.EXPORT_RATE)
    as_recorded = {row.key: row.status for row in analysis.quality_report(take)}
    normalized = ac.apply_gain(take, ac.TARGET_VOICED_DB - found.speech_db)
    as_normalized = {row.key: row.status for row in analysis.quality_report(normalized)}
    assert found.level == as_recorded['speech']
    assert found.room == as_normalized['noise']


# ---- listening, and letting go ---------------------------------------------------------------------------------------------

class Microphone:
    """The device a real Capture is opened on, fed a block at a time by the test."""

    kind, name, hostapi, rate, channels = 'mic', 'Test microphone', 'test', RATE, 1

    def __init__(self):
        self.on_block = None
        self.opened = self.closed = 0

    def open(self, on_block):
        self.on_block = on_block
        self.opened += 1

    def alive(self):
        return True

    def close(self):
        self.on_block = None
        self.closed += 1


class Room:
    """What the microphone hears: the room, and the voice for `reading` seconds once asked to read, after `reaction`."""

    def __init__(self, session, room_db=-70.0, level_db=-22.0, reaction=0.5, reading=2.5, silent=False):
        self.session, self.room_db, self.level_db = session, room_db, level_db
        self.reaction, self.reading, self.silent = reaction, reading, silent
        self.rng = np.random.default_rng(11)
        self.since = None
        self.sentence = voice(reading, level_db, room_db, self.rng)

    def hear(self, seconds):
        n = int(round(seconds * RATE))
        block = noise(seconds, self.room_db, self.rng)
        if self.session.room.phase == 'speak' and not self.silent:
            self.since = 0.0 if self.since is None else self.since
            first = int(round((self.since - self.reaction) * RATE))
            if 0 <= first + n and first < len(self.sentence):
                lo, hi = max(first, 0), min(first + n, len(self.sentence))
                block[lo - first:hi - first] = self.sentence[lo:hi]
            self.since += seconds
        return block.reshape(-1, 1)


def fit_microphone(monkeypatch):
    """Make every capture opened from here on listen to a new stand-in microphone, and return it."""
    device = Microphone()
    device.made = []                    # weakly: holding a capture here would keep what it heard alive

    def open_capture(source, index, pre_roll, channel=None):
        made = Capture(device, pre_roll)
        device.made.append(weakref.ref(made))
        return made
    monkeypatch.setattr(cap, 'open_capture', open_capture)
    return device


@pytest.fixture
def microphone(monkeypatch):
    return fit_microphone(monkeypatch)


@pytest.fixture
def listening(session, microphone, clock):
    def make(**room):
        heard = Room(session, **room)

        def wait(seconds, until=None):
            for _ in range(int(round(seconds / STEP))):
                if microphone.on_block is not None:
                    microphone.on_block(heard.hear(STEP), False)
                clock.advance(STEP)
                session.tick()
                if until is not None and until():
                    return True
            return False
        return wait
    return make


def files_under(folder):
    return sorted(path for path in folder.rglob('*') if path.is_file())


def test_the_check_listens_to_the_quiet_then_the_sentence_and_says_what_it_found(session, listening, microphone):
    wait = listening()
    before = files_under(session.lib.root)
    assert session.room.start() and session.room.phase == 'quiet' and microphone.on_block is not None
    assert session.status_text == 'Checking the room: stay quiet for a moment.'
    assert wait(roomcheck.QUIET_SECONDS + STEP, until=lambda: session.room.phase == 'speak')
    assert session.status_text == 'Now read the sentence on the screen.'
    assert wait(10, until=lambda: not session.room.active)
    found = session.room.result
    assert found.heard and (found.room, found.level, found.headroom) == ('ok', 'ok', 'ok')
    assert session.status_text == 'Room check done. Nothing it heard was kept.'
    assert microphone.closed == 1 and microphone.on_block is None
    assert files_under(session.lib.root) == before and session.workspace.takes == []      # no spool, no take


def test_nothing_it_heard_is_kept_in_memory_afterwards(session, listening, microphone):
    wait = listening()
    session.room.start()
    heard = microphone.made[-1]
    assert heard() is not None
    assert wait(15, until=lambda: not session.room.active)
    gc.collect()
    assert heard() is None and session.room.capture is None
    kept = [value for value in vars(session.room).values() if isinstance(value, np.ndarray)]
    assert kept == [] and session.room.result is not None


def test_the_input_armed_before_the_check_is_armed_again_after_it(session, listening, microphone):
    wait = listening()
    session.capturing.toggle_arm()
    assert session.capturing.armed and microphone.opened == 1
    session.room.start()
    assert not session.capturing.armed and session.capturing.capture is None        # the check has the device alone
    assert wait(15, until=lambda: not session.room.active)
    assert session.capturing.armed and microphone.opened == 3


def test_an_input_not_armed_stays_closed_after_the_check(session, listening, microphone):
    wait = listening()
    session.room.start()
    assert wait(15, until=lambda: not session.room.active)
    assert not session.capturing.armed and microphone.opened == 1


def test_a_check_with_no_voice_gives_up_and_says_none_was_heard(session, listening):
    wait = listening(silent=True)
    session.room.start()
    assert not wait(roomcheck.QUIET_SECONDS + roomcheck.SPEAK_SECONDS - 0.5, until=lambda: not session.room.active)
    assert wait(1.0, until=lambda: not session.room.active)
    assert not session.room.result.heard


def test_a_quiet_voice_is_judged_too_low(session, listening):
    wait = listening(level_db=-38.0)
    session.room.start()
    assert wait(15, until=lambda: not session.room.active)
    assert session.room.result.heard and session.room.result.level == 'bad'


def test_stopping_the_check_lets_what_it_heard_go_without_a_result(session, listening, microphone):
    wait = listening()
    session.room.start()
    wait(1.0)
    session.room.end()
    assert not session.room.active and session.room.result is None and microphone.closed == 1
    assert session.status_text == 'Room check stopped. Nothing it heard was kept.'


def test_a_microphone_that_stops_sending_ends_the_check(session, microphone, clock):
    session.room.start()
    for _ in range(int(roomcheck.NO_AUDIO_SECONDS / STEP) + 2):
        clock.advance(STEP)
        session.tick()
    assert not session.room.active and session.room.result is None
    assert session.status_text == 'The microphone stopped sending sound, so the room was not checked.'


def test_the_record_key_ends_the_check_and_records_nothing(session, listening, microphone):
    wait = listening()
    session.room.start()
    wait(0.5)
    session.record_pressed()
    assert not session.room.active and not session.capturing.recording and microphone.on_block is None


def test_the_check_waits_for_a_recording_and_needs_a_microphone(session, microphone):
    session.capturing.start()
    assert not session.room.start() and session.status_text == 'Stop the recording before checking the room.'
    session.capturing.finish_take()
    session.capturing.set_source('system')
    assert not session.room.start() and session.status_text == 'Choose Microphone to check the room and the level.'
