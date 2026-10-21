"""Synthetic speech-like audio for screenshots and demonstrations. No recording of any person is involved.

The take is a set of syllable-like bursts with a moving pitch, some fricative hiss, a faint 60 Hz hum, one click and one
loud syllable, so every tool of the editor has something to show.
"""
import math

import numpy as np
from scipy import signal

RATE = 48000


def syllables():
    """(start second, length, amplitude, has an 's' sound after it) for each burst."""
    return [(0.50, 0.38, 0.55, 0), (0.95, 0.30, 0.38, 0), (1.32, 0.46, 0.62, 1), (1.85, 0.36, 0.45, 0),
            (2.28, 0.52, 0.58, 0), (2.88, 0.32, 0.40, 1), (3.28, 0.44, 0.52, 0), (3.80, 0.56, 0.66, 0),
            (5.42, 0.36, 0.44, 0), (5.86, 0.48, 0.60, 1), (6.42, 0.34, 0.38, 0), (6.84, 0.44, 0.50, 0),
            (7.72, 0.40, 0.48, 0), (8.20, 0.52, 0.62, 0), (8.82, 0.34, 0.40, 1), (9.26, 0.38, 0.46, 0),
            (9.70, 0.34, 0.50, 0), (10.10, 0.52, 0.95, 0), (10.72, 0.38, 0.45, 0), (11.18, 0.44, 0.55, 1),
            (11.70, 0.38, 0.48, 0), (12.20, 0.46, 0.52, 0), (12.72, 0.50, 0.58, 0)]


def take(seconds=14.0, rate=RATE, level=0.55, seed=11, noise=0.0022):
    """About `seconds` of quiet-ish speech-like audio at `rate`, as float32 in -1..1. `noise` is the room noise's RMS."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    t = np.arange(n) / rate
    f0 = 112 + 15 * np.sin(2 * np.pi * 0.31 * t) + 6 * np.sin(2 * np.pi * 1.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    voice = np.zeros(n)
    for h in range(1, 36):
        freq = h * 112.0
        gain = (h ** -0.9 + 0.7 * math.exp(-((freq - 700) / 220) ** 2) + 0.5 * math.exp(-((freq - 1750) / 320) ** 2)
                + 0.15 * math.exp(-((freq - 3100) / 500) ** 2))
        voice += gain * np.sin(h * phase)
    voice /= np.max(np.abs(voice))
    hiss = signal.lfilter(*signal.butter(4, 4200 / (rate / 2), 'high'), rng.normal(size=n))
    hiss /= np.max(np.abs(hiss))
    env, fric = np.zeros(n), np.zeros(n)
    for start, length, amp, has_s in syllables():
        a, b = int(start * rate), min(n, int((start + length) * rate))
        if b - a < 4:
            continue
        env[a:b] = np.maximum(env[a:b], np.hanning(b - a) * amp)
        if has_s:
            c, d = max(0, b - int(0.05 * rate)), min(n, b + int(0.09 * rate))
            fric[c:d] = np.hanning(d - c) * 0.16
    breath_a, breath_b = int(4.62 * rate), min(n, int(5.18 * rate))
    if breath_b - breath_a > 4:
        fric[breath_a:breath_b] += np.hanning(breath_b - breath_a) * 0.07
    x = (level * (voice * env + hiss * fric) + 0.004 * np.sin(2 * np.pi * 60 * t) + 0.0016 * np.sin(2 * np.pi * 180 * t)
         + rng.normal(0, noise, n))
    click = int(7.42 * rate)
    if click + 70 < n:
        x[click:click + 70] += np.hanning(70) * 0.42
    return np.clip(x, -1.0, 1.0).astype(np.float32)


class SyntheticCapture:
    """Stands in for a running capture, so a screenshot can show the recording view without any audio device."""

    def __init__(self, seconds=12.48, rate=RATE):
        self.rate = rate
        self.channels = 1
        self.recording = True
        self.armed = True
        self.close_error = None
        self.backend = type('Backend', (), {'name': 'Synthetic input', 'hostapi': 'demo', 'kind': 'mic'})()
        self._samples = take(max(seconds, 14.0), rate)[:int(seconds * rate)]
        self.elapsed = self.wall_elapsed = float(seconds)

    @property
    def level(self):
        return (-7.0, -19.5)

    def alive(self):
        return True

    def stalled(self):
        return False

    def envelope(self):
        step = int(self.rate * 0.02)
        ends = np.arange(step, len(self._samples) + 1, step)
        rows = [(end / self.rate, float(self._samples[end - step:end].min()), float(self._samples[end - step:end].max()))
                for end in ends]
        return np.array(rows, dtype=np.float32).reshape(-1, 3)

    def start(self):
        self.recording = True

    def arm(self):
        self.armed = True

    def stop(self):
        self.recording = False
        return self._samples

    def close(self):
        pass


class NullPlayer:
    """A player that plays nothing, for screenshots and tests of the window."""

    def __init__(self):
        self.buffer = np.zeros(0, np.float32)
        self.rate = 0
        self.position = 0.0
        self.seconds = 0.0
        self.active = self.playing = self.paused = self.finished = self.looping = False

    def load(self, samples, rate):
        self.buffer, self.rate = samples, rate

    def play(self, start=0.0, loop=None):
        self.position, self.active, self.playing = start, True, True

    def pause(self):
        self.playing, self.paused = False, True

    def resume(self):
        self.playing, self.paused = True, False

    def stop(self):
        self.active = self.playing = self.paused = False

    def seek(self, seconds):
        self.position = seconds

    def set_loop(self, loop):
        self.looping = loop is not None

    def swap(self, samples, rate, seconds):
        self.buffer, self.position = samples, seconds
