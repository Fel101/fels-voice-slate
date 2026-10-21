"""A row of command buttons that makes room gracefully: the labels go first, then the least important buttons move
into a "more" menu, so nothing is ever clipped at the minimum window size."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QMenu, QToolButton

from . import icons
from .widgets import hbox

SPACING = 2
MARGINS = (8, 6, 8, 6)


class Entry:
    def __init__(self, kind, widget=None, action=None, overflow=0, w_text=0, w_icon=0):
        self.kind, self.widget, self.action, self.overflow = kind, widget, action, overflow
        self.w_text, self.w_icon = w_text, w_icon

    def width(self, mode):
        return (self.w_text if mode == 'text' else self.w_icon) + SPACING


class ToolBar(QFrame):
    """Buttons made from QActions. `overflow` ranks how soon a button moves into the menu (0 never; higher goes first)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('editbar')
        self._layout = hbox(self, MARGINS, SPACING)
        self._entries = []
        self.mode = 'text'
        self.hidden_actions = []
        self.more = QToolButton()
        self.more.setIcon(icons.icon('more'))
        self.more.setToolTip('More commands')
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.menu = QMenu(self.more)
        self.more.setMenu(self.menu)
        self.more.hide()
        self._more_width = 34

    # ---- building ----

    def _button(self, action):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _measure(self, button):
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.ensurePolished()
        w_text = button.sizeHint().width()
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        w_icon = button.sizeHint().width()
        return w_text, w_icon

    def add_action(self, action, overflow=0):
        button = self._button(action)
        w_text, w_icon = self._measure(button)
        if not action.icon().isNull() and not action.text():
            w_text = w_icon
        self._entries.append(Entry('action', button, action, overflow, w_text, w_icon))
        self._layout.addWidget(button)
        return button

    def add_widget(self, widget, overflow=0, action=None):
        """A widget that is not a button (a segmented control, say). `action` stands in for it in the more menu."""
        widget.ensurePolished()
        width = widget.sizeHint().width()
        self._entries.append(Entry('widget', widget, action, overflow, width, width))
        self._layout.addWidget(widget)
        return widget

    def add_separator(self):
        line = QFrame()
        line.setFixedSize(1, 20)
        line.setStyleSheet('background: #3a3731; border: none;')
        self._entries.append(Entry('sep', line, None, 0, 11, 11))
        self._layout.addSpacing(5)
        self._layout.addWidget(line)
        self._layout.addSpacing(5)

    def add_stretch(self):
        self._layout.addStretch(1)

    def finish(self):
        """Call after adding everything: puts the more button at the end and fits the row to its width."""
        self._layout.addWidget(self.more)
        self._fit()

    # ---- fitting ----

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _total(self, mode, hidden):
        total = sum(e.width(mode) for e in self._entries if e not in hidden)
        return total + (self._more_width + SPACING if hidden else 0)

    def _fit(self, width=None):
        """Show as much as fits in `width` (the toolbar's own width by default): labels, then icons, then a more menu."""
        if not self._entries:
            return
        available = (self.width() if width is None else width) - MARGINS[0] - MARGINS[2]
        hidden = []
        mode = 'text'
        if self._total(mode, hidden) > available:
            mode = 'icon'
        if self._total(mode, hidden) > available:
            for entry in sorted((e for e in self._entries if e.overflow), key=lambda e: -e.overflow):
                hidden.append(entry)
                if self._total(mode, hidden) <= available:
                    break
        self.mode = mode
        for entry in self._entries:
            if entry.kind == 'action':
                entry.widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if mode == 'text'
                                                else Qt.ToolButtonStyle.ToolButtonIconOnly)
            entry.widget.setVisible(entry not in hidden)
        self.menu.clear()
        for entry in hidden:
            if entry.action is not None:
                self.menu.addAction(entry.action)
        self.more.setVisible(bool(hidden))
        self.hidden_actions = [e.action for e in hidden if e.action is not None]
