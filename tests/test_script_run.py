"""Reading a script hands-free: the next line when a take ends, the end of a take after a pause, and a run with a count-in.

A real Capture records from a stand-in microphone, and a stand-in reader decides what it hears: the room, then the line
read aloud a moment after the take starts, then the room again. The clock is the test's, so nothing sleeps."""
import numpy as np
import pytest

from recorder import capture as cap
from recorder import settings
from recorder.capture import Capture
from recorder.core.analysis import live_pause
from recorder.session import run as run_mod

RATE = 16000
STEP = 0.05                             # a tick, as the window gives about twenty a second
LINES = [f'This is line number {n} of the test script.' for n in range(1, 11)]
EVERYTHING = {'next_line': True, 'stop_after_pause': True, 'pause_seconds': 1.5, 'auto_start': True, 'count_in': 3}


class Microphone:
    """The device a real Capture is opened on. What it hears comes from the reader, a block at a time."""

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

    @property
    def open_now(self):
        return self.on_block is not None


class Reader:
    """Someone at the microphone. When a take starts they wait `reaction` seconds, read for `reading` seconds with short
    gaps between the words, and are quiet after. `click` is a knock at the start of each take, such as a key."""

    def __init__(self, reaction=0.4, reading=1.6, click=False, silent=False):
        self.reaction, self.reading, self.click, self.silent = reaction, reading, click, silent
        self.since = None                   # seconds since the take being recorded started
        self._phase = 0.0

    def hear(self, seconds, recording):
        if not recording:
            self.since = None
        elif self.since is None:
            self.since = 0.0
        n = int(round(seconds * RATE))
        rng = np.random.default_rng(int(self._phase * 1000) % 2 ** 32)
        block = (rng.standard_normal(n) * 10 ** (-65 / 20)).astype(np.float32)          # the room, at -65 dBFS
        if self.since is not None:
            t = self.since + np.arange(n) / RATE
            words = (t >= self.reaction) & (t < self.reaction + self.reading) & ((t * 4) % 1 < 0.8)
            if not self.silent:
                block += (words * 0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
            if self.click:
                block += ((t < 0.02) * 0.5).astype(np.float32)
            self.since += seconds
        self._phase += seconds
        return block.reshape(-1, 1)


@pytest.fixture
def microphone(monkeypatch):
    device = Microphone()
    monkeypatch.setattr(cap, 'open_capture', lambda source, index, pre_roll, channel=None: Capture(device, pre_roll))
    return device


@pytest.fixture
def reading(make_session, microphone, clock, tmp_path):
    """A session with the test script loaded and the hands-free options given, and a way to let time pass."""
    def make(options=EVERYTHING, lines=LINES, reader=None):
        settings.update({'hands_free': options}, tmp_path / 'settings.json')
        session = make_session()
        session.script.load_text('\n'.join(lines))
        voice = reader or Reader()

        def wait(seconds, until=None):
            for _ in range(int(round(seconds / STEP))):
                if microphone.open_now:
                    microphone.on_block(voice.hear(STEP, session.capturing.recording), False)
                clock.advance(STEP)
                session.tick()
                if until is not None and until():
                    return True
            return False

        return session, wait
    return make


# ---- the whole script ----------------------------------------------------------------------------------------------------

def test_a_ten_line_script_is_read_hands_free_and_nothing_is_kept_or_ticked(reading, microphone, player):
    session, wait = reading()
    session.record_pressed()
    assert session.run.active and session.run.phase == 'counting' and microphone.open_now
    assert wait(120, until=lambda: not session.run.active)
    takes = list(reversed(session.workspace.takes))
    assert [take.meta['script_line'] for take in takes] == LINES
    assert all(3.0 < take.seconds < 6.0 for take in takes)              # each line, with the room before and after it
    assert not any(take.kept for take in takes) and not any(take.meta['consent_confirmed'] for take in takes)
    assert session.script.index == 9 and session.capturing.capture is None and not microphone.open_now
    assert 'last line' in session.status_text and 'microphone is closed' in session.status_text
    assert player.log == []                                              # the count-in is seen, never heard


def test_each_take_keeps_the_half_second_before_it_and_the_tail_after_it(reading):
    session, wait = reading()
    session.record_pressed()
    wait(3.05)                                                           # the count-in, and the take starts
    assert session.capturing.recording
    wait(10, until=lambda: not session.capturing.recording)
    take = session.workspace.takes[0]                                    # as saved: the room, the line, the room
    voice = np.flatnonzero(np.abs(take.samples) > 0.05)
    assert voice[0] / RATE >= 0.5 + 0.4 - STEP                           # the pre-roll, and the reader's moment
    assert len(take.samples) / RATE - voice[-1] / RATE >= 1.5 + 0.5 - STEP     # the pause, then the stop tail


# ---- the pause -------------------------------------------------------------------------------------------------------------

def test_a_take_ends_after_the_pause_only_once_the_voice_has_been_heard(reading):
    session, wait = reading(options={'stop_after_pause': True, 'pause_seconds': 1.5},
                            reader=Reader(reaction=2.5, reading=1.0))
    session.record_pressed()
    assert session.capturing.recording and not session.run.active        # without the count-in, no run
    wait(2.4)
    assert session.capturing.recording and not session.run.heard
    assert session.run.state()[0] == 'listening'
    wait(1.5)
    assert session.run.heard and session.run.state()[0] == 'heard'
    assert wait(5, until=lambda: session.capturing.finishing)
    assert session.capturing.elapsed == pytest.approx(2.5 + 1.0 + 1.5, abs=0.2)        # not armed: no pre-roll


def test_a_click_at_the_start_is_not_taken_for_the_voice(reading):
    session, wait = reading(options={'stop_after_pause': True, 'pause_seconds': 1.0},
                            reader=Reader(reaction=2.0, reading=1.0, click=True))
    session.record_pressed()
    wait(1.9)
    assert session.capturing.recording and not session.capturing.finishing and not session.run.heard


def test_a_pause_shorter_than_the_one_chosen_does_not_end_the_take(reading):
    """The gaps between the reader's words are 0.05 s; a breath between sentences is shorter than the setting."""
    session, wait = reading(options={'stop_after_pause': True, 'pause_seconds': 2.0}, reader=Reader(reading=1.0))
    session.record_pressed()
    wait(0.4 + 1.0 + 1.8)
    assert session.capturing.recording and not session.capturing.finishing


@pytest.mark.parametrize('rows, expected', [
    ([], (False, 0.0)),
    ([(0.05 * i, -65.0) for i in range(1, 41)], (False, 0.0)),                        # the room only
    ([(0.05 * i, -120.0 if i != 10 else -90.0) for i in range(1, 41)], (False, 0.0)),  # hiss on a silent input
    ([(0.05 * i, -20.0 if 10 <= i < 20 else -65.0) for i in range(1, 41)], (True, 1.05)),
])
def test_the_voice_is_told_from_the_room(rows, expected):
    heard, quiet = live_pause(rows)
    assert heard == expected[0] and quiet == pytest.approx(expected[1])


# ---- the next line ------------------------------------------------------------------------------------------------------------

def test_the_next_line_comes_when_a_take_ends(reading):
    session, wait = reading(options={'next_line': True})
    session.record_pressed()
    wait(1.0)
    session.record_pressed()
    wait(1.0)
    assert session.script.index == 1 and session.workspace.takes[0].meta['script_line'] == LINES[0]


def test_without_the_options_a_take_ends_and_the_line_stays_as_before(reading):
    session, wait = reading(options={})
    session.record_pressed()
    wait(6.0)
    assert session.capturing.recording
    session.record_pressed()
    wait(1.0)
    assert session.script.index == 0 and not session.capturing.recording


# ---- ending a run -------------------------------------------------------------------------------------------------------------

def test_ending_a_run_mid_take_saves_the_take_and_closes_the_microphone(reading, microphone):
    session, wait = reading()
    session.record_pressed()
    wait(3.0 + 1.0)
    assert session.capturing.recording
    session.run.end()
    assert not session.run.active
    wait(1.0)
    assert len(session.workspace.takes) == 1 and session.script.index == 0      # read again from this line
    assert not microphone.open_now and session.capturing.capture is None
    assert session.status_text.startswith('Take saved.') and 'microphone is closed' in session.status_text


def test_ending_a_run_in_the_count_in_closes_the_microphone_and_records_nothing(reading, microphone):
    session, wait = reading()
    session.record_pressed()
    wait(1.0)
    session.record_pressed()                                                 # the record key ends a run
    wait(5.0)
    assert not session.run.active and session.workspace.takes == [] and not microphone.open_now


def test_an_input_armed_before_the_run_stays_armed_after_it(reading, microphone):
    session, wait = reading()
    session.capturing.toggle_arm()
    session.record_pressed()
    wait(3.0 + 1.0)
    session.run.end()
    wait(1.0)
    assert session.capturing.armed and microphone.open_now and 'input stays armed' in session.status_text


def test_a_take_that_reaches_the_longest_recording_ends_the_run(reading, microphone):
    session, wait = reading(reader=Reader(silent=True))
    session.workspace.set_purpose('sentence')                            # 60 s at most
    session.record_pressed()
    assert wait(70, until=lambda: not session.run.active)
    assert len(session.workspace.takes) == 1 and 'longest recording' in session.status_text
    assert not microphone.open_now


def test_clearing_the_script_ends_the_run(reading, microphone):
    session, wait = reading()
    session.record_pressed()
    wait(1.0)
    session.script.clear()
    wait(0.1)
    assert not session.run.active and not microphone.open_now


# ---- again --------------------------------------------------------------------------------------------------------------------

def test_again_in_the_count_in_goes_back_to_the_line_just_read(reading):
    session, wait = reading()
    session.record_pressed()
    wait(30, until=lambda: session.script.index == 1 and session.run.phase == 'counting')
    session.run.again()
    assert session.script.index == 0 and session.run.phase == 'counting' and session.run.count_left == 3
    wait(30, until=lambda: len(session.workspace.takes) == 2)
    assert [take.meta['script_line'] for take in session.workspace.takes] == [LINES[0], LINES[0]]


def test_again_during_a_take_saves_it_and_reads_the_same_line_once_more(reading):
    session, wait = reading()
    session.record_pressed()
    wait(3.0 + 0.8)
    session.run.again()
    wait(30, until=lambda: session.run.phase == 'counting')
    assert len(session.workspace.takes) == 1 and session.script.index == 0
    wait(30, until=lambda: len(session.workspace.takes) == 2)
    assert [take.meta['script_line'] for take in session.workspace.takes] == [LINES[0], LINES[0]]


def test_again_outside_a_run_records_the_line_just_read_at_once(reading):
    session, wait = reading(options={'next_line': True})
    session.record_pressed()
    wait(1.0)
    session.record_pressed()
    wait(1.0)
    assert session.script.index == 1
    session.run.again()
    assert session.capturing.recording and session.script.index == 0


# ---- the options ----------------------------------------------------------------------------------------------------------------

def test_the_options_are_remembered(reading, make_session):
    session, _ = reading(options={})
    session.run.set_options(next_line=True, pause_seconds=2.5)
    assert make_session().run.options == {**run_mod.OPTIONS, 'next_line': True, 'pause_seconds': 2.5}


@pytest.mark.parametrize('saved, expected', [
    ('not a dict', {}),
    ({'next_line': 'yes', 'pause_seconds': 'long', 'count_in': float('nan')}, {}),
    ({'pause_seconds': 99, 'count_in': 0}, {'pause_seconds': 5.0, 'count_in': 1}),
    ({'auto_start': True}, {}),                                          # a run needs takes that end by themselves
    ({'auto_start': True, 'stop_after_pause': True}, {'auto_start': True, 'stop_after_pause': True}),
])
def test_options_that_cannot_be_used_are_the_defaults(saved, expected):
    assert run_mod.options({'hands_free': saved}) == {**run_mod.OPTIONS, **expected}


def test_with_no_script_the_record_key_records_as_always(make_session, microphone, tmp_path):
    settings.update({'hands_free': EVERYTHING}, tmp_path / 'settings.json')
    session = make_session()
    session.record_pressed()
    assert session.capturing.recording and not session.run.active
