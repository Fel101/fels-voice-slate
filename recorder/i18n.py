"""The window in other languages.

A language is a JSON file in resources/lang: the English text of the window as the key, and its translation as the value. A
text with no entry shows in English, so a language can be added a little at a time, and English needs no file at all. Text
that is built up from values has fields in it: `tr('{count} kept').format(count=3)`, and the fields of a translation must be
the same. `tools/strings.py` lists what a language still lacks.

The window asks for translations as it is built, through the helpers in ui/widgets.py and ui/translate.py, so most of the code
just writes English. What the session says in the status line is translated when it is one fixed text.

This module needs no toolkit, so that the session can use it too.
"""
import ctypes
import json
from pathlib import Path

FOLDER = Path(__file__).parent / 'resources' / 'lang'
LANGUAGES = {'en': 'English', 'de': 'Deutsch', 'es': 'Español', 'fr': 'Français'}
WINDOWS_LANGUAGES = {0x07: 'de', 0x09: 'en', 0x0a: 'es', 0x0c: 'fr'}     # the low ten bits of a Windows language id

_table = {}


def use(code):
    """Show the window in this language from now on. One with no file, or a file that cannot be read, gives English."""
    global _table
    _table = {}
    if code in LANGUAGES and code != 'en':
        try:
            data = json.loads((FOLDER / f'{code}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        _table = {key: value for key, value in data.items() if isinstance(value, str) and value} if isinstance(data, dict) else {}


def active():
    """True when something other than English is in use."""
    return bool(_table)


def tr(text):
    """`text` in the language in use, or as it is."""
    return _table.get(text, text)


def windows_language():
    """The language Windows shows itself in, if there is a file for it, else English."""
    try:
        return WINDOWS_LANGUAGES.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff, 'en')
    except (AttributeError, OSError):
        return 'en'
