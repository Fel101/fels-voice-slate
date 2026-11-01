"""Capture from a microphone (sounddevice) or from a Windows output's loopback (PyAudioWPatch).

WASAPI loopback only delivers audio while something plays, so a silent output stream on the same endpoint keeps the
loopback fed. Without it the capture callback never runs and the take is empty. In testing with the feed, a 1 kHz
test tone at -36 dBFS came back at 1000 Hz and about -43 dBFS RMS over the whole capture.

Libraries are imported lazily so the audio core and the tests run without audio hardware.
"""
import collections
import threading
import time

import numpy as np

# A device that has sent nothing for this long has stopped. Before its first block we allow more time,
# because a cold start can take a moment.
STALL_SECONDS = 2.0
FIRST_BLOCK_SECONDS = 10.0


def _mono_levels(x):
    if x.size == 0:
        return -120.0, -120.0
    m = x.mean(axis=1) if x.ndim == 2 else x
    peak = float(np.max(np.abs(m)))
    rms = float(np.sqrt(np.mean(m.astype(np.float64) ** 2)))
    return 20 * np.log10(max(peak, 1e-6)), 20 * np.log10(max(rms, 1e-6))


def _span(x):
    """(lowest, highest) sample of a block's mono mix, for the live waveform."""
    m = x.mean(axis=1) if x.ndim == 2 else x
    return float(m.min()), float(m.max())


# ---- device lists ---------------------------------------------------------------------------------------------------

