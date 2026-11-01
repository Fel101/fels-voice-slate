"""The left panel: who is speaking, the takes of that speaker, and the import and folder buttons."""
import datetime as dt
from pathlib import Path

import numpy as np
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea, QWidget

from ..store import clean_label
from . import theme
from .palette import color
from .widgets import ElidedCombo, button, caption, chip, hbox, label, vbox

SOURCES = {'mic': 'Mic', 'system': 'System', 'import': 'Import'}
STATUS_COLOURS = {'In review': 'amber', 'Kept': 'green', 'Not kept': 'muted', 'Recording': 'red', 'Not saved': 'red'}
THUMB_COLUMNS = 90
EMPTY_TEXT = 'No takes yet.\nPress Record (F9), or import a recording.'


def when(created, now=None):
    """'Today 13:42', 'Yesterday 21:07' or 'Oct 18 09:30' for a take's creation time."""
    try:
        moment = dt.datetime.fromisoformat(created).astimezone()
    except (TypeError, ValueError):
        return ''
    now = now or dt.datetime.now().astimezone()
    days = (now.date() - moment.date()).days
    day = 'Today' if days == 0 else 'Yesterday' if days == 1 else moment.strftime('%b %d').replace(' 0', ' ')
    return f'{day} {moment:%H:%M}'


def thumbnail(session, take):
    """The take's outline for its card: (highs, lows) in 90 columns, computed once."""
    if 'thumb' not in take.cache:
        pyramid = session.measure.pyramid(take)
        lo, hi = pyramid.columns(0, pyramid.n, THUMB_COLUMNS)
        take.cache['thumb'] = (hi, lo)
    return take.cache['thumb']


def live_thumbnail(rows, elapsed):
    """The outline of what is being recorded so far, as highs and lows in at most 90 columns."""
    if not len(rows) or elapsed <= 0:
        return None
    edges = np.linspace(0, elapsed, THUMB_COLUMNS + 1)
    index = np.minimum(np.searchsorted(edges, rows[:, 0], side='left') - 1, THUMB_COLUMNS - 1).clip(0)
    hi, lo = np.zeros(THUMB_COLUMNS), np.zeros(THUMB_COLUMNS)
    np.maximum.at(hi, index, rows[:, 2])
    np.minimum.at(lo, index, rows[:, 1])
    return hi, lo


class TakeCard(QWidget):
    """One take in the list: an outline of its waveform, a name, when and from where, and its state."""

    clicked = Signal(object)
    contextRequested = Signal(object, object)
    dragRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.take = None
        self.title = self.sub = self.status = ''
        self.selected = self.live = self.hover = self.named = False
        self.thumb = None
        self._press = None                  # where the left button went down, until it is let go or a drag starts
        self.setFixedHeight(74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def set_state(self, take, title, sub, status, selected, thumb, live=False, named=False):
        """`named` is true when the title is a label someone chose, which can be long and is cut to fit."""
        changed = ((self.title, self.sub, self.status, self.selected, self.live, self.named)
                   != (title, sub, status, selected, live, named))
        self.take, self.title, self.sub, self.status = take, title, sub, status
        self.selected, self.live, self.thumb, self.named = selected, live, thumb, named
        if changed or live:
            self.update()

    def _fit(self, text, font, room):
        """The text as it is, or for a label cut with an ellipsis to the room the card has."""
        if not self.named:
            return text
        return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(room) + 4)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.take is not None:
            self._press = e.position().toPoint()
            self.clicked.emit(self.take)
        elif e.button() == Qt.MouseButton.RightButton and self.take is not None:
            self.contextRequested.emit(self.take, e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        """Pulling a kept take's card away from where the button went down offers its clip to whatever it is dropped on."""
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton and self.take is not None \
                and (e.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
            self._press = None
            if self.take.kept:
                self.dragRequested.emit(self.take)

    def mouseReleaseEvent(self, e):
        self._press = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(color('accent', 190) if self.selected else color('line2' if self.hover else 'line'), 1.2))
        p.setBrush(color('bg3') if self.selected else color('bg3' if self.hover else 'bg2'))
        p.drawRoundedRect(card, 10, 10)
        if self.selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawRoundedRect(QRectF(0.5, 14, 3.5, self.height() - 28), 1.7, 1.7)
        thumb = QRectF(14, 12, 96, 50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('bg0'))
        p.drawRoundedRect(thumb, 6, 6)
        if self.thumb is not None:
            hi, lo = self.thumb
            xs = thumb.left() + 3 + np.arange(len(hi)) * (thumb.width() - 6) / len(hi)
            mid = thumb.center().y()
            fit = 21.0 / max(float(np.max(np.abs(hi))), float(np.max(np.abs(lo))), 1e-3)
            p.setPen(QPen(color('accent', 220 if self.selected else 160), 1.2))
            for x, top, bottom in zip(xs.tolist(), hi.tolist(), lo.tolist()):
                p.drawLine(QPointF(x, mid - max(top, 0.01) * fit), QPointF(x, mid - min(bottom, -0.01) * fit))
        room = self.width() - 134
        title_font, sub_font = theme.ui_font(13, QFont.Weight.DemiBold), theme.ui_font(11)
        p.setFont(title_font)
        p.setPen(color('text'))
        p.drawText(QRectF(122, 12, room, 20), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self._fit(self.title, title_font, room))
        p.setFont(sub_font)
        p.setPen(color('muted'))
        p.drawText(QRectF(122, 32, room, 16), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self._fit(self.sub, sub_font, room))
        name = STATUS_COLOURS.get(self.status, 'muted')
        chip_font = theme.ui_font(10, QFont.Weight.DemiBold)
        chip_rect = QRectF(122, 50, QFontMetrics(chip_font).horizontalAdvance(self.status) + 16, 17)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(name, 38))
        p.drawRoundedRect(chip_rect, 8.5, 8.5)
        p.setPen(color(name))
        p.setFont(chip_font)
        p.drawText(chip_rect, int(Qt.AlignmentFlag.AlignCenter), self.status)
        if self.live:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('red'))
            p.drawEllipse(QPointF(thumb.right() - 9, thumb.top() + 9), 4, 4)


