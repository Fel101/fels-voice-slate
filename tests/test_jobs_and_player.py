import threading
import time

import numpy as np
import pytest
from sounddevice import CallbackStop

from recorder.audio_io import Player
from recorder.jobs import Jobs


# ---- jobs -------------------------------------------------------------------------------------------------------------

def test_an_inline_job_runs_at_once_but_is_delivered_only_when_drained():
    jobs, seen = Jobs(inline=True), []
    jobs.submit(lambda: 6 * 7, seen.append)
    assert seen == [] and jobs.pending == 1
    assert jobs.drain() == 1 and seen == [42] and jobs.pending == 0


def test_a_job_that_raises_is_reported_through_its_error_callback():
    jobs, seen = Jobs(inline=True), []
    jobs.submit(lambda: 1 / 0, seen.append, lambda e: seen.append(type(e).__name__))
    jobs.drain()
    assert seen == ['ZeroDivisionError']


def test_an_error_with_no_handler_goes_to_on_unhandled():
    jobs, seen = Jobs(inline=True), []
    jobs.on_unhandled = seen.append
    jobs.submit(lambda: 1 / 0, lambda value: None)
    jobs.drain()
    assert len(seen) == 1 and isinstance(seen[0], ZeroDivisionError)


def test_results_come_back_in_the_order_they_finished_and_only_on_the_draining_thread():
    jobs, seen, threads = Jobs(workers=2), [], []
    gate = threading.Event()

    def slow():
        gate.wait(5)
        return 'slow'

    jobs.submit(slow, lambda value: (seen.append(value), threads.append(threading.current_thread())))
    jobs.submit(lambda: 'quick', lambda value: (seen.append(value), threads.append(threading.current_thread())))
    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        jobs.drain()
        time.sleep(0.01)
    assert seen == ['quick']
    gate.set()
    while len(seen) < 2 and time.time() < deadline:
        jobs.drain()
        time.sleep(0.01)
    assert seen == ['quick', 'slow'] and all(t is threading.current_thread() for t in threads)
    jobs.close()


# ---- the player -------------------------------------------------------------------------------------------------------

class FakeStream:
    def __init__(self, rate, callback):
        self.rate, self.callback = rate, callback
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class Rig:
    """A player on a stand-in sound card. `refuse(rate, device)` says which streams will not open, as an output that
    takes only its own rate does, and `own_rate` is the rate each device says it runs at."""

    def __init__(self, refuse=lambda rate, device: False, own_rate=48000):
        self.streams = []
        self.tried = []
        self.refuse = refuse
        self.player = Player(stream_factory=self.make, own_rate=lambda device: own_rate)

    def make(self, rate, callback, device):
        self.tried.append((rate, device))
        if self.refuse(rate, device):
            raise OSError(f'Error opening OutputStream: Invalid sample rate, device {device} at {rate}')
        stream = FakeStream(rate, callback)
        stream.device = device
        self.streams.append(stream)
        return stream

    def pull(self, frames):
        out = np.full((frames, 1), 7.0, dtype=np.float32)
        self.streams[-1].callback(out, frames, None, None)
        return out[:, 0]


@pytest.fixture
def rig():
    return Rig()


def counting(n=1000):
    return np.arange(n, dtype=np.float32) / n


