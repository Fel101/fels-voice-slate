"""Small widgets shared by the panels: layout helpers, chips, the level meter, a switch, a segmented control."""
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle,
                               QStyleOptionComboBox, QStylePainter, QVBoxLayout, QWidget)

from .. import audio_core as ac
from . import icons, theme
from .palette import color


# ---- helpers ----------------------------------------------------------------------------------------------------------

def vbox(parent=None, margins=(0, 0, 0, 0), spacing=0):
    layout = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def hbox(parent=None, margins=(0, 0, 0, 0), spacing=0):
    layout = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def clear_layout(layout):
    """Empty a layout and delete what was in it. Each widget is hidden and detached at once, because `deleteLater` waits
    for the event loop and the old widget would otherwise still be painted over the new ones until then."""
    while layout.count():
        item = layout.takeAt(0)
        widget, inner = item.widget(), item.layout()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif inner is not None:
            clear_layout(inner)
            inner.deleteLater()


def tagged(widget, **props):
    """Set dynamic properties the stylesheet selects on, and have the style look again."""
    for key, value in props.items():
        widget.setProperty(key, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def caption(text):
    """A small spaced-out capital heading above a group of controls."""
    label = QLabel(text.upper())
    label.setFont(theme.ui_font(11, QFont.Weight.DemiBold, 0.9))
    label.setProperty('role', 'dim')
    return label


def label(text='', role=None, size=None, weight=None, wrap=False):
    out = QLabel(text)
    if size is not None or weight is not None:
        out.setFont(theme.ui_font(size or theme.BASE_PIXELS, weight))
    if role:
        out.setProperty('role', role)
    out.setWordWrap(wrap)
    return out


class Chip(QLabel):
    """A small rounded label for a state, a count or a warning. It paints itself: a stylesheet does not round the
    background of a plain label."""

    COLOURS = {'amber': ('amber', 38), 'green': ('green', 34), 'red': ('red', 38), 'violet': ('accent', 40),
               'gray': (None, 0)}

    def __init__(self, text='', kind='gray', parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
        self.setContentsMargins(9, 2, 9, 2)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        name, alpha = self.COLOURS.get(self.kind, self.COLOURS['gray'])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(name, alpha) if name else color('bg3'))
        radius = min(self.height() / 2, 10)
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        p.setPen(color(name) if name else color('muted'))
        p.setFont(self.font())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if self.wordWrap():
            flags |= Qt.TextFlag.TextWordWrap
        p.drawText(self.contentsRect(), int(flags), self.text())


def chip(text, kind):
    return Chip(text, kind)


def set_chip(widget, text, kind=None):
    """Change a chip's text and colour; an empty text hides it."""
    widget.setText(text)
    if kind:
        widget.set_kind(kind)
    widget.setVisible(bool(text))


def button(text='', kind=None, icon=None, tip=None, size=None):
    """A push button. It takes focus from the Tab key only, so a click never steals Space from play and pause."""
    out = QPushButton(text)
    if kind:
        tagged(out, kind=kind)
    if icon:
        out.setIcon(icons.icon(icon, 'text' if kind not in ('primary', 'recording', 'record') else _icon_color(kind)))
        out.setIconSize(QSize(16, 16))
    if tip:
        out.setToolTip(tip)
    if size:
        out.setFixedSize(*size)
    out.setCursor(Qt.CursorShape.PointingHandCursor)
    out.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return out


def _icon_color(kind):
    return {'primary': 'ink', 'record': '#230d0b', 'recording': 'red'}.get(kind, 'text')


def card():
    frame = QFrame()
    frame.setObjectName('card')
    return frame


class ElidedLabel(QLabel):
    """A label that shortens its text with an ellipsis instead of forcing its container wider. With `drop`, text that
    does not fit is left out altogether, which suits a hint that is useless half-written."""

    def __init__(self, text='', mode=Qt.TextElideMode.ElideRight, drop=False, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.drop = drop
        self.full_text = ''
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        self.set_full_text(text)

    def set_full_text(self, text):
        self.full_text = text
        if not self.drop:
            self.setToolTip(text)
        self.update()

    def paintEvent(self, event):
        metrics = QFontMetrics(self.font())
        if self.drop and metrics.horizontalAdvance(self.full_text) > self.width():
            return
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        shown = metrics.elidedText(self.full_text, self.mode, self.width())
        p.drawText(self.rect(), int(self.alignment()), shown)


class ElidedCombo(QComboBox):
    """A drop-down that shows a long choice shortened in the middle, with the whole name in its tooltip."""

    def paintEvent(self, event):
        p = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        room = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxEditField, self)
        option.currentText = option.fontMetrics.elidedText(option.currentText, Qt.TextElideMode.ElideMiddle,
                                                           max(room.width() - 8, 10))
        p.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


def format_clock(seconds):
    seconds = max(0.0, float(seconds))
    return f'{int(seconds // 60):02d}:{seconds % 60:06.3f}'


# ---- the level meter --------------------------------------------------------------------------------------------------

class Meter(QWidget):
    """Level in dBFS: a bar for the RMS, a tick for the latest peak and a held peak that falls after a moment."""

    FLOOR = -60.0
    HOLD_SECONDS = 1.2
    MARKS = (-60, -48, -36, -24, -12, -6, 0)
    FEW_MARKS = (-60, -36, -12, 0)              # when the meter is too narrow for every label

    def __init__(self, width=300, parent=None):
        super().__init__(parent)
        self.rms = self.peak = self.hold = self.FLOOR
        self._drawn = (self.FLOOR, self.FLOOR, self.FLOOR)
        self._hold_at = 0.0
        self.setMinimumWidth(120)
        self.setMaximumWidth(width)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(40)
        self.setToolTip('Input level in dBFS. The bar is the RMS level, the white tick the latest peak and the amber '
                        'tick the held peak.')

    def set_levels(self, peak_db, rms_db, now=None):
        now = time.monotonic() if now is None else now
        peak_db, rms_db = max(self.FLOOR, float(peak_db)), max(self.FLOOR, float(rms_db))
        if peak_db >= self.hold or now - self._hold_at > self.HOLD_SECONDS:
            self.hold, self._hold_at = peak_db, now
        self.rms, self.peak = rms_db, peak_db
        shown = (self.rms, self.peak, self.hold)
        if any(abs(a - b) > 0.4 for a, b in zip(shown, self._drawn)):       # a change of under half a dB is not visible
            self._drawn = shown
            self.update()

    def reset(self):
        self.rms = self.peak = self.hold = self.FLOOR
        self._drawn = (self.FLOOR, self.FLOOR, self.FLOOR)
        self.update()

    def _x(self, db, width):
        return max(0.0, min(1.0, (db - self.FLOOR) / -self.FLOOR)) * width

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        track = QRectF(0, 4, w, 12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('bg3'))
        p.drawRoundedRect(track, 6, 6)
        gradient = QLinearGradient(0, 0, w, 0)
        for stop, name in ((0.0, 'green'), (0.62, 'green'), (0.84, 'amber'), (1.0, 'red')):
            gradient.setColorAt(stop, color(name))
        p.setBrush(QBrush(gradient))
        p.setClipRect(QRectF(0, 0, self._x(self.rms, w), 20))
        p.drawRoundedRect(track, 6, 6)
        p.setClipping(False)
        for db, name in ((self.peak, 'text'), (self.hold, 'amber')):
            if db > self.FLOOR:
                x = self._x(db, w)
                p.setPen(QPen(color(name), 2.0))
                p.drawLine(QPointF(x, 3), QPointF(x, 17))
        p.setFont(theme.ui_font(10))
        p.setPen(color('dim'))
        for db in self.MARKS if w >= 230 else self.FEW_MARKS:
            x = self._x(db, w)
            p.drawLine(QPointF(x, 18), QPointF(x, 21))
            left = min(max(x - 16, 0), w - 32)
            align = Qt.AlignmentFlag.AlignLeft if db == -60 else Qt.AlignmentFlag.AlignRight if db == 0 \
                else Qt.AlignmentFlag.AlignHCenter
            p.drawText(QRectF(left, 22, 32, 14), int(align | Qt.AlignmentFlag.AlignVCenter), str(db))


# ---- a switch ---------------------------------------------------------------------------------------------------------

class Switch(QAbstractButton):
    """An on/off switch. `toggled(bool)` comes from QAbstractButton."""

    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(38, 22)

    def sizeHint(self):
        return QSize(38, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('accent' if on else 'bg4', None if enabled else 110))
        p.drawRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        p.setBrush(color('ink' if on else 'muted'))
        p.drawEllipse(QRectF(19 if on else 3, 3, 16, 16))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color('accent'), 1.5))
            p.drawRoundedRect(QRectF(-1, -1, 40, 24), 12, 12)


