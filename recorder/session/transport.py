"""Playback of the current take: the edited clip or the original, with pause, seek, loop and A/B.

The edited clip is exactly what Keep would write (regions, cuts, filters, cleaning, gain and limiter). While it
plays, the position comes from the audio itself and is mapped back to the master timeline, so the playhead on the
waveform stays where the sound is even across cuts.
"""
import time

import numpy as np

from ..audio_io import Player
from ..clean import CleanError
from ..core import render


REFRESH_DELAY = 0.15            # seconds an edit waits, so a slider dragged during playback re-renders once, not per step


class Transport:
    def __init__(self, bus, workspace, editor, player=None, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.editor = editor
        self.player = player or Player()
        self.clock = clock
        self._stale_since = None        # when the first edit not yet heard in the playing clip was made
        self.mode = None                # 'edited', 'original' or 'joins' while something is loaded for playing
        self.listen = 'edited'          # what Space plays: the edited clip or the original recording
        self.looping = False
        self._segments = None
        self._rate = 0
        bus.listen(self._heard)

    def _heard(self, topic, payload):
        if topic == 'current':
            self.stop()
        elif topic == 'edits' and self.mode == 'edited' and self.player.active and self._stale_since is None:
            self._stale_since = self.clock()

    # ---- state --------------------------------------------------------------------------------------------------------

    @property
    def active(self):
        return self.mode is not None and self.player.active

    @property
    def playing(self):
        return self.mode is not None and self.player.playing

    @property
    def paused(self):
        return self.mode is not None and self.player.paused

    @property
    def head(self):
        """Master seconds of the sound being heard, or None when nothing is playing."""
        if not self.active or self.mode == 'joins':
            return None
        position = self.player.position
        if self.mode == 'edited':
            return render.edited_to_master(position, self._segments, self._rate)
        return position

    # ---- buffers ------------------------------------------------------------------------------------------------------

    def _buffer(self, take, mode):
        if mode == 'edited':
            return (self.workspace.lib.process_native(take),
                    render.segments_of(take.samples, take.rate, take.history.edits))
        return take.mono, None

    def _to_buffer_time(self, master_seconds):
        if self.mode == 'edited':
            return render.master_to_edited(master_seconds, self._segments, self._rate)
        return master_seconds

    def _loop_range(self):
        if not self.looping:
            return None
        sel = self.editor.sel
        if sel is None:
            return (0.0, self.player.seconds)
        a, b = self._to_buffer_time(sel[0]), self._to_buffer_time(sel[1])
        return (a, b) if b > a else None

    # ---- controls -----------------------------------------------------------------------------------------------------

    def play(self, edited=None):
        """Play from the playhead. The edited clip is what Keep writes; the original is the untouched recording.
        Without an argument, whichever the person last chose to listen to."""
        take = self.workspace.current
        if take is None:
            return False
        mode = self.listen if edited is None else 'edited' if edited else 'original'
        self.listen = mode
        try:
            data, segments = self._buffer(take, mode)
        except CleanError as e:
            self.bus.status(str(e))
            return False
        self.mode, self._segments, self._rate = mode, segments, take.rate
        begin = self._to_buffer_time(self.editor.playhead)
        if int(round(begin * take.rate)) >= len(data):
            self.mode = None
            self.bus.status('Nothing is left to play from the playhead.')
            return False
        self.player.load(data, take.rate)
        self.player.play(begin, loop=self._loop_range())
        self.bus.status('Playing the edited clip from the playhead. The playhead follows it.' if edited
                        else 'Playing the original from the playhead.')
        self.bus.notify('transport')
        return True

    def toggle(self):
        """Space: pause what is playing, resume what is paused, otherwise play the edited clip from the playhead."""
        if self.playing:
            self.pause()
        elif self.paused:
            self.resume()
        else:
            self.play()

    def pause(self):
        if self.playing:
            self.player.pause()
            self.bus.notify('transport')

    def resume(self):
        if self.paused:
            self.player.resume()
            self.bus.notify('transport')

    def stop(self):
        """Stop. The playhead goes back to where playing began, as in most editors."""
        if self.mode is None and not self.player.active:
            return
        self.player.stop()
        self.mode = None
        self._stale_since = None
        self.bus.notify('transport')

    def seek(self, master_seconds):
        """Move the playhead; if something is playing or paused, move the sound with it."""
        self.editor.set_playhead(master_seconds)
        if self.active and self.mode != 'joins':
            self.player.seek(self._to_buffer_time(self.editor.playhead))

    def toggle_loop(self):
        self.looping = not self.looping
        if self.active and self.mode != 'joins':
            self.player.set_loop(self._loop_range())
        self.bus.notify('transport')
        return self.looping

    def set_listen(self, which):
        """Choose what plays: 'edited' or 'original'. If something is playing, it switches at the same moment."""
        if which not in ('edited', 'original'):
            raise ValueError(which)
        if self.active and self.mode in ('edited', 'original'):
            if which != self.mode:
                self.ab()
            return
        if which != self.listen:
            self.listen = which
            self.bus.status('Space will play the edited clip.' if which == 'edited' else 'Space will play the original.')
            self.bus.notify('transport')

    def ab(self):
        """Switch between the edited clip and the original at the same moment of the recording."""
        take = self.workspace.current
        if take is None:
            return
        if not self.active or self.mode == 'joins':
            self.set_listen('original' if self.listen == 'edited' else 'edited')
            return
        moment = self.head
        mode = 'original' if self.mode == 'edited' else 'edited'
        try:
            data, segments = self._buffer(take, mode)
        except CleanError as e:
            self.bus.status(str(e))
            return
        self.mode, self._segments, self.listen = mode, segments, mode
        self.player.swap(data, take.rate, self._to_buffer_time(moment))
        self.player.set_loop(self._loop_range())
        self.bus.status('Playing the edited clip.' if mode == 'edited' else 'Playing the original.')
        self.bus.notify('transport')

    def _refresh_edited(self):
        """An edit was made while the edited clip plays: continue with the new render from the same moment."""
        self._stale_since = None
        take = self.workspace.current
        moment = self.head
        try:
            data, segments = self._buffer(take, 'edited')
        except CleanError as e:
            self.bus.status(str(e))
            self.stop()
            return
        self._segments = segments
        self.player.swap(data, take.rate, render.master_to_edited(moment, segments, take.rate))
        self.player.set_loop(self._loop_range())

    def check_cuts(self):
        """Play one second either side of each join, so every cut can be judged without playing the whole clip."""
        take = self.workspace.current
        if take is None or not take.history.cuts:
            self.bus.status('No cuts to check.')
            return False
        try:
            rendered = self.workspace.lib.process_native(take)
        except CleanError as e:
            self.bus.status(str(e))
            return False
        joins = render.join_positions(render.segments_of(take.samples, take.rate, take.history.edits))
        if not joins:
            return False
        gap = np.zeros(int(0.5 * take.rate), dtype=np.float32)
        pieces = []
        for j in joins:
            pieces.extend([rendered[max(0, j - take.rate): j + take.rate], gap])
        self.mode, self._segments, self._rate = 'joins', None, take.rate
        self.player.load(np.concatenate(pieces), take.rate)
        self.player.play(0.0)
        self.bus.status('Playing 1 s either side of each join.')
        self.bus.notify('transport')
        return True

    def tick(self):
        if self.mode is not None and self.player.finished:
            self.stop()
        elif self._stale_since is not None:
            if self.mode == 'edited' and self.player.active:
                if self.clock() - self._stale_since >= REFRESH_DELAY:
                    self._refresh_edited()
            else:
                self._stale_since = None
