"""Capture behaviour that needs no audio hardware: a fake backend stands in for the device."""
from types import SimpleNamespace

import numpy as np
import pytest

from recorder import capture as cap


class FakeStream:
    def __init__(self, **options):
        self.callback = options['callback']

    def start(self):
        pass


def test_the_microphone_passes_on_what_the_sound_library_says_it_lost():
    """The flags come from sounddevice, whose names a test with a fake backend would never touch."""
    streams = []

    def stream(**options):
        streams.append(FakeStream(**options))
        return streams[-1]

    backend = cap._MicBackend.__new__(cap._MicBackend)
    backend._sd, backend.device, backend.rate, backend.channels = SimpleNamespace(InputStream=stream), 1, 48000, 1
    heard = []
    backend.open(lambda block, lost: heard.append(lost))
    silence = np.zeros((480, 1), dtype=np.float32)
    sounddevice = pytest.importorskip('sounddevice')
    for flags, lost in ((0, False), (sounddevice._lib.paInputOverflow, True), (sounddevice._lib.paInputUnderflow, True)):
        streams[0].callback(silence, 480, None, sounddevice.CallbackFlags(flags))
        assert heard[-1] is lost


def test_system_audio_can_read_the_flags_it_needs_from_its_library():
    pyaudio = pytest.importorskip('pyaudiowpatch')
    assert pyaudio.paInputOverflow and pyaudio.paInputUnderflow


class FakeBackend:
    kind = 'system'
    name = 'fake device'
    hostapi = 'test'
    rate = 48000
    channels = 1

    def __init__(self, close_error=None):
        self.on_block = None
        self.closed = 0
        self._close_error = close_error

    def open(self, on_block):
        self.on_block = on_block

    def alive(self):
        return True

    def close(self):
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error


def block(value, frames=480):
    return np.full((frames, 1), value, dtype=np.float32)


def test_stop_returns_the_audio_even_when_the_device_fails_to_close():
    backend = FakeBackend(close_error=OSError('device unplugged'))
    capture = cap.Capture(backend)
    capture.start()
    backend.on_block(block(0.25))
    backend.on_block(block(0.25))
    samples = capture.stop()
    assert len(samples) == 960
    assert np.allclose(samples, 0.25)
    assert isinstance(capture.close_error, OSError)
    assert backend.closed == 1


def test_a_clean_stop_reports_no_close_error():
    backend = FakeBackend()
    capture = cap.Capture(backend)
    capture.start()
    backend.on_block(block(0.1))
    samples = capture.stop()
    assert len(samples) == 480
    assert capture.close_error is None


def test_a_device_that_has_sent_nothing_for_too_long_is_stalled():
    backend = FakeBackend()
    capture = cap.Capture(backend)
    capture.start()
    assert not capture.stalled()
    capture._started -= cap.FIRST_BLOCK_SECONDS + 1
    assert capture.stalled()
    backend.on_block(block(0.1))
    assert not capture.stalled()
    capture._last_block -= cap.STALL_SECONDS + 1
    assert capture.stalled()
    capture.stop()
    assert not capture.stalled()


def test_wall_elapsed_keeps_counting_when_no_audio_arrives():
    capture = cap.Capture(FakeBackend())
    assert capture.wall_elapsed == 0.0
    capture.start()
    capture._started -= 40
    assert capture.wall_elapsed == pytest.approx(40, abs=0.5)
    assert capture.elapsed == 0.0


def test_the_live_envelope_grows_with_the_recording_and_covers_the_pre_roll():
    backend = FakeBackend()
    backend.kind = 'mic'
    capture = cap.Capture(backend, pre_roll_seconds=0.02)
    capture.arm()
    assert capture.envelope().shape == (0, 3)
    backend.on_block(block(0.5))                                    # 10 ms, kept as pre-roll
    backend.on_block(block(0.1))                                    # 10 ms more, still before Start
    capture.start()
    assert capture.envelope().shape == (2, 3)
    backend.on_block(np.linspace(-0.8, 0.9, 480, dtype=np.float32).reshape(-1, 1))
    rows = capture.envelope()
    assert rows.shape == (3, 3)
    assert rows[:, 0] == pytest.approx([0.01, 0.02, 0.03])           # seconds recorded at the end of each block
    assert rows[2, 1] == pytest.approx(-0.8) and rows[2, 2] == pytest.approx(0.9)
    capture.stop()


def test_the_envelope_of_a_stereo_capture_is_its_mono_mix():
    backend = FakeBackend()
    backend.channels = 2
    capture = cap.Capture(backend)
    capture.start()
    backend.on_block(np.tile(np.array([[0.6, -0.2]], dtype=np.float32), (480, 1)))
    row = capture.envelope()[0]
    assert row[1] == pytest.approx(0.2) and row[2] == pytest.approx(0.2)
    capture.stop()


def test_audio_the_device_says_it_lost_is_counted_while_recording():
    backend = FakeBackend()
    backend.kind = 'mic'
    capture = cap.Capture(backend, pre_roll_seconds=0.02)
    capture.arm()
    backend.on_block(block(0.1), True)                              # lost before the take: not this take's problem
    capture.start()
    assert capture.dropouts == 0
    backend.on_block(block(0.1), True)                              # the first block of a take can say so about nothing
    backend.on_block(block(0.1))
    backend.on_block(block(0.1), True)
    backend.on_block(block(0.1), True)
    assert capture.dropouts == 2
    capture.stop()
    capture.start()
    assert capture.dropouts == 0


def test_new_blocks_hands_each_block_over_once_and_starts_with_the_pre_roll():
    backend = FakeBackend()
    backend.kind = 'mic'
    capture = cap.Capture(backend, pre_roll_seconds=0.05)
    capture.arm()
    backend.on_block(block(0.5))
    capture.start()
    backend.on_block(block(0.25))
    assert [float(b[0, 0]) for b in capture.new_blocks()] == [0.5, 0.25]
    assert capture.new_blocks() == []
    backend.on_block(block(0.125))
    assert [float(b[0, 0]) for b in capture.new_blocks()] == [0.125]
    capture.stop()
