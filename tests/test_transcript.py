"""Draft transcripts: the optional recognizer is run in another Python, so the tests stand in for it."""
import io
import json
import subprocess
import sys
import types

import numpy as np
import pytest

from recorder import asr_worker, transcript


@pytest.fixture
def recognizer(tmp_path):
    python = tmp_path / 'python.exe'
    python.write_bytes(b'')
    model = tmp_path / 'model'
    model.mkdir()
    return python, model


def test_the_drafts_are_unavailable_until_both_settings_point_at_something(tmp_path, recognizer):
    python, model = recognizer
    assert transcript.available(python, model) is True
    assert transcript.available(None, model) is False
    assert transcript.available(python, None) is False
    assert transcript.available(tmp_path / 'missing.exe', model) is False
    assert transcript.available(python, tmp_path / 'missing') is False


def test_asking_for_a_draft_without_the_recognizer_explains_how_to_get_one():
    with pytest.raises(transcript.DraftUnavailable, match='README'):
        transcript.suggest(np.zeros(24000, np.float32), None, None)


def test_a_long_clip_is_given_longer_to_draft(recognizer, monkeypatch):
    python, model = recognizer
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout='{"text": "Words."}', stderr='')

    monkeypatch.setattr(transcript.subprocess, 'run', run)
    transcript.suggest(np.zeros(24000 * 600, np.float32), python, model)
    assert seen['timeout'] == transcript.TIMEOUT_SECONDS + 1800


def test_a_draft_is_read_from_the_last_line_of_the_workers_output(recognizer, monkeypatch):
    python, model = recognizer
    seen = {}

    def run(command, **kwargs):
        seen['command'] = command
        return subprocess.CompletedProcess(command, 0, stdout='loading...\n' + json.dumps(
            {'text': ' Hello there. ', 'language': 'en', 'duration_seconds': 1.0}) + '\n', stderr='')

    monkeypatch.setattr(transcript.subprocess, 'run', run)
    result = transcript.suggest(np.zeros(24000, np.float32), python, model)
    assert result == {'text': 'Hello there.', 'words': [], 'language': 'en', 'duration_seconds': 1.0, 'tidied': False}
    assert seen['command'][:3] == [str(python), '-I', str(transcript.WORKER)]
    assert '--model-dir' in seen['command'] and str(model) in seen['command']
    assert '--language' not in seen['command']


def test_the_language_is_passed_on_only_when_one_is_set(recognizer, monkeypatch):
    python, model = recognizer
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'text': 'Hola.'}) + '\n', stderr='')

    monkeypatch.setattr(transcript.subprocess, 'run', run)
    transcript.suggest(np.zeros(24000, np.float32), python, model, 'es')
    assert seen[-1][-2:] == ['--language', 'es']
    transcript.suggest(np.zeros(24000, np.float32), python, model, None)
    assert '--language' not in seen[-1]


def test_a_stuck_ending_is_removed_from_the_draft_and_the_draft_says_so(recognizer, monkeypatch):
    python, model = recognizer
    stuck = 'Things that hurt me, things I want And... And... And...'
    monkeypatch.setattr(transcript.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, stdout=json.dumps({'text': stuck}) + '\n', stderr=''))
    result = transcript.suggest(np.zeros(24000, np.float32), python, model)
    assert result['text'] == 'Things that hurt me, things I want' and result['tidied'] is True


@pytest.mark.parametrize('said, kept', [
    ('Things I want And... And... And...', 'Things I want'),
    ('Things I want And... And...', 'Things I want'),
    ('We went home. Thank you. Thank you. Thank you.', 'We went home. Thank you.'),
    ('It was very very very very good.', 'It was very good.'),
    ('Go to the shop, go to the shop, go to the shop, go to the shop, and buy bread.', 'Go to the shop, and buy bread.'),
])
def test_a_word_or_phrase_the_recognizer_got_stuck_on_is_kept_once(said, kept):
    assert transcript.tidy_draft(said) == (kept, True)


@pytest.mark.parametrize('said', [
    'I said no, no, no, that is not it.',                  # three in the middle is how people talk
    'And... then we left.',                                # one trailing-off fragment is kept
    'Wait... wait. What did you say?',
    'It is a very, very good day.',
    '',
])
def test_ordinary_speech_is_left_exactly_as_it_was(said):
    assert transcript.tidy_draft(said) == (said, False)


