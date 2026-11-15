"""The room and level check, in a small window of its own: what to do at each step, then what it found, in plain words
with what to change. The listening itself is session/roomcheck.py."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QFrame, QLabel

from .. import audio_core as ac
from ..core import analysis
from ..i18n import tr
from ..session import roomcheck
from . import theme
from .inspector import GLYPH_WORDS, GLYPHS
from .palette import PALETTE
from .translate import translate_tree
from .widgets import Meter, button, clear_layout, hbox, label, vbox

STATE_OF = {'ok': 'ok', 'warn': 'advice', 'bad': 'bad'}
WHERE_THE_LEVEL_IS = "Windows Settings > System > Sound, under Input"


def findings(result):
    """What a check found, as (state, heading, what it means and what to change), worst first within its order: the
    voice, the room, the headroom, and hum when there is any. `state` is a key of the checklist's GLYPHS."""
    rows = []
    target = f'{ac.TARGET_VOICED_DB:.0f}'
    if not result.heard:
        rows.append(('bad', tr('No voice was heard'),
                     tr('Check that the microphone chosen beside Record is the one you speak into, that it is not muted, '
                        'and that its level in {where} is up. Then check again.').format(where=tr(WHERE_THE_LEVEL_IS))))
    else:
        speech = f'{result.speech_db:.0f}'
        low = result.speech_db < ac.TARGET_VOICED_DB
        if result.level == 'ok':
            rows.append(('ok', tr('The microphone level is right'),
                         tr('Your voice measured {speech} dB, and the target is {target} dB.').format(speech=speech,
                                                                                                     target=target)))
        elif low:
            heading = tr('The microphone level is a little low') if result.level == 'warn' else \
                tr('The microphone level is too low')
            rows.append((STATE_OF[result.level], heading,
                         tr('Your voice measured {speech} dB, and the target is {target} dB. Raise the input level in '
                            '{where}, or speak a little closer to the microphone. Normalize in Process can raise it '
                            'later, but it raises the noise of the room with it.').format(
                             speech=speech, target=target, where=tr(WHERE_THE_LEVEL_IS))))
        else:
            heading = tr('The microphone level is a little high') if result.level == 'warn' else \
                tr('The microphone level is too high')
            rows.append((STATE_OF[result.level], heading,
                         tr('Your voice measured {speech} dB, and the target is {target} dB. Lower the input level in '
                            '{where}, or move a little further from the microphone.').format(
                             speech=speech, target=target, where=tr(WHERE_THE_LEVEL_IS))))
    floor = f'{result.floor_at_target_db:.0f}'
    measured = (tr('With your voice at the target level, the room would measure {floor} dB.') if result.heard else
                tr('The room measured {floor} dB.')).format(floor=floor)
    quieter = tr('Turn off a fan or anything else that hums or whirs nearby, close the door and the windows, or move the '
                 'microphone closer to your mouth.')
    asks = tr('The Check tab asks for {limit} dB or less.').format(limit=f'{analysis.NOISE_OK_DB:.0f}')
    flags = tr('The Check tab flags anything above {limit} dB.').format(limit=f'{analysis.NOISE_WARN_DB:.0f}')
    if result.room == 'ok':
        rows.append(('ok', tr('The room is quiet enough'), f'{measured} {asks}'))
    elif result.room == 'warn':
        rows.append(('advice', tr('The room is a little noisy'), f'{measured} {asks} {quieter}'))
    else:
        rows.append(('bad', tr('The room is too noisy'), f'{measured} {flags} {quieter}'))
    if result.heard:
        if result.headroom == 'bad':
            rows.append(('bad', tr('The voice clipped'),
                         tr('{share}% of it reached full scale, which cannot be repaired afterwards. Lower the input level '
                            'until the loudest words stay below it.').format(share=f'{result.clipped * 100:.1f}')))
        elif result.headroom == 'warn':
            rows.append(('advice', tr('The loudest words come close to full scale'),
                         tr('They reached {peak} dBFS, and the Check tab asks for {limit} dBFS or less. The limiter in '
                            'Process keeps peaks under it; lowering the input level a little leaves room.').format(
                             peak=f'{result.peak_dbfs:.1f}', limit=f'{analysis.PEAK_OK_DBFS:.0f}')))
        else:
            rows.append(('ok', tr('There is room above the loudest words'),
                         tr('They reached {peak} dBFS.').format(peak=f'{result.peak_dbfs:.1f}')))
    if result.hum is not None:
        rows.append(('advice', tr('Mains hum at {hz} Hz').format(hz=f'{result.hum[0]:.0f}'),
                     tr('A steady hum was heard in the quiet. Move the microphone and its cable away from power leads '
                        'and chargers, or turn on the Hum filter in Process.')))
    return rows


