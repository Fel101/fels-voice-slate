"""Put the language in use on a finished part of the window: its labels, buttons, fields, tooltips and the names a screen
reader reads. A panel can then be written in English, and what it says later goes through tr() in the helpers."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QWidget

from .. import i18n
from ..i18n import tr


def _swap(get, put):
    text = get()
    if text and tr(text) != text:
        put(tr(text))


def _words_of(widget):
    _swap(widget.toolTip, widget.setToolTip)
    _swap(widget.accessibleName, widget.setAccessibleName)
    _swap(widget.accessibleDescription, widget.setAccessibleDescription)
    _swap(widget.windowTitle, widget.setWindowTitle)
    if isinstance(widget, (QLabel, QAbstractButton)):
        _swap(widget.text, widget.setText)
    elif isinstance(widget, (QLineEdit, QPlainTextEdit)):
        _swap(widget.placeholderText, widget.setPlaceholderText)
    elif isinstance(widget, QComboBox):
        _swap(widget.placeholderText, widget.setPlaceholderText)
        for index in range(widget.count()):
            _swap(lambda: widget.itemText(index), lambda text: widget.setItemText(index, text))
            tip = widget.itemData(index, Qt.ItemDataRole.ToolTipRole)
            if isinstance(tip, str) and tr(tip) != tip:
                widget.setItemData(index, tr(tip), Qt.ItemDataRole.ToolTipRole)


def translate_tree(root):
    """Translate `root` and everything inside it. Nothing happens in English."""
    if not i18n.active():
        return root
    for widget in [root, *root.findChildren(QWidget)]:
        _words_of(widget)
    for action in root.findChildren(QAction):
        _swap(action.text, action.setText)
        _swap(action.toolTip, action.setToolTip)
        _swap(action.statusTip, action.setStatusTip)
    return root