@pytest.mark.parametrize('outcome, message', [
    (subprocess.CompletedProcess([], 3, stdout='', stderr='boom'), 'boom'),
    (subprocess.CompletedProcess([], 0, stdout='not json', stderr=''), 'nothing readable'),
    (subprocess.CompletedProcess([], 0, stdout='', stderr=''), 'nothing readable'),
])
def test_a_failing_worker_is_reported_in_words(recognizer, monkeypatch, outcome, message):
    python, model = recognizer
    monkeypatch.setattr(transcript.subprocess, 'run', lambda *a, **k: outcome)
    with pytest.raises(transcript.DraftUnavailable, match=message):
        transcript.suggest(np.zeros(24000, np.float32), python, model)


@pytest.mark.parametrize('said', ['Déjà vu, il a été très café.', 'It’s £5 at the café…', 'Zażółć gęślą jaźń.',
                                  'Привет, мир.', 'こんにちは世界。', 'مرحبا بالعالم'])
def test_a_draft_in_any_language_comes_through_a_real_pipe(tmp_path, recognizer, monkeypatch, said):
    """The real worker in a real process, with only the recognizer stood in for. On Windows a pipe uses the code page."""
    worker = tmp_path / 'worker.py'
    worker.write_text(f'''
import runpy, sys, types

sys.stdout.reconfigure(encoding='cp1252')           # what a pipe has here, and it must not depend on the test's computer


class Model:
    def __init__(self, path, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        return [types.SimpleNamespace(text={said!r})], types.SimpleNamespace(language='xx', language_probability=1.0,
                                                                             duration=1.0)


sys.modules['faster_whisper'] = types.SimpleNamespace(WhisperModel=Model)
runpy.run_path({asr_worker.__file__!r}, run_name='__main__')
''', encoding='utf-8')
    monkeypatch.setattr(transcript, 'WORKER', worker)
    assert transcript.suggest(np.zeros(24000, np.float32), sys.executable, recognizer[1])['text'] == said


def test_a_message_in_the_code_page_still_reaches_the_person(tmp_path, recognizer, monkeypatch):
    """Windows and Python write their own errors in the code page, so a letter of one is not UTF-8."""
    worker = tmp_path / 'worker.py'
    worker.write_text("import sys\nsys.stderr.buffer.write(b'Das Modul f\\xfcr ctranslate2 fehlt\\n')\nsys.exit(1)\n",
                      encoding='utf-8')
    monkeypatch.setattr(transcript, 'WORKER', worker)
    with pytest.raises(transcript.DraftUnavailable, match='ctranslate2 fehlt'):
        transcript.suggest(np.zeros(24000, np.float32), sys.executable, recognizer[1])


def test_a_timeout_or_a_missing_interpreter_is_reported_in_words(recognizer, monkeypatch):
    python, model = recognizer

    def slow(*a, **k):
        raise subprocess.TimeoutExpired('x', 1)

    monkeypatch.setattr(transcript.subprocess, 'run', slow)
    with pytest.raises(transcript.DraftUnavailable, match='too long'):
        transcript.suggest(np.zeros(24000, np.float32), python, model)

    def broken(*a, **k):
        raise OSError('not a valid Win32 application')

    monkeypatch.setattr(transcript.subprocess, 'run', broken)
    with pytest.raises(transcript.DraftUnavailable, match='could not be started'):
        transcript.suggest(np.zeros(24000, np.float32), python, model)


def test_the_worker_reads_a_local_folder_and_never_downloads(tmp_path, monkeypatch, capsys):
    seen = {}

    class FakeModel:
        def __init__(self, path, **kwargs):
            seen['path'], seen['kwargs'] = path, kwargs

        def transcribe(self, audio, **kwargs):
            segment = types.SimpleNamespace(text=' Good morning. ')
            return [segment, types.SimpleNamespace(text='Everyone.')], types.SimpleNamespace(
                language='en', language_probability=0.9876, duration=3.0)

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=FakeModel))
    assert asr_worker.main(['--model-dir', str(tmp_path), '--audio', 'clip.wav']) == 0
    assert seen['kwargs']['local_files_only'] is True
    assert json.loads(capsys.readouterr().out) == {'text': 'Good morning. Everyone.', 'words': [], 'language': 'en',
                                                   'language_probability': 0.988, 'duration_seconds': 3.0,
                                                   'silence_filter': True}


def test_the_worker_cuts_out_the_silence_and_names_the_language_when_it_is_told_to(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeModel:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return [types.SimpleNamespace(text='Hello.')], types.SimpleNamespace(language='es', language_probability=1.0,
                                                                                 duration=1.0)

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=FakeModel))
    assert asr_worker.main(['--model-dir', str(tmp_path), '--audio', 'clip.wav', '--language', 'ES']) == 0
    assert calls[-1]['vad_filter'] is True and calls[-1]['language'] == 'es'
    assert calls[-1]['vad_parameters'] == asr_worker.VOICE_ACTIVITY
    assert calls[-1]['condition_on_previous_text'] is False
    assert asr_worker.main(['--model-dir', str(tmp_path), '--audio', 'clip.wav']) == 0
    assert calls[-1]['language'] is None
    capsys.readouterr()


