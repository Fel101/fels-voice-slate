"""Reading a script hands-free: the next line when a take ends, the end of a take after a pause, and the next take by
itself after a count-in.

Each is an option the person turns on, remembered in the settings (`hands_free`), and each applies to takes of a script
line. With the count-in on, the record key starts a *run*: the line is counted in on the screen, recorded, ended by the
pause after it, and the next line is counted in, until the script ends or the person ends the run with Esc or the record
key. The program makes no sound for the count-in, so none of it reaches the recording.

A run only records. It never keeps a clip and never ticks the permission box: the takes are kept afterwards with Keep
selected takes, whose statement the person makes for the list in front of them. Nothing here touches either.

The microphone is open for the whole run, so that each take has the half second before it and the meter stays live; it
closes when the run ends, unless it was armed before the run began. Like the rest of the session there are no timers:
`tick` checks the count-in and the pause against the injected clock and the audio itself.
"""
import math
import time

from .. import settings
from ..core.analysis import live_pause
from ..i18n import tr

OPTIONS = {'next_line': False, 'stop_after_pause': False, 'pause_seconds': 1.5, 'auto_start': False, 'count_in': 3}
PAUSE_RANGE = (0.5, 5.0)
COUNT_IN_RANGE = (1, 10)
AGAIN_KEYS = ('F8', 'F7')             # F7 when the record key is F8, which 0.6 allowed


def again_key(record_key):
    """The key that reads the line again: one key, and never the record key."""
    return next(key for key in AGAIN_KEYS if key.lower() != str(record_key).strip().lower())


def options(data=None):
    """The hands-free options from the settings, each made safe: anything unreadable is the default, which is off."""
    found = (data if data is not None else settings.load()).get('hands_free')
    found = found if isinstance(found, dict) else {}
    chosen = dict(OPTIONS)
    for key in ('next_line', 'stop_after_pause', 'auto_start'):
        chosen[key] = found.get(key) is True
    for key, (low, high), kind in (('pause_seconds', PAUSE_RANGE, float), ('count_in', COUNT_IN_RANGE, int)):
        value = found.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            chosen[key] = kind(min(max(value, low), high))
    chosen['auto_start'] = chosen['auto_start'] and chosen['stop_after_pause']     # a run goes on only if takes end by themselves
    return chosen


