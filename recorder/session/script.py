"""Script mode: the prompts a speaker reads, one per take.

The script and the line in front are remembered in a small file beside the settings, so a session of a few hundred lines can
be left and picked up again. What has been recorded of each line is not stored here: it is read from the takes themselves, so
it is always true for the speaker in front.
"""
import codecs
import json
import locale
import os
from collections import namedtuple
from pathlib import Path

from .. import audio_core as ac

SCHEMA = 'voice-slate-script-v1'
FILE_NAME = 'script.json'

Line = namedtuple('Line', 'number text takes kept')     # number from 1; how many takes there are of it, and how many are kept


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
    def __init__(self, bus, path=None):
        self.bus = bus
        self.path = Path(path) if path else None    # where it is remembered; None remembers nothing
        self.lines = []
        self.index = 0
        self._warned = False
        self._restore()

    @property
    def current(self):
        return self.lines[self.index] if self.lines else None

    @property
    def label(self):
        if not self.lines:
            return ''
        return f'{self.index + 1} of {len(self.lines)}'

    # ---- remembering ----

    def _restore(self):
        """Pick up the script of the last session. A file that cannot be used is the same as no file."""
        try:
            data = json.loads(self.path.read_text(encoding='utf-8')) if self.path else None
        except (OSError, ValueError):
            return
        lines = data.get('lines') if isinstance(data, dict) and data.get('schema') == SCHEMA else None
        if isinstance(lines, list) and lines and all(isinstance(line, str) and line.strip() for line in lines):
            index = data.get('index')
            self.lines = lines
            self.index = index if isinstance(index, int) and 0 <= index < len(lines) else 0

    def _remember(self):
        if self.path is None:
            return
        tmp = self.path.with_name(self.path.name + '.tmp')
        try:
            if self.lines:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps({'schema': SCHEMA, 'index': self.index, 'lines': self.lines}, ensure_ascii=False,
                                          indent=0), encoding='utf-8')
                os.replace(tmp, self.path)
            else:
                self.path.unlink(missing_ok=True)
        except OSError as e:
            if not self._warned:                    # the script itself is fine: only the next session will not find it
                self._warned = True
                self.bus.status(f'The place in the script could not be saved: {e}')

    def _changed(self):
        self._remember()
        self.bus.notify('script')

    # ---- what is loaded ----

    def load_text(self, text):
        self.lines = ac.script_lines(text)
        self.index = 0
        self._changed()

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
        self._changed()

    def go_to(self, index):
        if 0 <= index < len(self.lines):
            self.index = index
            self._changed()

    def clear(self):
        self.lines, self.index = [], 0
        self._changed()

    # ---- what has been done of it ----

    def progress(self, takes):
        """Every line with how many takes there are of it and how many of those are kept. `takes` are the speaker's."""
        counts, kept = {}, {}
        for take in takes:
            line = take.meta.get('script_line')
            if line is not None:
                counts[line] = counts.get(line, 0) + 1
                kept[line] = kept.get(line, 0) + bool(take.kept)
        return [Line(number, text, counts.get(text, 0), kept.get(text, 0)) for number, text in enumerate(self.lines, 1)]

    def next_to_record(self, takes):
        """The position of the next line with no kept take, going on from the line in front and round to the start, or
        None when every line has one."""
        progress = self.progress(takes)
        for offset in range(1, len(progress) + 1):
            place = (self.index + offset) % len(progress)
            if not progress[place].kept:
                return place
        return None

    def go_to_next(self, takes):
        """Move to the next line that still needs a kept take. Returns False when none does."""
        place = self.next_to_record(takes)
        if place is None:
            return False
        self.go_to(place)
        return True
