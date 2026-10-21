"""Helpers for the window tests: fonts for the offscreen platform, canned dialogs and mouse gestures.

The window tests run on Qt's offscreen platform, so no window appears and no display is needed. Nothing here opens an
audio device, and nothing shows a dialog: the window asks through `Dialogs`, and `StubDialogs` answers for it.
"""
import os
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFontDatabase, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def register_fonts():
    """The offscreen platform finds no fonts on Windows, which makes every text measure wrong. Load the system's own."""
    if QFontDatabase.families():
        return
    folder = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'consola.ttf'):
        if (folder / name).exists():
            QFontDatabase.addApplicationFont(str(folder / name))


def pump(app, rounds=3):
    for _ in range(rounds):
        app.processEvents()


def tear_down_qt(app):
    """Destroy the windows and the cached icons in order, while Python is still whole.

    Left to the interpreter's exit, Qt objects are destroyed in no fixed order, and the process sometimes aborted after
    the last test had passed.
    """
    import gc

    from recorder.ui import icons
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(app)
    icons.icon.cache_clear()
    gc.collect()


class StubDialogs:
    """Stands in for `Dialogs`: records what the window asked and answers from attributes the test sets."""

    def __init__(self):
        self.notices, self.confirmations, self.opened, self.shown, self.menus = [], [], [], [], []
        self.confirm_answer = True               # a bool, or a list of answers given in turn
        self.text_answer = None
        self.number_answer = None
        self.folder_answer = None
        self.file_answer = None
        self.settings_answer = None
        self.settings_seen = None

    def notice(self, level, title, text):
        self.notices.append(SimpleNamespace(level=level, title=title, text=text))

    def confirm(self, title, text, yes='OK', no='Cancel', danger=False):
        self.confirmations.append(SimpleNamespace(title=title, text=text, yes=yes, danger=danger))
        answer = self.confirm_answer
        return answer.pop(0) if isinstance(answer, list) else answer

    def ask_text(self, title, prompt, text=''):
        return self.text_answer

    def ask_number(self, title, prompt, value, low, high, decimals=1):
        return self.number_answer

    def choose_folder(self, title, start=''):
        return self.folder_answer

    def choose_file(self, title, start='', filter=''):
        return self.file_answer

    def settings(self, values):
        self.settings_seen = dict(values)
        return self.settings_answer

    def about(self):
        self.shown.append('about')

    def shortcuts(self):
        self.shown.append('shortcuts')

    def popup(self, menu, pos):
        self.menus.append(menu)

    def open_path(self, path):
        self.opened.append(path)


# ---- mouse and keyboard ---------------------------------------------------------------------------------------------

def point(x, y):
    return QPoint(round(x), round(y))


def click(widget, x, y, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(widget, button, modifiers, point(x, y))


def drag(widget, start, end, steps=5, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    """Press at `start`, move in `steps` steps and release at `end`, all in the widget's own coordinates."""
    (x0, y0), (x1, y1) = start, end
    QTest.mousePress(widget, button, modifiers, point(x0, y0))
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, point(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps))
    QTest.mouseRelease(widget, button, modifiers, point(x1, y1))


def hover(widget, x, y):
    """Move the pointer over (x, y) with no button down. The event goes straight to the widget, so it does not matter where
    the offscreen window sits on the offscreen screen."""
    where = QPointF(x, y)
    QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, where, widget.mapToGlobal(where), Qt.MouseButton.NoButton,
                                               Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def wheel(widget, x, y, dy=120, dx=0, modifiers=Qt.KeyboardModifier.NoModifier):
    """One notch of the mouse wheel (120 units) at (x, y)."""
    where = QPointF(x, y)
    event = QWheelEvent(where, widget.mapToGlobal(where), QPoint(0, 0), QPoint(dx, dy), Qt.MouseButton.NoButton, modifiers,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def key(widget, which, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(widget, which, modifiers)