def test_the_worker_carries_on_without_the_silence_filter_when_that_filter_cannot_run(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeModel:
        def __init__(self, path, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs['vad_filter'])
            if kwargs['vad_filter']:
                raise RuntimeError('onnxruntime is not installed')
            return [types.SimpleNamespace(text='Still works.')], types.SimpleNamespace(language='en',
                                                                                      language_probability=1.0,
                                                                                      duration=1.0)

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=FakeModel))
    assert asr_worker.main(['--model-dir', str(tmp_path), '--audio', 'clip.wav']) == 0
    out, err = capsys.readouterr()
    assert calls == [True, False] and json.loads(out)['silence_filter'] is False
    assert json.loads(out)['text'] == 'Still works.' and 'onnxruntime' in err


@pytest.mark.parametrize('given, expected', [('', None), ('auto', None), (' EN ', 'en'), ('pt', 'pt'), ('fil', 'fil')])
def test_a_language_code_is_read_as_a_code_or_as_detect(given, expected):
    assert asr_worker.language_code(given) == expected


@pytest.mark.parametrize('given', ['english', 'e', '12', 'en-US'])
def test_anything_else_for_the_language_is_refused(given, tmp_path, capsys):
    with pytest.raises(ValueError):
        asr_worker.language_code(given)
    assert asr_worker.main(['--model-dir', str(tmp_path), '--audio', 'clip.wav', '--language', given]) == 2
    assert 'language' in capsys.readouterr().err


def test_the_worker_refuses_a_model_name_instead_of_a_folder(capsys):
    assert asr_worker.main(['--model-dir', 'large-v3', '--audio', 'clip.wav']) == 2
    assert 'does not exist' in capsys.readouterr().err


def test_the_worker_needs_a_clip_unless_it_serves(tmp_path, capsys):
    with pytest.raises(SystemExit):
        asr_worker.main(['--model-dir', str(tmp_path)])
    assert '--serve' in capsys.readouterr().err


# ---- the recognizer kept running for a session's drafts ---------------------------------------------------------------

KEPT = r'''
import runpy, sys, types
from pathlib import Path

sys.stdout.reconfigure(encoding='cp1252')           # what a pipe has here, and it must not depend on the test's computer
here = Path(__file__).parent
with open(here / 'starts.txt', 'a', encoding='utf-8') as starts:
    starts.write('start\n')


class Model:
    def __init__(self, path, **kwargs):
        with open(here / 'loads.txt', 'a', encoding='utf-8') as loads:
            loads.write('load\n')
        if LOAD_FAILS:
            raise RuntimeError('model.bin is damaged')

    def transcribe(self, audio, language=None, **kwargs):
        text = SAID + ' (' + (language or 'detected') + ')'
        return [types.SimpleNamespace(text=text)], types.SimpleNamespace(language=language or 'en', language_probability=1.0,
                                                                          duration=1.0)


sys.modules['faster_whisper'] = types.SimpleNamespace(WhisperModel=Model)
runpy.run_path(WORKER, run_name='__main__')
'''


def kept(tmp_path, monkeypatch, said='Grüße aus dem Café.', load_fails=False, script=None):
    """A Worker running the real asr_worker.py --serve in a real process, with only faster-whisper stood in for (or
    `script` instead), and the model folder to give it. The stand-in counts its starts and model loads in files."""
    worker_file = tmp_path / 'worker.py'
    worker_file.write_text(script or KEPT.replace('SAID', repr(said)).replace('LOAD_FAILS', repr(load_fails))
                           .replace('WORKER', repr(asr_worker.__file__)), encoding='utf-8')
    monkeypatch.setattr(transcript, 'WORKER', worker_file)
    model = tmp_path / 'model'
    model.mkdir(exist_ok=True)
    return transcript.Worker(), model


def draft(worker, model, language=None):
    return transcript.suggest(np.zeros(24000, np.float32), sys.executable, model, language, 24000, worker)


def counted(tmp_path, name):
    """How many lines the stand-in wrote to starts.txt or loads.txt."""
    path = tmp_path / f'{name}.txt'
    return len(path.read_text(encoding='utf-8').splitlines()) if path.exists() else 0


