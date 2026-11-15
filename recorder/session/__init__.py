"""The session: everything the recorder does, with no window in it.

`Session` wires the parts together. A window drives it by calling its methods and `tick()` (about twenty times a
second), and draws whatever the bus topics say changed. Nothing here imports a toolkit, so all of it is tested
without a display, a microphone or a speaker.

- `workspace`: speakers, takes, the current take, its text fields, keep, discard, close
- `capturing`: devices and the recording state machine
- `editor`: selection, playhead, every edit, the chain settings, undo
- `transport`: playback, pause, seek, loop, A/B
- `drafts`: transcript drafts from the optional speech recognizer
- `autosave`: the work that Keep has not written yet, saved beside the originals as it happens
- `measure`: background measurements, the spectrogram and the peaks for drawing
- `advisor`: Auto analyze (what could be better, in plain words) and Auto fix (one undo step)
- `script`: script mode
- `run`: reading the script hands-free: the next line, the end of a take after a pause, and a count-in
- `batch`: keeping, discarding or moving many takes, a slice at a time
"""
import time
from pathlib import Path

from .. import clean, settings
from ..i18n import tr
from ..jobs import Jobs
from ..store import Library
from .advisor import Advisor
from .autosave import Autosave
from .batch import Batch
from .bus import Bus
from .capturing import Capturing
from .drafts import Drafts
from .editor import Editor
from .measure import Measurements
from .roomcheck import RoomCheck
from .run import ScriptRun
from .script import FILE_NAME, Script
from .transport import Transport
from .workspace import Workspace


class Session:
    def __init__(self, settings_file=None, jobs=None, player=None, clock=time.monotonic, library=None):
        self.settings_file = settings_file or settings.settings_path()
        data = settings.load(self.settings_file)
        clean.set_ffmpeg_path(data.get('ffmpeg'))
        self.bus = Bus()
        self.jobs = jobs or Jobs()
        self.jobs.on_unhandled = lambda error: self.bus.status(tr('A background task failed: {error}').format(error=error))
        self.workspace = Workspace(self.bus, library or Library(data.get('root') or settings.default_root()),
                                   self.settings_file, is_recording=lambda: self.capturing.recording)
        self.jobs.front = lambda: self.workspace.current        # its measurements and its draft go first
        self.script = Script(self.bus, Path(self.settings_file).with_name(FILE_NAME))
        self.capturing = Capturing(self.bus, self.workspace, self.script, self.settings_file, clock)
        self.editor = Editor(self.bus, self.workspace)
        self.transport = Transport(self.bus, self.workspace, self.editor, player, clock)
        self.run = ScriptRun(self.bus, self.workspace, self.script, self.capturing, self.settings_file, clock)
        self.room = RoomCheck(self.bus, self.workspace, self.capturing, clock)
        self.transport.set_output(data.get('output_device'), settings.every_interface(data))
        self.drafts = Drafts(self.bus, self.workspace, self.jobs, self.settings_file, clock)
        self.autosave = Autosave(self.bus, self.workspace, clock)
        self.measure = Measurements(self.bus, self.workspace, self.jobs)
        self.advisor = Advisor(self.bus, self.workspace, self.editor, self.jobs)
        self.batch = Batch(self.bus, self.workspace, self.script, clock)
        self.workspace.on_take_added = lambda take: self.drafts.request(take, automatic=True)
        self.bus.listen(self._heard)
        self.closed = False
        self._closing = False
        self._autosave_warned = False
        self.workspace.refresh_speakers()
        self.capturing.refresh_devices()
        self.workspace._speaker_changed()
        if self.lib.recovery_note:
            self.bus.status(self.lib.recovery_note)
        self.workspace._say_out_of_reach()

    @property
    def lib(self):
        return self.workspace.lib

    @property
    def current(self):
        return self.workspace.current

    @property
    def status_text(self):
        return self.bus.status_text

    def _heard(self, topic, payload):
        if topic == 'edits':
            self.drafts.edited()

    # ---- the loop -----------------------------------------------------------------------------------------------------

    def tick(self):
        """Call about twenty times a second. A fault in one part is reported without disturbing the others."""
        try:
            self.capturing.tick()
        except Exception as e:                  # a meter or device error must not lose the take; see capture_failed
            self.capturing.capture_failed(e)
        try:
            self.run.tick()
        except Exception as e:                  # a fault in the run ends it; the take going on is the recording's own
            self.run.end(tr('The script run has ended: {error}').format(error=e))
        try:
            self.room.tick()
        except Exception as e:                  # a fault in the room check ends it, and lets what it heard go
            self.room.end(tr('The room check has stopped: {error}').format(error=e))
        for name, step in ((tr('Playback'), self.transport.tick), (tr('Draft transcript'), self.drafts.tick),
                           (tr('Autosave'), self.autosave.tick), (tr('Background task'), self.jobs.drain),
                           (tr('Batch'), self.batch.tick)):
            try:
                step()
            except Exception as e:              # these touch only the display; a failure must not touch the recording
                self.bus.status(tr('{part} error: {error}').format(part=name, error=e))

    # ---- actions that span parts --------------------------------------------------------------------------------------

    def keep(self):
        """Keep the current take. Returns the paths; raises KeepBlocked, CleanError or OSError with the reason.

        The script moves on when the line in front has been read and kept. Updating a kept clip, or keeping an older take of
        another line, leaves it where it is, so no line is skipped.
        """
        take = self.workspace.current
        line = take.meta.get('script_line') if take is not None and not take.kept else None
        paths = self.workspace.keep()
        if paths and line is not None and line == self.script.current:
            if not self.script.go_to_next(self.workspace.takes):
                self.bus.status('Every line of the script has a kept clip.')
        return paths

    def record_pressed(self):
        """The record key: start or stop a take. With the count-in on and a script loaded it starts a script run, and
        during a run it ends the run. During a room check it ends the check, which has the microphone."""
        if self.room.active:
            self.room.end()
        elif self.run.active:
            self.run.end()
        elif self.run.would_run and not self.capturing.recording:
            self.run.start()
        else:
            self.capturing.toggle()

    def re_record(self):
        """Replace the current take: it goes to the Recycle Bin and a new recording starts."""
        if self.workspace.current is None:
            return False
        self.workspace.discard()
        return self.capturing.start()

    def close(self):
        """Finish the session: save the running take and every original still only in memory, stop playback.

        Returns the failures. An empty list means the session is closed; otherwise it stays open so the audio in
        memory is not lost, and closing again retries.
        """
        if self.closed or self._closing:
            return []
        self._closing = True
        self.workspace.closing = True
        try:
            self.batch.cancel()
            self.run.end()
            self.room.end()
            self.transport.stop()
            self.capturing.finish_take()
            self.capturing.close_capture()
            failures = self.workspace.close()
            if failures:
                self.bus.status('Could not save all originals. The window remains open and the audio is still in memory.')
                return failures
            lost = self.autosave.flush()
            if lost and not self._autosave_warned:
                self._autosave_warned = True        # the next close leaves anyway, so a folder that stays broken cannot trap it
                self.bus.status('Could not save your latest changes. Close again to leave without them.')
                return lost
            self.closed = True
            self.drafts.close()
            self.jobs.close()
            return []
        except (OSError, RuntimeError) as e:
            self.bus.status(tr('Could not finish closing: {error}. The window remains open.').format(error=e))
            return [str(e)]
        finally:
            self._closing = False
            self.workspace.closing = False