# ---- a segmented control ----------------------------------------------------------------------------------------------

class Segmented(QWidget):
    """Two or three joined buttons of which one is chosen. `currentChanged(int)` is emitted when the person picks one."""

    currentChanged = Signal(int)

    SHAPES = {'l': 'border-top-right-radius:0;border-bottom-right-radius:0;',
              'm': 'border-radius:0;border-left:none;',
              'r': 'border-top-left-radius:0;border-bottom-left-radius:0;border-left:none;',
              's': ''}

    def __init__(self, labels, current=0, fixed=False, parent=None):
        super().__init__(parent)
        if fixed:                                   # never squeezed below its own size, so no label is clipped
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = hbox(self)
        self.buttons = []
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            shape = 's' if len(labels) == 1 else 'l' if i == 0 else 'r' if i == len(labels) - 1 else 'm'
            b.setStyleSheet(self.SHAPES[shape])
            b.clicked.connect(lambda checked=False, index=i: self._picked(index))
            layout.addWidget(b)
            self.buttons.append(b)
        self._current = -1
        self.set_current(current)

    @property
    def current(self):
        return self._current

    def _picked(self, index):
        if index != self._current:
            self.set_current(index)
            self.currentChanged.emit(index)

    def set_current(self, index):
        self._current = index
        for i, b in enumerate(self.buttons):
            tagged(b, seg='on' if i == index else 'off')

    def set_tooltips(self, *tips):
        for b, tip in zip(self.buttons, tips):
            b.setToolTip(tip)


