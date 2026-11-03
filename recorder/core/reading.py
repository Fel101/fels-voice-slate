"""The reading check and the words of a take: pure functions on text and times.

`compare` sets what was meant to be read against what the speech recognizer heard. It says nothing about who is right: a
recognizer gets words wrong too, so a difference is a place to listen, not a mistake. The words are compared without their
capitals and punctuation, so "Well, then!" and "well then" are the same reading. Languages written without spaces between
their words are compared letter by letter.
"""
import difflib
import re
from collections import namedtuple

Word = namedtuple('Word', 'text state heard')                   # state: same, changed, missed or added
Reading = namedtuple('Reading', 'words same total')             # what `compare` returns: `same` of the `total` script words

_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_NO_SPACES = re.compile('[぀-ヿ㐀-鿿가-힣฀-๿]')


def tokens(text):
    """The words of a text, as they are written. A run in a language with no spaces is cut into its letters."""
    found = []
    for word in _TOKEN.findall(str(text or '')):
        found.extend(word if _NO_SPACES.search(word) else [word])
    return found


def _folded(word):
    return word.casefold().replace('’', "'")


def compare(script, heard):
    """How `heard` differs from `script`. Returns a Reading whose words are the script's in order: each `same`, `changed`
    (with what was heard instead) or `missed`, with `added` entries where more was heard than was written."""
    written, spoken = tokens(script), tokens(heard)
    matcher = difflib.SequenceMatcher(None, [_folded(w) for w in written], [_folded(w) for w in spoken], autojunk=False)
    words, same = [], 0
    for action, a, b, c, d in matcher.get_opcodes():
        if action == 'equal':
            words.extend(Word(word, 'same', word) for word in written[a:b])
            same += b - a
        elif action == 'delete':
            words.extend(Word(word, 'missed', '') for word in written[a:b])
        elif action == 'insert':
            words.append(Word(' '.join(spoken[c:d]), 'added', ' '.join(spoken[c:d])))
        else:                                   # replaced: pair them off, and whatever is left over stands alone
            pairs = min(b - a, d - c)
            for offset in range(pairs):
                words.append(Word(written[a + offset], 'changed', spoken[c + offset]))
            words.extend(Word(word, 'missed', '') for word in written[a + pairs:b])
            if d - c > pairs:
                words.append(Word(' '.join(spoken[c + pairs:d]), 'added', ' '.join(spoken[c + pairs:d])))
    return Reading(words, same, len(written))


def differences(reading):
    """How many places differ."""
    return sum(1 for word in reading.words if word.state != 'same')


def clean_words(found):
    """The words of a draft as stored: (text, start, end) rows with text and times that can be used, anything else left out.
    A sidecar or an autosave file may have been edited, and the words are only a help."""
    rows = []
    for row in found if isinstance(found, (list, tuple)) else ():
        try:
            word, start, end = row
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if isinstance(word, str) and word.strip() and 0.0 <= start <= end < 1e7:
            rows.append((word.strip(), round(start, 3), round(end, 3)))
    return rows


def word_is_cut(start, end, edits):
    """True when the middle of a word lies inside a cut or a silence, so that it is no longer in the clip."""
    middle = (start + end) / 2
    return any(e.get('op', 'cut') in ('cut', 'silence') and e['start_seconds'] <= middle <= e['end_seconds'] for e in edits)


def words_between(words, first, last):
    """The text of the rows from `first` to `last` seconds: a word belongs where its middle is."""
    return ' '.join(word for word, start, end in words if first <= (start + end) / 2 < last)
