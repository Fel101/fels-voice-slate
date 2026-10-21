"""Editing the current take: selection and playhead, every operation, the chain settings, and undo.

An edit that fails (a cut that would remove the whole clip, a setting out of range) leaves the take exactly as it
was, redo steps included. The master is never changed: the take holds a recipe, and Keep renders it.
"""
from collections import namedtuple

from .. import audio_core as ac
from ..clean import CleanError
from ..core import analysis, chain as chain_mod
from ..core import render

Normalized = namedtuple('Normalized', 'gain_db wanted_db capped')
GAIN_STEP_LIMITS = chain_mod.LIMITS[('level', 'gain_db')]


class Editor:
    def __init__(self, bus, workspace):
        self.bus = bus
        self.workspace = workspace
        self.sel = None                 # (start, end) seconds on the master timeline
        self.playhead = 0.0             # the edit cursor, in master seconds
        self.in_point = self.out_point = None
        bus.listen(self._heard)

    def _heard(self, topic, payload):
        if topic == 'current':
            self.reset_view()

    @property
    def take(self):
        return self.workspace.current

    def reset_view(self):
        self.sel = None
        self.in_point = self.out_point = None
        self.playhead = 0.0
        self.bus.notify('selection')
        self.bus.notify('playhead')

    # ---- selection and playhead ---------------------------------------------------------------------------------------

    def _clamp(self, t):
        take = self.take
        return min(max(float(t), 0.0), take.seconds) if take is not None else 0.0

    def set_playhead(self, t):
        self.playhead = self._clamp(t)
        self.bus.notify('playhead')

    def set_selection(self, a, b):
        """Select a range (in either order). A range of no length clears the selection."""
        a, b = sorted((self._clamp(a), self._clamp(b)))
        self.sel = (a, b) if b - a > 0 else None
        self.in_point, self.out_point = self.sel if self.sel else (None, None)     # the markers follow the selection
        self.bus.notify('selection')

    def clear_selection(self):
        self.sel = None
        self.in_point = self.out_point = None
        self.bus.notify('selection')

    def set_in(self):
        self._set_bound(0)

    def set_out(self):
        self._set_bound(1)

    def _set_bound(self, which):
        """[ sets the in point and ] the out point at the playhead. Once both are set, they are the selection."""
        if self.take is None:
            return
        if which == 0:
            self.in_point = self.playhead
        else:
            self.out_point = self.playhead
        if self.in_point is not None and self.out_point is not None:
            a, b = sorted((self.in_point, self.out_point))
            self.sel = (a, b) if b - a >= ac.MIN_SELECTION_SECONDS else None
        self.bus.notify('selection')

    # ---- running an edit ----------------------------------------------------------------------------------------------

    def _apply(self, change, message, keep_selection=False):
        """Run `change(history)`; on a refusal restore the take as it was and say why. Returns True on success."""
        take = self.take
        if take is None:
            return False
        before = take.history.copy()
        try:
            change(take.history)
            n = len(take.samples)
            cuts = ac.cut_ranges(take.history.edits, take.rate, n, take.mono)
            if not ac.kept_segments(cuts, n):       # the snapped ranges can remove more than the times suggest
                raise ValueError('A cut cannot remove the whole clip')
        except ValueError as e:
            take.history = before
            self.bus.status(str(e))
            return False
        self._edited(message, keep_selection)
        return True

    def _edited(self, message, keep_selection=False):
        take = self.take
        take.needs_transcript_review = take.history.words_changed
        if not keep_selection:
            self.sel = None
            self.in_point = self.out_point = None
            self.bus.notify('selection')
        self.bus.notify('edits')
        if message:
            self.bus.status(message)

    # ---- operations on a selection ------------------------------------------------------------------------------------

    def _need_selection(self, what):
        if self.sel is None:
            self.bus.status(f'Select a range on the waveform first ({what}).')
            return None
        return self.sel

    def cut_selection(self):
        sel = self._need_selection('drag across it')
        if sel:
            self._apply(lambda h: h.cut(*sel), 'Cut recorded. The master file is unchanged.')

    def cut_from_start(self):
        if self.take is None:
            return
        end = self.sel[1] if self.sel else self.playhead
        self._apply(lambda h: h.cut(0.0, end), 'Cut recorded. The master file is unchanged.')

    def cut_to_end(self):
        take = self.take
        if take is None:
            return
        start = self.sel[0] if self.sel else self.playhead
        self._apply(lambda h: h.cut(start, take.seconds), 'Cut recorded. The master file is unchanged.')

    def keep_only_selection(self):
        sel = self._need_selection('the part to keep')
        take = self.take
        if not sel or take is None:
            return
        a, b = sel

        def run(history):
            ops = []
            if a > 1e-6:
                ops.append(('cut', 0.0, a, {}))
            if b < take.seconds - 1e-6:
                ops.append(('cut', b, take.seconds, {}))
            history.add_ops(ops, label='Keep only the selection')

        self._apply(run, 'Cut recorded. The master file is unchanged.')

    def silence_selection(self):
        sel = self._need_selection('the part to silence')
        if sel:
            self._apply(lambda h: h.silence(*sel), 'Silenced. The words in that part are gone from the clip.')

    def fade_selection(self, direction):
        sel = self._need_selection(f'for the fade {direction}')
        if sel:
            self._apply(lambda h: h.fade(direction, *sel), f'Fade {direction} added.')

    def gain_selection(self, gain_db):
        sel = self._need_selection('the part to change')
        if sel:
            self._apply(lambda h: h.gain(gain_db, *sel), f'Gain {float(gain_db):+.1f} dB on the selection.')

    # ---- operations found by analysis ---------------------------------------------------------------------------------

    def _edited_audio(self):
        """The current edited clip before gain, and the map from its time to master time."""
        take = self.take
        lib = self.workspace.lib
        audio = lib.before_level(take)
        segments = render.segments_of(take.samples, take.rate, take.history.edits)
        return audio, segments

    def _to_master(self, ranges, segments, rate):
        out = []
        for a, b in ranges:
            out.append((render.edited_to_master(a, segments, rate), render.edited_to_master(b, segments, rate)))
        return out

    def auto_trim(self, margin=0.15):
        """Cut the pauses at both ends of the clip down to `margin` seconds."""
        self._analysed_cuts('Auto-trim', 'Nothing to trim: the ends are already tight.',
                            lambda audio, rate: analysis.auto_trim_ranges(audio, rate, margin))

    def shorten_pauses(self, keep_seconds=0.5):
        """Cut long pauses inside the clip down to `keep_seconds` each."""
        self._analysed_cuts('Shorten pauses', 'No pause is longer than that.',
                            lambda audio, rate: analysis.shorten_pause_ranges(audio, rate, keep_seconds))

    def _analysed_cuts(self, label, nothing, finder):
        take = self.take
        if take is None:
            return
        try:
            audio, segments = self._edited_audio()
        except CleanError as e:
            self.bus.status(str(e))
            return
        ranges = self._to_master(finder(audio, take.rate), segments, take.rate)
        if not ranges:
            self.bus.status(nothing)
            return
        ops = [('cut', a, b, {}) for a, b in ranges]
        self._apply(lambda h: h.add_ops(ops, label=f'{label} ({len(ops)} cut{"s" if len(ops) > 1 else ""})'),
                    f'{label}: {len(ops)} cut{"s" if len(ops) > 1 else ""} recorded.')

    def apply_auto_fix(self, diagnosis):
        """Apply an Auto fix plan (core/autofix.py) as one undo step. Returns True if it was applied."""
        def run(history):
            if not history.add_step(diagnosis.ops, diagnosis.chain, diagnosis.label):
                raise ValueError('Auto fix has nothing to change.')

        return self._apply(run, f'{diagnosis.label} done. Undo takes all of it back in one step. '
                                'The original recording is unchanged.')

    # ---- history ------------------------------------------------------------------------------------------------------

    def _history_step(self, step):
        take = self.take
        if take is None or not step(take.history):
            return False
        self._edited(None, keep_selection=True)
        return True

    def undo(self):
        return self._history_step(lambda h: h.undo())

    def redo(self):
        return self._history_step(lambda h: h.redo())

    def jump_to(self, position):
        return self._history_step(lambda h: h.jump(position))

    def remove_edit(self, index):
        take = self.take
        if take is None:
            return False
        before = take.history.copy()
        try:
            take.history.remove(index)
            if not ac.kept_segments(ac.cut_ranges(take.history.edits, take.rate, len(take.samples), take.mono),
                                    len(take.samples)):
                raise ValueError('A cut cannot remove the whole clip')
        except (ValueError, IndexError) as e:
            take.history = before
            self.bus.status(str(e))
            return False
        self._edited(None, keep_selection=True)
        return True

    # ---- the chain ----------------------------------------------------------------------------------------------------

    def set_fade_ms(self, ms):
        return self._chain_step(lambda h: h.set_fade_ms(ms))

    def set_chain(self, section, label, merge=None, **values):
        """Change a chain setting as one undo step. Returns True if something changed."""
        return self._chain_step(lambda h: h.set_chain(section, label, merge, **values))

    def _chain_step(self, change):
        take = self.take
        if take is None:
            return False
        try:
            changed = change(take.history)
        except ValueError as e:
            self.bus.status(str(e))
            return False
        if changed:
            self.bus.notify('edits')
        return changed

    def set_denoise(self, enabled):
        changed = self.set_chain('denoise', 'Noise cleaning on' if enabled else 'Noise cleaning off', enabled=enabled)
        if changed and enabled:
            self.bus.status('Cleaning background noise. This runs FFmpeg on the clip.')
        return changed

    def normalize_level(self, limit=None):
        """Set the gain so the voiced part sits at the target. With the limiter on (or `limit=True`), one loud peak no
        longer holds the gain down. Returns a Normalized result, or None if the clip could not be measured."""
        take = self.take
        if take is None:
            return None
        use_limiter = take.history.chain['limiter']['enabled'] if limit is None else bool(limit)
        try:
            base = self.workspace.lib.before_level(take)
        except CleanError as e:
            self.bus.status(str(e))
            return None
        wanted = ac.normalize_gain_db(base, take.rate, cap_peak=False)
        gain = wanted if use_limiter else ac.normalize_gain_db(base, take.rate)
        low, high = GAIN_STEP_LIMITS
        gain, wanted = min(max(gain, low), high), min(max(wanted, low), high)
        changes = {'level': {'gain_db': gain}}
        if use_limiter and not take.history.chain['limiter']['enabled']:
            changes['limiter'] = {'enabled': True}
        take.history.update_chain(changes, f'Normalize {gain:+.1f} dB')
        capped = not use_limiter and wanted - gain > 1.0
        self.bus.notify('edits')
        message = f'Level set to {gain:+.1f} dB. The original recording is unchanged.'
        if capped:
            message += f' A loud peak holds it below the {wanted:+.1f} dB the speech needs: turn on the limiter.'
        self.bus.status(message)
        return Normalized(gain, wanted, capped)

    def reset_level(self):
        if self.set_chain('level', 'Level reset', gain_db=0.0):
            self.bus.status('Level reset to the recorded level.')