def list_microphones():
    """[(index, label)] for input devices, host API included (the Windows device names repeat across APIs)."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0:
            out.append((i, f"{d['name']}  [{apis[d['hostapi']]['name']}]"))
    return out


def default_microphone():
    import sounddevice as sd
    index = sd.default.device[0]
    return index if isinstance(index, int) and index >= 0 else None


def list_loopbacks():
    """[(index, label)] for WASAPI loopback devices: one per output endpoint."""
    import pyaudiowpatch as pa
    p = pa.PyAudio()
    try:
        return [(d['index'], d['name']) for d in p.get_loopback_device_info_generator()]
    finally:
        p.terminate()


def default_loopback():
    import pyaudiowpatch as pa
    p = pa.PyAudio()
    try:
        return p.get_default_wasapi_loopback()['index']
    finally:
        p.terminate()


# ---- backends -------------------------------------------------------------------------------------------------------

class _MicBackend:
    kind = 'mic'

    def __init__(self, device):
        import sounddevice as sd
        self._sd = sd
        info = sd.query_devices(device, 'input')
        self.device = int(device)
        self.name = info['name']
        self.rate = int(info['default_samplerate'])
        self.channels = max(1, min(2, int(info['max_input_channels'])))
        self.hostapi = sd.query_hostapis(info['hostapi'])['name']
        self._stream = None

    def open(self, on_block):
        def callback(indata, frames, time_info, status):
            on_block(np.array(indata, dtype=np.float32), bool(status.input_overflow or status.input_underflow))

        self._stream = self._sd.InputStream(device=self.device, samplerate=self.rate, channels=self.channels,
                                            dtype='float32', callback=callback)
        self._stream.start()

    def alive(self):
        return bool(self._stream is not None and self._stream.active)

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


class _LoopbackBackend:
    kind = 'system'

    def __init__(self, device):
        import pyaudiowpatch as pa
        self._pa = pa
        self._p = pa.PyAudio()
        try:
            info = self._p.get_device_info_by_index(int(device))
            if not info.get('isLoopbackDevice'):
                raise ValueError('That device is not a loopback device')
            self.device = int(device)
            self.name = info['name']
            self.rate = int(info['defaultSampleRate'])
            self.channels = int(info['maxInputChannels'])
            self.hostapi = 'Windows WASAPI'
            self._out_index, self._out_channels = self._feed_output()
        except Exception:
            self._p.terminate()
            raise
        self._stream = None
        self._out = None
        self._feed = None
        self._stop_feed = threading.Event()

    def _feed_output(self):
        """The output endpoint behind this loopback, found by name; the default output when the name does not match."""
        pa = self._pa
        wasapi = self._p.get_host_api_info_by_type(pa.paWASAPI)['index']
        base = self.name.replace(' [Loopback]', '')
        for i in range(self._p.get_device_count()):
            d = self._p.get_device_info_by_index(i)
            if d['hostApi'] == wasapi and d['maxOutputChannels'] > 0 and d['name'] == base:
                return i, min(2, int(d['maxOutputChannels']))
        d = self._p.get_default_output_device_info()
        return int(d['index']), min(2, int(d['maxOutputChannels']))

    def open(self, on_block):
        pa = self._pa
        self._out = self._p.open(format=pa.paInt16, channels=self._out_channels, rate=self.rate, output=True,
                                 output_device_index=self._out_index, frames_per_buffer=1024)
        self._stop_feed.clear()
        self._feed = threading.Thread(target=self._keep_fed, name='loopback-feed', daemon=True)
        self._feed.start()

        def callback(in_data, frame_count, time_info, status):
            x = np.frombuffer(in_data, dtype='<i2').astype(np.float32) / 32768.0
            on_block(x.reshape(-1, self.channels), bool(status & (pa.paInputOverflow | pa.paInputUnderflow)))
            return None, pa.paContinue

        self._stream = self._p.open(format=pa.paInt16, channels=self.channels, rate=self.rate, input=True,
                                    input_device_index=self.device, frames_per_buffer=1024,
                                    stream_callback=callback)
        self._stream.start_stream()

    def _keep_fed(self):
        silence = b'\x00' * (1024 * self._out_channels * 2)
        while not self._stop_feed.is_set():
            try:
                self._out.write(silence)
            except Exception:
                return

    def alive(self):
        return bool(self._stream is not None and self._stream.is_active())

    def close(self):
        self._stop_feed.set()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            finally:
                self._stream = None
        if self._feed is not None:
            self._feed.join(timeout=2)
            self._feed = None
        if self._out is not None:
            try:
                self._out.stop_stream()
                self._out.close()
            finally:
                self._out = None

    def terminate(self):
        self.close()
        self._p.terminate()


# ---- the capture ------------------------------------------------------------------------------------------------------

class Capture:
    """One take in progress. For a microphone, `arm()` opens the input and keeps a pre-roll buffer before `start()`."""

    def __init__(self, backend, pre_roll_seconds=0.0):
        self.backend = backend
        self.pre_roll = float(pre_roll_seconds) if backend.kind == 'mic' else 0.0
        self._lock = threading.Lock()
        self._blocks = []
        self._ring = collections.deque()
        self._ring_frames = 0
        self._recording = False
        self._frames = 0
        self._columns = []                     # (frames so far, lowest, highest) per recorded block: the live waveform
        self._spooled = 0                      # how many of the blocks new_blocks has handed out
        self.dropouts = 0                      # how many times the device said it lost audio during this take
        self.level = (-120.0, -120.0)
        self.armed = False
        self.close_error = None
        self._started = None
        self._last_block = None

    @property
    def rate(self):
        return self.backend.rate

    @property
    def channels(self):
        return self.backend.channels

    @property
    def recording(self):
        return self._recording

    @property
    def elapsed(self):
        return self._frames / self.rate if self.rate else 0.0

    def envelope(self):
        """What has been recorded so far, for drawing: an array of rows (end second, lowest, highest), one per block."""
        with self._lock:
            columns = list(self._columns)
        if not columns or not self.rate:
            return np.zeros((0, 3), dtype=np.float32)
        out = np.array(columns, dtype=np.float64)
        out[:, 0] /= self.rate
        return out.astype(np.float32)

    def _on_block(self, x, lost=False):
        """A block from the device. `lost` says the device lost audio just before it. The first block of a stream can say
        that about nothing but the stream starting, so it is not counted."""
        levels = _mono_levels(x)
        with self._lock:
            self.level = levels
            first = self._last_block is None
            self._last_block = time.monotonic()
            if self._recording:
                self.dropouts += bool(lost and not first)
                self._blocks.append(x)
                self._frames += len(x)
                if len(x):
                    self._columns.append((self._frames, *_span(x)))
            elif self.pre_roll > 0:
                self._ring.append(x)
                self._ring_frames += len(x)
                while self._ring and self._ring_frames - len(self._ring[0]) >= self.pre_roll * self.rate:
                    self._ring_frames -= len(self._ring.popleft())

    def arm(self):
        if not self.armed:
            self.backend.open(self._on_block)
            self.armed = True

    def start(self):
        with self._lock:
            self._blocks = list(self._ring)
            self._frames = self._ring_frames
            self._columns, frames = [], 0
            for block in self._blocks:                    # the pre-roll is part of the take, so it is drawn too
                frames += len(block)
                if len(block):
                    self._columns.append((frames, *_span(block)))
            self._ring.clear()
            self._ring_frames = 0
            self._spooled = 0
            self.dropouts = 0
            self._recording = True
            self._started = time.monotonic()
            self._last_block = None
        if not self.armed:
            self.backend.open(self._on_block)
            self.armed = True

    def new_blocks(self):
        """The blocks recorded since the last call, for writing to disk as the take goes on."""
        with self._lock:
            blocks = self._blocks[self._spooled:]
            self._spooled = len(self._blocks)
        return blocks

    def alive(self):
        return self.backend.alive()

    def stalled(self):
        """True while recording and the device has sent nothing for too long: it has stopped."""
        with self._lock:
            if not self._recording or self._started is None:
                return False
            last_block, started = self._last_block, self._started
        now = time.monotonic()
        if last_block is None:
            return now - started > FIRST_BLOCK_SECONDS
        return now - last_block > STALL_SECONDS

    @property
    def wall_elapsed(self):
        """Seconds since recording started, by the clock. It keeps counting when the device stops sending."""
        started = self._started
        return 0.0 if started is None else time.monotonic() - started

    def stop(self):
        """Stop the device and return the master samples, shape (N,) or (N, channels), float32.

        The audio is already in memory, so a device that fails to close (an unplugged headset, say) must not cost
        it. The error is kept in `close_error` and the samples are returned regardless.
        """
        with self._lock:
            self._recording = False
        self.close_error = None
        try:
            self.backend.close()
        except Exception as e:
            self.close_error = e
        self.armed = False
        with self._lock:
            blocks, self._blocks = self._blocks, []
        if not blocks:
            return np.zeros((0, self.channels), dtype=np.float32)
        x = np.concatenate(blocks).astype(np.float32)
        return x[:, 0] if self.channels == 1 else x

    def close(self):
        if self.armed or self._recording:
            self.stop()
        if hasattr(self.backend, 'terminate'):
            self.backend.terminate()


def open_capture(source, device, pre_roll_seconds=0.0):
    """Build a Capture for 'mic' (sounddevice input index) or 'system' (PyAudioWPatch loopback index)."""
    if source == 'mic':
        backend = _MicBackend(device)
    elif source == 'system':
        backend = _LoopbackBackend(device)
    else:
        raise ValueError(f'Unknown source: {source}')
    return Capture(backend, pre_roll_seconds)


if __name__ == '__main__':
    import sys
    print('microphones:')
    for index, label in list_microphones():
        print(f'  {index}: {label}')
    print('loopbacks:')
    for index, label in list_loopbacks():
        print(f'  {index}: {label}')
    if len(sys.argv) > 1 and sys.argv[1] in ('mic', 'system'):
        seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
        if len(sys.argv) > 3:
            device = int(sys.argv[3])
        else:
            device = default_microphone() if sys.argv[1] == 'mic' else default_loopback()
        cap = open_capture(sys.argv[1], device)
        cap.start()
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            time.sleep(0.25)
        x = cap.stop()
        cap.close()
        peak, rms = _mono_levels(x)
        print(f'{sys.argv[1]} capture: {len(x) / cap.rate:.2f} s at {cap.rate} Hz, {cap.channels} ch, '
              f'peak {peak:.1f} dBFS, rms {rms:.1f} dBFS')
