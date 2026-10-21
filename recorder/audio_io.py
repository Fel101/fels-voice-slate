"""Playback through a sounddevice output stream, with pause, seek, loop and a position taken from the audio itself.

The stream's callback copies from one buffer, and the position is how much of it has been handed to the sound card,
so the playhead cannot drift from what is heard (the earlier version estimated it from the wall clock).
"""
import threading

import numpy as np


def _default_stream(rate, callback):
    import sounddevice as sd
    return sd.OutputStream(samplerate=int(rate), channels=1, dtype='float32', callback=callback)


class Player:
    def __init__(self, stream_factory=None):
        self._factory = stream_factory or _default_stream
        self._lock = threading.Lock()
        self._stream = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._rate = 0
        self._pos = 0
        self._loop = None                   # (first, end) sample indices
        self._paused = False
        self._finished = False
        self.error = None

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
            self._buffer = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
            self._rate = int(rate)
            self._pos = 0
            self._loop = None
            self._finished = False

    def play(self, start=0.0, loop=None):
        """Play from `start` seconds. `loop` is (first, end) seconds to repeat, or None."""
        if not len(self._buffer):
            return
        self._close_stream()
        with self._lock:
            self._pos = min(max(int(start * self._rate), 0), len(self._buffer))
            self._loop = self._loop_samples(loop)
            self._paused = False
            self._finished = False
            self.error = None
        self._stream = self._factory(self._rate, self._callback)
        self._stream.start()

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
        if int(rate) != self._rate:
            raise ValueError('Both buffers must have the same sample rate')
        with self._lock:
            self._buffer = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
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
