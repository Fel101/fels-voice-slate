"""List the text the window shows, for translating it, and what a language file still lacks.

    python tools/strings.py                 every text that can be translated, one per line, as JSON
    python tools/strings.py --missing de    what recorder/resources/lang/de.json has no entry for
    python tools/strings.py --stale de      what it has an entry for and the program no longer says

A text can be translated when it is written out in full where the window uses it. One built up from values (an f-string) is
left in English until it is rewritten with `{}` fields and tr(...).format(...): see recorder/i18n.py.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [*sorted((ROOT / 'recorder' / 'ui').glob('*.py')), ROOT / 'recorder' / 'session' / 'capturing.py',
         ROOT / 'recorder' / 'session' / 'workspace.py', ROOT / 'recorder' / 'session' / 'editor.py',
         ROOT / 'recorder' / 'session' / 'transport.py', ROOT / 'recorder' / 'session' / 'drafts.py',
         ROOT / 'recorder' / 'session' / 'advisor.py', ROOT / 'recorder' / 'session' / 'script.py',
         ROOT / 'recorder' / 'session' / 'run.py', ROOT / 'recorder' / 'session' / 'roomcheck.py',
         ROOT / 'recorder' / 'session' / 'batch.py', ROOT / 'recorder' / 'dataset.py',
         # what the Check tab, Auto analyze and the Keep card say is worked out here, in the window's language
         ROOT / 'recorder' / 'core' / 'analysis.py', ROOT / 'recorder' / 'core' / 'autofix.py',
         ROOT / 'recorder' / 'audio_core.py', ROOT / 'recorder' / 'store.py',
         # and the history of edits, the status lines and the reasons a clip or a key is refused
         ROOT / 'recorder' / 'core' / 'edits.py', ROOT / 'recorder' / 'session' / '__init__.py',
         ROOT / 'recorder' / 'session' / 'autosave.py', ROOT / 'recorder' / 'manifest.py', ROOT / 'recorder' / 'share.py',
         ROOT / 'recorder' / 'handoff.py', ROOT / 'recorder' / 'hotkey.py']
# These modules also do work outside the window. Only their explicit translation keys and named messages belong here.
MESSAGE_FILES = [ROOT / 'recorder' / name for name in ('audio_import.py', 'audio_io.py', 'capture.py', 'clean.py',
                                                        'transcript.py', 'asr_setup.py', '__main__.py',
                                                        'core/chain.py', 'core/render.py')]
CALLS = {'end', 'label', 'caption', 'button', 'chip', 'set_chip', 'setToolTip', 'setPlaceholderText', 'setText', 'setWindowTitle',
         'addRow', 'addItem', 'QCheckBox', 'QPushButton', 'QLabel', 'notice', 'confirm', 'ask_text', 'choose_item', 'addAction',
         'addMenu', 'setAccessibleName', 'setAccessibleDescription', 'set_tooltips', 'Segmented', 'status', 'tr', 'Step',
         'setStatusTip', 'StepCard', 'choose_file', 'choose_files', 'choose_folder', 'choose_save_file', 'add_note', '_field',
         'PathRow', 'say', 'get', 'format', '_refused', '_busy', '_busy_with', '_start', '_spin', 'handoff', 'file_filter'}
NAMES = {'STATEMENT', 'EMPTY_TEXT', 'OUT_OF_REACH_TEXT', 'INTRO', 'SPEC', 'TABS', 'THEME_NAMES', 'MOUSE_ROWS', 'SHORTCUT_GROUPS',
         'STATES', 'SOURCES', 'STATUS_WORDS', 'GLYPH_WORDS', 'ORDERS', 'SHOWN', 'CHOICE_TIPS', 'SENTENCE',
         'WHERE_THE_LEVEL_IS', 'REFUSALS', 'LAYOUTS', 'SPLITS', 'STOP_TIP', 'STOP_COPY_TIP', 'MONTHS',
         'NEEDS_FFMPEG', 'NEEDS_PYSIDE', 'NOT_FOUND', 'QT_FILE_LABELS'}
SKIP = {'F9', 'muted', 'dim', 'ghost', 'primary', 'link', 'danger', 'amber', 'violet', 'green', 'red', 'gray', 'text',
        'Record      F9', 'Reading check: {count} place differ'}
DISPLAY_CALLS = {'status', 'notice', 'confirm', 'setText', 'setAccessibleName', 'setAccessibleDescription', 'setToolTip',
                 'setStatusTip', 'setWindowTitle', 'setPlaceholderText', 'addAction', 'label', 'caption', 'button', 'end'}
# Texts the search below does not find, because the code holds them in a variable or in the rules of a purpose.
EXTRA = ('Length {detail}  ({allowed})', 'Length {detail}: this purpose needs {allowed}', 'Transcript, {detail}',
         'Transcript must be {low} to {high} characters ({detail})', 'Reading check: {count} place differs',
         'Reading check: {count} places differ', 'Reference', 'Sentence', 'Audition',
         'One good clip of a voice, for a text-to-speech tool', 'One sentence per take, for a dataset or voice banking',
         'A full performance, kept at the best quality', 'In review', 'Kept', 'Changed', 'Not kept', 'Not saved',
         'Playhead steps', 'Left / Right: 10 ms; Shift: 100 ms; Ctrl: 1 s; Home, End', 'Drafting...', 'Draft: check it',
         'Check after cuts', 'From script', 'From script: check it', 'Read as written',
         'Cut recorded. The master file is unchanged.', 'stopping...', 'Ready.', 'Voice and Clip Recorder',
         'WAV, 16-bit', 'FLAC, lossless and smaller')


def _constants(node):
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            yield item.value


def _wanted(text):
    """Leaves out names of things in the code and keys of the keyboard."""
    if text in SKIP or len(text) < 2 or text != text.strip() or '_' in text or not any(c.isalpha() for c in text):
        return False
    return not (text.islower() and ' ' not in text) and not text.startswith(('Ctrl+', 'Alt+')) \
        and not (text[0] == 'F' and text[1:].isdigit()) and text not in ('Space', 'Esc', 'Delete', 'A/B', '50 Hz', '60 Hz')


def translation_keys(node):
    """Literal keys, including alternatives chosen before a call to tr()."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        yield node.value
    elif isinstance(node, ast.IfExp):
        yield from translation_keys(node.body)
        yield from translation_keys(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from translation_keys(value)


def texts():
    """Every text in the source that the window can show, in the order they appear."""
    found = {}
    for path in FILES + MESSAGE_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
                if name == 'tr' and node.args:
                    found.update(dict.fromkeys(translation_keys(node.args[0])))
                elif path in FILES and name in CALLS:
                    for argument in [*node.args, *(k.value for k in node.keywords)]:
                        if isinstance(argument, ast.JoinedStr):
                            continue
                        for text in _constants(argument):
                            if _wanted(text):
                                found[text] = True
            elif isinstance(node, ast.Assign) and any(getattr(t, 'id', '') in NAMES for t in node.targets):
                for text in _constants(node.value):
                    if _wanted(text) and (' ' in text or text[0].isupper()):
                        found[text] = True
            elif path.name in ('share.py', 'manifest.py') and isinstance(node, ast.Assign) and any(
                    getattr(target, 'id', '') == 'FORMATS' for target in node.targets):
                for value in node.value.values:
                    for text in translation_keys(value.elts[0]):
                        found[text] = True
    return [*found, *(text for text in EXTRA if text not in found)]


def untranslated_formats():
    """An f-string with prose at the display boundary cannot be looked up after its values are inserted."""
    for path in FILES + MESSAGE_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = getattr(call.func, 'attr', getattr(call.func, 'id', ''))
            if name not in DISPLAY_CALLS and name != 'tr':
                continue
            for argument in [*call.args, *(item.value for item in call.keywords)]:
                if isinstance(argument, ast.JoinedStr):
                    prose = ''.join(item.value for item in argument.values if isinstance(item, ast.Constant))
                    if name == 'tr' or re.search(r'\b[A-Za-z]{3,}\b', re.sub(r'<[^>]*>', '', prose)):
                        yield f'{path.relative_to(ROOT)}:{argument.lineno}: {ast.unparse(argument)}'
                elif name == 'tr' and isinstance(argument, ast.Call) and getattr(argument.func, 'attr', '') == 'format':
                    yield f'{path.relative_to(ROOT)}:{argument.lineno}: {ast.unparse(argument)}'


def main():
    # Piped or redirected, Windows gives the output its old code page, which has no "✓" and stopped the list half way.
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--missing', metavar='LANGUAGE')
    parser.add_argument('--stale', metavar='LANGUAGE')
    args = parser.parse_args()
    language = args.missing or args.stale
    known = texts()
    if language:
        table = json.loads((ROOT / 'recorder' / 'resources' / 'lang' / f'{language}.json').read_text(encoding='utf-8'))
        known = [text for text in known if text not in table] if args.missing else [text for text in table if text not in known]
    json.dump(known, sys.stdout, ensure_ascii=False, indent=0)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