def test_a_kept_recognizer_loads_its_model_once_for_all_the_drafts(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch)
    try:
        first, second = draft(worker, model), draft(worker, model, 'de')
    finally:
        worker.close()
    assert first['text'] == 'Grüße aus dem Café. (detected)' and second['text'] == 'Grüße aus dem Café. (de)'
    assert counted(tmp_path, 'starts') == 1 and counted(tmp_path, 'loads') == 1


def test_a_kept_recognizer_that_cannot_start_says_why(tmp_path, monkeypatch):
    """What a recognizer environment says once its Python was uninstalled. The message is how the cause is found."""
    worker, model = kept(tmp_path, monkeypatch, script='import sys\n'
                         'sys.stderr.write("No Python at \'C:\\\\NoSuchPython313\\\\python.exe\'\\n")\n'
                         'sys.exit(103)\n')
    with pytest.raises(transcript.DraftUnavailable, match=r"failed: No Python at 'C:\\NoSuchPython313"):
        draft(worker, model)
    worker.close()


def test_a_kept_recognizer_with_no_message_gives_its_exit_code(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch, script='import sys\nsys.exit(7)\n')
    with pytest.raises(transcript.DraftUnavailable, match='exit code 7'):
        draft(worker, model)
    worker.close()


def test_a_model_that_will_not_load_is_reported_and_tried_again_for_the_next_draft(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch, load_fails=True)
    try:
        for _ in range(2):
            with pytest.raises(transcript.DraftUnavailable, match='RuntimeError: model.bin is damaged'):
                draft(worker, model)
    finally:
        worker.close()
    assert counted(tmp_path, 'starts') == 1 and counted(tmp_path, 'loads') == 2


def test_a_kept_recognizer_that_takes_too_long_is_stopped_and_a_new_one_started(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch, script='import sys\nfrom pathlib import Path\n'
                         "Path(__file__).with_name('starts.txt').open('a').write('start\\n')\n"
                         'for line in sys.stdin:\n    pass\n')            # never answers
    monkeypatch.setattr(transcript, 'TIMEOUT_SECONDS', 0.3)
    monkeypatch.setattr(transcript, 'TIMEOUT_PER_SECOND', 0)
    try:
        for _ in range(2):
            with pytest.raises(transcript.DraftUnavailable, match='too long'):
                draft(worker, model)
    finally:
        worker.close()
    assert counted(tmp_path, 'starts') == 2


def test_other_recognizer_settings_start_another_recognizer(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch)
    other = tmp_path / 'other-model'
    other.mkdir()
    try:
        draft(worker, model)
        draft(worker, other)
        draft(worker, other)
    finally:
        worker.close()
    assert counted(tmp_path, 'starts') == 2


def test_a_closed_recognizer_is_stopped_and_starts_no_other(tmp_path, monkeypatch):
    worker, model = kept(tmp_path, monkeypatch)
    draft(worker, model)
    process = worker._process
    worker.close()
    assert process.poll() is not None
    with pytest.raises(transcript.DraftUnavailable, match='stopped'):
        draft(worker, model)
    assert counted(tmp_path, 'starts') == 1


def test_the_serving_worker_answers_each_request_by_its_id_and_carries_on_after_a_bad_one(tmp_path, monkeypatch):
    loads = []
    monkeypatch.setattr(asr_worker, 'load', lambda folder: loads.append(folder) or object())
    monkeypatch.setattr(asr_worker, 'transcribe', lambda model, audio, language, filtered: (
        f'Heard {audio}.', [['Heard', 0.0, 0.4]], types.SimpleNamespace(language=language or 'en', language_probability=0.5,
                                                                        duration=1.0)))
    requests = io.StringIO('\n'.join([json.dumps({'id': 1, 'audio': 'a.wav', 'language': 'de'}), '',
                                      json.dumps({'id': 2, 'audio': 'b.wav', 'language': 'not a code'}),
                                      json.dumps({'id': 3, 'audio': 'c.wav'})]) + '\n')
    replies = io.StringIO()
    assert asr_worker.serve(str(tmp_path), requests, replies) == 0
    answers = [json.loads(line) for line in replies.getvalue().splitlines()]
    assert [answer['id'] for answer in answers] == [1, 2, 3]
    assert answers[0]['text'] == 'Heard a.wav.' and answers[0]['language'] == 'de'
    assert answers[0]['words'] == [['Heard', 0.0, 0.4]]
    assert answers[1]['error'].startswith('ValueError: The language must be a code')
    assert answers[2]['text'] == 'Heard c.wav.' and answers[2]['language'] == 'en'
    assert loads == [str(tmp_path)]