class ScriptRun:
    def __init__(self, bus, workspace, script, capturing, settings_file=None, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.script = script
        self.capturing = capturing
        self.settings_file = settings_file
        self.clock = clock
        self.options = options(settings.load(settings_file))
        self.active = False                 # a run is going on
        self.phase = None                   # in a run: 'counting' before a take, 'recording' during one
        self.heard = False                  # the voice has been heard in the take of a line being recorded
        self._deadline = None               # when the count-in ends
        self._was_armed = False             # the input was open before the run began, so it stays open after it
        self._taking = None                 # the line (from 0) of the take being recorded, while there is one
        self._last_read = None              # the line of the last take that ended
        self._again = False                 # the take being recorded ends to be read again
        self._ending = None                 # what to say once the last take of a run that has ended is saved
        self._shown = None

    # ---- the options ----------------------------------------------------------------------------------------------------

    def set_options(self, **changes):
        chosen = {**self.options, **changes}
        settings.update({'hands_free': chosen}, self.settings_file)
        self.options = options(settings.load(self.settings_file))
        self.bus.notify('run')

    @property
    def would_run(self):
        """Whether the record key starts a run rather than a take."""
        return self.options['auto_start'] and bool(self.script.lines)

    def _applies(self):
        return bool(self.script.lines) and self._taking is not None

    # ---- what the window shows ----------------------------------------------------------------------------------------

    @property
    def count_left(self):
        """Whole seconds left of the count-in, or None when there is none."""
        if self.phase != 'counting' or self._deadline is None:
            return None
        return max(1, math.ceil(self._deadline - self.clock() - 1e-9))

    def state(self):
        """What the window says about the run and the take, or None when hands-free reading has nothing to say."""
        lines = len(self.script.lines)
        if self.phase == 'counting':
            return ('counting', self.count_left, self.script.index + 1, lines)
        if self._taking is not None and self.capturing.recording:
            if self.capturing.finishing:
                return ('stopping', None, self._taking + 1, lines)
            if self.options['stop_after_pause']:
                return ('heard' if self.heard else 'listening', self.options['pause_seconds'], self._taking + 1, lines)
        return None

    # ---- the run ----------------------------------------------------------------------------------------------------

    def start(self):
        """Begin a run at the line in front: open the input and count in. Returns False, having said why, if it cannot."""
        capturing = self.capturing
        if self.active or not self.would_run or capturing.recording or capturing.nowhere_to_save():
            return False
        self._was_armed = capturing.armed
        if not capturing.armed:
            capturing.toggle_arm()
            if not capturing.armed:             # it said why
                return False
        self.active, self._ending = True, None
        self._count_in()
        self.bus.status(tr('Script run: line {line} of {lines} after the count-in. Esc or {key} ends the run.').format(
            line=self.script.index + 1, lines=len(self.script.lines), key=capturing.record_key))
        return True

    def end(self, message=None):
        """End the run at once. A take being recorded stops as Stop stops it, and is kept as a take; the input closes
        unless it was open before the run began. `message`, in English, says why."""
        if not self.active:
            return
        self.active, self.phase, self._deadline = False, None, None
        capturing = self.capturing
        armed = tr('The input stays armed.' if self._was_armed else 'The microphone is closed.')
        said = f'{tr(message or "The script run has ended.")} {armed}'
        if capturing.recording:                 # said once the take is saved, after what is said about the take
            self._ending = f'{tr("Take saved.")} {said}'
            capturing.keep_armed_after_take(self._was_armed)
            capturing.stop()
        else:
            if not self._was_armed:
                capturing.release_input()
            self.bus.status(said)
        self.bus.notify('run')

    def again(self):
        """Record the line just read once more. Its take stays: this is another, to choose between them."""
        script, capturing = self.script, self.capturing
        if not script.lines:
            return
        if self._taking is not None and capturing.recording:
            self._again = True
            if not capturing.finishing:
                capturing.stop()
            self.bus.status(tr('Line {line} again, once this take has been saved.').format(line=self._taking + 1))
            return
        line = self._last_read if self._last_read is not None else script.index
        if 0 <= line < len(script.lines):
            script.go_to(line)
        if self.phase == 'counting':
            self._count_in()
        elif not capturing.recording and not self.start():
            if not self.would_run:
                capturing.start()

    def _count_in(self):
        self.phase = 'counting'
        self._deadline = self.clock() + self.options['count_in']
        self.bus.notify('run')

    def _begin_take(self):
        self._deadline = None
        self.phase = 'recording'
        if not self.capturing.start():          # it said why
            self.end('The script run has ended: the take could not start.')
            return
        self._taking, self.heard = self.script.index, False
        self.bus.status(tr('Recording line {line} of {lines}. Esc or {key} ends the run.').format(
            line=self.script.index + 1, lines=len(self.script.lines), key=self.capturing.record_key))

    # ---- the loop -----------------------------------------------------------------------------------------------------

    def tick(self):
        capturing = self.capturing
        if self.active and not self.script.lines:
            self.end('The script run has ended: the script was cleared.')
        if self._taking is not None and not capturing.recording:
            self._ended()
        elif self._taking is None and capturing.recording and capturing.take_script_line is not None:
            self._taking, self.heard = self.script.index, False
        if self._taking is not None and capturing.recording and not capturing.finishing:
            self._listen(capturing)
        if self.phase == 'counting' and self.clock() >= self._deadline:
            self._begin_take()
        shown = self.state()
        if shown != self._shown:
            self._shown = shown
            self.bus.notify('run')

    def _listen(self, capturing):
        """End the take once the voice has been heard and the room has been quiet for the time chosen."""
        if not self.options['stop_after_pause']:
            return
        loudness = getattr(capturing.capture, 'loudness', None)
        if loudness is None:
            return
        self.heard, quiet = live_pause(loudness())
        if self.heard and quiet >= self.options['pause_seconds']:
            capturing.stop()

    def _ended(self):
        """A take of a line has ended: move on, count in the next, or read it again, as the options and the person say."""
        line, self._taking = self._taking, None
        self._last_read, again, ending = line, self._again, self._ending
        self._again, self._ending = False, None
        capturing, script = self.capturing, self.script
        if ending is not None:
            if capturing.ended_by == 'stop':
                self.bus.status(ending)
            return
        if again:
            script.go_to(line)
            if self.active:
                self._count_in()
            else:
                capturing.start()
            return
        if capturing.ended_by != 'stop':
            self.end({'limit': 'The script run has ended: the take reached the longest recording of its purpose.',
                      'device': 'The script run has ended: the device stopped sending sound.'}.get(
                capturing.ended_by, 'The script run has ended: the take could not be saved.'))
            return
        if self.options['next_line'] and script.index == line:
            if line >= len(script.lines) - 1:
                self.end('That was the last line of the script. The script run has ended.')
                return
            script.step(1)
        if self.active:
            if not capturing.armed:             # it could not be armed again, and said why
                self.end('The script run has ended.')
                return
            self._count_in()
            self.bus.status(tr('Take saved. Line {line} of {lines} after the count-in.').format(
                line=script.index + 1, lines=len(script.lines)))
