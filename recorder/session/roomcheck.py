"""A check of the room and the microphone's level before a session.

It listens for a few seconds of quiet, then for one spoken sentence, and judges what it heard by the Check tab's own
measures and limits (analysis.room_check), so that the two never disagree.

Nothing it hears is kept. The input is opened for the check on its own, not through the recording: no spool is written,
no take is made, and once the measures are worked out the audio is let go. The input that was armed before the check is
armed again after it. Like the rest of the session there are no timers: `tick` moves on by the audio that has arrived,
and gives up by the injected clock if the device sends nothing.
"""
import time

from .. import audio_core as ac
from .. import capture as cap
from ..core import analysis

QUIET_SECONDS = 3.0                 # of the room alone
SPEAK_SECONDS = 15.0                # the longest wait for the sentence, from the end of the quiet
PAUSE_AFTER_SECONDS = 1.0           # the sentence has ended after this much quiet, once the voice has been heard
NO_AUDIO_SECONDS = 3.0              # a device that sends nothing for this long has stopped
SENTENCE = 'The harbour lights came on at dusk, and the boats drifted slowly back to the shore.'
REFUSALS = {'recording': 'Stop the recording before checking the room.',
            'source': 'Choose Microphone to check the room and the level.',
            'device': 'Choose a microphone first.'}


class RoomCheck:
    def __init__(self, bus, workspace, capturing, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.capturing = capturing
        self.clock = clock
        self.phase = None                   # 'quiet', then 'speak', while the check listens
        self.result = None                  # analysis.Room of the last check that ended with a result
        self.capture = None
        self._quiet_frames = 0              # where the quiet ends and the sentence starts
        self._was_armed = False
        self._last_audio = None             # when the audio last grew, by the clock
        self._frames_seen = 0

    @property
    def active(self):
        return self.phase is not None

    def refused(self):
        """Why the check cannot start now, or ''."""
        capturing = self.capturing
        if self.active:
            return ''
        if capturing.recording:
            return REFUSALS['recording']
        if capturing.source != 'mic':
            return REFUSALS['source']
        if capturing.device is None:
            return REFUSALS['device']
        return ''

    def start(self):
        """Open the microphone and listen. Returns False, with the reason in the status line, when it cannot."""
        if self.active:
            return True
        reason = self.refused()
        if reason:
            self.bus.status(reason)
            return False
        capturing = self.capturing
        self._was_armed = capturing.armed
        capturing.release_input()           # the device is the check's alone while it listens
        try:
            self.capture = cap.open_capture('mic', capturing.device, 0.0, capturing.channel)
            self.capture.start()            # into memory only: nothing takes its blocks to disk
        except Exception as e:
            self.capture = None
            self._rearm()
            self.bus.notice('error', 'Could not open the microphone', str(e))
            return False
        self.result = None
        self.phase = 'quiet'
        self._quiet_frames = 0
        self._frames_seen = 0
        self._last_audio = self.clock()
        self.bus.status('Checking the room: stay quiet for a moment.')
        self.bus.notify('room')
        return True

    def heard_seconds(self):
        """How much audio has arrived in the step going on."""
        capture = self.capture
        if capture is None:
            return 0.0
        return max(0.0, capture.elapsed - (self._quiet_frames / capture.rate if self.phase == 'speak' else 0.0))

    def tick(self):
        capture = self.capture
        if not self.active or capture is None:
            return
        self.capturing.level = tuple(capture.level)        # the meter shows what the check hears
        frames = int(round(capture.elapsed * capture.rate))
        now = self.clock()
        if frames != self._frames_seen:
            self._frames_seen, self._last_audio = frames, now
        if not capture.alive() or now - self._last_audio > NO_AUDIO_SECONDS:
            self.end('The microphone stopped sending sound, so the room was not checked.')
            return
        if self.phase == 'quiet':
            if capture.elapsed >= QUIET_SECONDS:
                self._quiet_frames = frames
                self.phase = 'speak'
                self.bus.status('Now read the sentence on the screen.')
                self.bus.notify('room')
            return
        rows = capture.loudness()
        rows = rows[rows[:, 0] > self._quiet_frames / capture.rate] if len(rows) else rows
        heard, quiet = analysis.live_pause(rows)
        if (heard and quiet >= PAUSE_AFTER_SECONDS) or self.heard_seconds() >= SPEAK_SECONDS:
            self._finish()

    def _finish(self):
        capture, split = self.capture, self._quiet_frames
        self.capture = None
        samples = capture.stop()
        capture.close()
        rate = capture.rate
        mono = ac.to_mono(samples)
        del samples
        try:
            self.result = analysis.room_check(mono[:split], mono[split:], rate,
                                              self.workspace.purpose.rate_for(rate))
        finally:
            del mono                        # what was heard goes here: only the measures are kept
        self.phase = None
        self._settle()
        self.bus.status('Room check done. Nothing it heard was kept.')
        self.bus.notify('room')

    def end(self, message=None):
        """Stop listening, without a result, and let the audio go."""
        if not self.active:
            return
        capture, self.capture = self.capture, None
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass
        self.phase = None
        self._settle()
        self.bus.status(message or 'Room check stopped. Nothing it heard was kept.')
        self.bus.notify('room')

    def _settle(self):
        self.capturing.level = (-120.0, -120.0)
        self._rearm()

    def _rearm(self):
        if self._was_armed and self.capturing.capture is None and not self.workspace.closing:
            self.capturing.toggle_arm()
        self._was_armed = False