def verdict(rows):
    """A heading for the whole check."""
    states = {state for state, _, _ in rows}
    if 'bad' in states:
        return tr('Change this before you record')
    if 'advice' in states:
        return tr('Ready to record, and worth a look')
    return tr('Ready to record')


class RoomCheckDialog(QDialog):
    """Start, follow and read a check. It does not block the window, and closing it stops a check that is listening."""

    closed = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle('Check the room and level')
        self.setMinimumWidth(520)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label('This listens for a few seconds of quiet, then to you reading one sentence, and says whether '
                               'the room is quiet enough and whether the microphone is set right, by the Check tab\'s own '
                               'limits.', wrap=True))
        self.privacy = label('Nothing it hears is saved: no file is written, and once the check is done the sound is let '
                             'go, so it is not kept in memory either. Only the measures stay, in this window.', 'muted',
                             size=11, wrap=True)
        layout.addWidget(self.privacy)
        self.step = QLabel()
        self.step.setFont(theme.ui_font(17, QFont.Weight.DemiBold))
        self.step.setWordWrap(True)
        layout.addWidget(self.step)
        self.sentence = QLabel()
        self.sentence.setFont(theme.ui_font(19))
        self.sentence.setWordWrap(True)
        self.sentence.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.sentence)
        self.meter = Meter(460)
        self.meter.setAccessibleName('Input level')
        layout.addWidget(self.meter)
        self.found = QFrame()
        self.found_rows = vbox(self.found, spacing=10)
        layout.addWidget(self.found)
        layout.addStretch(1)
        row = hbox(spacing=8)
        row.addStretch(1)
        self.start_button = button('Start', 'primary', None, 'Listen to the room, then to you')
        self.start_button.clicked.connect(self._start_or_stop)
        self.close_button = button('Close')
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.start_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        translate_tree(self)
        self.refresh()

    def _start_or_stop(self):
        room = self.session.room
        if room.active:
            room.end()
        else:
            self.session.transport.stop()           # a take playing would be measured as the room
            room.start()
        self.refresh()

    def refresh(self):
        """Show the step going on, or what the last check found."""
        room = self.session.room
        self.meter.setVisible(room.active)
        self.sentence.setVisible(room.phase == 'speak')
        if room.phase == 'quiet':
            left = max(0, int(roomcheck.QUIET_SECONDS - room.heard_seconds() + 0.999))
            self.step.setText(tr('Stay quiet: listening to the room... {seconds}').format(seconds=left))
        elif room.phase == 'speak':
            self.step.setText(tr('Now read this, at your usual distance and loudness:'))
            self.sentence.setText(f'“{tr(roomcheck.SENTENCE)}”')
        elif room.result is not None:
            rows = findings(room.result)
            self.step.setText(verdict(rows))
        else:
            self.step.setText(tr('Press Start, then stay quiet for {seconds} seconds.').format(
                seconds=f'{roomcheck.QUIET_SECONDS:g}'))
        self.step.setAccessibleName(self.step.text())
        self._show_findings(findings(room.result) if room.result is not None and not room.active else [])
        self.start_button.setText(tr('Stop') if room.active else tr('Check again') if room.result is not None else tr('Start'))
        reason = room.refused()
        self.start_button.setEnabled(room.active or not reason)
        self.start_button.setToolTip(tr(reason) if reason and not room.active else tr('Listen to the room, then to you'))
        # The meter and the sentence come and go with the steps. The dialog's minimum width keeps Qt from growing it on
        # its own, so it is sized again whenever they change: the sentence to read is never cut off.
        parts = (self.meter.isVisibleTo(self), self.sentence.isVisibleTo(self))
        if parts != getattr(self, '_parts', None):
            self._parts = parts
            self.adjustSize()

    def tick(self):
        """Called by the window's timer while a check listens: the meter and the countdown."""
        self.meter.set_levels(*self.session.capturing.level)
        if self.session.room.phase == 'quiet':
            self.refresh()

    def _show_findings(self, rows):
        if getattr(self, '_shown', None) == rows:
            return
        self._shown = rows
        clear_layout(self.found_rows)
        for state, heading, text in rows:
            line = hbox(spacing=10)
            glyph, name = GLYPHS[state]
            mark = QLabel(glyph)
            mark.setAccessibleName(tr(GLYPH_WORDS[state]))
            mark.setFont(theme.ui_font(14, QFont.Weight.Bold))
            mark.setFixedWidth(18)
            mark.setStyleSheet(f'color: {PALETTE[name]};')
            line.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
            words = vbox(spacing=2)
            title = label(heading, weight=QFont.Weight.DemiBold, wrap=True)
            words.addWidget(title)
            words.addWidget(label(text, 'muted', wrap=True))
            line.addLayout(words, 1)
            self.found_rows.addLayout(line)
        self.found.setVisible(bool(rows))
        self.adjustSize()

    def closeEvent(self, event):
        if self.session.room.active:
            self.session.room.end()
        self.closed.emit()
        super().closeEvent(event)
