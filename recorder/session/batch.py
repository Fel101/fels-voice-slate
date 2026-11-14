"""Doing something to many takes: keeping, discarding or moving them, a few at a time; and the long jobs on one take that
must not freeze the window: importing recordings and splitting a take.

The work runs in `tick`, a slice at a time, so the window stays alive and the person can stop it. Nothing here happens on a
worker thread, because each step changes the files and the lists the window shows. A step that has more of the same item
to do returns MORE (an import copies a long recording a block at a time), and one that can do nothing until later returns
WAIT (FFmpeg is still writing the recording out on a thread of its own).

Keeping many takes at once still needs the statement that the person has the right to use the voice. It is made in one
sitting for a list the person has in front of them: the window shows the takes and what each says about its origin and
allowed use, with a box that is not ticked. `keep` is called only after that. The statement is never remembered: it goes on
each take for its own Keep, is taken off again if that Keep fails, and the sidecar says it was given for a batch
(`consent_batch`, the number of takes it covered).
"""
import time
from pathlib import Path

from ..clean import CleanError
from ..store import KeepBlocked, TrashUnavailable, now_iso

SLICE_SECONDS = 0.04        # how long one tick may work, so that the window keeps moving
MORE = 'more'               # a step's item is not finished: it is called again with the same one
WAIT = 'wait'               # nor can it go on yet: it is called again on the next tick


