"""Everything that asks the person something: messages, confirmations, file choosers, and the three dialogs.

The window never builds a dialog itself. It calls `Dialogs`, so the tests replace it with canned answers and no test
ever waits for a click.
"""
import html
import platform
from pathlib import Path

import PySide6
from PySide6.QtCore import QDate, QRegularExpression, Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QKeySequence, QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QInputDialog, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QWidget)

from .. import __version__, audio_import, clean, dataset, manifest, settings, share, transcript
from .. import audio_core as ac
from ..i18n import LANGUAGES, tr
from .actions import SPEC, key_problem, shortcut_text
from .palette import PALETTE
from .translate import translate_tree
from .widgets import button, hbox, label, vbox

AUDIO_FILTER = f'Audio files ({" ".join("*" + suffix for suffix in audio_import.SUFFIXES)});;All files (*)'
THEME_NAMES = {'dark': 'Dark', 'light': 'Light', 'contrast': 'High contrast', 'system': 'As in Windows'}
PREFERRED_HEIGHT = 820              # of the Settings dialog, when the screen has the room
LEVEL_ICONS = {'info': QMessageBox.Icon.Information, 'warning': QMessageBox.Icon.Warning,
               'error': QMessageBox.Icon.Critical}


class Dialogs:
    """The window's way of asking. Each method returns the answer, or None / False when the person backs out."""

    def __init__(self, parent):
        self.parent = parent

    def notice(self, level, title, text):
        box = QMessageBox(LEVEL_ICONS.get(level, QMessageBox.Icon.Information), tr(title), tr(text),
                          QMessageBox.StandardButton.Ok, self.parent)
        box.exec()

    def confirm(self, title, text, yes='OK', no='Cancel', danger=False):
        box = QMessageBox(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question, tr(title), tr(text),
                          QMessageBox.StandardButton.NoButton, self.parent)
        accept = box.addButton(tr(yes), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr(no), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(accept if not danger else box.buttons()[-1])
        box.exec()
        return box.clickedButton() is accept

    def ask_text(self, title, prompt, text=''):
        value, ok = QInputDialog.getText(self.parent, tr(title), tr(prompt), QLineEdit.EchoMode.Normal, text)
        return value.strip() if ok and value.strip() else None

    def ask_number(self, title, prompt, value, low, high, decimals=1):
        number, ok = QInputDialog.getDouble(self.parent, tr(title), tr(prompt), value, low, high, decimals)
        return number if ok else None

    def choose_folder(self, title, start=''):
        folder = QFileDialog.getExistingDirectory(self.parent, tr(title), str(start))
        return folder or None

    def choose_file(self, title, start='', filter=AUDIO_FILTER):
        path, _ = QFileDialog.getOpenFileName(self.parent, tr(title), str(start), tr(filter))
        return path or None

    def choose_files(self, title, start='', filter=AUDIO_FILTER):
        """The files chosen, in a dialog that lets several be picked at once: an empty list when it is cancelled."""
        paths, _ = QFileDialog.getOpenFileNames(self.parent, tr(title), str(start), tr(filter))
        return paths

    def choose_save_file(self, title, start='', filter='All files (*)'):
        """A file name to write to. The dialog asks before it replaces a file."""
        path, _ = QFileDialog.getSaveFileName(self.parent, tr(title), str(start), filter)
        return path or None

    def choose_item(self, title, prompt, items):
        """The position of the item chosen from a list of names, or None."""
        item, ok = QInputDialog.getItem(self.parent, tr(title), tr(prompt), list(items), 0, False)
        return list(items).index(item) if ok and item in items else None

    def copy_options(self, name, kept_rate=ac.EXPORT_RATE):
        """(format, sample rate) for a copy of a kept clip, or None."""
        return self._ask(CopyDialog(name, self.parent, kept_rate))

    def manifest_options(self, speaker_name):
        """('speaker' or 'all', 'jsonl' or 'csv') for a manifest, or None."""
        return self._ask(ManifestDialog(speaker_name, self.parent))

    def settings(self, values):
        return self._ask(SettingsDialog(values, self.parent))

    @staticmethod
    def _ask(dialog):
        return dialog.values() if translate_tree(dialog).exec() == QDialog.DialogCode.Accepted else None

    def speaker_details(self, name, record, has_file):
        """What is typed in the Speaker details dialog, as a dict for Library.set_release, or None."""
        return self._ask(SpeakerDialog(name, record, has_file, self.parent))

    def dataset_options(self, speaker_name):
        """('speaker' or 'all', layout, sample rate, from the originals?) for a dataset, or None."""
        return self._ask(DatasetDialog(speaker_name, self.parent))

    def keep_many(self, speaker_name, takes, defaults):
        """(origin, allowed use) for the takes that have none, once the person has made the statement for all of `takes`,
        or None when they back out."""
        return self._ask(KeepManyDialog(speaker_name, takes, defaults, self.parent))

    def about(self):
        translate_tree(AboutDialog(self.parent)).exec()

    def shortcuts(self):
        translate_tree(ShortcutsDialog(self.parent)).exec()

    def popup(self, menu, pos):
        """Show a context menu at a screen position and run what is chosen."""
        translate_tree(menu).exec(pos)

    def open_path(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ---- settings ---------------------------------------------------------------------------------------------------------

class PathRow(QWidget):
    """A text box for a path, a Browse button and a line saying whether the path is any good."""

    def __init__(self, value, choose, tip, parent=None):
        super().__init__(parent)
        layout = vbox(self, spacing=4)
        row = hbox(spacing=6)
        self.edit = QLineEdit(value or '')
        self.edit.setToolTip(tip)
        self.edit.setAccessibleName(tip)
        row.addWidget(self.edit, 1)
        browse = QPushButton('Browse...')
        browse.clicked.connect(lambda: self._browse(choose))
        row.addWidget(browse)
        layout.addLayout(row)
        self.status = label('', 'dim', size=11, wrap=True)
        layout.addWidget(self.status)

    def _browse(self, choose):
        chosen = choose(self.edit.text())
        if chosen:
            self.edit.setText(str(Path(chosen)))

    def text(self):
        return self.edit.text().strip()

    def say(self, text, colour='dim'):
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {PALETTE.get(colour, colour)};')


class SettingsDialog(QDialog):
    """Output folder, stop tail, the record key, FFmpeg and draft transcripts. Nothing is applied until OK."""

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setMinimumWidth(680)
        outer = vbox(self, (22, 20, 22, 18), 14)
        # The rows scroll, so that they keep their height on a small screen and OK stays in reach.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows = QWidget()
        layout = vbox(rows, (0, 0, 10, 0), 14)
        area.setWidget(rows)
        outer.addWidget(area, 1)
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self.root = PathRow(values.get('root'), self._folder, 'Where speakers, originals and kept clips are stored')
        self.root.say('Recordings are voice data. Keep this folder out of OneDrive, Dropbox and other synced folders '
                      'unless you want them copied to a cloud.')
        form.addRow('Output folder', self.root)

        self.tail = QDoubleSpinBox()
        self.tail.setRange(0.0, 1.0)
        self.tail.setSingleStep(0.1)
        self.tail.setDecimals(2)
        self.tail.setSuffix(' s')
        self.tail.setValue(float(values.get('tail_seconds', 0.5)))
        self.tail.setToolTip('How long recording goes on after Stop, so the end of the last word is not cut off')
        self.tail.setAccessibleName('Stop tail')
        self.tail.setFixedWidth(110)
        form.addRow('Stop tail', self.tail)

        look = QWidget()
        look_layout = hbox(look, spacing=8)
        self.theme = QComboBox()
        self.theme.setAccessibleName('Theme')
        for key, text in THEME_NAMES.items():
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme(values)))
        self.text_scale = QComboBox()
        self.text_scale.setAccessibleName('Size of the window and its text')
        self.text_scale.addItem('Size: as in Windows', None)
        for scale in settings.TEXT_SCALES:
            self.text_scale.addItem(f'Size: {scale * 100:.0f}%', scale)
        self.text_scale.setCurrentIndex(self.text_scale.findData(settings.text_scale(values)))
        self.language_choice = QComboBox()
        self.language_choice.setAccessibleName('Language of the window')
        self.language_choice.addItem('Language: as in Windows', None)
        for code, name in LANGUAGES.items():
            self.language_choice.addItem(name, code)
        self.language_choice.setCurrentIndex(max(self.language_choice.findData(settings.language(values)), 0))
        look_layout.addWidget(self.theme)
        look_layout.addWidget(self.text_scale)
        look_layout.addWidget(self.language_choice)
        look_layout.addStretch(1)
        form.addRow('Look', look)
        form.addRow('', label('Used the next time the program starts. The translations are new and have not been checked by '
                              'native speakers: corrections are welcome.', 'dim', size=11, wrap=True))

        self.output = QComboBox()
        self.output.setAccessibleName('Playback device')
        self.output.addItem("Windows' default", None)
        for name in values.get('outputs') or ():
            self.output.addItem(name, name)
        wanted = values.get('output_device')
        if wanted and self.output.findData(wanted) < 0:
            self.output.addItem(f'{wanted} (not connected now)', wanted)
        self.output.setCurrentIndex(max(self.output.findData(wanted), 0))
        self.output.setToolTip('Where takes are played')
        form.addRow('Playback device', self.output)

        keys = QWidget()
        keys_layout = vbox(keys, spacing=4)
        row = hbox(spacing=8)
        self.record_key = QKeySequenceEdit(QKeySequence(settings.record_key(values)))
        self.record_key.setMaximumSequenceLength(1)
        self.record_key.setAccessibleName('Record key')
        self.record_key.setToolTip('Click here and press the key to use')
        self.record_key.setFixedWidth(150)
        self.record_key.keySequenceChanged.connect(self._check_key)
        row.addWidget(self.record_key)
        row.addStretch(1)
        keys_layout.addLayout(row)
        self.key_note = label('', 'dim', size=11, wrap=True)
        keys_layout.addWidget(self.key_note)
        self.hotkey = QCheckBox('This key also starts and stops recording while another program is in front')
        self.hotkey.setChecked(settings.global_hotkey(values))
        self.hotkey.setToolTip('Windows tells this program when the key is pressed, wherever the focus is. It is told about '
                               'that one key only.')
        keys_layout.addWidget(self.hotkey)
        keys_layout.addWidget(label('While this is on, no other program receives the key, and a stray press starts the '
                                    'microphone. A sign on top of every window shows that it is recording.', 'dim', size=11,
                                    wrap=True))
        form.addRow('Record key', keys)

        self.ffmpeg = PathRow(values.get('ffmpeg'), lambda start: self._file(start, 'ffmpeg.exe (ffmpeg.exe);;All files (*)'),
                              'Leave empty to search PATH and the WinGet folders')
        self.ffmpeg.edit.setPlaceholderText('Search automatically')
        self.ffmpeg.edit.textChanged.connect(self._check_ffmpeg)
        form.addRow('FFmpeg', self.ffmpeg)

        self.python = PathRow(values.get('asr_python'), lambda start: self._file(start, 'python.exe (python.exe);;All files (*)'),
                              'A Python that has faster-whisper installed')
        self.python.edit.setPlaceholderText('Optional')
        self.python.edit.textChanged.connect(self._check_recognizer)
        form.addRow('Speech recognizer: Python', self.python)
        self.model = PathRow(values.get('asr_model'), self._folder,
                             f'A faster-whisper model folder ({transcript.SETUP_SCRIPT} downloads one and fills this in)')
        self.model.edit.setPlaceholderText('Optional')
        self.model.edit.textChanged.connect(self._check_recognizer)
        form.addRow('Speech recognizer: model', self.model)

        self.language = QLineEdit(values.get('asr_language') or '')
        self.language.setPlaceholderText('Detect')
        self.language.setMaxLength(3)
        self.language.setValidator(QRegularExpressionValidator(QRegularExpression('[A-Za-z]{0,3}'), self.language))
        self.language.setToolTip('A language code such as en, es or fr. Naming the language makes drafts faster and '
                                 'avoids wrong guesses. Leave empty to detect it for each clip.')
        self.language.setFixedWidth(110)
        self.language.setAccessibleName('Speech language')
        form.addRow('Speech language', self.language)
        layout.addLayout(form)
        layout.addWidget(label('FFmpeg (noise reduction) and the speech recognizer (draft transcripts) are optional; see the '
                               f'README. {transcript.SETUP_SCRIPT} in the program folder sets the recognizer up and '
                               'fills these in. The program itself never goes online.', 'muted', wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('OK')
        ok.setDefault(True)
        ok.setProperty('kind', 'primary')
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        outer.addLayout(row)
        self.ok = ok
        screen = QGuiApplication.primaryScreen()
        self.resize(720, min(PREFERRED_HEIGHT, screen.availableGeometry().height() - 60) if screen is not None else PREFERRED_HEIGHT)
        self._check_ffmpeg()
        self._check_recognizer()
        self._check_key()

    def _check_key(self):
        problem = key_problem(self._key_text())
        self.key_note.setText(problem)
        self.key_note.setStyleSheet(f'color: {PALETTE["amber"]};' if problem else '')
        self.key_note.setVisible(bool(problem))
        self.ok.setEnabled(not problem)

    def _key_text(self):
        return self.record_key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _folder(self, start):
        return QFileDialog.getExistingDirectory(self, 'Choose a folder', start)

    def _file(self, start, filter):
        return QFileDialog.getOpenFileName(self, 'Choose a file', start, filter)[0]

    def _check_ffmpeg(self):
        path = self.ffmpeg.text()
        found = clean.locate(path or None)
        if found:
            self.ffmpeg.say(f'Found: {found}', 'green')
        elif path:
            self.ffmpeg.say('That file does not exist.', 'red')
        else:
            self.ffmpeg.say('Not found. Noise reduction is unavailable until FFmpeg is installed '
                            '(winget install Gyan.FFmpeg).', 'amber')

    def _check_recognizer(self):
        python, model = self.python.text(), self.model.text()
        if transcript.available(python, model):
            self.python.say('Ready: draft transcripts are available.', 'green')
        elif python or model:
            self.python.say('Both a Python and a model folder are needed, and both must exist.', 'amber')
        else:
            self.python.say(f'Not set up. Run {transcript.SETUP_SCRIPT} in the program folder to get a speech recognizer, '
                            'or type the transcript: that always works.', 'dim')

    def values(self):
        """The settings as typed. An empty optional text removes that setting."""
        return {'root': self.root.text() or None, 'tail_seconds': round(self.tail.value(), 2),
                'global_hotkey': self.hotkey.isChecked(), 'output_device': self.output.currentData(),
                'theme': self.theme.currentData(), 'text_scale': self.text_scale.currentData(),
                'language': self.language_choice.currentData(),
                'record_key': None if self._key_text() == settings.DEFAULT_RECORD_KEY else self._key_text(),
                'ffmpeg': self.ffmpeg.text() or None, 'asr_python': self.python.text() or None,
                'asr_model': self.model.text() or None, 'asr_language': self.language.text().strip().lower() or None}


# ---- sharing ----------------------------------------------------------------------------------------------------------

class CopyDialog(QDialog):
    """The format and sample rate of a copy of a kept clip. Choosing the file comes next, in the system's own dialog."""

    def __init__(self, name, parent=None, kept_rate=ac.EXPORT_RATE):
        super().__init__(parent)
        self.setWindowTitle('Save a copy')
        self.setMinimumWidth(460)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label(f'A copy of "{name}" is made from the kept clip, which does not change.', wrap=True))
        form = QFormLayout()
        form.setSpacing(10)
        self.kind = QComboBox()
        for key, (text, *_) in share.FORMATS.items():
            self.kind.addItem(text, key)
        self.kind.setCurrentIndex(self.kind.findData(share.DEFAULT_KIND))
        self.rate = QComboBox()
        for rate in share.RATES:
            self.rate.addItem(share.rate_text(rate) + (' (as kept)' if rate == kept_rate else ''), rate)
        self.rate.setCurrentIndex(self.rate.findData(share.DEFAULT_RATE))
        form.addRow('Format', self.kind)
        form.addRow('Sample rate', self.rate)
        layout.addLayout(form)
        layout.addWidget(label('The copy is mono, like the kept clip. A higher sample rate adds no detail: some programs and '
                               'sites just ask for 44.1 or 48 kHz.', 'muted', size=11, wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('Choose file...')
        ok.setDefault(True)
        ok.setProperty('kind', 'primary')
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def values(self):
        return self.kind.currentData(), self.rate.currentData()


class ManifestDialog(QDialog):
    """Whose clips a manifest lists, and in which file format. Choosing the file comes next."""

    def __init__(self, speaker_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Export manifest')
        self.setMinimumWidth(480)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label('A manifest lists the kept clips, one row each: the file, the transcript, the length, the '
                               'speaker and where the voice came from. Programs that train or test on speech read it '
                               'instead of walking the folders.', wrap=True))
        form = QFormLayout()
        form.setSpacing(10)
        self.scope = QComboBox()
        self.scope.addItem(f'Kept clips of {speaker_name}', 'speaker')
        self.scope.addItem('Kept clips of all speakers', 'all')
        form.addRow('List', self.scope)
        self.kind = QComboBox()
        for key, (text, ending) in manifest.FORMATS.items():
            self.kind.addItem(f'{text} ({ending})', key)
        form.addRow('File type', self.kind)
        layout.addLayout(form)
        layout.addWidget(label('Paths in the file are relative to the folder you save it in. A clip whose file has changed '
                               'since it was kept is left out, and named.', 'muted', size=11, wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('Choose file...')
        ok.setDefault(True)
        ok.setProperty('kind', 'primary')
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def values(self):
        return self.scope.currentData(), self.kind.currentData()


class DatasetDialog(QDialog):
    """Whose clips a dataset holds, its layout, its sample rate, and what the audio is made from. The folder comes next."""

    def __init__(self, speaker_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Export dataset')
        self.setMinimumWidth(520)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label('A dataset is the kept clips gathered in one new folder, with a list of them, for a tool that '
                               'trains or tests on speech. The clips in the library do not change.', wrap=True))
        form = QFormLayout()
        form.setSpacing(10)
        self.scope = QComboBox()
        self.scope.addItem(f'Kept clips of {speaker_name}', 'speaker')
        self.scope.addItem('Kept clips of all speakers', 'all')
        form.addRow('Clips', self.scope)
        self.layout_choice = QComboBox()
        for key, text in dataset.LAYOUTS.items():
            self.layout_choice.addItem(text, key)
        self.layout_choice.currentIndexChanged.connect(self._layout_changed)
        form.addRow('Layout', self.layout_choice)
        self.rate = QComboBox()
        for rate in dataset.RATES:
            self.rate.addItem(share.rate_text(rate), rate)
        form.addRow('Sample rate', self.rate)
        self.source = QComboBox()
        self.source.addItem('The original recordings', True)
        self.source.addItem('The kept clips', False)
        self.source.setToolTip('The originals keep detail that a 24 kHz clip no longer has. The kept clips are quicker')
        form.addRow('Made from', self.source)
        layout.addLayout(form)
        layout.addWidget(label('A clip made from its original goes through its own edits and settings again, so it says the '
                               'same words. A clip whose file has changed since it was kept, or whose release has run out, '
                               'is left out and named.', 'muted', size=11, wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('Choose folder...')
        ok.setDefault(True)
        ok.setProperty('kind', 'primary')
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)
        self._layout_changed()

    def _layout_changed(self):
        self.rate.setCurrentIndex(self.rate.findData(dataset.DEFAULT_RATE[self.layout_choice.currentData()]))

    def values(self):
        return self.scope.currentData(), self.layout_choice.currentData(), self.rate.currentData(), self.source.currentData()


class SpeakerDialog(QDialog):
    """The signed release of a speaker: the file, when it was signed, and when it runs out. It is a record of what the person
    holds, for the voices that are not their own. Nothing here stands in for the statement made for each clip."""

    def __init__(self, name, record, has_file, parent=None):
        super().__init__(parent)
        record = record or {}
        self.setWindowTitle(f'Speaker details: {name}')
        self.setMinimumWidth(560)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label('Keep the signed release for this voice here, so that it stays with their clips. Your own voice '
                               'needs none. Once a release has run out, no more clips of this speaker can be kept.', wrap=True))
        form = QFormLayout()
        form.setSpacing(10)
        self.file = PathRow('', self._choose, 'A copy of this file goes in the release folder of this speaker')
        self.file.edit.setAccessibleName('The signed release')
        self.file.edit.setPlaceholderText(record.get('file') if has_file else 'Choose the file, such as a signed PDF')
        self.file.say(f"On record: {record.get('file')}" if has_file else
                      ('The file on record is not in the folder any more.' if record.get('file') else 'No file on record.'))
        form.addRow('Signed release', self.file)
        self.signed_known = QCheckBox('Signed on')
        self.signed = self._date(record.get('signed'), 'Signed on')
        self.signed_known.setChecked(bool(record.get('signed')))
        form.addRow(self.signed_known, self.signed)
        self.expires_known = QCheckBox('Runs out on')
        self.expires = self._date(record.get('expires'), 'Runs out on')
        self.expires_known.setChecked(bool(record.get('expires')))
        form.addRow(self.expires_known, self.expires)
        self.notes = QPlainTextEdit(record.get('notes', ''))
        self.notes.setAccessibleName('Notes about the release')
        self.notes.setPlaceholderText('What it covers, who to ask, anything worth remembering')
        self.notes.setTabChangesFocus(True)
        self.notes.setFixedHeight(80)
        form.addRow('Notes', self.notes)
        layout.addLayout(form)
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('Save')
        ok.setDefault(True)
        ok.setProperty('kind', 'primary')
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)
        self.signed_known.toggled.connect(self.signed.setEnabled)
        self.expires_known.toggled.connect(self.expires.setEnabled)
        self.signed.setEnabled(self.signed_known.isChecked())
        self.expires.setEnabled(self.expires_known.isChecked())

    @staticmethod
    def _date(text, name):
        edit = QDateEdit(QDate.fromString(text, Qt.DateFormat.ISODate) if text else QDate.currentDate())
        edit.setCalendarPopup(True)
        edit.setDisplayFormat('yyyy-MM-dd')
        edit.setFixedWidth(130)
        edit.setAccessibleName(name)
        return edit

    def _choose(self, start):
        return QFileDialog.getOpenFileName(self, 'Choose the signed release', start, 'All files (*)')[0]

    def values(self):
        def date(known, edit):
            return edit.date().toString(Qt.DateFormat.ISODate) if known.isChecked() else ''

        return {'source': self.file.text() or None, 'signed': date(self.signed_known, self.signed),
                'expires': date(self.expires_known, self.expires), 'notes': self.notes.toPlainText()}


# ---- several takes at once --------------------------------------------------------------------------------------------

class KeepManyDialog(QDialog):
    """The statement of permission for several takes. It is made here, for what is listed here: the box starts empty every
    time, and Keep stays off until it is ticked. A take that says nothing about its origin or allowed use needs words typed
    for it, so that nothing is kept without them."""

    def __init__(self, speaker_name, takes, defaults, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Keep several takes')
        self.setMinimumWidth(560)
        count = len(takes)
        layout = vbox(self, (22, 20, 22, 18), 12)
        layout.addWidget(label(f'Keep {count} takes of {speaker_name}?', size=15, weight=QFont.Weight.DemiBold))
        groups = {}
        for take in takes:
            said = tuple(str(take.meta.get(key) or '').strip() for key in ('origin', 'allowed_use'))
            groups[said] = groups.get(said, 0) + 1
        rows = [f'<tr><td style="padding:2px 14px 2px 0"><b>{amount}</b></td><td>{html.escape(origin)}</td>'
                f'<td style="padding-left:14px">{html.escape(allowed)}</td></tr>'
                for (origin, allowed), amount in groups.items() if origin and allowed]
        if rows:
            layout.addWidget(label('What these takes say:', 'muted'))
            table = QLabel('<table><tr><td></td><td><b>Origin</b></td><td style="padding-left:14px"><b>Allowed use</b></td>'
                           f'</tr>{"".join(rows)}</table>')
            table.setTextFormat(Qt.TextFormat.RichText)
            table.setWordWrap(True)
            layout.addWidget(table)
        self.origin = self.allowed = None
        if any(not all(said) for said in groups):
            layout.addWidget(label('Some of these takes do not say where they came from or what they may be used for. '
                                   'These words go on those takes only:', 'muted', wrap=True))
            form = QFormLayout()
            form.setSpacing(8)
            self.origin = QLineEdit(defaults.get('origin', ''))
            self.origin.setPlaceholderText('Where the recording came from')
            self.origin.textChanged.connect(self._check)
            form.addRow('Origin', self.origin)
            self.allowed = QLineEdit(defaults.get('allowed_use', ''))
            self.allowed.setPlaceholderText('What the voice may be used for')
            self.allowed.textChanged.connect(self._check)
            form.addRow('Allowed use', self.allowed)
            layout.addLayout(form)
        self.consent = QCheckBox(f'I have the right to use this voice for the allowed use stated on each of these {count} '
                                 'takes: it is my own voice, or the speaker has given permission.')
        self.consent.setStyleSheet('QCheckBox { spacing: 10px; }')
        self.consent.toggled.connect(self._check)
        statement = label(self.consent.text(), wrap=True)
        statement.setAccessibleName(self.consent.text())
        self.consent.setText('')
        self.consent.setAccessibleName(statement.text())
        row = hbox(spacing=10)
        row.addWidget(self.consent, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(statement, 1)
        layout.addLayout(row)
        layout.addWidget(label('This is one statement for all of them. Each kept clip records it, with the time you made it '
                               'and how many takes it covered. A take that fails a Keep rule is left out, and named.',
                               'muted', size=11, wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        self.keep = QPushButton(f'Keep {count} takes')
        self.keep.setDefault(True)
        self.keep.setProperty('kind', 'primary')
        self.keep.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.keep)
        layout.addLayout(row)
        self._check()

    def _check(self):
        typed = all(edit is None or edit.text().strip() for edit in (self.origin, self.allowed))
        self.keep.setEnabled(self.consent.isChecked() and typed)

    def values(self):
        return (self.origin.text().strip() if self.origin else '', self.allowed.text().strip() if self.allowed else '')


# ---- about and shortcuts ----------------------------------------------------------------------------------------------

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'About {settings.APP_NAME}')
        self.setMinimumWidth(520)
        layout = vbox(self, (26, 24, 26, 20), 10)
        layout.addWidget(label(settings.APP_NAME, size=22, weight=QFont.Weight.DemiBold))
        layout.addWidget(label(f'{settings.APP_TAGLINE} - version {__version__}', 'muted'))
        layout.addSpacing(6)
        layout.addWidget(label('Record, edit and keep clean clips of one voice: reference clips, a sentence at a time for a '
                               'dataset, or full audition takes, with the transcript, where the recording came from and what '
                               'it may be used for written next to the audio. Originals are never overwritten, and nothing '
                               'leaves this computer.', wrap=True))
        layout.addSpacing(4)
        layout.addWidget(label('Record only voices you own or have permission to use. See the responsible-use notes in '
                               'the documentation.', 'muted', wrap=True))
        layout.addSpacing(4)
        layout.addWidget(label(
            f'Released under the MIT License. Built with Qt {qVersion()} through PySide6 {PySide6.__version__} (LGPL-3.0), '
            f'NumPy, SciPy, SoundFile, sounddevice, PyAudioWPatch and Send2Trash; their licences are listed in '
            f'THIRD_PARTY_NOTICES.md. Python {platform.python_version()}, {platform.system()} {platform.release()}.',
            'dim', size=11, wrap=True))
        row = hbox(spacing=8)
        row.addStretch(1)
        notices = settings.program_dir() / 'THIRD_PARTY_NOTICES.md'
        if notices.exists():
            show = button('Open notices', None)
            show.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(notices))))
            row.addWidget(show)
        close = QPushButton('Close')
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


SHORTCUT_GROUPS = (
    ('Record and listen', ('record', 'arm', 'play', 'loop', 'ab', 'escape')),
    ('Edit', ('undo', 'redo', 'cut', 'trim', 'silence', 'select_all', 'set_in', 'set_out')),
    ('Check and repair', ('auto_analyze', 'auto_fix')),
    ('View', ('zoom_in', 'zoom_out', 'fit', 'zoom_selection')),
    ('Clips and files', ('keep', 'rename', 'next_take', 'prev_take', 'save_copy', 'import_recording', 'new_speaker',
                         'open_folder', 'load_script', 'prev_line', 'next_line', 'next_open')),
    ('Program', ('settings', 'shortcuts', 'quit')),
)
MOUSE_ROWS = (('Select', 'Drag on the waveform'), ('Move the playhead', 'Click, or the arrow keys'),
              ('Zoom', 'Mouse wheel'), ('Scroll while zoomed', 'Shift + wheel'))


def shortcut_table(sections):
    """An HTML table of headed sections of (action, keys) rows."""
    rows = []
    for title, entries in sections:
        rows.append(f'<tr><td colspan="2" style="padding:12px 0 4px 0; color:{PALETTE["dim"]}"><b>{title.upper()}</b></td></tr>')
        rows.extend(f'<tr><td style="padding:2px 22px 2px 0">{action}</td>'
                    f'<td style="color:{PALETTE["muted"]}">{keys}</td></tr>' for action, keys in entries)
    return f'<table>{"".join(rows)}</table>'


class ShortcutsDialog(QDialog):
    """Every shortcut, from the same table that defines them, in two columns so the dialog stays short."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Keyboard shortcuts')
        layout = vbox(self, (26, 14, 26, 18), 10)
        def keys(name):
            """The record key can be changed, so the window is asked what it is."""
            actions = getattr(parent, 'actions', None)
            return actions[name].shortcut().toString() if name == 'record' and actions else shortcut_text(SPEC[name][2])

        sections = [(tr(title), [(tr(SPEC[name][0]).rstrip('.'), keys(name)) for name in names])
                    for title, names in SHORTCUT_GROUPS]
        rows = [*MOUSE_ROWS, ('Playhead steps', 'Left / Right: 10 ms; Shift: 100 ms; Ctrl: 1 s; Home, End')]
        sections.append((tr('Mouse and playhead'), [(tr(action), tr(keys)) for action, keys in rows]))
        columns = hbox(spacing=36)
        for part in (sections[:3], sections[3:]):
            body = QLabel(shortcut_table(part))
            body.setTextFormat(Qt.TextFormat.RichText)
            body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            columns.addWidget(body, 0, Qt.AlignmentFlag.AlignTop)
        columns.addStretch(1)
        layout.addLayout(columns)
        row = hbox()
        row.addStretch(1)
        close = QPushButton('Close')
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
