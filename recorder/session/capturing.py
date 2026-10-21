"""Recording: devices, start, stop with a tail, and what happens when a device fails or the take reaches 40 s.

There are no timers here. The window calls `Session.tick()` about twenty times a second, and `tick` checks the meter,
the device and the stop deadline against an injectable clock, so the whole state machine runs in tests with no
toolkit and no sleeping.
"""
import time

from .. import audio_core as ac
from .. import capture as cap
from .. import settings

PRE_ROLL_SECONDS = 0.5

STATES = {
    'idle': 'Idle',
    'armed': 'Armed (meter live, pre-roll on)',
    'recording': 'Recording',
    'stopping': 'Stopping',
    'stopped': 'Stopped',
    'interrupted': 'Interrupted (kept what was captured)',
    'error': 'Error',
}


class Capturing:
    def __init__(self, bus, workspace, script, settings_file=None, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.script = script
        self.settings_file = settings_file
        self.clock = clock
        self.capture = None
        self.source = 'mic'
        self.devices = []                   # [(index, label)] for the current source
        self.device = None                  # index of the chosen device
        self.interrupted = False
        self.finishing = False
        self.state = 'idle'
        self.level = (-120.0, -120.0)       # peak and RMS in dBFS, from the last tick
        self.elapsed = 0.0
        self.take_script_line = None
        self.tail_seconds = settings.tail_seconds(settings.load(settings_file))
        self._stop = None                   # (capture, deadline) while a stop tail is pending

    # ---- state --------------------------------------------------------------------------------------------------------

    @property
    def recording(self):
        return self.capture is not None and self.capture.recording

    @property
    def armed(self):
        return self.capture is not None and self.capture.armed and not self.capture.recording

    @property
    def state_text(self):
        return STATES[self.state]

    def _set_state(self, state):
        self.state = state
        self.bus.notify('recording')

    def set_tail(self, value):
        """The stop tail in seconds, kept between 0 and 1. Anything that is not a number gives the default."""
        self.tail_seconds = settings.tail_seconds({'tail_seconds': value})
        settings.update({'tail_seconds': self.tail_seconds}, self.settings_file)
        self.bus.notify('settings')

    # ---- devices ------------------------------------------------------------------------------------------------------

    def set_source(self, source):
        if source == self.source:
            return
        self.source = source
        self.refresh_devices()
        if self.capture is not None and not self.recording:
            self.close_capture()

    def refresh_devices(self):
        try:
            if self.source == 'mic':
                options, default = cap.list_microphones(), cap.default_microphone()
            else:
                options, default = cap.list_loopbacks(), cap.default_loopback()
        except Exception as e:                  # a device library error is shown, not raised
            options, default = [], None
            self.bus.status(f'Could not list devices: {e}')
        self.devices = list(options)
        indexes = [index for index, _ in self.devices]
        self.device = default if default in indexes else (indexes[0] if indexes else None)
        self.bus.notify('devices')

    def select_device(self, index):
        """A device was picked from the list. Keep that choice; an idle capture is released so the next start uses it."""
        self.device = index
        if self.capture is not None and not self.recording:
            self.close_capture()
            self._set_state('idle')
        self.bus.notify('devices')

    def device_label(self):
        return next((label for index, label in self.devices if index == self.device), '')

    def info(self):
        """What the window shows about the take being made. The rate and channels are known once a device is open."""
        capture = self.capture
        return {'device': self.device_label(), 'rate': capture.rate if capture is not None else None,
                'channels': capture.channels if capture is not None else None,
                'pre_roll': PRE_ROLL_SECONDS if self.source == 'mic' else 0.0, 'tail': self.tail_seconds}

    def envelope(self):
        """Rows of (end second, lowest, highest) of what is being recorded, or None when nothing is."""
        capture = self.capture
        return capture.envelope() if capture is not None and capture.recording else None

    # ---- the capture --------------------------------------------------------------------------------------------------

    def _ensure_capture(self):
        if self.capture is None:
            if self.device is None:
                raise ValueError('Choose a device first.')
            self.capture = cap.open_capture(self.source, self.device,
                                            PRE_ROLL_SECONDS if self.source == 'mic' else 0.0)
        return self.capture

    def toggle(self):
        if self.recording:
            self.stop()
        else:
            self.start()

    def toggle_arm(self):
        if self.armed:
            self.close_capture()
            self._set_state('idle')
            return
        try:
            self._ensure_capture().arm()
        except Exception as e:
            self.bus.notice('error', 'Could not arm', str(e))
            self.capture = None
            return
        self._set_state('armed')

    def start(self):
        if self.recording:
            return False
        self._cancel_stop()
        if not self.workspace.speaker:
            self.workspace.ensure_speaker()
        try:
            capture = self._ensure_capture()
            self.take_script_line = self.script.current
            self.interrupted = False
            capture.start()
        except Exception as e:
            self.bus.notice('error', 'Could not start recording', str(e))
            self.close_capture()
            return False
        self._set_state('recording')
        self.bus.status('Recording. Press F9 or Stop when finished.')
        return True

    def stop(self):
        """Begin the stop: the take ends once the tail has passed (see tick)."""
        if self.finishing or self.capture is None or not self.capture.recording:
            return
        self._stop = (self.capture, self.clock() + self.tail_seconds)
        self.finishing = True
        self._set_state('stopping')

    def _cancel_stop(self):
        self._stop = None
        self.finishing = False

    def tick(self):
        """The meter, the elapsed time, and the three ways a take ends by itself: the tail passes, the device stops,
        or 40 s is reached."""
        capture = self.capture
        if capture is None:
            self._cancel_stop()
            return
        self.level = tuple(capture.level)
        if not capture.recording:
            return
        self.elapsed = max(capture.elapsed, capture.wall_elapsed)       # the clock keeps counting if the device stops
        if not capture.alive() or capture.stalled():
            self.interrupted = True
            self.finish_take()
        elif self.elapsed >= ac.MAX_SECONDS:
            self.finish_take(message='Stopped at 40 s, the longest a reference can be.')
        elif self._stop is not None:
            pending, deadline = self._stop
            if pending is not capture:
                self._cancel_stop()             # a tail left over from another capture can never end this one
            elif self.clock() >= deadline:
                self.finish_take()

    def capture_failed(self, error):
        """An error while a device was open. Save what a running take captured, and always release the device."""
        capture = self.capture
        if capture is None:
            self.bus.status(f'Capture error: {error}')
            return
        if not capture.recording:
            self.close_capture()
            self._set_state('error')
            self.bus.status(f'Capture error: {error}. The device was released.')
            return
        self.interrupted = True
        try:
            self.finish_take()
            self.bus.status(f'Capture error: {error}. The audio captured before the error was saved.')
        except Exception as e:
            self.close_capture()
            self._set_state('error')
            self.bus.status(f'Capture error: {error}. The take could not be saved: {e}')

    def finish_take(self, message=None):
        self._cancel_stop()
        capture = self.capture
        if capture is None or not capture.recording:
            return None
        samples = capture.stop()
        close_error = capture.close_error
        rate = capture.rate
        device = {'name': capture.backend.name, 'hostapi': capture.backend.hostapi, 'native_rate': rate,
                  'channels': capture.channels}
        kind = capture.backend.kind
        self.close_capture()
        flags = ['interrupted'] if self.interrupted else []
        take = self.workspace.add_take(samples, rate, kind, device, flags, script_line=self.take_script_line)
        if take is None:
            self._set_state('idle')
            return None
        self._set_state('interrupted' if self.interrupted else 'stopped')
        if take.unsaved:
            self.bus.status(f'The original could not be written to disk ({take.save_error}). The take is still in '
                            'memory: free up space, then press Keep to write it.')
        elif close_error is not None:
            self.bus.status(f'Saved. The device reported an error as it stopped: {close_error}')
        else:
            self.bus.status(message or 'Take saved. Review it, then Keep.')
        return take

    def close_capture(self):
        self._cancel_stop()
        if self.capture is not None:
            try:
                self.capture.close()
            except Exception:
                pass
        self.capture = None
        self.level = (-120.0, -120.0)
        self.bus.notify('recording')
