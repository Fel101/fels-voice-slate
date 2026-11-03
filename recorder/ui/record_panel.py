"""What the middle of the window shows while a take is being made, and the script line to read."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QWidget

from .. import audio_core as ac
from ..i18n import tr
from . import theme
from .timeline import LiveWaveView
from .widgets import TargetBar, button, caption, card, hbox, label, vbox


class ScriptStrip(QFrame):
    """The current line of the script in large type, with buttons to move along the script."""

    previousRequested = Signal()
    nextRequested = Signal()

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
        layout.addLayout(column, 1)
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
            self.previous.setEnabled(script.index > 0)
            self.next.setEnabled(script.index < len(script.lines) - 1)


class RecordPanel(QWidget):
    """The live waveform of the take being recorded, and where its length falls against the rules."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = vbox(self, spacing=10)
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

    def set_state(self, rows, elapsed, purpose=None):
        rules = purpose or ac.PURPOSES[ac.DEFAULT_PURPOSE]
        self.wave.set_state(rows, elapsed, rules.record_limit)
        self.target.set_seconds(elapsed, rules)
        self.length.setText(f'{elapsed:.1f} s  ·  ' + tr('aim for {low} to {high} s, {least} to {most} s can be kept').format(
            low=f'{rules.best_from:g}', high=f'{rules.best_to:g}', least=f'{rules.min_seconds:g}',
            most=f'{rules.max_seconds:g}'))
