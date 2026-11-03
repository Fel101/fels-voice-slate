"""The bar across the top: record, transport, the clock, the source and device, and the level meter."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from ..i18n import tr
from . import icons, theme
from .palette import PALETTE
from .widgets import ElidedCombo, Meter, Segmented, button, format_clock, hbox, tagged


class TopBar(QWidget):
    """Buttons are wired to the window's actions; this class only lays them out and shows the state."""

    sourceChanged = Signal(str)
    deviceChanged = Signal(int)
    channelChanged = Signal(object)         # which input of the device, from 0; None mixes the first two

    def __init__(self, session, actions, parent=None):
        super().__init__(parent)
        self.session = session
        self.actions = actions
        self.setObjectName('topbar')
        self.setFixedHeight(78)
        layout = hbox(self, (18, 0, 18, 0), 10)

        self.record_key = 'F9'
        self.record = button('Record      F9', 'record', 'record', actions.record.toolTip())
        self.record.setMinimumWidth(140)
        self.record.setFixedHeight(42)
        self.record.setAccessibleName('Record')
        self.record.clicked.connect(actions.record.trigger)
        layout.addWidget(self.record)
        self.play = self._tool(actions.play, 'play')
        self.stop = self._tool(actions.stop, 'stop')
        self.loop = self._tool(actions.loop, 'loop')
        for b in (self.play, self.stop, self.loop):
            layout.addWidget(b)
        layout.addSpacing(4)

        clock = QVBoxLayout()
        clock.setSpacing(0)
        self.clock = QLabel('00:00.000')
        self.clock.setAccessibleName('Time')
        self.clock.setFont(theme.mono_font(27, QFont.Weight.DemiBold))
        self.clock.setMinimumWidth(142)
        self.sub = QLabel('no take')
        self.sub.setProperty('role', 'muted')
        self.sub.setFont(theme.ui_font(11))
        clock.addWidget(self.clock)
        clock.addWidget(self.sub)
        layout.addLayout(clock)
        layout.addStretch(1)

        self.source = Segmented(['Microphone', 'System audio'], 0, fixed=True)
        self.source.set_tooltips('Record from a microphone', 'Record what this computer is playing (audio you own or '
                                                              'may use)')
        self.source.currentChanged.connect(lambda i: self.sourceChanged.emit(('mic', 'system')[i]))
        layout.addWidget(self.source)
        self.device = ElidedCombo()
        self.device.setMinimumWidth(100)
        self.device.setMaximumWidth(340)
        self.device.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.device.setToolTip('The input to record from')
        self.device.activated.connect(lambda i: self.deviceChanged.emit(self.device.itemData(i)))
        self.device.setAccessibleName('Input device')
        layout.addWidget(self.device, 2)
        self.channel = ElidedCombo()
        self.channel.setAccessibleName('Which input of the device')
        self.channel.setToolTip('Which input of the device to record. With one microphone on a two-input interface, choose '
                                'its input: mixing it with a silent one lowers the voice by 6 dB')
        self.channel.setFixedWidth(100)
        self.channel.activated.connect(lambda i: self.channelChanged.emit(self.channel.itemData(i)))
        self.channel.hide()
        layout.addWidget(self.channel)
        self.arm = self._tool(actions.arm, 'arm')
        layout.addWidget(self.arm)
        self.meter = Meter(300)
        layout.addWidget(self.meter, 2)
        self.gear = QToolButton()
        self.gear.setIcon(icons.icon('settings'))
        self.gear.setIconSize(QSize(18, 18))
        self.gear.setFixedSize(38, 38)
        self.gear.setToolTip('Settings and more')
        self.gear.setAccessibleName('Settings and more')
        self.gear.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.gear.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.menu = QMenu(self.gear)
        self.gear.setMenu(self.menu)
        layout.addWidget(self.gear)

    def _tool(self, action, icon):
        b = QToolButton()
        b.setDefaultAction(action)
        b.setAccessibleName(action.text())
        b.setIcon(icons.icon(icon))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(38, 38)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return b

    # ---- state ----

    def refresh_recording(self):
        capturing = self.session.capturing
        recording = capturing.recording
        stopping = capturing.finishing
        if recording:
            self.record.setText(tr('Stopping...') if stopping else f'{tr("Stop")}      {self.record_key}')
            self.record.setAccessibleName(tr('Stop'))
            self.record.setIcon(icons.icon('stop', 'red'))
            tagged(self.record, kind='recording')
        else:
            self.record.setText(f'{tr("Record")}      {self.record_key}')
            self.record.setAccessibleName(tr('Record'))
            self.record.setIcon(icons.icon('record', 'red_ink'))
            tagged(self.record, kind='record')
        for widget in (self.source, self.device, self.channel):
            widget.setEnabled(not recording)
        self.arm.setEnabled(not recording)
        self.arm.setChecked(capturing.armed)
        self.clock.setStyleSheet(f'color: {PALETTE["red"]};' if recording else '')

    def refresh_devices(self):
        capturing = self.session.capturing
        self.device.blockSignals(True)
        self.device.clear()
        for index, name in capturing.devices:
            self.device.addItem(name, index)
            self.device.setItemData(self.device.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
        self.device.setCurrentIndex(self.device.findData(capturing.device))
        self.device.setToolTip(self.device.currentText() or tr('The input to record from'))
        self.device.blockSignals(False)
        self.source.set_current(0 if capturing.source == 'mic' else 1)
        if not capturing.devices:
            self.device.setPlaceholderText(tr('No input device found'))
        self.channel.blockSignals(True)
        self.channel.clear()
        if capturing.inputs > 1:
            self.channel.addItem(tr('Mix 1+2'), None)
            for number in range(capturing.inputs):
                self.channel.addItem(f'{tr("Input")} {number + 1}', number)
            self.channel.setCurrentIndex(self.channel.findData(capturing.channel))
        self.channel.blockSignals(False)
        self.channel.setVisible(capturing.inputs > 1)

    def refresh_transport(self):
        transport = self.session.transport
        self.play.setIcon(icons.icon('pause' if transport.playing else 'play'))
        self.actions.play.setText(tr('Pause' if transport.playing else 'Play'))
        self.loop.setChecked(transport.looping)
        self.stop.setEnabled(transport.active)

    def set_time(self, seconds, sub, recording=False):
        self.clock.setText(format_clock(seconds))
        self.sub.setText(sub)
