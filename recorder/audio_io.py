"""Playback through a sounddevice output stream, with pause, seek, loop and a position taken from the audio itself.

The stream's callback copies from one buffer, and the position is how much of it has been handed to the sound card,
so the playhead cannot drift from what is heard (the earlier version estimated it from the wall clock).

An output does not always take the take's own rate. WASAPI opens only at the rate the device runs at unless Windows is
asked to convert, and some WDM-KS outputs refuse low rates. So the player tries the take's rate, then the device's own
rate with the buffer resampled to it, then Windows' default output, and Play is never dead while any output works.
"""
import threading

import numpy as np

from . import devices
from .audio_core import resample


def _default_stream(rate, callback, device=None):
    import sounddevice as sd
    info = sd.query_devices(device, 'output')
    extra = None
    if 'WASAPI' in sd.query_hostapis(info['hostapi'])['name']:
        extra = sd.WasapiSettings(auto_convert=True)        # without it WASAPI opens only at the device's own rate
    return sd.OutputStream(samplerate=int(rate), channels=1, dtype='float32', callback=callback, device=device,
                           extra_settings=extra)


def _own_rate(device):
    """The rate the output runs at by itself, as Windows reports it."""
    import sounddevice as sd
    return int(sd.query_devices(device, 'output')['default_samplerate'])


def list_outputs(every=False):
    """The names of the output devices as the microphone list shows them: each once, or with `every` through every Windows
    audio interface (see devices.py)."""
    return [shown for _, shown in devices.entries('output', every)]


def find_output(name, every=False):
    """The number of the output device called `name` in the list Settings shows, or None for Windows' default, also when
    that device has gone. A name kept by 0.6, with its interface after it, finds the same device (devices.match)."""
    if not name:
        return None
    try:
        return devices.match(name, devices.entries('output', every))
    except Exception:                       # no sound library, no devices: playing says so when it is tried
        return None