class LibraryPanel(QWidget):
    """Speaker picker, the list of takes (newest first) and the Import and Folder buttons."""

    speakerPicked = Signal(str)
    speakerContext = Signal(object)
    newSpeakerRequested = Signal()
    takePicked = Signal(object)
    takeContext = Signal(object, object)
    importRequested = Signal()
    folderRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('library')
        self.setFixedWidth(292)
        self._cards = []
        self._live = None
        # A real drag waits for the mouse, inside Qt; the tests put something else here.
        self.run_drag = lambda drag, actions: drag.exec(actions)
        layout = vbox(self, (16, 16, 16, 14), 10)
        layout.addWidget(caption('Speaker'))
        row = hbox(spacing=8)
        self.speakers = ElidedCombo()
        self.speakers.setPlaceholderText('Add a speaker with +')
        self.speakers.setToolTip('Whose voice the takes below belong to')
        self.speakers.activated.connect(self._speaker_activated)
        self.speakers.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.speakers.customContextMenuRequested.connect(
            lambda pos: self.speakerContext.emit(self.speakers.mapToGlobal(pos)))
        row.addWidget(self.speakers, 1)
        self.add_speaker = button('', 'ghost', 'plus', 'New speaker (Ctrl+N)', size=(36, 36))
        self.add_speaker.clicked.connect(self.newSpeakerRequested)
        row.addWidget(self.add_speaker)
        layout.addLayout(row)
        layout.addSpacing(4)
        head = hbox(spacing=8)
        head.addWidget(caption('Takes'))
        head.addStretch(1)
        self.count = chip('0', 'gray')
        head.addWidget(self.count)
        layout.addLayout(head)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Filter by label or transcript')
        self.filter.setToolTip('Show only the takes whose label, transcript or notes contain all of these words')
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda text: self.refresh_takes())
        layout.addWidget(self.filter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.cards_layout = vbox(holder, (0, 0, 4, 0), 8)
        self.cards_layout.addStretch(1)
        self.empty = label(EMPTY_TEXT, 'dim', wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cards_layout.insertWidget(0, self.empty)
        self.scroll.setWidget(holder)
        layout.addWidget(self.scroll, 1)
        row = hbox(spacing=6)
        self.import_button = button('Import...', None, 'import', 'Copy a recording in as a new take (Ctrl+O)')
        self.import_button.clicked.connect(self.importRequested)
        self.folder_button = button('Folder', None, 'folder', 'Open the output folder in Explorer')
        self.folder_button.clicked.connect(self.folderRequested)
        row.addWidget(self.import_button, 1)
        row.addWidget(self.folder_button, 1)
        layout.addLayout(row)

    # ---- speakers ----

    def _speaker_activated(self, index):
        slug = self.speakers.itemData(index)
        if slug:
            self.speakerPicked.emit(slug)

    def refresh_speakers(self):
        workspace = self.session.workspace
        self.speakers.blockSignals(True)
        self.speakers.clear()
        for slug, name in workspace.speakers:
            self.speakers.addItem(name, slug)
        index = self.speakers.findData(workspace.speaker)
        self.speakers.setCurrentIndex(index)
        self.speakers.blockSignals(False)
        self.refresh_enabled()

    def refresh_enabled(self):
        """The picker and the + are off while a recording runs, so each recording keeps the speaker it started with.
        Called whenever the recording starts or ends, not only when the list of speakers changes."""
        recording = self.session.capturing.recording
        self.speakers.setEnabled(not recording and self.speakers.count() > 0)
        self.add_speaker.setEnabled(not recording)

    # ---- takes ----

    def _card(self, index):
        while len(self._cards) <= index:
            card = TakeCard()
            card.clicked.connect(self.takePicked)
            card.contextRequested.connect(self.takeContext)
            card.dragRequested.connect(self._drag)
            self._cards.append(card)
            self.cards_layout.insertWidget(len(self._cards) - 1, card)
        return self._cards[index]

    def _words(self):
        """The words typed in the filter box, folded so that capitals do not matter."""
        return self.filter.text().casefold().split()

    @staticmethod
    def _matches(take, words):
        meta = take.meta
        text = ' '.join((clean_label(meta.get('label')), str(meta.get('transcript') or ''),
                         str(meta.get('notes') or ''))).casefold()
        return all(word in text for word in words)

    @staticmethod
    def _tip(take, number, label_text):
        lines = [label_text] if label_text else []
        lines.append(f'Take {number}  ·  {take.seconds:.1f} s  ·  {when(take.created)}  ·  '
                     f'{SOURCES.get(take.source, take.source)}')
        text = ' '.join(str(take.meta.get('transcript') or '').split())
        if text:
            lines.append(f'"{text[:140]}{"..." if len(text) > 140 else ""}"')
        if take.kept:
            lines.append('Drag the card into Explorer or another program to copy the clip.')
        return '\n'.join(lines)

    # ---- dragging a clip out ----

    def make_drag(self, take, source):
        """The drag that carries a kept clip's WAV file out of the window, or None when the take is not kept or the file is
        not there."""
        path = take.export_path
        if path is None or not Path(path).is_file():
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        drag = QDrag(source)
        drag.setMimeData(mime)
        picture = source.grab()
        if not picture.isNull():
            drag.setPixmap(picture.scaledToWidth(min(picture.width(), 240), Qt.TransformationMode.SmoothTransformation))
            drag.setHotSpot(QPoint(16, 16))
        return drag

    def _drag(self, take):
        if self.session.capturing.recording:
            return
        card = next((c for c in self._cards if c.take is take and not c.isHidden()), self)
        drag = self.make_drag(take, card)
        if drag is not None:
            # Only copying is offered: whatever the clip is dropped on can copy it but cannot move it out of the library.
            self.run_drag(drag, Qt.DropAction.CopyAction)

    def refresh_takes(self):
        workspace = self.session.workspace
        takes = workspace.takes
        words = self._words()
        shown = [t for t in takes if self._matches(t, words)] if words else list(takes)
        live = self._live is not None
        rows = ([None] if live else []) + shown
        for i, item in enumerate(rows):
            card = self._card(i)
            card.show()
            if item is None:
                thumb = live_thumbnail(*self._live)
                card.set_state(None, 'Recording...', f'Now  ·  {"Mic" if self.session.capturing.source == "mic" else "System"}',
                               'Recording', True, thumb, live=True)
                card.setToolTip('')
                continue
            number = len(takes) - takes.index(item)            # counted in the whole list, so a filter keeps the numbers
            status = ('Kept' if item.kept else 'Not saved' if item.unsaved else
                      'In review' if item is workspace.current else 'Not kept')
            name = clean_label(item.meta.get('label'))
            if name:
                title, sub = name, f'{when(item.created)}  ·  {item.seconds:.1f} s'
            else:
                title = f'Take {number}  ·  {item.seconds:.1f} s'
                sub = f'{when(item.created)}  ·  {SOURCES.get(item.source, item.source)}'
            card.set_state(item, title, sub, status, item is workspace.current and not live,
                           thumbnail(self.session, item), named=bool(name))
            card.setToolTip(self._tip(item, number, name))
        for card in self._cards[len(rows):]:
            card.hide()
        self.count.setText(str(len(takes)) if len(shown) == len(takes) else f'{len(shown)} of {len(takes)}')
        self.empty.setText(EMPTY_TEXT if not takes else f'No take has all of these words:\n{" ".join(words)}')
        self.empty.setVisible(not rows)
        self.filter.setVisible(bool(takes) or bool(words))

    def set_live(self, rows, elapsed):
        """While recording, the first card shows what has been captured so far. `None` ends it."""
        was = self._live is not None
        self._live = (rows, elapsed) if rows is not None else None
        if self._live is not None or was:
            self.refresh_takes()
