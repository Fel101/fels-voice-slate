"""Shared synthetic audio and stand-ins for the tests. Nothing here needs a microphone, a speaker or a display."""
from types import SimpleNamespace

import numpy as np
from scipy.signal.windows import tukey

RATE = 48000


def speech(seconds, rate=RATE, amp=0.2, freq=180.0):
    """A steady voiced-like tone with a slow level wobble."""
    t = np.arange(int(round(seconds * rate))) / rate
    return (amp * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def bursts(seconds, spans, rate=RATE, amp=0.2, noise=0.0005, seed=7, f0=180.0):
    """Voiced-like bursts (a harmonic series on `f0`, 30 ms ramps) at the given (start, end) seconds over a noise floor.

    Each burst has the RMS of a sine of amplitude `amp`, so `amp=0.14` puts the speech near -20 dB.
    """
    n = int(round(seconds * rate))
    x = np.random.default_rng(seed).normal(0, noise, n).astype(np.float32)
    for a, b in spans:
        i, j = int(a * rate), int(b * rate)
        t = np.arange(j - i) / rate
        voiced = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 41))
        voiced = voiced / np.sqrt(np.mean(voiced ** 2)) * amp / np.sqrt(2)
        x[i:j] += (voiced * tukey(j - i, alpha=min(1.0, 0.06 / (b - a)))).astype(np.float32)
    return x


def with_pop(x, at=7.42, amp=0.9, rate=RATE):
    """The same clip with one loud, very short pop."""
    y = x.copy()
    i = int(at * rate)
    y[i:i + 70] += np.hanning(70).astype(np.float32) * amp
    return y


def tone(seconds, freq=220.0, amp=0.3, rate=RATE):
    t = np.arange(int(round(seconds * rate))) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeCapture:
    """Stands in for a running Capture. The test decides how the device and the meter behave."""

    def __init__(self, samples=None, level_error=None, alive=True, rate=RATE, kind='system'):
        self.recording = True
        self.armed = True
        self.rate = rate
        self.channels = 1
        self.elapsed = 1.0
        self.close_error = None
        self.stopped = False
        self.closed = False
        self._level_error = level_error
        self._alive = alive
        self._samples = samples if samples is not None else speech(1.0)
        self.backend = SimpleNamespace(name='fake device', hostapi='test', kind=kind)
        self.stall = False
        self.wall = 1.0

    @property
    def level(self):
        if self._level_error is not None:
            raise self._level_error
        return (-20.0, -30.0)

    def alive(self):
        return self._alive

    def envelope(self):
        """One row per 20 ms of the samples, as the real capture reports what it has recorded."""
        x = np.asarray(self._samples)
        x = x.mean(axis=1) if x.ndim == 2 else x
        step = max(1, int(self.rate * 0.02))
        ends = np.arange(step, len(x) + step, step)
        rows = [(min(end, len(x)) / self.rate, float(x[end - step:end].min()), float(x[end - step:end].max()))
                for end in ends if len(x[end - step:end])]
        return np.array(rows, dtype=np.float32).reshape(-1, 3)

    def stalled(self):
        return self.stall

    @property
    def wall_elapsed(self):
        return self.wall

    def start(self):
        self.recording = True

    def arm(self):
        self.armed = True

    def stop(self):
        self.stopped = True
        self.recording = False
        return self._samples

    def close(self):
        self.closed = True


class Bin:
    """Stands in for the Recycle Bin: remembers what was sent there, and removes the files."""

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, paths):
        from recorder.store import TrashUnavailable
        if self.fail:
            raise TrashUnavailable(self.fail)
        self.sent.append([p.name for p in paths])
        for path in paths:
            path.unlink()


class FakePlayer:
    """Stands in for audio_io.Player: records what the transport asks for, and plays nothing."""

    def __init__(self):
        self.buffer = np.zeros(0, np.float32)
        self.rate = 0
        self.pos = 0.0
        self.loop = None
        self._open = self._paused = self.finished = False
        self.log = []

    @property
    def position(self):
        return self.pos

    @property
    def seconds(self):
        return len(self.buffer) / self.rate if self.rate else 0.0

    @property
    def active(self):
        return self._open and not self.finished

    @property
    def playing(self):
        return self.active and not self._paused

    @property
    def paused(self):
        return self.active and self._paused

    @property
    def looping(self):
        return self.loop is not None

    def load(self, samples, rate):
        self.buffer, self.rate, self.pos, self._open, self._paused, self.finished = samples, rate, 0.0, False, False, False
        self.log.append(('load', len(samples), rate))

    def play(self, start=0.0, loop=None):
        self.pos, self.loop, self._open, self._paused, self.finished = start, loop, True, False, False
        self.log.append(('play', start, loop))

    def pause(self):
        self._paused = True
        self.log.append(('pause',))

    def resume(self):
        self._paused = False
        self.log.append(('resume',))

    def stop(self):
        self._open = self._paused = False
        self.log.append(('stop',))

    def seek(self, seconds):
        self.pos = seconds
        self.log.append(('seek', seconds))

    def set_loop(self, loop):
        self.loop = loop
        self.log.append(('loop', loop))

    def swap(self, samples, rate, seconds):
        self.buffer, self.pos, self.loop = samples, seconds, None
        self.log.append(('swap', len(samples), seconds))


class Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HeldJobs:
    """Background jobs that wait until the test runs them, so it can decide the order they finish in."""

    def __init__(self):
        self.queue = []
        self.on_unhandled = None

    @property
    def pending(self):
        return len(self.queue)

    def submit(self, fn, on_done, on_error=None):
        self.queue.append((fn, on_done, on_error))

    def run(self, index):
        """Run one held job and deliver its result straight away."""
        fn, on_done, on_error = self.queue[index]
        try:
            result = fn()
        except Exception as e:
            if on_error is not None:
                on_error(e)
            return
        on_done(result)

    def drain(self):
        return 0

    def close(self):
        pass


def fill(session, transcript='The harbour lights came on at dusk.', consent=True):
    """Enter the text fields of the current take, as the window does while someone types."""
    session.workspace.set_meta(transcript=transcript, origin='recorded in my room',
                               allowed_use='my own voice references', consent_confirmed=consent)


def record(session, seconds=12.0, source='mic', samples=None, **kwargs):
    """Put a finished capture into the session, as Stop does. Returns the new take."""
    return session.workspace.add_take(speech(seconds) if samples is None else samples, RATE, source, {}, **kwargs)


def settle(session):
    """Deliver every finished background job."""
    session.jobs.drain()