# ---- the take length bar ----------------------------------------------------------------------------------------------

class TargetBar(QWidget):
    """Where the take's length falls against the rules: 6 to 40 s can be kept, 10 to 25 s works best."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seconds = 0.0
        self.setFixedHeight(36)

    def set_seconds(self, seconds):
        self.seconds = float(seconds)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        def x(s):
            return min(s / ac.MAX_SECONDS, 1.0) * w

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('bg3'))
        p.drawRoundedRect(QRectF(0, 6, w, 10), 5, 5)
        for a, b, name in ((ac.MIN_SECONDS, ac.TARGET_MIN_SECONDS, 'amber'),
                           (ac.TARGET_MIN_SECONDS, ac.TARGET_MAX_SECONDS, 'green'),
                           (ac.TARGET_MAX_SECONDS, ac.MAX_SECONDS, 'amber')):
            p.setBrush(color(name, 110 if name == 'amber' else 170))
            p.drawRect(QRectF(x(a), 6, x(b) - x(a), 10))
        p.setBrush(color('text'))
        p.drawRoundedRect(QRectF(x(self.seconds) - 1.5, 2, 3, 18), 1.5, 1.5)
        p.setFont(theme.ui_font(10))
        p.setPen(color('dim'))
        for s in (0, 6, 10, 25, 40):
            left = min(max(x(s) - 14, 0), w - 28)
            align = Qt.AlignmentFlag.AlignLeft if s == 0 else Qt.AlignmentFlag.AlignRight if s == 40 \
                else Qt.AlignmentFlag.AlignHCenter
            p.drawText(QRectF(left, 20, 28, 12), int(align | Qt.AlignmentFlag.AlignVCenter), f'{s}')
