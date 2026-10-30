"""Draft a transcript with faster-whisper, when the user has set it up. This part of the recorder is optional.

The recognizer runs in its own Python environment, so the recorder does not need its large packages. Two settings
select it: `asr_python` (a Python with faster-whisper installed) and `asr_model` (a folder holding a downloaded
faster-whisper model). A third, `asr_language`, names the language (en, es, ...) instead of detecting it each time.
The recorder never downloads a model, and the worker refuses anything but a local folder. `asr_setup.py` (run by
setup-transcripts.bat) installs both and fills the settings in, but only when a person runs it and agrees.
The result is a draft: the words can be wrong, so listen before keeping. See "Draft transcripts" in the README.
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import soundfile as sf

from . import audio_core as ac

WORKER = Path(__file__).with_name('asr_worker.py')
TIMEOUT_SECONDS = 600
SETUP_SCRIPT = 'setup-transcripts.bat'
NOT_SET_UP = (f'Draft transcripts are not set up: run {SETUP_SCRIPT} in the program folder (it downloads a speech model '
              'once), or see the README. Type the transcript instead.')
ELLIPSIS = ('...', '…')


class DraftUnavailable(ValueError):
    pass


def available(python, model):
    """True when both settings are given and point at something that exists."""
    return bool(python and model and Path(python).is_file() and Path(model).is_dir() and WORKER.is_file())


# ---- tidying a draft --------------------------------------------------------------------------------------------------

def _words(text):
    """(raw token, the word in lower case without punctuation) for each whitespace-separated token."""
    return [(raw, re.sub(r"[^\w']+", '', raw.lower())) for raw in text.split()]


def _stuck_ending(tokens):
    """Where a run of two or more identical fragments that each end in an ellipsis ("And... And...") begins, else None."""
    first = len(tokens)
    while first > 0 and tokens[first - 1][0].endswith(ELLIPSIS) and tokens[first - 1][1] == tokens[-1][1]:
        first -= 1
    return first if len(tokens) - first >= 2 and tokens[-1][1] else None


def _repeat(tokens):
    """(start, end) of tokens to drop from a repeated word or phrase of up to four words, else None.

    Four repeats in a row are kept once; so are three repeats at the very end, which is where a small model gets stuck.
    """
    words = [word for _, word in tokens]
    count = len(words)
    for size in range(1, 5):
        for i in range(count - 2 * size + 1):
            unit = words[i:i + size]
            if not all(unit):
                continue
            times = 1
            while words[i + times * size:i + (times + 1) * size] == unit:       # a short slice at the end never matches
                times += 1
            end = i + times * size
            if times >= 4 or (times >= 3 and end == count):
                return i + size, end
    return None


def tidy_draft(text):
    """(text, changed): the draft without the stuck repeats a small model produces, mostly at the end of a clip.

    A trailing run of "And... And..." is dropped. A word or phrase said four times in a row (or three times at the very
    end) is kept once. Nothing else is touched.
    """
    tokens = _words(text)
    changed = False
    stuck = _stuck_ending(tokens)
    if stuck is not None:
        tokens, changed = tokens[:stuck], True
    while True:
        found = _repeat(tokens)
        if found is None:
            break
        tokens, changed = tokens[:found[0]] + tokens[found[1]:], True
    return (' '.join(raw for raw, _ in tokens) if changed else text.strip()), changed


# ---- asking the recognizer ---------------------------------------------------------------------------------------------

def suggest(export_samples, python, model, language=None):
    """Transcribe a 24 kHz mono clip. Returns {'text': ..., 'language': ..., 'duration_seconds': ..., 'tidied': ...}."""
    if not available(python, model):
        raise DraftUnavailable(NOT_SET_UP)
    with tempfile.TemporaryDirectory(prefix='recorder-draft-') as tmp:
        clip = Path(tmp) / 'draft.wav'
        sf.write(str(clip), export_samples, ac.EXPORT_RATE, subtype='PCM_16', format='WAV')
        command = [str(python), '-I', str(WORKER), '--model-dir', str(model), '--audio', str(clip)]
        if language:
            command += ['--language', str(language)]
        try:
            reply = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=TIMEOUT_SECONDS,
                                   check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise DraftUnavailable('The transcript draft took too long. Type the transcript instead.') from None
        except OSError as e:
            raise DraftUnavailable(f'The speech recognizer could not be started: {e}') from None
    if reply.returncode:
        raise DraftUnavailable('The transcript draft failed: ' + reply.stderr.strip()[-300:])
    try:
        result = json.loads(reply.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise DraftUnavailable('The transcript draft returned nothing readable.') from None
    text, tidied = tidy_draft(str(result.get('text', '')))
    return {'text': text, 'language': result.get('language'), 'duration_seconds': result.get('duration_seconds'),
            'tidied': tidied}