def test_the_callback_hands_over_the_buffer_in_order_and_the_position_follows(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    assert rig.streams[-1].started and rig.player.playing
    assert np.array_equal(rig.pull(300), counting()[:300])
    assert rig.player.position == pytest.approx(0.3)
    assert np.array_equal(rig.pull(200), counting()[300:500])
    assert rig.player.position == pytest.approx(0.5)


def test_play_can_start_part_way_in(rig):
    rig.player.load(counting(), 1000)
    rig.player.play(start=0.25)
    assert np.array_equal(rig.pull(10), counting()[250:260])


def test_pause_gives_silence_and_keeps_the_position_until_resumed(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.pull(100)
    rig.player.pause()
    assert rig.player.paused and not rig.player.playing
    assert not rig.pull(50).any() and rig.player.position == pytest.approx(0.1)
    rig.player.resume()
    assert np.array_equal(rig.pull(10), counting()[100:110])


def test_seek_moves_the_next_samples(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.player.seek(0.8)
    assert np.array_equal(rig.pull(5), counting()[800:805])


def test_reaching_the_end_pads_with_silence_and_stops_the_stream(rig):
    rig.player.load(counting(), 1000)
    rig.player.play(start=0.9)
    out = np.full((250, 1), 7.0, dtype=np.float32)
    with pytest.raises(CallbackStop):
        rig.streams[-1].callback(out, 250, None, None)
    assert np.array_equal(out[:100, 0], counting()[900:]) and not out[100:].any()
    assert rig.player.finished and not rig.player.active and rig.player.position == pytest.approx(1.0)


def test_a_loop_wraps_around_inside_one_callback(rig):
    rig.player.load(counting(), 1000)
    rig.player.play(start=0.1, loop=(0.1, 0.2))
    out = rig.pull(250)
    assert np.array_equal(out[:100], counting()[100:200]) and np.array_equal(out[100:200], counting()[100:200])
    assert np.array_equal(out[200:], counting()[100:150]) and rig.player.looping
    rig.player.set_loop(None)
    assert not rig.player.looping


def test_swapping_the_buffer_keeps_playing_from_the_chosen_time(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.pull(100)
    other = -counting(2000)
    rig.player.swap(other, 1000, 1.5)
    assert np.array_equal(rig.pull(10), other[1500:1510])
    with pytest.raises(ValueError):
        rig.player.swap(other, 48000, 0.0)


def test_stop_closes_the_stream_and_keeps_the_position(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.pull(100)
    stream = rig.streams[-1]
    rig.player.stop()
    assert stream.stopped and stream.closed and not rig.player.active
    assert rig.player.position == pytest.approx(0.1)


def test_playing_again_opens_a_fresh_stream(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.player.play(start=0.5)
    assert len(rig.streams) == 2 and rig.streams[0].closed


def test_an_empty_buffer_plays_nothing(rig):
    rig.player.play()
    assert rig.streams == [] and not rig.player.active


def test_a_device_that_will_not_close_does_not_raise(rig):
    rig.player.load(counting(), 1000)
    rig.player.play()
    rig.streams[-1].stop = lambda: (_ for _ in ()).throw(OSError('device removed'))
    rig.player.stop()
    assert isinstance(rig.player.error, OSError) and not rig.player.active


# ---- an output that will not take the take's rate -----------------------------------------------------------------------

def tone(seconds, rate, freq=5.0):
    return np.sin(2 * np.pi * freq * np.arange(int(seconds * rate)) / rate).astype(np.float32)


def test_an_output_that_refuses_the_takes_rate_plays_it_at_its_own():
    """A WASAPI or WDM-KS output that runs at 2000 here, given a take at 1000: the take is resampled, and the position,
    seeking and the loop are still in seconds of the take."""
    rig = Rig(refuse=lambda rate, device: rate != 2000, own_rate=2000)
    rig.player.device = 5
    rig.player.load(tone(1.0, 1000), 1000)
    rig.player.play(start=0.25)
    assert rig.tried == [(1000, 5), (2000, 5)] and rig.streams[-1].device == 5 and rig.player.fallback is None
    assert rig.player.seconds == pytest.approx(1.0) and rig.player.position == pytest.approx(0.25)
    heard = rig.pull(400)
    assert np.allclose(heard, tone(1.0, 2000)[500:900], atol=0.01)       # the same sound, twice as many samples
    assert rig.player.position == pytest.approx(0.45)
    rig.player.seek(0.8)
    assert np.allclose(rig.pull(10), tone(1.0, 2000)[1600:1610], atol=0.01)
    rig.player.set_loop((0.5, 0.6))
    rig.player.seek(0.55)
    rig.pull(150)
    assert rig.player.position == pytest.approx(0.525)     # to the loop's end at 0.6, then 0.025 s from its start


def test_the_resampled_take_is_made_once_for_each_load(monkeypatch):
    import recorder.audio_io as audio_io
    made = []
    real = audio_io.resample
    monkeypatch.setattr(audio_io, 'resample', lambda x, a, b: made.append(b) or real(x, a, b))
    rig = Rig(refuse=lambda rate, device: rate != 2000, own_rate=2000)
    rig.player.load(tone(1.0, 1000), 1000)
    rig.player.play()
    rig.player.play(start=0.5)
    assert made == [2000]
    rig.player.load(tone(1.0, 1000), 1000)
    rig.player.play()
    assert made == [2000, 2000]


def test_a_b_carries_on_at_the_rate_the_output_took():
    rig = Rig(refuse=lambda rate, device: rate != 2000, own_rate=2000)
    rig.player.load(tone(1.0, 1000), 1000)
    rig.player.play()
    rig.pull(100)
    other = tone(1.0, 1000, freq=3.0)
    rig.player.swap(other, 1000, 0.5)
    assert rig.player.position == pytest.approx(0.5)
    assert np.allclose(rig.pull(10), tone(1.0, 2000, freq=3.0)[1000:1010], atol=0.01)
    with pytest.raises(ValueError):
        rig.player.swap(other, 2000, 0.0)       # what the output runs at is not the take's rate


def test_an_output_that_opens_at_no_rate_gives_way_to_windows_default():
    rig = Rig(refuse=lambda rate, device: device == 5)
    rig.player.device = 5
    rig.player.load(counting(), 1000)
    rig.player.play()
    assert rig.tried == [(1000, 5), (48000, 5), (1000, None)]
    assert rig.streams[-1].device is None and rig.streams[-1].started and rig.player.playing
    assert 'device 5 at 1000' in str(rig.player.fallback)
    assert np.array_equal(rig.pull(10), counting()[:10])
    rig.refuse = lambda rate, device: False
    rig.player.play()
    assert rig.player.fallback is None and rig.streams[-1].device == 5


def test_a_stream_that_opens_but_will_not_start_is_closed_and_the_next_tried():
    rig = Rig()
    rig.player.device = 5
    opened = []
    make = rig.make

    def will_not_start(rate, callback, device):
        stream = make(rate, callback, device)
        opened.append(stream)
        if device == 5:
            stream.start = lambda: (_ for _ in ()).throw(OSError('Error starting stream: Invalid device'))
        return stream

    rig.player._factory = will_not_start
    rig.player.load(counting(), 1000)
    rig.player.play()
    assert [s.device for s in opened] == [5, 5, None]
    assert all(s.closed for s in opened[:2]) and opened[2].started and not opened[2].closed
    assert 'Invalid device' in str(rig.player.fallback)


def test_with_no_output_at_all_the_chosen_ones_own_error_is_raised():
    rig = Rig(refuse=lambda rate, device: True)
    rig.player.device = 5
    rig.player.load(counting(), 1000)
    with pytest.raises(OSError, match='device 5 at 1000'):
        rig.player.play()
    assert not rig.player.active and rig.streams == []


def test_a_device_that_cannot_be_described_still_gets_its_take_rate_tried():
    def unknown(device):
        raise OSError('Error querying device -1')

    rig = Rig()
    rig.player._own_rate = unknown
    rig.player.load(counting(), 1000)
    rig.player.play()
    assert rig.tried == [(1000, None)] and rig.player.playing