class Batch:
    def __init__(self, bus, workspace, script, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.script = script
        self.clock = clock
        self.title = ''
        self.items = []
        self.done = 0
        self.ok = 0
        self.left_out = []          # (what, why) for each item that was not done
        self.part = None            # how far the item being worked on has got, from 0 to 1, when it takes a while
        self._step = None
        self._finish = None

    # ---- state --------------------------------------------------------------------------------------------------------

    @property
    def running(self):
        return self._step is not None

    @property
    def progress_text(self):
        if self.part is None:
            return f'{self.title}: {self.done} of {len(self.items)}'
        if len(self.items) == 1:
            return f'{self.title}: {self.part:.0%}'
        return f'{self.title}: {self.done + 1} of {len(self.items)}, {self.part:.0%}'

    # ---- the three jobs -----------------------------------------------------------------------------------------------

    def keep(self, takes, origin='', allowed_use=''):
        """Keep each take. `origin` and `allowed_use` fill in a take that has none: what a take says for itself is left."""
        stated_at, covered = now_iso(), len(takes)

        def step(take):
            self._keep_one(take, origin, allowed_use, stated_at, covered)

        def finish(stopped):
            word = 'Kept' if not stopped else 'Stopped. Kept'
            self.bus.status(f'{word} {self.ok} of {len(self.items)} takes.')
            self.bus.notify('meta')

        self._start('Keeping takes', takes, step, finish)

    def discard(self, takes):
        """Send each take to the Recycle Bin. When the bin cannot take one, the job stops: nothing is deleted for good here."""
        def step(take):
            try:
                self.workspace.discard_take(take)
            except TrashUnavailable as e:
                self.left_out.append((self._name(take), str(e)))
                self.cancel()
                return
            self.ok += 1

        def finish(stopped):
            self.bus.status(f'Moved {self.ok} of {len(self.items)} takes to the Recycle Bin.')

        self._start('Discarding takes', takes, step, finish)

    def move(self, takes, slug):
        """File each take under the speaker `slug`."""
        def step(take):
            try:
                left = self.workspace.move_take(slug, take)
            except (ValueError, OSError) as e:
                self.left_out.append((self._name(take), str(e)))
                return
            self.ok += 1
            for path in left or ():
                self.left_out.append((path.name, 'the old file could not be removed'))

        def finish(stopped):
            self.bus.status(f'Moved {self.ok} of {len(self.items)} takes to {self.workspace.name_of(slug) or slug}.')

        self._start('Moving takes', takes, step, finish)

    def export_dataset(self, export, rows):
        """Write the clips of a dataset (recorder/dataset.py), and the files that list them once that is done."""
        def step(row):
            try:
                export.add(row)
            except (CleanError, OSError, ValueError, KeyError, RuntimeError) as e:
                self.left_out.append((Path(row['audio_filepath']).name, str(e)))
                return
            self.ok += 1

        def finish(stopped):
            try:
                count = export.finish()
            except OSError as e:
                self.left_out.append(('The list of clips', str(e)))
                return
            word = 'Stopped. Wrote' if stopped else 'Wrote'
            self.bus.status(f'{word} {count} of {len(self.items)} clips to {export.folder}')

        self._start('Exporting a dataset', rows, step, finish)

    def relocate(self, relocation, arrived):
        """Move a whole output folder (store.Relocation). `arrived()` puts the workspace on the new folder, once every file
        is there. Stopped or failed on the way, the copies are removed and the old folder stays in use."""
        def step(path):
            try:
                relocation.copy(path)
            except OSError as e:
                self.left_out.append((path.name, str(e)))
                self.cancel()
                return
            self.ok += 1

        def finish(stopped):
            if stopped or self.done < len(self.items):
                relocation.undo()
                self.bus.status('The recordings were not moved. They are where they were.')
                return
            left = relocation.finish()
            arrived()
            self.bus.status(f'Moved {self.ok} files to {relocation.target}.' +
                            (f' {len(left)} old files could not be removed.' if left else ''))

        self._start('Moving your recordings', relocation.files, step, finish)

    def import_files(self, paths):
        """Copy recordings in as takes (store.Importing), a block at a time, each one opening as it arrives. One that cannot
        be read is listed at the end. When there is nowhere to put them, because the folder cannot be reached, the job stops
        and that is said once, not for each file. Stopped, the recording being copied is left out and nothing of it stays."""
        state = {'importing': None}

        def drop():
            importing, state['importing'] = state['importing'], None
            if importing is not None:
                importing.cancel()

        def step(path):
            importing = state['importing']
            if importing is None:
                importing = state['importing'] = self.workspace.start_import(path)
                if importing is None:               # the notice says why
                    self.cancel()
                    return None
            try:
                finished = importing.step()
            except Exception as e:                  # unreadable, unsupported or too long: say so at the end, go on
                drop()
                self.left_out.append((Path(path).name, str(e)))
                return None
            if not finished:
                self.part = importing.progress
                return WAIT if importing.waiting else MORE
            state['importing'] = None
            self.workspace.finish_import(importing)
            self.ok += 1
            return None

        def finish(stopped):
            drop()
            if stopped:
                self.bus.status(f'Stopped. Imported {self.ok} of {len(self.items)} recordings.')
            elif self.ok > 1:
                self.bus.status(f'Imported {self.ok} recordings. Edit each one, then Keep.')

        self._start('Importing recordings', paths, step, finish)

    def split(self, take, ranges):
        """Make a new take of each part of `take`, (first, end) sample ranges of its original (Workspace.split_part). Each
        part's original is written as it is made and its audio let go of, so a long take is not held in memory twice.
        Stopped, or when a part cannot be written, the parts already made stay."""
        parts = []

        def step(span):
            try:
                parts.append(self.workspace.split_part(take, *span))
            except OSError as e:
                self.left_out.append((f'The part at {span[0] / take.rate:.1f} s', str(e)))
                self.cancel()
                return
            self.ok += 1

        def finish(stopped):
            self.workspace.split_done(take, parts, len(self.items), stopped)

        self._start('Splitting the take', ranges, step, finish)

    def _keep_one(self, take, origin, allowed_use, stated_at, covered):
        meta = take.meta
        before = {key: meta.get(key) for key in ('origin', 'allowed_use', 'consent_confirmed', 'consent_confirmed_at',
                                                 'consent_batch')}
        if not str(meta.get('origin') or '').strip():
            meta['origin'] = origin
        if not str(meta.get('allowed_use') or '').strip():
            meta['allowed_use'] = allowed_use
        meta.update(consent_confirmed=True, consent_confirmed_at=stated_at, consent_batch=covered)
        try:
            self.workspace.keep_take(take)
        except KeepBlocked as e:
            self._undo(meta, before)
            self.left_out.append((self._name(take), ' '.join(e.problems)))
        except (CleanError, OSError) as e:
            self._undo(meta, before)
            self.left_out.append((self._name(take), str(e)))
        else:
            self.ok += 1
            self.bus.notify('meta', take)

    @staticmethod
    def _undo(meta, before):
        """A Keep that did not happen leaves no statement behind."""
        meta.update({key: value for key, value in before.items() if value is not None})
        meta.update(consent_confirmed=False, consent_confirmed_at='')
        if before['consent_batch'] is None:
            meta.pop('consent_batch', None)

    @staticmethod
    def _name(take):
        return take.meta.get('label') or take.stem

    # ---- running ------------------------------------------------------------------------------------------------------

    def _start(self, title, items, step, finish):
        self.title, self.items = title, list(items)
        self.done, self.ok, self.left_out, self.part = 0, 0, [], None
        self._step, self._finish = step, finish
        self.bus.notify('batch')

    def tick(self):
        """Do what fits in one slice of time, and tell the window how far it has got."""
        if not self.running:
            return
        started = self.clock()
        while self.running and self.done < len(self.items) and self.clock() - started < SLICE_SECONDS:
            item = self.items[self.done]
            self.done += 1
            outcome = self._step(item)
            if outcome in (MORE, WAIT) and self.running:
                self.done -= 1                      # the same item again
                if outcome == WAIT:
                    break
            else:
                self.part = None
        if self.running and self.done >= len(self.items):
            self._end(False)
        elif self.running:
            self.bus.status(self.progress_text + '...')
            self.bus.notify('batch')

    def cancel(self):
        """Stop. What has been done stays done; a recording being imported is left out."""
        if self.running:
            self._end(True)

    def _end(self, stopped):
        finish, self._step, self._finish, self.part = self._finish, None, None, None
        finish(stopped)
        self.bus.notify('takes')
        self.bus.notify('batch')
        if self.left_out:
            detail = '\n'.join(f'- {what}: {why}' for what, why in self.left_out[:30])
            more = len(self.left_out) - 30
            self.bus.notice('warning', self.title, f'{len(self.left_out)} of them could not be done:\n\n{detail}'
                            + (f'\n\nand {more} more.' if more > 0 else ''))
