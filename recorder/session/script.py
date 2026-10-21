"""Script mode: the prompts a speaker reads, one per take."""
from pathlib import Path

from .. import audio_core as ac


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
            text = Path(path).read_text(encoding='utf-8')
        except UnicodeDecodeError:
            self.bus.notice('error', 'Script', 'The file is not UTF-8 text. Save it as UTF-8 and load it again.')
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
