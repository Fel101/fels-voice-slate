"""Editing the current take: selection and playhead, every operation, the chain settings, and undo.

An edit that fails (a cut that would remove the whole clip, a setting out of range) leaves the take exactly as it
was, redo steps included. The master is never changed: the take holds a recipe, and Keep renders it.
"""
from collections import namedtuple

from .. import audio_core as ac
from ..clean import CleanError
from ..core import analysis, autofix, chain as chain_mod
from ..core import render
from ..core.edits import WORD_OPS, words_key
from ..i18n import tr
from ..store import clean_markers

Normalized = namedtuple('Normalized', 'gain_db wanted_db capped')
GAIN_STEP_LIMITS = chain_mod.LIMITS[('level', 'gain_db')]
MARKER_REACH = 0.05             # seconds: a marker this near the moment marked is the one taken away instead
MARKER_PASSED = 0.001           # a marker this near the playhead is the one it is on, not the next


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

    # ---- markers --------------------------------------------------------------------------------------------------------

    def markers(self):
        take = self.take
        return list(take.meta.get('markers') or []) if take is not None else []

    def toggle_marker(self, t=None):
        """Drop a marker at `t` (the playhead if not given), or take away the one already there. Markers are saved with the
        take, and with a kept clip in its sidecar alone: they do not change the sound, so the clip stays as it was kept."""
        take = self.take
        if take is None:
            return
        t = self._clamp(self.playhead if t is None else t)
        markers = self.markers()
        near = min(markers, key=lambda m: abs(m - t), default=None)
        if near is not None and abs(near - t) <= MARKER_REACH:
            markers.remove(near)
            message = tr('Marker taken away at {seconds} s.').format(seconds=f'{near:.2f}')
        else:
            markers.append(t)
            ordered = clean_markers(markers, take.seconds)
            message = tr('Marker {number} of {count} at {seconds} s. Press M on it to take it away.').format(
                number=ordered.index(round(t, 3)) + 1, count=len(ordered), seconds=f'{t:.2f}')
        take.meta['markers'] = clean_markers(markers, take.seconds)
        self.bus.notify('meta', take)
        self.bus.status(message)

    def marker_beside(self, t, forward):
        """The first marker after `t`, or the last one before it; None when there is none that way."""
        markers = self.markers()
        if forward:
            return next((m for m in markers if m > t + MARKER_PASSED), None)
        return next((m for m in reversed(markers) if m < t - MARKER_PASSED), None)

    def went_to_marker(self, moment, forward):
        """Say which marker the playhead went to, or with `moment` None that there is none that way."""
        markers = self.markers()
        if moment is None or moment not in markers:
            self.bus.status(tr('There is no marker after the playhead.') if forward else
                            tr('There is no marker before the playhead.'))
            return
        self.bus.status(tr('Marker {number} of {count}, at {seconds} s.').format(
            number=markers.index(moment) + 1, count=len(markers), seconds=f'{moment:.2f}'))

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

    # ---- the words of the take --------------------------------------------------------------------------------------------

    def select_words(self, first, last):
        """Select from the word at position `first` to the one at `last`, and move the playhead to the start of them. The
        edges go to the quiet just outside the words, so that a cut takes all of them."""
        take = self.take
        words = (take.meta.get('draft_words') or []) if take is not None else []
        if 0 <= first <= last < len(words):
            start = analysis.quietest_near(take.mono, take.rate, words[first][1])
            end = analysis.quietest_near(take.mono, take.rate, words[last][2])
            self.set_selection(start, end)
            self.set_playhead(start)

    def restore_word(self, position):
        """Bring back a word that was cut or silenced, by removing the edit that covers it. Returns True if one did."""
        take = self.take
        words = (take.meta.get('draft_words') or []) if take is not None else []
        if not 0 <= position < len(words):
            return False
        middle = (words[position][1] + words[position][2]) / 2
        for index, edit in reversed(list(enumerate(take.history.edits))):
            if edit.get('op', 'cut') in WORD_OPS and edit['start_seconds'] <= middle <= edit['end_seconds']:
                return self.remove_edit(index)
        return False

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
                raise ValueError(tr('A cut cannot remove the whole clip'))
        except ValueError as e:
            take.history = before
            self.bus.status(str(e))
            return False
        self._edited(message, keep_selection)
        return True

    def _edited(self, message, keep_selection=False):
        take = self.take
        # Only a change to the cuts and silences can change the words, and going back to what was checked needs no new check.
        take.needs_transcript_review = take.history.words_changed and words_key(take.history) != take.confirmed_words
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
            self.bus.status(tr('Select a range on the waveform first ({what}).').format(what=what))
            return None
        return self.sel

    def cut_selection(self):
        sel = self._need_selection(tr('drag across it'))
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
        sel = self._need_selection(tr('the part to keep'))
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
            history.add_ops(ops, label=tr('Keep only the selection'))

        self._apply(run, 'Cut recorded. The master file is unchanged.')

    def silence_selection(self):
        sel = self._need_selection(tr('the part to silence'))
        if sel:
            self._apply(lambda h: h.silence(*sel), 'Silenced. The words in that part are gone from the clip.')

    def fade_selection(self, direction):
        sel = self._need_selection(tr('for the fade in' if direction == 'in' else 'for the fade out'))
        if sel:
            self._apply(lambda h: h.fade(direction, *sel), tr('Fade in added.' if direction == 'in' else 'Fade out added.'))

    def gain_selection(self, gain_db):
        sel = self._need_selection(tr('the part to change'))
        if sel:
            self._apply(lambda h: h.gain(gain_db, *sel),
                        tr('Gain {gain} dB on the selection.').format(gain=f'{float(gain_db):+.1f}'))

    # ---- operations found by analysis ---------------------------------------------------------------------------------

    def _edited_audio(self):
        """The current edited clip before gain, and the map from its time to master time."""
        take = self.take
        lib = self.workspace.lib
        audio = lib.before_level(take)
        segments = render.segments_of(take.mono, take.rate, take.history.edits)
        return audio, segments

    def _to_master(self, ranges, segments, rate):
        out = []
        for a, b in ranges:
            out.append((render.edited_to_master(a, segments, rate), render.edited_to_master(b, segments, rate)))
        return out

    def auto_trim(self, margin=0.15):
        """Cut the pauses at both ends of the clip down to `margin` seconds."""
        self._analysed_cuts(tr('Auto-trim'), tr('Nothing to trim: the ends are already tight.'),
                            lambda audio, rate: analysis.auto_trim_ranges(audio, rate, margin))

    def shorten_pauses(self, keep_seconds=0.5):
        """Cut long pauses inside the clip down to `keep_seconds` each."""
        self._analysed_cuts(tr('Shorten pauses'), tr('No pause is longer than that.'),
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
        one = len(ops) == 1
        step = tr('{name} ({count} cut)' if one else '{name} ({count} cuts)').format(name=label, count=len(ops))
        self._apply(lambda h: h.add_ops(ops, label=step),
                    tr('{name}: {count} cut recorded.' if one else '{name}: {count} cuts recorded.').format(
                        name=label, count=len(ops)))

    def apply_auto_fix(self, diagnosis):
        """Apply an Auto fix plan (core/autofix.py) as one undo step. Returns True if it was applied."""
        def run(history):
            if not history.add_step(diagnosis.ops, diagnosis.chain, diagnosis.label):
                raise ValueError(tr('Auto fix has nothing to change.'))

        return self._apply(run, tr('{name} done. Undo takes all of it back in one step. The original recording is '
                                   'unchanged.').format(name=diagnosis.label))

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
                raise ValueError(tr('A cut cannot remove the whole clip'))
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
        changed = self.set_chain('denoise', tr('Noise reduction on' if enabled else 'Noise reduction off'), enabled=enabled)
        if changed and enabled:
            self.bus.status('Cleaning background noise. This runs FFmpeg on the clip.')
        return changed

    def fit_denoise(self):
        """Set noise reduction from the noise this clip has: about 5 dB above its noise floor, which did best in testing.
        The floor is measured on the clip as it would be without noise reduction. Returns the setting, or None."""
        take = self.take
        if take is None:
            return None
        plain = take.history.copy()
        plain.set_chain('denoise', 'measuring', enabled=False)
        try:
            floor = analysis.noise_floor_db(self.workspace.lib.before_level(take, plain), take.rate)
        except CleanError as e:
            self.bus.status(str(e))
            return None
        nf = autofix.noise_setting(floor)
        self.set_chain('denoise', tr('Noise floor {db} dB, from this clip').format(db=f'{nf:.0f}'), enabled=True, nf=nf)
        self.bus.status(tr('Noise floor of this clip: {floor} dB. Noise reduction is set {above} dB above it. Listen to the '
                           'result.').format(floor=f'{floor:.0f}', above=f'{nf - floor:.0f}'))
        return nf

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
        take.history.update_chain(changes, tr('Normalize {gain} dB').format(gain=f'{gain:+.1f}'))
        capped = not use_limiter and wanted - gain > 1.0
        self.bus.notify('edits')
        message = tr('Level set to {gain} dB. The original recording is unchanged.').format(gain=f'{gain:+.1f}')
        if capped:
            message += ' ' + tr('A loud peak holds it below the {wanted} dB the speech needs: turn on the limiter.').format(
                wanted=f'{wanted:+.1f}')
        self.bus.status(message)
        return Normalized(gain, wanted, capped)

    def reset_level(self):
        if self.set_chain('level', tr('Level reset'), gain_db=0.0):
            self.bus.status('Level reset to the recorded level.')
