"""A small sign that stays on top of everything while a recording runs and the window is out of sight.

The record key can start a recording from another program, so a stray press must not leave the microphone open with nothing
to show for it. The sign says that it is recording and for how long, and a click on it stops the take. It takes no focus, so
it never steals the keyboard from the program in front.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QLabel, QWidget

from .palette import PALETTE
from .widgets import format_clock, hbox, label

MARGIN = 16


class RecordingBadge(QWidget):
    stopRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName('badge')
        self.setAccessibleName('Recording')
        self.setToolTip('Recording. Click to stop')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = hbox(self, (14, 8, 14, 8), 8)
        dot = QLabel('●')
        dot.setAccessibleName('Recording')
        dot.setStyleSheet(f'color: {PALETTE["red"]};')
        layout.addWidget(dot)
        self.text = label('Recording', size=13, weight=QFont.Weight.DemiBold)
        layout.addWidget(self.text)

    def show_for(self, seconds, key):
        self.text.setText(f'Recording  {format_clock(seconds)[:5]}  ·  {key} stops')
        if not self.isVisible():
            self.adjustSize()
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                room = screen.availableGeometry()
                self.move(room.right() - self.width() - MARGIN, room.top() + MARGIN)
            self.show()

    def mousePressEvent(self, event):
        self.stopRequested.emit()
