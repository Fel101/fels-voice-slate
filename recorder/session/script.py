"""Script mode: the prompts a speaker reads, one per take."""
import codecs
import locale
from pathlib import Path

from .. import audio_core as ac


def decode(raw):
    """A text file's bytes as text: what Notepad saves as UTF-8 or Unicode (with or without a byte-order mark), then what
    older programs save in the Windows code page. Raises UnicodeDecodeError, or ValueError for something that is not text."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode('utf-16')
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode(locale.getencoding())
    if '\x00' in text:
        raise ValueError('it holds characters that are not text')
    return text


class Script:
    def __init__(self, bus):
        self.bus = bus
        self.lines = []
        self.index = 0

    @property
    def current(self):
        return self.lines[self.index] if self.lines else None

    @property
    def label(self):
        if not self.lines:
            return ''
        return f'{self.index + 1} of {len(self.lines)}'

    def load_text(self, text):
        self.lines = ac.script_lines(text)
        self.index = 0
        self.bus.notify('script')

    def load_file(self, path):
        try:
            text = decode(Path(path).read_bytes())
        except ValueError:                      # UnicodeDecodeError is one
            self.bus.notice('error', 'Script', 'The file is not plain text. Save it as UTF-8 text and load it again.')
            return False
        except OSError as e:
            self.bus.notice('error', 'Script', f'The file could not be read: {e}')
            return False
        self.load_text(text)
        return True

    def step(self, delta):
        if not self.lines:
            return
        self.index = min(max(self.index + delta, 0), len(self.lines) - 1)
        self.bus.notify('script')

    def clear(self):
        self.lines, self.index = [], 0
        self.bus.notify('script')
