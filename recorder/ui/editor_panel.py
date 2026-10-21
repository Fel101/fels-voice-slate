"""The middle of the window: the edit toolbar, waveform, spectrogram, overview and the numeric readout; the live view
while recording; and the welcome screen before there is a take."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QLayout, QStackedWidget, QWidget

from . import theme
from .record_panel import RecordPanel, ScriptStrip
from .timeline import Minimap, SpectrogramView, ViewRange, WaveformView
from .toolbar import ToolBar
from .widgets import ElidedLabel, Segmented, button, hbox, label, vbox


class InfoRow(QWidget):
    """In, Out, Length and Playhead as numbers that can be typed, then the zoom and a mouse hint.

    In a narrow window it gives things up in this order until it fits: the zoom and the hint, the long names, the Length
    box (the selection's length is also on the waveform).
    """

    LEVELS = ((True, True, True), (True, False, True), (False, False, True), (False, False, False))   # long, extras, length

    def __init__(self, session, view, parent=None):
        super().__init__(parent)
        self.session = session
        self.view = view
        layout = hbox(self, spacing=12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)      # this row fits itself to the room it gets
        self._names = []
        self.in_spin, self.in_label = self._spin(layout, 'In', 'In', self._in_done, 'Start of the selection, in seconds')
        self.out_spin, self.out_label = self._spin(layout, 'Out', 'Out', self._out_done, 'End of the selection, in seconds')
        self.length_spin, self.length_label = self._spin(layout, 'Length', 'Len', self._length_done,
                                                         'Length of the selection, in seconds')
        self.head_spin, self.head_label = self._spin(layout, 'Playhead', 'Head', self._head_done,
                                                     'Where playing starts, in seconds')
        self.zoom_label = label('Zoom', 'dim')
        self.zoom = label('', size=11)
        self.zoom.setFont(theme.mono_font(11))
        layout.addWidget(self.zoom_label)
        layout.addWidget(self.zoom)
        self.hint = ElidedLabel('Wheel zooms  ·  Shift+wheel pans', drop=True)
        self.hint.setProperty('role', 'dim')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.hint, 1)
        view.changed.connect(self.refresh)

    def _spin(self, layout, name, short, done, tip):
        cell = hbox(spacing=5)
        title = label(name, 'dim')
        cell.addWidget(title)
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setSingleStep(0.01)
        spin.setSuffix(' s')
        spin.setKeyboardTracking(False)
        # No setButtonSymbols(NoButtons): with this stylesheet it shrinks the text box to one pixel. The stylesheet already
        # gives the arrows no width, so the box is plain text that still steps with the wheel and the arrow keys.
        spin.setFont(theme.mono_font(12))
        spin.setProperty('flat', True)
        spin.setFixedWidth(80)
        spin.setToolTip(tip)
        spin.editingFinished.connect(done)
        cell.addWidget(spin)
        layout.addLayout(cell)
        self._names.append((title, name, short))
        return spin, title

    def _apply(self, level):
        long_names, extras, show_length = self.LEVELS[level]
        for title, name, short in self._names:
            title.setText(name if long_names else short)
        for widget in (self.zoom_label, self.zoom, self.hint):
            widget.setVisible(extras)
        self.length_label.setVisible(show_length)
        self.length_spin.setVisible(show_length)

    def fit(self, width):
        """Choose the richest arrangement that fits in `width`; returns its level."""
        layout = self.layout()
        for level in range(len(self.LEVELS)):
            self._apply(level)
            layout.activate()
            if layout.minimumSize().width() <= width:
                return level
        return len(self.LEVELS) - 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit(self.width())

    def _bounds(self):
        editor = self.session.editor
        sel = editor.sel
        a = sel[0] if sel else (editor.in_point if editor.in_point is not None else editor.playhead)
        b = sel[1] if sel else (editor.out_point if editor.out_point is not None else editor.playhead)
        return a, b

    def _in_done(self):
        _, b = self._bounds()
        self.session.editor.set_selection(self.in_spin.value(), b)

    def _out_done(self):
        a, _ = self._bounds()
        self.session.editor.set_selection(a, self.out_spin.value())

    def _length_done(self):
        a, _ = self._bounds()
        self.session.editor.set_selection(a, a + self.length_spin.value())

    def _head_done(self):
        self.session.transport.seek(self.head_spin.value())

    def refresh(self):
        take = self.session.current
        enabled = take is not None
        for spin in (self.in_spin, self.out_spin, self.length_spin, self.head_spin):
            spin.setEnabled(enabled)
        if not enabled:
            return
        a, b = self._bounds()
        total = take.seconds
        heard = self.session.transport.head
        head = self.session.editor.playhead if heard is None else heard
        values = ((self.in_spin, a, total), (self.out_spin, b, total), (self.length_spin, max(b - a, 0.0), total),
                  (self.head_spin, head, total))
        for spin, value, high in values:
            if spin.hasFocus():
                continue                                    # never rewrite a number while it is being typed
            spin.blockSignals(True)
            spin.setRange(0.0, high)
            spin.setValue(value)
            spin.blockSignals(False)
        self.zoom.setText(f'{self.view.zoom:.1f}×')


class EmptyState(QFrame):
    """Shown until there is a take: how to make one."""

    recordRequested = Signal()
    importRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('empty')
        layout = vbox(self, (40, 40, 40, 40), 10)
        layout.addStretch(1)
        title = label('Nothing to edit yet', size=20, weight=QFont.Weight.DemiBold)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        text = label('Record a take with a microphone, record what this computer plays (audio you own or may use), or '
                     'import a recording. The original is saved the moment you stop, and edits never change it.',
                     'muted', wrap=True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column = hbox()                      # a wrapped label inside a layout row gets its full height; centred by
        column.addStretch(1)                 # alignment alone it would be cut to the height of its first lines
        column.addWidget(text, 8)
        column.addStretch(1)
        layout.addLayout(column)
        layout.addSpacing(10)
        row = hbox(spacing=10)
        row.addStretch(1)
        record = button('Record  (F9)', 'primary', 'record')
        record.clicked.connect(self.recordRequested)
        imported = button('Import a recording...', None, 'import')
        imported.clicked.connect(self.importRequested)
        row.addWidget(record)
        row.addWidget(imported)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)


class EditorPanel(QWidget):
    """Script line on top, then one of: the editor, the live recording view or the welcome screen."""

    recordRequested = Signal()
    importRequested = Signal()

    EMPTY, EDIT, LIVE = 0, 1, 2

    def __init__(self, session, actions, parent=None):
        super().__init__(parent)
        self.session = session
        self.view = ViewRange(self)
        layout = vbox(self, (16, 14, 16, 8), 10)
        self.script = ScriptStrip(session)
        self.script.hide()
        layout.addWidget(self.script)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.empty = EmptyState()
        self.empty.recordRequested.connect(self.recordRequested)
        self.empty.importRequested.connect(self.importRequested)
        self.stack.addWidget(self.empty)

        edit = QWidget()
        column = vbox(edit, spacing=10)
        self.toolbar = ToolBar()
        self.listen = Segmented(['Original', 'Edited'], 1, fixed=True)
        self.listen.set_tooltips('Play the untouched recording', 'Play the clip as Keep will write it')
        a = actions
        for item, overflow in ((a.undo, 0), (a.redo, 0)):
            self.toolbar.add_action(item, overflow)
        self.toolbar.add_separator()
        for item, overflow in ((a.cut, 0), (a.trim, 1), (a.silence, 3), (a.fade_in, 4), (a.fade_out, 4), (a.gain, 5)):
            self.toolbar.add_action(item, overflow)
        self.toolbar.add_separator()
        for item, overflow in ((a.auto_analyze, 1), (a.auto_fix, 1), (a.auto_trim, 8), (a.shorten, 7)):
            self.toolbar.add_action(item, overflow)
        self.toolbar.add_stretch()
        self.toolbar.add_widget(self.listen, 2, a.ab)
        self.toolbar.add_action(a.check_joins, 6)
        self.toolbar.add_separator()
        for item in (a.zoom_out, a.zoom_in, a.fit):
            self.toolbar.add_action(item, 0)
        self.toolbar.finish()
        column.addWidget(self.toolbar)
        self.waveform = WaveformView(session, self.view)
        self.spectrogram = SpectrogramView(session, self.view)
        self.minimap = Minimap(session, self.view)
        column.addWidget(self.waveform, 5)
        column.addWidget(self.spectrogram, 3)
        column.addWidget(self.minimap)
        self.info = InfoRow(session, self.view)
        column.addWidget(self.info)
        self.stack.addWidget(edit)

        self.live = RecordPanel()
        self.stack.addWidget(self.live)

    def set_mode(self, mode):
        self.stack.setCurrentIndex(mode)
        self.script.set_large(mode == self.LIVE)
