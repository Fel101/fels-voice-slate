"""Draft a transcript with faster-whisper, when the user has set it up. This part of the recorder is optional.

The recognizer runs in its own Python environment, so the recorder does not need its large packages. Two settings
select it: `asr_python` (a Python with faster-whisper installed) and `asr_model` (a folder holding a downloaded
faster-whisper model). A third, `asr_language`, names the language (en, es, ...) instead of detecting it each time.
The recorder never downloads a model, and the worker refuses anything but a local folder. `asr_setup.py` (run by
setup-transcripts.bat) installs both and fills the settings in, but only when a person runs it and agrees.
The window's session keeps one worker running (Worker), which loads the model once for all its drafts; the setup's own
check starts a worker for one clip, as suggest() does without a Worker.
The result is a draft: the words can be wrong, so listen before keeping. See "Draft transcripts" in the README.
"""
import collections
import json
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import soundfile as sf

from . import audio_core as ac
from .core.reading import clean_words

WORKER = Path(__file__).with_name('asr_worker.py')
TIMEOUT_SECONDS = 600
TIMEOUT_PER_SECOND = 3              # and this for each second of the clip: an audition runs for minutes
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

def suggest(export_samples, python, model, language=None, rate=ac.EXPORT_RATE, worker=None):
    """Transcribe a mono clip at `rate`. Returns {'text': ..., 'words': ..., 'language': ..., 'duration_seconds': ...,
    'tidied': ...}, where `words` is (word, start, end) rows in seconds of this clip.

    With a `worker` (a Worker), the clip goes to the recognizer it keeps running, which has its model loaded already.
    Without one, a recognizer is started for this clip alone.
    """
    if not available(python, model):
        raise DraftUnavailable(NOT_SET_UP)
    timeout = TIMEOUT_SECONDS + TIMEOUT_PER_SECOND * len(export_samples) / rate
    with tempfile.TemporaryDirectory(prefix='recorder-draft-') as tmp:
        clip = Path(tmp) / 'draft.wav'
        sf.write(str(clip), export_samples, int(rate), subtype='PCM_16', format='WAV')
        if worker is not None:
            result = worker.ask(python, model, clip, language, timeout)
        else:
            result = _ask_once(python, model, clip, language, timeout)
    text, tidied = tidy_draft(str(result.get('text', '')))
    return {'text': text, 'words': clean_words(result.get('words')), 'language': result.get('language'),
            'duration_seconds': result.get('duration_seconds'), 'tidied': tidied}


def _failed(detail):
    return 'The transcript draft failed: ' + detail.strip()[-300:]


def _ask_once(python, model, clip, language, timeout):
    """The answer of a recognizer started for this one clip: it loads the model, answers and stops."""
    command = [str(python), '-I', str(WORKER), '--model-dir', str(model), '--audio', str(clip)]
    if language:
        command += ['--language', str(language)]
    try:
        # Windows' own messages arrive in the code page, and one letter of that must not lose the message.
        reply = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=timeout, check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise DraftUnavailable('The transcript draft took too long. Type the transcript instead.') from None
    except OSError as e:
        raise DraftUnavailable(f'The speech recognizer could not be started: {e}') from None
    if reply.returncode:
        raise DraftUnavailable(_failed(reply.stderr))
    try:
        return json.loads(reply.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise DraftUnavailable('The transcript draft returned nothing readable.') from None


def _pump(stream, put, end=None):
    """Hand each line of a pipe to `put` until it closes, then `end` if one is given."""
    try:
        for line in stream:
            put(line)
    except (OSError, ValueError):               # the pipe was closed under it
        pass
    finally:
        if end is not None:
            put(end)


class Worker:
    """A recognizer kept running for a session's drafts (`asr_worker.py --serve`), so its model is loaded once.

    It starts with the first draft, and starts again after the recognizer settings change, after it stops, and after a
    draft that took too long. One clip is asked at a time. The last lines it writes to stderr are kept, so that a recognizer
    that stops says why, as one started for a single clip does: a Python that cannot start, say.
    """

    ERROR_LINES = 50
    _STOPPED = object()                         # put after the last line of answers, when the recognizer has gone

    def __init__(self):
        self._lock = threading.Lock()
        self._key = None                        # (python, model) of the recognizer running
        self._process = None
        self._answers = None                    # the lines it prints, then _STOPPED
        self._errors = None                     # the last lines of its stderr
        self._error_reader = None
        self._last_id = 0
        self._closed = False

    def ask(self, python, model, clip, language, timeout):
        """The recognizer's answer for one clip, as a dict. Raises DraftUnavailable."""
        with self._lock:
            if self._closed:
                raise DraftUnavailable('Draft transcripts have stopped: the session is closing.')
            key = (str(python), str(model))
            if self._process is None or self._key != key or self._process.poll() is not None:
                self._stop()
                self._start(key)
            self._last_id += 1
            ident = self._last_id
            request = {'id': ident, 'audio': str(clip), 'language': language or ''}
            try:
                self._process.stdin.write(json.dumps(request) + '\n')
                self._process.stdin.flush()
            except OSError:                     # it stopped before it was asked
                raise DraftUnavailable(self._why_stopped()) from None
            deadline = time.monotonic() + timeout
            while True:
                try:
                    line = self._answers.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    self._stop()
                    raise DraftUnavailable('The transcript draft took too long. Type the transcript instead.') from None
                if line is self._STOPPED:
                    raise DraftUnavailable(self._why_stopped())
                try:
                    reply = json.loads(line)
                except json.JSONDecodeError:
                    continue                    # something else the recognizer printed
                if not isinstance(reply, dict) or reply.get('id') != ident:
                    continue
                if reply.get('error'):
                    raise DraftUnavailable(_failed(str(reply['error'])))
                return reply

    def close(self):
        """Stop the recognizer for good, when the session ends. A draft it is still making is abandoned."""
        self._closed = True
        process = self._process
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _start(self, key):
        try:
            process = subprocess.Popen([key[0], '-I', str(WORKER), '--model-dir', key[1], '--serve'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding='utf-8', errors='replace', bufsize=1,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            raise DraftUnavailable(f'The speech recognizer could not be started: {e}') from None
        self._key, self._process = key, process
        self._answers, self._errors = queue.Queue(), collections.deque(maxlen=self.ERROR_LINES)
        threading.Thread(target=_pump, args=(process.stdout, self._answers.put, self._STOPPED),
                         name='recognizer-answers', daemon=True).start()
        self._error_reader = threading.Thread(target=_pump, args=(process.stderr, self._errors.append),
                                              name='recognizer-errors', daemon=True)
        self._error_reader.start()

    def _why_stopped(self):
        """What to say about a recognizer that stopped before it answered: the end of what it wrote to stderr."""
        try:
            code = self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        self._error_reader.join(timeout=5)
        detail = ''.join(self._errors).strip()
        self._stop()
        return _failed(detail or f'the speech recognizer stopped (exit code {code}).')

    def _stop(self):
        """Let the recognizer go: it is told there is nothing more to do, and ended if it is still busy."""
        process, self._process, self._key = self._process, None, None
        if process is None:
            return
        try:
            process.stdin.close()               # the end of its input, which --serve stops at
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
