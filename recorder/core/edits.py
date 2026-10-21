"""The edit recipe of one take, with a single undo history for every change.

A recipe is the timed operations (cut, silence, fade, gain), the take-wide chain settings (core/chain.py) and the join
fade. Times are seconds on the master timeline (the untouched capture), so operations survive undo and redo.
The history keeps every state, so Undo, Redo and "go back to step N" all work, and a failed change leaves it untouched.
"""
import copy
import math

from .. import audio_core as ac
from . import chain as chain_mod

OPS = ('cut', 'silence', 'fade', 'gain')
WORD_OPS = ('cut', 'silence')           # operations that can remove spoken words
GAIN_LIMITS = (-60.0, 24.0)             # region gain, dB
SLACK = 0.001                           # sidecars round times to 0.1 ms


def validate_edits(edits, seconds):
    """Check a stored operation list against the master length. Raises ValueError saying what is wrong."""
    if not isinstance(edits, list):
        raise ValueError('the edit list must be a list')
    for e in edits:
        if not isinstance(e, dict) or e.get('op') not in OPS:
            raise ValueError(f'an entry is not a known edit: {str(e)[:60]!r}')
        try:
            a, b = float(e['start_seconds']), float(e['end_seconds'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('an edit has no usable start and end') from None
        if not (0.0 <= a < b <= seconds + SLACK):
            raise ValueError(f'an edit lies outside the clip ({a:.3f} to {b:.3f} s, clip is {seconds:.3f} s)')
        if e['op'] == 'fade' and e.get('direction') not in ('in', 'out'):
            raise ValueError('a fade must be "in" or "out"')
        if e['op'] == 'gain':
            try:
                gain = float(e['gain_db'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('a gain edit has no usable gain_db') from None
            if not math.isfinite(gain) or not GAIN_LIMITS[0] <= gain <= GAIN_LIMITS[1]:
                raise ValueError(f'gain_db must be between {GAIN_LIMITS[0]:g} and {GAIN_LIMITS[1]:g}')
    return True


def describe(edit):
    """One line for the edit list, e.g. "Cut 4.20 to 5.10 s"."""
    a, b = edit['start_seconds'], edit['end_seconds']
    span = f'{a:.2f} to {b:.2f} s'
    op = edit.get('op', 'cut')
    if op == 'silence':
        return f'Silence {span}'
    if op == 'fade':
        return f"Fade {edit['direction']} {span}"
    if op == 'gain':
        return f"Gain {edit['gain_db']:+.1f} dB {span}"
    return f'Cut {span}'


class History:
    """The recipe of one take. `edits`, `chain` and `fade_ms` always describe the current state."""

    def __init__(self, edits=None, seconds=None, chain=None, fade_ms=ac.DEFAULT_FADE_MS):
        self.seconds = seconds
        self._states = [{'edits': [dict(e) for e in (edits or [])], 'chain': chain_mod.normalize(chain),
                         'fade_ms': float(fade_ms)}]
        self._labels = ['Start']
        self._at = 0
        self._merge = None              # key of the last change, so a slider's many small changes become one step

    # ---- the current state --------------------------------------------------------------------------------------------

    @property
    def edits(self):
        return self._states[self._at]['edits']

    @property
    def chain(self):
        return self._states[self._at]['chain']

    @property
    def fade_ms(self):
        return self._states[self._at]['fade_ms']

    @property
    def cuts(self):
        return [e for e in self.edits if e.get('op', 'cut') == 'cut']

    @property
    def words_changed(self):
        """True when an operation could have removed spoken words, so the transcript needs checking."""
        return any(e.get('op', 'cut') in WORD_OPS for e in self.edits)

    def state_copy(self):
        return copy.deepcopy(self._states[self._at])

    def to_json(self):
        return {'edits': copy.deepcopy(self.edits), 'join_fade_ms': self.fade_ms, 'chain': copy.deepcopy(self.chain)}

    # ---- steps --------------------------------------------------------------------------------------------------------

    @property
    def steps(self):
        """Labels of the recorded steps, oldest first (the start state is not a step)."""
        return self._labels[1:]

    @property
    def position(self):
        """How many steps are applied; the steps after it can be redone."""
        return self._at

    @property
    def can_undo(self):
        return self._at > 0

    @property
    def can_redo(self):
        return self._at < len(self._states) - 1

    def undo(self):
        return self.jump(self._at - 1)

    def redo(self):
        return self.jump(self._at + 1)

    def jump(self, position):
        if not 0 <= position < len(self._states) or position == self._at:
            return False
        self._at = position
        self._merge = None
        return True

    def copy(self):
        return copy.deepcopy(self)

    def _push(self, state, label, merge=None):
        if merge is not None and merge == self._merge and self._at == len(self._states) - 1 and self._at > 0:
            self._states[self._at] = state          # the same control moved again: one step, not many
            self._labels[self._at] = label
            return
        del self._states[self._at + 1:]
        del self._labels[self._at + 1:]
        self._states.append(state)
        self._labels.append(label)
        self._at += 1
        self._merge = merge

    def _next(self):
        return self.state_copy()

    # ---- operations ---------------------------------------------------------------------------------------------------

    def _span(self, start, end):
        start, end = float(start), float(end)
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError('Edit points must be numbers')
        if self.seconds is not None:
            start, end = max(0.0, start), min(self.seconds, end)
        if end - start < ac.MIN_SELECTION_SECONDS:
            raise ValueError('Selection shorter than 20 ms is ignored')
        return start, end

    def _region(self, start, end):
        if self.seconds is not None:
            if start <= 1e-9:
                return 'start'
            if end >= self.seconds - 1e-9:
                return 'end'
        return 'middle'

    def _check_coverage(self, edits):
        """A set of cuts must leave some audio. Overlapping cuts are merged before they are counted."""
        if self.seconds is None:
            return
        covered = 0.0
        for cut in sorted((e for e in edits if e.get('op', 'cut') == 'cut'), key=lambda e: e['start_seconds']):
            if cut['start_seconds'] > covered + 1e-9:
                break
            covered = max(covered, cut['end_seconds'])
            if covered >= self.seconds - 1e-9:
                raise ValueError('A cut cannot remove the whole clip')

    def cut(self, start, end):
        return self.add_ops([('cut', start, end, {})])[0]

    def silence(self, start, end):
        return self.add_ops([('silence', start, end, {})])[0]

    def fade(self, direction, start, end):
        if direction not in ('in', 'out'):
            raise ValueError('A fade must be "in" or "out"')
        return self.add_ops([('fade', start, end, {'direction': direction})])[0]

    def gain(self, gain_db, start, end):
        gain_db = float(gain_db)
        if not math.isfinite(gain_db) or not GAIN_LIMITS[0] <= gain_db <= GAIN_LIMITS[1]:
            raise ValueError(f'Gain must be between {GAIN_LIMITS[0]:g} and {GAIN_LIMITS[1]:g} dB')
        return self.add_ops([('gain', start, end, {'gain_db': round(gain_db, 2)})])[0]

    def _extend(self, state, ops):
        """Append operations to a state being built. Returns the edits added; raises ValueError for one that is refused."""
        added = []
        for name, start, end, params in ops:
            a, b = self._span(start, end)
            edit = {'op': name}
            if name == 'cut':
                edit['region'] = self._region(a, b)
            edit.update(params)
            edit.update(start_seconds=round(a, 4), end_seconds=round(b, 4))
            if name == 'cut' and self.seconds is not None and b - a >= self.seconds - 1e-9:
                raise ValueError('A cut cannot remove the whole clip')
            state['edits'].append(edit)
            added.append(edit)
        self._check_coverage(state['edits'])
        return added

    def add_ops(self, ops, label=None):
        """Add operations in one step: all of them, or none if any is refused. `ops` is (name, start, end, params)."""
        state = self._next()
        added = self._extend(state, ops)
        if not added:
            raise ValueError('Nothing to do')
        self._push(state, label or (describe(added[0]) if len(added) == 1 else f'{len(added)} edits'))
        return self.edits[-len(added):]

    def add_step(self, ops, chain_changes, label):
        """Operations and chain settings together as one undo step (Auto fix). Returns False when nothing would change.

        `chain_changes` is {section: {name: value}}, as for update_chain. All of it is applied or none of it.
        """
        state = self._next()
        added = self._extend(state, ops)
        for section, values in chain_changes.items():
            state['chain'] = chain_mod.with_values(state['chain'], section, **values)
        if not added and state['chain'] == self.chain:
            return False
        self._push(state, label)
        return True

    def remove(self, index):
        state = self._next()
        removed = state['edits'].pop(index)
        self._push(state, f'Remove: {describe(removed)}')

    # ---- chain and fade -----------------------------------------------------------------------------------------------

    def set_chain(self, section, label, merge=None, **values):
        """Change the chain settings of one section as one undo step. Raises ValueError if a value is out of range."""
        return self.update_chain({section: values}, label, merge)

    def update_chain(self, changes, label, merge=None):
        """Change settings in several sections as one undo step. `changes` is {section: {name: value}}."""
        state = self._next()
        chain = state['chain']
        for section, values in changes.items():
            chain = chain_mod.with_values(chain, section, **values)
        state['chain'] = chain
        if chain == self.chain:
            return False
        self._push(state, label, merge)
        return True

    def set_fade_ms(self, ms):
        ms = float(ms)
        if not math.isfinite(ms):
            raise ValueError('The join fade must be a number')
        ms = min(max(ms, 0.0), float(ac.MAX_FADE_MS))
        if ms == self.fade_ms:
            return False
        state = self._next()
        state['fade_ms'] = ms
        self._push(state, f'Join fade {ms:g} ms', merge='fade')
        return True
