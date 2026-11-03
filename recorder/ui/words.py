"""The words of the take, under the waveform: click a word to select it, Shift+click to run on to another, and Delete cuts
them, as for a selection made on the waveform. A word that was cut stands struck through, and clicking it brings it back.

The words are what the speech recognizer heard, with the times it gave them, so they are only here for a take that has a
draft. They are plain links in a text view, which gives the keyboard (Tab, Enter) and a screen reader everything they need.
"""
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QTextBrowser

from ..core.reading import word_is_cut
from . import theme
from .palette import PALETTE

HEIGHT = 66


class WordStrip(QTextBrowser):
    """The clickable words of the open take. It only reports what was clicked; the window asks the editor to do it."""

    wordsPicked = Signal(int, int)      # the first and last word, in order
    wordRestored = Signal(int)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('words')
        self.setAccessibleName('The words of this take')
        self.setAccessibleDescription('Choose a word to select it on the waveform. Delete cuts it. Choosing a word that was '
                                      'cut brings it back')
        self.setToolTip('Click a word to select it, Shift+click to select up to another, then press Delete to cut. '
                        'Click a struck-out word to bring it back')
        self.setOpenLinks(False)
        self.setFixedHeight(HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)            # a click must not take Space and Delete from the window
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFont(theme.ui_font(13))
        self.anchorClicked.connect(self._clicked)
        self._anchor = None                 # the word a Shift+click runs from
        self._drawn = None
        self.hide()

    def _words(self):
        take = self.session.current
        return (take.meta.get('draft_words') or []) if take is not None else []

    def _chosen(self):
        """Which words the selection covers: all of a word's middle has to be inside it."""
        sel = self.session.editor.sel
        if sel is None:
            return set()
        return {i for i, (_, start, end) in enumerate(self._words()) if sel[0] <= (start + end) / 2 <= sel[1]}

    def refresh(self):
        take = self.session.current
        words = self._words()
        self.setVisible(bool(words) and not self.session.capturing.recording)
        if not words:
            self._drawn = None
            return
        edits = take.history.edits
        cut = [i for i, (_, start, end) in enumerate(words) if word_is_cut(start, end, edits)]
        chosen = self._chosen()
        drawn = (take.id, len(words), words[0], words[-1], tuple(cut), tuple(sorted(chosen)))
        if drawn == self._drawn:
            return
        self._drawn = drawn
        top = self.verticalScrollBar().value()
        self.setHtml(self._page(words, set(cut), chosen))
        self.verticalScrollBar().setValue(top)

    @staticmethod
    def _page(words, cut, chosen):
        parts = []
        for i, (word, _, _) in enumerate(words):
            text = html.escape(word)
            if i in cut:
                body = f'<s style="color:{PALETTE["dim"]}">{text}</s>'
            elif i in chosen:
                body = f'<span style="background-color:{PALETTE["accent_d"]};color:{PALETTE["ink"]}">{text}</span>'
            else:
                body = f'<span style="color:{PALETTE["text"]}">{text}</span>'
            parts.append(f'<a href="{i}" style="text-decoration:none">{body}</a>')
        return f'<div style="line-height:130%">{" ".join(parts)}</div>'

    def _clicked(self, url):
        index = int(url.toString())
        take = self.session.current
        words = self._words()
        if take is None or not 0 <= index < len(words):
            return
        _, start, end = words[index]
        if word_is_cut(start, end, take.history.edits):
            self.wordRestored.emit(index)
            return
        shift = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        first = self._anchor if shift and self._anchor is not None else index
        self._anchor = first
        self.wordsPicked.emit(*sorted((first, index)))