class Player:
    def __init__(self, stream_factory=None, own_rate=None):
        self.device = None                  # the output to play through; None is Windows' default
        self._factory = stream_factory or _default_stream       # (rate, callback, device) -> a stream not started yet
        self._own_rate = own_rate or _own_rate
        self._lock = threading.Lock()
        self._stream = None
        self._source = np.zeros(0, dtype=np.float32)            # what was loaded, at the rate it was loaded at
        self._source_rate = 0
        self._converted = {}                # the source resampled to a device's own rate, by that rate
        self._buffer = self._source         # what the callback plays: the source, or one of the converted copies
        self._rate = 0                      # the rate of _buffer, which is the rate the stream runs at
        self._pos = 0
        self._loop = None                   # (first, end) sample indices
        self._paused = False
        self._finished = False
        self.error = None
        self.fallback = None                # why the output chosen did not play the last Play, when Windows' default did

    # ---- state --------------------------------------------------------------------------------------------------------

    @property
    def position(self):
        """Seconds into the loaded buffer that have been handed to the sound card."""
        return self._pos / self._rate if self._rate else 0.0

    @property
    def seconds(self):
        return len(self._buffer) / self._rate if self._rate else 0.0

    @property
    def active(self):
        """True while a stream is open and the end has not been reached: playing, or paused."""
        return self._stream is not None and not self._finished

    @property
    def playing(self):
        return self.active and not self._paused

    @property
    def paused(self):
        return self.active and self._paused

    @property
    def finished(self):
        return self._finished

    @property
    def looping(self):
        return self._loop is not None

    # ---- control ------------------------------------------------------------------------------------------------------

    def load(self, samples, rate):
        """Replace the buffer (mono) and stop. The position goes back to the start."""
        self.stop()
        with self._lock:
            self._source = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
            self._source_rate = int(rate)
            self._converted = {}
            self._buffer, self._rate = self._source, self._source_rate
            self._pos = 0
            self._loop = None
            self._finished = False

    def play(self, start=0.0, loop=None):
        """Play from `start` seconds. `loop` is (first, end) seconds to repeat, or None.

        The position, the loop and seeking are all in seconds, so they stay right when the buffer has been resampled to
        what the output runs at. Raises the chosen output's own error when no output would play at all."""
        if not len(self._source):
            return
        self._close_stream()
        self.error = self.fallback = None
        first_error = None
        for device in ([self.device, None] if self.device is not None else [None]):
            for rate in self._rates_for(device):
                try:
                    self._open(device, rate, start, loop)
                except Exception as e:      # the sound library's own errors: a rate or a device it will not open
                    self._close_stream()
                    first_error = first_error or e
                    continue
                if device != self.device:
                    self.fallback = first_error
                return
        raise first_error

    def _rates_for(self, device):
        """The take's own rate, then the rate the device runs at, when that differs and can be found."""
        rates = [self._source_rate]
        try:
            own = int(self._own_rate(device))
        except Exception:                   # a device that cannot even be described will not open either
            own = 0
        if own > 0 and own != self._source_rate:
            rates.append(own)
        return rates

    def _open(self, device, rate, start, loop):
        buffer = self._at(rate)
        with self._lock:
            self._buffer, self._rate = buffer, rate
            self._pos = min(max(int(start * rate), 0), len(self._buffer))
            self._loop = self._loop_samples(loop)
            self._paused = False
            self._finished = False
        self._stream = self._factory(rate, self._callback, device)
        self._stream.start()

    def _at(self, rate):
        """The source at `rate`, resampled once and kept until the next load."""
        if rate == self._source_rate:
            return self._source
        if rate not in self._converted:
            self._converted[rate] = np.ascontiguousarray(resample(self._source, self._source_rate, rate))
        return self._converted[rate]

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        if not self.active:
            return
        with self._lock:
            self._paused = False

    def stop(self):
        """Stop and close the stream. The position stays where it was."""
        self._close_stream()
        with self._lock:
            self._paused = False

    def seek(self, seconds):
        """Move the position. A player that has finished stays finished: start it again with play()."""
        with self._lock:
            self._pos = min(max(int(seconds * self._rate), 0), len(self._buffer))

    def set_loop(self, loop):
        with self._lock:
            self._loop = self._loop_samples(loop)

    def swap(self, samples, rate, seconds):
        """Replace the buffer while playing (for A/B) and continue from `seconds` of the new one."""
        if int(rate) != self._source_rate:
            raise ValueError('Both buffers must have the same sample rate')
        source = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
        playing = source if self._rate == self._source_rate else np.ascontiguousarray(
            resample(source, self._source_rate, self._rate))
        with self._lock:
            self._source, self._converted = source, {} if playing is source else {self._rate: playing}
            self._buffer = playing
            self._pos = min(max(int(seconds * self._rate), 0), len(self._buffer))
            self._loop = None

    def _loop_samples(self, loop):
        if not loop:
            return None
        first, end = max(int(loop[0] * self._rate), 0), min(int(loop[1] * self._rate), len(self._buffer))
        return (first, end) if end - first > 0 else None

    def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:           # a device that has gone away must not stop the window
                self.error = e

    # ---- the audio thread ---------------------------------------------------------------------------------------------

    def _callback(self, outdata, frames, time_info, status):
        out = outdata[:, 0] if outdata.ndim == 2 else outdata
        reached_end = False
        with self._lock:
            if self._paused or not len(self._buffer):
                out[:] = 0.0
                return
            buffer, pos, loop = self._buffer, self._pos, self._loop
            end = loop[1] if loop else len(buffer)
            filled = 0
            while filled < frames:
                if pos >= end:
                    if loop:
                        pos = loop[0]
                    else:
                        break
                count = min(frames - filled, end - pos)
                out[filled:filled + count] = buffer[pos:pos + count]
                filled += count
                pos += count
            out[filled:] = 0.0
            self._pos = pos
            if filled < frames:
                self._finished = reached_end = True
        if reached_end:
            from sounddevice import CallbackStop
            raise CallbackStop
