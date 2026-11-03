"""The left panel: who is speaking, what new takes are for, the takes of that speaker, and the import and folder buttons.

The takes are a list view over a model, drawn by a delegate. Only the rows on screen are ever drawn, so a list of thousands
costs nothing, and the list gets the keyboard, several selected rows and what a screen reader needs from Qt itself. Each row
draws an outline of its take, which is worked out in the background the first time the row is on screen.
"""
import datetime as dt
from collections import namedtuple
from pathlib import Path

import numpy as np
from PySide6.QtCore import QAbstractListModel, QMimeData, QModelIndex, QPoint, QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListView, QStyle, QStyledItemDelegate, QWidget

from .. import audio_core as ac
from ..i18n import tr
from ..session.measure import THUMB_COLUMNS
from ..store import clean_label
from . import theme
from .palette import color
from .widgets import ElidedCombo, button, caption, chip, hbox, label, set_chip, vbox

SOURCES = {'mic': 'Mic', 'system': 'System', 'import': 'Import'}
STATUS_COLOURS = {'In review': 'amber', 'Kept': 'green', 'Changed': 'amber', 'Not kept': 'muted', 'Recording': 'red',
                  'Not saved': 'red'}
EMPTY_TEXT = 'No takes yet.\nPress Record, or import a recording.'
ROW_HEIGHT = 74
ROW_GAP = 4                     # on every side of a row, so 8 pixels between two rows
ROW_ROLE = Qt.ItemDataRole.UserRole

Row = namedtuple('Row', 'take title sub status tip named opened live')


def when(created, now=None):
    """'Today 13:42', 'Yesterday 21:07' or 'Oct 18 09:30' for a take's creation time."""
    try:
        moment = dt.datetime.fromisoformat(created).astimezone()
    except (TypeError, ValueError):
        return ''
    now = now or dt.datetime.now().astimezone()
    days = (now.date() - moment.date()).days
    day = tr('Today') if days == 0 else tr('Yesterday') if days == 1 else moment.strftime('%b %d').replace(' 0', ' ')
    return f'{day} {moment:%H:%M}'


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


def tip_for(take, number, label_text):
    lines = [label_text] if label_text else []
    lines.append(f'{tr("Take")} {number}  ·  {take.seconds:.1f} s  ·  {when(take.created)}  ·  '
                 f'{tr(SOURCES.get(take.source, take.source))}  ·  {tr(take.purpose.name)}')
    text = ' '.join(str(take.meta.get('transcript') or '').split())
    if text:
        lines.append(f'"{text[:140]}{"..." if len(text) > 140 else ""}"')
    if take.changed:
        lines.append(tr('Changed since it was kept. Update kept clip writes the changes.'))
    if take.kept:
        lines.append(tr('Drag the card into Explorer or another program to copy the clip.'))
    return '\n'.join(lines)


