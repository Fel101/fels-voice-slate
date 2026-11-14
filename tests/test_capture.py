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
    backend.channel, backend._opened = None, 1
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


# ---- system audio at full resolution ---------------------------------------------------------------------------------------

def loopback_library(monkeypatch, refuse=()):
    """A stand-in for PyAudioWPatch with one output and its loopback. Input streams in a format in `refuse` fail to open as
    PortAudio's do. Returns the streams opened."""
    import sys
    import time
    opened = []
    devices = [{'index': 0, 'name': 'Speakers (Realtek(R) Audio)', 'hostApi': 0, 'maxOutputChannels': 2,
                'maxInputChannels': 0, 'defaultSampleRate': 48000.0},
               {'index': 1, 'name': 'Speakers (Realtek(R) Audio) [Loopback]', 'hostApi': 0, 'maxOutputChannels': 0,
                'maxInputChannels': 2, 'defaultSampleRate': 48000.0, 'isLoopbackDevice': True}]

    class Stream:
        def __init__(self, **options):
            self.options = options
            self.active = self.closed = False

        def start_stream(self):
            self.active = True

        def stop_stream(self):
            self.active = False

        def close(self):
            self.closed = True

        def is_active(self):
            return self.active

        def write(self, data):
            time.sleep(0.005)               # the silent feed, which a sound card would hold up

    class PyAudio:
        def get_device_info_by_index(self, index):
            return devices[index]

        def get_host_api_info_by_type(self, kind):
            return {'index': 0}

        def get_device_count(self):
            return len(devices)

        def get_default_output_device_info(self):
            return devices[0]

        def open(self, **options):
            if options.get('input') and options['format'] in refuse:
                raise OSError(-9994, 'Sample format not supported')
            opened.append(Stream(**options))
            return opened[-1]

        def terminate(self):
            pass

    monkeypatch.setitem(sys.modules, 'pyaudiowpatch', SimpleNamespace(
        PyAudio=PyAudio, paFloat32=1, paInt16=8, paWASAPI=13, paContinue=0, paInputOverflow=2, paInputUnderflow=4))
    return opened


def inputs(opened):
    return [stream for stream in opened if stream.options.get('input')]


def test_system_audio_is_recorded_in_floating_point(monkeypatch):
    """It was asked for in 16 bits, although the original is saved in 24: anything quieter than -96 dB was lost."""
    opened = loopback_library(monkeypatch)
    capture = cap.open_capture('system', 1)
    capture.start()
    stream = inputs(opened)[0]
    assert stream.options['format'] == 1
    quiet = np.full((480, 2), 1e-5, np.float32)                 # -100 dBFS: below 16 bits' smallest step
    stream.options['stream_callback'](quiet.tobytes(), 480, None, 0)
    took = capture.stop()
    capture.close()
    assert took.shape == (480, 2) and np.allclose(took, 1e-5)


def test_a_device_that_refuses_floating_point_is_recorded_in_16_bits(monkeypatch):
    opened = loopback_library(monkeypatch, refuse=(1,))
    capture = cap.open_capture('system', 1)
    capture.start()
    stream = inputs(opened)[0]
    assert stream.options['format'] == 8
    stream.options['stream_callback'](np.full((480, 2), 16384, '<i2').tobytes(), 480, None, 0)
    took = capture.stop()
    capture.close()
    assert np.allclose(took, 0.5)


def test_a_loopback_that_will_not_open_leaves_no_silent_feed_playing(monkeypatch):
    opened = loopback_library(monkeypatch, refuse=(1, 8))
    backend = cap._LoopbackBackend(1)
    with pytest.raises(OSError, match='Sample format not supported'):
        backend.open(lambda block, lost: None)
    assert backend._feed is None and backend._out is None and opened[0].closed
    backend.terminate()
