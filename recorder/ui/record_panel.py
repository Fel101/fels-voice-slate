"""What the middle of the window shows while a take is being made, and the script line to read."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QWidget

from .. import audio_core as ac
from ..i18n import tr
from ..session.run import again_key
from . import theme
from .palette import PALETTE
from .timeline import LiveWaveView
from .widgets import TargetBar, button, caption, card, hbox, label, vbox


def hands_free_summary(options, record_key):
    """The hands-free options that are on, in a line, or '' when none is."""
    parts = []
    if options['next_line']:
        parts.append(tr('the next line after each take'))
    if options['stop_after_pause']:
        parts.append(tr('a take ends after {seconds} s of quiet').format(seconds=f"{options['pause_seconds']:g}"))
    if options['auto_start']:
        parts.append(tr('a {seconds} s count-in, so {key} starts a run').format(seconds=options['count_in'], key=record_key))
    return tr('Hands-free: {options}').format(options=', '.join(parts)) if parts else ''


class ScriptStrip(QFrame):
    """The current line of the script in large type, with buttons to move along the script, and which hands-free options
    are on."""

    previousRequested = Signal()
    nextRequested = Signal()
    handsFreeRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('script')
        layout = hbox(self, (20, 14, 14, 14), 14)
        column = vbox(spacing=4)
        self.caption = caption('Script')
        column.addWidget(self.caption)
        self.line = QLabel()
        self.line.setWordWrap(True)
        self.line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.line)
        self.hands = label('', 'dim', size=11, wrap=True)
        column.addWidget(self.hands)
        layout.addLayout(column, 1)
        self.hands_free = button('Hands-free...', 'ghost', None, 'Go to the next line, end a take after a pause, and count in '
                                                                 'the next take, by themselves (Ctrl+Shift+H)')
        self.hands_free.clicked.connect(self.handsFreeRequested)
        layout.addWidget(self.hands_free, 0, Qt.AlignmentFlag.AlignVCenter)
        self.previous = button('', 'ghost', 'chevron_left', 'Previous line (Ctrl+Up)', size=(36, 36))
        self.next = button('', 'ghost', 'chevron_right', 'Next line (Ctrl+Down)', size=(36, 36))
        self.previous.setAccessibleName('Previous line')
        self.next.setAccessibleName('Next line')
        self.previous.clicked.connect(self.previousRequested)
        self.next.clicked.connect(self.nextRequested)
        layout.addWidget(self.previous)
        layout.addWidget(self.next)
        self.set_large(False)

    def set_large(self, large):
        """Large type while recording, where it is read aloud from a distance; compact otherwise."""
        self.line.setFont(theme.ui_font(22 if large else 15, QFont.Weight.DemiBold if large else QFont.Weight.Normal))

    def refresh(self):
        script = self.session.script
        self.setVisible(bool(script.lines))
        if script.lines:
            done = sum(1 for line in script.progress(self.session.workspace.takes) if line.kept)
            self.caption.setText(tr('Script  ·  line {line} of {total}  ·  {done} kept').format(
                line=script.index + 1, total=len(script.lines), done=done).upper())
            self.line.setText(script.current)
            running = self.session.run.active or self.session.capturing.recording
            self.previous.setEnabled(script.index > 0 and not running)
            self.next.setEnabled(script.index < len(script.lines) - 1 and not running)
            self.hands_free.setEnabled(not running)
            summary = hands_free_summary(self.session.run.options, self.session.capturing.record_key)
            self.hands.setText(summary)
            self.hands.setVisible(bool(summary))


class RunBanner(QFrame):
    """What hands-free reading is doing, in large type: the count-in, then whether the voice has been heard, and the keys
    that end the run and read the line again. Nothing of it is heard: the count-in must not reach the recording."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        layout = vbox(self, (18, 12, 18, 12), 4)
        self.big = label('', size=24, weight=QFont.Weight.DemiBold, wrap=True)
        self.big.setAccessibleName('Script run')
        layout.addWidget(self.big)
        self.keys = label('', 'muted', wrap=True)
        layout.addWidget(self.keys)
        self.hide()

    def show_state(self, state, active, record_key):
        """`state` is ScriptRun.state(): (what, value, line, lines), or None to hide."""
        if state is None:
            self.hide()
            return
        what, value, line, lines = state
        colour = 'amber' if what == 'counting' else 'red'
        self.big.setFont(theme.ui_font(44 if what == 'counting' else 24, QFont.Weight.DemiBold))
        if what == 'counting':
            text = tr('Next line in {seconds}').format(seconds=value)
        elif what == 'listening':
            text = tr('Recording: read the line')
        elif what == 'heard':
            text = tr('Recording: it ends after {seconds} s of quiet').format(seconds=f'{value:g}')
        else:
            text = tr('Stopping...')
        self.big.setText(text)
        self.big.setStyleSheet(f'color: {PALETTE[colour]};')
        keys = tr('Line {line} of {lines}. Esc or {key} ends the run, {again} reads the line again.') if active else \
            tr('Line {line} of {lines}. {key} stops, {again} reads the line again.')
        self.keys.setText(keys.format(line=line, lines=lines, key=record_key, again=again_key(record_key)))
        self.show()


class RecordPanel(QWidget):
    """The live waveform of the take being recorded, and where its length falls against the rules."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = vbox(self, spacing=10)
        self.banner = RunBanner()
        layout.addWidget(self.banner)
        self.waiting = QWidget()                # takes the room of the live view during a count-in, when nothing records
        layout.addWidget(self.waiting, 1)
        self.waiting.hide()
        self.wave = LiveWaveView()
        layout.addWidget(self.wave, 1)
        box = card()
        inner = vbox(box, (16, 12, 16, 12), 6)
        head = hbox(spacing=8)
        head.addWidget(caption('Take length'))
        head.addStretch(1)
        self.length = label('', 'muted')
        head.addWidget(self.length)
        inner.addLayout(head)
        self.target = TargetBar()
        inner.addWidget(self.target)
        layout.addWidget(box)
        self.length_box = box

    def set_counting(self, counting):
        """During a count-in nothing is being recorded, so the live view and its REC sign give way to the count-in."""
        self.waiting.setVisible(counting)
        self.wave.setVisible(not counting)
        self.length_box.setVisible(not counting)

    def set_state(self, rows, elapsed, purpose=None, markers=()):
        rules = purpose or ac.PURPOSES[ac.DEFAULT_PURPOSE]
        self.wave.set_state(rows, elapsed, rules.record_limit, markers)
        self.target.set_seconds(elapsed, rules)
        self.length.setText(f'{elapsed:.1f} s  ·  ' + tr('aim for {low} to {high} s, {least} to {most} s can be kept').format(
            low=f'{rules.best_from:g}', high=f'{rules.best_to:g}', least=f'{rules.min_seconds:g}',
            most=f'{rules.max_seconds:g}'))