class TakeModel(QAbstractListModel):
    """The rows of the list: the recording in progress, if there is one, and then the takes that pass the filter."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.items = []                 # a take for each row, or None for the recording in progress
        self.numbers = {}               # take -> its number, counted in the whole list so a filter keeps the numbers
        self.live = None                # (rows, elapsed) while recording

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() and self.items[index.row()] is not None else flags

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def row_at(self, position):
        take = self.items[position]
        if take is None:
            source = tr('Mic' if self.session.capturing.source == 'mic' else 'System')
            return Row(None, tr('Recording...'), f'{tr("Now")}  ·  {source}', 'Recording', '', False, True, True)
        workspace = self.session.workspace
        number = self.numbers.get(take, 0)
        status = ('Changed' if take.changed else 'Kept' if take.kept else 'Not saved' if take.unsaved else
                  'In review' if take is workspace.current else 'Not kept')
        name = clean_label(take.meta.get('label'))
        if name:
            title, sub = name, f'{when(take.created)}  ·  {take.seconds:.1f} s'
        else:
            title = f'{tr("Take")} {number}  ·  {take.seconds:.1f} s'
            sub = f'{when(take.created)}  ·  {tr(SOURCES.get(take.source, take.source))}'
        return Row(take, title, sub, status, tip_for(take, number, name), bool(name),
                   take is workspace.current and self.live is None, False)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == ROW_ROLE:
            return self.row_at(index.row())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            row = self.row_at(index.row())
            return f'{row.title}, {row.sub}, {tr(row.status)}'.replace('  ·  ', ', ')
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.row_at(index.row()).tip or None
        return None

    def show(self, takes, shown, live):
        """Show `shown` out of `takes`. The rows stay as they are when only what they say has changed, so that typing in
        the Clip tab does not lose the selection or the place in the list. Returns True when the rows themselves changed."""
        self.numbers = {take: len(takes) - place for place, take in enumerate(takes)}
        items = ([None] if live else []) + shown
        if items == self.items:
            if items:
                self.dataChanged.emit(self.index(0), self.index(len(items) - 1))
            return False
        self.beginResetModel()
        self.items = items
        self.endResetModel()
        return True

    def changed(self, take):
        """Redraw the row of one take, if it is showing."""
        if take in self.items:
            where = self.index(self.items.index(take))
            self.dataChanged.emit(where, where)


class TakeDelegate(QStyledItemDelegate):
    """Draws one take: an outline of its waveform, a name, when and from where, and its state."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    @staticmethod
    def fit(text, font, room, named):
        """The text as it is, or for a label cut with an ellipsis to the room the row has."""
        if not named:
            return text
        return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(room) + 4)

    def paint(self, p, option, index):
        row = index.data(ROW_ROLE)
        if row is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        p.translate(rect.left(), rect.top())
        width, height = rect.width(), rect.height()
        selected = bool(option.state & QStyle.StateFlag.State_Selected) or row.opened
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = QRectF(0.5, 0.5, width - 1, height - 1)
        p.setPen(QPen(color('accent', 190) if selected else color('line2' if hover else 'line'), 1.2))
        p.setBrush(color('bg3') if selected else color('bg3' if hover else 'bg2'))
        p.drawRoundedRect(card, 10, 10)
        if option.state & QStyle.StateFlag.State_HasFocus:
            p.setPen(QPen(color('accent'), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(card.adjusted(1, 1, -1, -1), 9, 9)
        if row.opened:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawRoundedRect(QRectF(0.5, 14, 3.5, height - 28), 1.7, 1.7)
        thumb = QRectF(14, 12, 96, 50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('bg0'))
        p.drawRoundedRect(thumb, 6, 6)
        outline = self._outline(index, row)
        if outline is not None:
            hi, lo = outline
            xs = thumb.left() + 3 + np.arange(len(hi)) * (thumb.width() - 6) / len(hi)
            mid = thumb.center().y()
            fit = 21.0 / max(float(np.max(np.abs(hi))), float(np.max(np.abs(lo))), 1e-3)
            p.setPen(QPen(color('accent', 220 if selected else 160), 1.2))
            for x, top, bottom in zip(xs.tolist(), hi.tolist(), lo.tolist()):
                p.drawLine(QPointF(x, mid - max(top, 0.01) * fit), QPointF(x, mid - min(bottom, -0.01) * fit))
        room = width - 134
        title_font, sub_font = theme.ui_font(13, QFont.Weight.DemiBold), theme.ui_font(11)
        p.setFont(title_font)
        p.setPen(color('text'))
        p.drawText(QRectF(122, 12, room, 20), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.fit(row.title, title_font, room, row.named))
        p.setFont(sub_font)
        p.setPen(color('muted'))
        p.drawText(QRectF(122, 32, room, 16), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.fit(row.sub, sub_font, room, row.named))
        name = STATUS_COLOURS.get(row.status, 'muted')
        chip_font = theme.ui_font(10, QFont.Weight.DemiBold)
        chip_text = tr(row.status)
        chip_rect = QRectF(122, 50, QFontMetrics(chip_font).horizontalAdvance(chip_text) + 16, 17)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(name, 38))
        p.drawRoundedRect(chip_rect, 8.5, 8.5)
        p.setPen(color(name))
        p.setFont(chip_font)
        p.drawText(chip_rect, int(Qt.AlignmentFlag.AlignCenter), chip_text)
        if row.live:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('red'))
            p.drawEllipse(QPointF(thumb.right() - 9, thumb.top() + 9), 4, 4)
        p.restore()

    def _outline(self, index, row):
        if row.live:
            live = index.model().live
            return live_thumbnail(*live) if live is not None else None
        return self.session.measure.thumbnail(row.take)


