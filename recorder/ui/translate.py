"""Put the language in use on a finished part of the window: its labels, buttons, fields, tooltips and the names a screen
reader reads. A panel can then be written in English, and what it says later goes through tr() in the helpers."""
from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QWidget

from .. import i18n
from ..i18n import tr

QT_FILE_LABELS = {'&Look in:', 'Files of &type:'}


class _DialogTranslator(QTranslator):
    """Fill gaps in Qt's catalogue for the file picker labels used here."""

    def translate(self, context, source, disambiguation=None, n=-1):
        if context == 'QFileDialog' and source in QT_FILE_LABELS:
            return tr(source)
        return super().translate(context, source, disambiguation, n) or None


def install_qt_language(app, code):
    """Give Qt's own menus, standard dialogs and date controls the window's language."""
    QLocale.setDefault(QLocale(code))
    if code == 'en':
        return None
    translator = _DialogTranslator(app)
    if translator.load(f'qtbase_{code}', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
        return translator
    return None


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
