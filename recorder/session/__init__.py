"""The session: everything the recorder does, with no window in it.

`Session` wires the parts together. A window drives it by calling its methods and `tick()` (about twenty times a
second), and draws whatever the bus topics say changed. Nothing here imports a toolkit, so all of it is tested
without a display, a microphone or a speaker.

- `workspace`: speakers, takes, the current take, its text fields, keep, discard, close
- `capturing`: devices and the recording state machine
- `editor`: selection, playhead, every edit, the chain settings, undo
- `transport`: playback, pause, seek, loop, A/B
- `drafts`: transcript drafts from the optional speech recognizer
- `autosave`: the work on takes that are not kept yet, saved beside their originals as it happens
- `measure`: background measurements, the spectrogram and the peaks for drawing
- `advisor`: Auto analyze (what could be better, in plain words) and Auto fix (one undo step)
- `script`: script mode
"""
import time

from .. import clean, settings
from ..jobs import Jobs
from ..store import Library
from .advisor import Advisor
from .autosave import Autosave
from .bus import Bus
from .capturing import Capturing
from .drafts import Drafts
from .editor import Editor
from .measure import Measurements
from .script import Script
from .transport import Transport
from .workspace import Workspace


class Session:
    def __init__(self, settings_file=None, jobs=None, player=None, clock=time.monotonic, library=None):
        self.settings_file = settings_file or settings.settings_path()
        data = settings.load(self.settings_file)
        clean.set_ffmpeg_path(data.get('ffmpeg'))
        self.bus = Bus()
        self.jobs = jobs or Jobs()
        self.jobs.on_unhandled = lambda error: self.bus.status(f'A background task failed: {error}')
        self.workspace = Workspace(self.bus, library or Library(data.get('root') or settings.default_root()),
                                   self.settings_file, is_recording=lambda: self.capturing.recording)
        self.script = Script(self.bus)
        self.capturing = Capturing(self.bus, self.workspace, self.script, self.settings_file, clock)
        self.editor = Editor(self.bus, self.workspace)
        self.transport = Transport(self.bus, self.workspace, self.editor, player, clock)
        self.drafts = Drafts(self.bus, self.workspace, self.jobs, self.settings_file, clock)
        self.autosave = Autosave(self.bus, self.workspace, clock)
        self.measure = Measurements(self.bus, self.workspace, self.jobs)
        self.advisor = Advisor(self.bus, self.workspace, self.editor, self.jobs)
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
        for name, step in (('Playback', self.transport.tick), ('Draft transcript', self.drafts.tick),
                           ('Autosave', self.autosave.tick), ('Background task', self.jobs.drain)):
            try:
                step()
            except Exception as e:              # these touch only the display; a failure must not touch the recording
                self.bus.status(f'{name} error: {e}')

    # ---- actions that span parts --------------------------------------------------------------------------------------

    def keep(self):
        """Keep the current take. Returns the paths; raises KeepBlocked, CleanError or OSError with the reason."""
        take = self.workspace.current
        had_line = take is not None and take.meta.get('script_line') is not None
        paths = self.workspace.keep()
        if paths and had_line and self.script.lines:
            self.script.step(1)
        return paths

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
            self.jobs.close()
            return []
        except (OSError, RuntimeError) as e:
            self.bus.status(f'Could not finish closing: {e}. The window remains open.')
            return [str(e)]
        finally:
            self._closing = False
            self.workspace.closing = False