class TakeList(QListView):
    """The list itself. A press on a row and a pull starts a drag, which the panel decides about."""

    dragRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('takes')
        self.setAccessibleName('Takes')
        self.setAccessibleDescription('The takes of this speaker, newest first. Up and Down open a take. Hold Ctrl or Shift '
                                      'to select several')
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setSpacing(ROW_GAP)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def startDrag(self, actions):
        self.dragRequested.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class LibraryPanel(QWidget):
    """Speaker picker, the list of takes (newest first) and the Import and Folder buttons."""

    speakerPicked = Signal(str)
    speakerContext = Signal(object)
    purposePicked = Signal(str)
    newSpeakerRequested = Signal()
    takePicked = Signal(object)
    takeContext = Signal(object, object)
    selectionContext = Signal(object, object)
    selectionChanged = Signal()
    discardRequested = Signal()
    importRequested = Signal()
    folderRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('library')
        self.setFixedWidth(292)
        self._syncing = False               # the selection is being set from the session, not by the person
        # A real drag waits for the mouse, inside Qt; the tests put something else here.
        self.run_drag = lambda drag, actions: drag.exec(actions)
        layout = vbox(self, (16, 16, 16, 14), 10)
        layout.addWidget(caption('Speaker'))
        row = hbox(spacing=8)
        self.speakers = ElidedCombo()
        self.speakers.setAccessibleName('Speaker')
        self.speakers.setPlaceholderText('Add a speaker with +')
        self.speakers.setToolTip('Whose voice the takes below belong to')
        self.speakers.activated.connect(self._speaker_activated)
        self.speakers.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.speakers.customContextMenuRequested.connect(
            lambda pos: self.speakerContext.emit(self.speakers.mapToGlobal(pos)))
        row.addWidget(self.speakers, 1)
        self.add_speaker = button('', 'ghost', 'plus', 'New speaker (Ctrl+N)', size=(36, 36))
        self.add_speaker.setAccessibleName('New speaker')
        self.add_speaker.clicked.connect(self.newSpeakerRequested)
        row.addWidget(self.add_speaker)
        layout.addLayout(row)
        self.purpose = ElidedCombo()
        self.purpose.setAccessibleName('New takes are for')
        for rules in ac.PURPOSES.values():
            self.purpose.addItem(tr('New takes: {purpose}').format(purpose=tr(rules.name)), rules.key)
            self.purpose.setItemData(self.purpose.count() - 1, self._about(rules), Qt.ItemDataRole.ToolTipRole)
        self.purpose.setToolTip('What new takes are for. It sets how long a clip can be and how it is kept. The Clip tab '
                                'changes it for one take')
        self.purpose.activated.connect(lambda i: self.purposePicked.emit(self.purpose.itemData(i)))
        layout.addWidget(self.purpose)
        self.release_note = chip('', 'amber')
        self.release_note.setToolTip("See Speaker details in the gear menu")
        self.release_note.hide()
        layout.addWidget(self.release_note)
        layout.addSpacing(4)
        head = hbox(spacing=8)
        head.addWidget(caption('Takes'))
        head.addStretch(1)
        self.count = chip('0', 'gray')
        head.addWidget(self.count)
        layout.addLayout(head)
        self.filter = QLineEdit()
        self.filter.setAccessibleName('Filter the takes')
        self.filter.setPlaceholderText('Filter by label or transcript')
        self.filter.setToolTip('Show only the takes whose label, transcript or notes contain all of these words')
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda text: self.refresh_takes())
        layout.addWidget(self.filter)
        self.model = TakeModel(session, self)
        self.list = TakeList()
        self.list.setModel(self.model)
        self.list.setItemDelegate(TakeDelegate(session, self.list))
        self.list.selectionModel().selectionChanged.connect(self._selection_changed)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.dragRequested.connect(self._drag_selection)
        self.list.deleteRequested.connect(self.discardRequested)
        layout.addWidget(self.list, 1)
        self.empty = label(EMPTY_TEXT, 'dim', wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty, 1)
        row = hbox(spacing=6)
        self.import_button = button('Import...', None, 'import', 'Copy a recording in as a new take (Ctrl+O)')
        self.import_button.clicked.connect(self.importRequested)
        self.folder_button = button('Folder', None, 'folder', 'Open the output folder in Explorer')
        self.folder_button.clicked.connect(self.folderRequested)
        row.addWidget(self.import_button, 1)
        row.addWidget(self.folder_button, 1)
        layout.addLayout(row)

    # ---- speakers ----

    @staticmethod
    def _about(rules):
        return tr('{about}: {low} to {high} s, kept at {rate} kHz and {bits}-bit').format(
            about=tr(rules.about), low=f'{rules.min_seconds:g}', high=f'{rules.max_seconds:g}',
            rate=f'{rules.export_rate / 1000:g}', bits=rules.bit_depth)

    def refresh_purpose(self):
        self.purpose.setCurrentIndex(self.purpose.findData(self.session.workspace.purpose.key))

    def refresh_release(self):
        """Say when the speaker's release is about to run out, or has."""
        slug = self.session.workspace.speaker
        state, date = self.session.lib.release_ends(slug) if slug else ('none', '')
        text = {'soon': tr('Release runs out on {date}'), 'over': tr('Release ran out on {date}')}.get(state, '').format(date=date)
        set_chip(self.release_note, text, 'red' if state == 'over' else 'amber')

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
        self.purpose.setEnabled(not recording)

    # ---- takes ----

    def _words(self):
        """The words typed in the filter box, folded so that capitals do not matter."""
        return self.filter.text().casefold().split()

    @staticmethod
    def _matches(take, words):
        meta = take.meta
        text = ' '.join((clean_label(meta.get('label')), str(meta.get('transcript') or ''),
                         str(meta.get('notes') or ''))).casefold()
        return all(word in text for word in words)

    def rows(self):
        """What the rows of the list say, in order."""
        return [self.model.row_at(place) for place in range(self.model.rowCount())]

    def selected_takes(self):
        """The takes selected in the list, top first. The recording in progress is not one."""
        places = sorted(index.row() for index in self.list.selectionModel().selectedRows())
        return [self.model.items[place] for place in places if self.model.items[place] is not None]

    def _selection_changed(self):
        self.selectionChanged.emit()
        if self._syncing:
            return
        takes = self.selected_takes()
        if len(takes) == 1 and takes[0] is not self.session.current:
            self.takePicked.emit(takes[0])

    def _follow_current(self):
        """The open take is the selected row, unless several rows are selected, which are the person's to change."""
        take = self.session.current
        chosen = self.selected_takes()
        if len(chosen) > 1 or take not in self.model.items:
            return
        where = self.model.index(self.model.items.index(take))
        if chosen == [take] and self.list.currentIndex() == where:
            return
        self._syncing = True
        self.list.selectionModel().setCurrentIndex(where, self.list.selectionModel().SelectionFlag.ClearAndSelect)
        self.list.scrollTo(where)
        self._syncing = False

    def _context_menu(self, pos):
        index = self.list.indexAt(pos)
        take = self.model.items[index.row()] if index.isValid() else None
        if take is None:
            return
        chosen = self.selected_takes()
        where = self.list.viewport().mapToGlobal(pos)
        if take in chosen and len(chosen) > 1:
            self.selectionContext.emit(chosen, where)
        else:
            self.takeContext.emit(take, where)

    # ---- dragging clips out ----

    def make_drag(self, takes):
        """The drag that carries the WAV files of kept clips out of the window, or None when none of the takes has one."""
        kept = [take for take in takes if take.export_path is not None and Path(take.export_path).is_file()]
        if not kept:
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(take.export_path)) for take in kept])
        drag = QDrag(self.list)
        drag.setMimeData(mime)
        if kept[0] in self.model.items:
            place = self.list.visualRect(self.model.index(self.model.items.index(kept[0])))
            picture = self.list.viewport().grab(place)
            if not picture.isNull():
                drag.setPixmap(picture.scaledToWidth(min(picture.width(), 240), Qt.TransformationMode.SmoothTransformation))
                drag.setHotSpot(QPoint(16, 16))
        return drag

    def _drag_selection(self):
        self._drag(self.selected_takes())

    def _drag(self, takes):
        if self.session.capturing.recording:
            return
        drag = self.make_drag(takes)
        if drag is not None:
            # Only copying is offered: whatever a clip is dropped on can copy it but cannot move it out of the library.
            self.run_drag(drag, Qt.DropAction.CopyAction)

    def refresh_takes(self):
        takes = self.session.workspace.takes
        words = self._words()
        shown = [t for t in takes if self._matches(t, words)] if words else list(takes)
        self.model.show(takes, shown, self.model.live is not None)
        self._follow_current()
        self.count.setText(str(len(takes)) if len(shown) == len(takes) else f'{len(shown)} of {len(takes)}')
        self.empty.setText(tr(EMPTY_TEXT) if not takes else f'{tr("No take has all of these words:")}\n{" ".join(words)}')
        none = not self.model.items
        self.empty.setVisible(none)
        self.list.setVisible(not none)
        self.filter.setVisible(bool(takes) or bool(words))

    def take_changed(self, take):
        self.model.changed(take)

    def set_live(self, rows, elapsed):
        """While recording, the first row shows what has been captured so far. `None` ends it."""
        was = self.model.live is not None
        self.model.live = (rows, elapsed) if rows is not None else None
        if was and rows is not None:
            first = self.model.index(0)
            self.model.dataChanged.emit(first, first)
        elif was or rows is not None:
            self.refresh_takes()
