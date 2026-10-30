"""The left panel: who is speaking, the takes of that speaker, and the import and folder buttons."""
import datetime as dt

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QWidget

from . import theme
from .palette import color
from .widgets import ElidedCombo, button, caption, chip, hbox, label, vbox

SOURCES = {'mic': 'Mic', 'system': 'System', 'import': 'Import'}
STATUS_COLOURS = {'In review': 'amber', 'Kept': 'green', 'Not kept': 'muted', 'Recording': 'red', 'Not saved': 'red'}
THUMB_COLUMNS = 90


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.take = None
        self.title = self.sub = self.status = ''
        self.selected = self.live = self.hover = False
        self.thumb = None
        self.setFixedHeight(74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def set_state(self, take, title, sub, status, selected, thumb, live=False):
        changed = (self.title, self.sub, self.status, self.selected, self.live) != (title, sub, status, selected, live)
        self.take, self.title, self.sub, self.status = take, title, sub, status
        self.selected, self.live, self.thumb = selected, live, thumb
        if changed or live:
            self.update()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.take is not None:
            self.clicked.emit(self.take)
        elif e.button() == Qt.MouseButton.RightButton and self.take is not None:
            self.contextRequested.emit(self.take, e.globalPosition().toPoint())

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
        p.setFont(theme.ui_font(13, QFont.Weight.DemiBold))
        p.setPen(color('text'))
        p.drawText(QRectF(122, 12, self.width() - 134, 20), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.title)
        p.setFont(theme.ui_font(11))
        p.setPen(color('muted'))
        p.drawText(QRectF(122, 32, self.width() - 134, 16), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.sub)
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
        layout = vbox(self, (16, 16, 16, 14), 10)
        layout.addWidget(caption('Speaker'))
        row = hbox(spacing=8)
        self.speakers = ElidedCombo()
        self.speakers.setPlaceholderText('Add a speaker with +')
        self.speakers.setToolTip('Whose voice the takes below belong to')
        self.speakers.activated.connect(self._speaker_activated)
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
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.cards_layout = vbox(holder, (0, 0, 4, 0), 8)
        self.cards_layout.addStretch(1)
        self.empty = label('No takes yet.\nPress Record (F9), or import a recording.', 'dim', wrap=True)
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
            self._cards.append(card)
            self.cards_layout.insertWidget(len(self._cards) - 1, card)
        return self._cards[index]

    def refresh_takes(self):
        workspace = self.session.workspace
        takes = workspace.takes
        live = self._live is not None
        rows = ([None] if live else []) + list(takes)
        for i, item in enumerate(rows):
            card = self._card(i)
            card.show()
            if item is None:
                thumb = live_thumbnail(*self._live)
                card.set_state(None, 'Recording...', f'Now  ·  {"Mic" if self.session.capturing.source == "mic" else "System"}',
                               'Recording', True, thumb, live=True)
                continue
            number = len(takes) - takes.index(item)
            status = ('Kept' if item.kept else 'Not saved' if item.unsaved else
                      'In review' if item is workspace.current else 'Not kept')
            card.set_state(item, f'Take {number}  ·  {item.seconds:.1f} s',
                           f'{when(item.created)}  ·  {SOURCES.get(item.source, item.source)}', status,
                           item is workspace.current and not live, thumbnail(self.session, item))
        for card in self._cards[len(rows):]:
            card.hide()
        self.count.setText(str(len(takes)))
        self.empty.setVisible(not rows)

    def set_live(self, rows, elapsed):
        """While recording, the first card shows what has been captured so far. `None` ends it."""
        was = self._live is not None
        self._live = (rows, elapsed) if rows is not None else None
        if self._live is not None or was:
            self.refresh_takes()
