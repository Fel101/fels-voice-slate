"""Everything that asks the person something: messages, confirmations, file choosers, and the three dialogs.

The window never builds a dialog itself. It calls `Dialogs`, so the tests replace it with canned answers and no test
ever waits for a click.
"""
import platform
from pathlib import Path

import PySide6
from PySide6.QtCore import QRegularExpression, Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QWidget)

from .. import __version__, clean, settings, transcript
from .actions import SPEC, shortcut_text
from .palette import PALETTE
from .widgets import button, hbox, label, vbox

AUDIO_FILTER = 'Audio files (*.wav *.flac *.ogg *.opus *.mp3 *.aif *.aiff);;All files (*)'
LEVEL_ICONS = {'info': QMessageBox.Icon.Information, 'warning': QMessageBox.Icon.Warning,
               'error': QMessageBox.Icon.Critical}


class Dialogs:
    """The window's way of asking. Each method returns the answer, or None / False when the person backs out."""

    def __init__(self, parent):
        self.parent = parent

    def notice(self, level, title, text):
        box = QMessageBox(LEVEL_ICONS.get(level, QMessageBox.Icon.Information), title, text, QMessageBox.StandardButton.Ok,
                          self.parent)
        box.exec()

    def confirm(self, title, text, yes='OK', no='Cancel', danger=False):
        box = QMessageBox(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question, title, text,
                          QMessageBox.StandardButton.NoButton, self.parent)
        accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(no, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(accept if not danger else box.buttons()[-1])
        box.exec()
        return box.clickedButton() is accept

    def ask_text(self, title, prompt, text=''):
        value, ok = QInputDialog.getText(self.parent, title, prompt, QLineEdit.EchoMode.Normal, text)
        return value.strip() if ok and value.strip() else None

    def ask_number(self, title, prompt, value, low, high, decimals=1):
        number, ok = QInputDialog.getDouble(self.parent, title, prompt, value, low, high, decimals)
        return number if ok else None

    def choose_folder(self, title, start=''):
        folder = QFileDialog.getExistingDirectory(self.parent, title, str(start))
        return folder or None

    def choose_file(self, title, start='', filter=AUDIO_FILTER):
        path, _ = QFileDialog.getOpenFileName(self.parent, title, str(start), filter)
        return path or None

    def settings(self, values):
        dialog = SettingsDialog(values, self.parent)
        return dialog.values() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def about(self):
        AboutDialog(self.parent).exec()

    def shortcuts(self):
        ShortcutsDialog(self.parent).exec()

    def popup(self, menu, pos):
        """Show a context menu at a screen position and run what is chosen."""
        menu.exec(pos)

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
        self.setMinimumWidth(640)
        layout = vbox(self, (22, 20, 22, 18), 14)
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
        self.tail.setFixedWidth(110)
        form.addRow('Stop tail', self.tail)

        keys = QWidget()
        keys_layout = vbox(keys, spacing=4)
        self.hotkey = QCheckBox('F9 starts and stops recording in any program')
        self.hotkey.setChecked(settings.global_hotkey(values))
        self.hotkey.setToolTip('Windows tells this program when F9 is pressed, wherever the focus is. It is told about that '
                               'one key only.')
        keys_layout.addWidget(self.hotkey)
        keys_layout.addWidget(label('While this is on, another program does not receive F9. Turn it off if you need F9 '
                                    'there; it then works only while this window is in front.', 'dim', size=11, wrap=True))
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
        self.model = PathRow(values.get('asr_model'), self._folder, 'A faster-whisper model folder you downloaded yourself')
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
        form.addRow('Speech language', self.language)
        layout.addLayout(form)
        layout.addWidget(label('FFmpeg (noise reduction) and the speech recognizer (draft transcripts) are optional and '
                               'installed separately; see the README. The recorder never downloads anything.', 'muted',
                               wrap=True))
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
        layout.addLayout(row)
        self._check_ffmpeg()
        self._check_recognizer()

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
            self.python.say('Not set up. Draft transcripts are optional; typing the transcript always works.', 'dim')

    def values(self):
        """The settings as typed. An empty optional text removes that setting."""
        return {'root': self.root.text() or None, 'tail_seconds': round(self.tail.value(), 2),
                'global_hotkey': self.hotkey.isChecked(),
                'ffmpeg': self.ffmpeg.text() or None, 'asr_python': self.python.text() or None,
                'asr_model': self.model.text() or None, 'asr_language': self.language.text().strip().lower() or None}


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
        layout.addWidget(label('Record, edit and keep clean voice reference clips: 6 to 40 seconds of one voice, with the '
                               'transcript, where it came from and what it may be used for written next to the audio. '
                               'Originals are never overwritten, and nothing leaves this computer.', wrap=True))
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
    ('Clips and files', ('keep', 'import_recording', 'new_speaker', 'open_folder', 'load_script', 'prev_line',
                         'next_line')),
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
        sections = [(title, [(SPEC[name][0].rstrip('.'), shortcut_text(SPEC[name][2])) for name in names])
                    for title, names in SHORTCUT_GROUPS]
        sections.append(('Mouse and playhead', list(MOUSE_ROWS) + [('Playhead steps', 'Left / Right: 10 ms; Shift: 100 ms; '
                                                                     'Ctrl: 1 s; Home, End')]))
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
