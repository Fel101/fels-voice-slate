"""Saving the work on a take while it happens, so a crash or a close does not lose it.

A take that is not kept has its original on disk from the moment it was made, but its cuts, its Process settings and the
text typed for it live in memory until Keep. This part writes them to an autosave file beside the original (store.py says
what the file holds) shortly after each change, and again when another take is opened and when the program closes. The
take comes back with that work when the program starts again. A kept take has its sidecar, so only a new label is
written there.

A change is written once the take has been still for a moment, or after a few seconds of steady changes such as typing,
so a burst of edits is one write.
"""
import time

from ..store import Take

QUIET_SECONDS = 1.0         # a take that has been still this long is written
LONGEST_WAIT_SECONDS = 5.0  # a take that keeps changing (someone typing) is written at least this often
RETRY_SECONDS = 10.0        # after a write that failed


class Autosave:
    def __init__(self, bus, workspace, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.clock = clock
        self._due = {}              # take -> (write when this time comes, write by this time whatever happens)
        self._failed = {}           # take -> why its last write failed
        bus.listen(self._heard)

    def _heard(self, topic, payload):
        if topic == 'edits':
            self.mark(self.workspace.current)
        elif topic == 'meta' and isinstance(payload, Take):         # a 'meta' with no take changed nothing worth saving
            self.mark(payload)
        elif topic == 'current':
            self.flush()                    # leaving a take is a natural time to save it

    def mark(self, take):
        """Something about `take` changed: it will be written soon."""
        if take is None:
            return
        now = self.clock()
        _, latest = self._due.get(take, (None, now + LONGEST_WAIT_SECONDS))
        self._due[take] = (now + QUIET_SECONDS, latest)

    @property
    def waiting(self):
        """The takes whose latest changes are not on disk yet."""
        return list(self._due)

    def tick(self):
        if not self._due:
            return
        now = self.clock()
        for take, (soon, latest) in list(self._due.items()):
            if (now >= soon or now >= latest) and not take.unsaved:
                self._write(take)

    def flush(self):
        """Write every waiting take now. Returns why each write that failed did."""
        failures = []
        for take in list(self._due):
            if take.unsaved:
                continue                    # its original is not on disk yet; the close writes that, then this
            reason = self._write(take)
            if reason:
                failures.append(reason)
        return failures

    def _write(self, take):
        """Write one take. Returns None on success (or when there is nothing to write), else why it failed."""
        try:
            self.workspace.lib.autosave(take)
        except (OSError, ValueError, TypeError) as e:       # TypeError: something in the take cannot be written as JSON
            first = take not in self._failed
            self._failed[take] = str(e)
            retry = self.clock() + RETRY_SECONDS
            self._due[take] = (retry, retry)
            reason = f'{take.stem}: your latest changes could not be saved ({e})'
            if first:
                self.bus.status(f'Could not save your changes to this take: {e}. They are still in the window.')
            return reason
        self._due.pop(take, None)
        self._failed.pop(take, None)
        return None
