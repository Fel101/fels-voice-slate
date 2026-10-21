"""Draft transcripts: the optional recognizer is run in another Python, so the tests stand in for it."""
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


def test_a_draft_is_read_from_the_last_line_of_the_workers_output(recognizer, monkeypatch):
    python, model = recognizer
    seen = {}

    def run(command, **kwargs):
        seen['command'] = command
        return subprocess.CompletedProcess(command, 0, stdout='loading...\n' + json.dumps(
            {'text': ' Hello there. ', 'language': 'en', 'duration_seconds': 1.0}) + '\n', stderr='')

    monkeypatch.setattr(transcript.subprocess, 'run', run)
    result = transcript.suggest(np.zeros(24000, np.float32), python, model)
    assert result == {'text': 'Hello there.', 'language': 'en', 'duration_seconds': 1.0, 'tidied': False}
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
    assert json.loads(capsys.readouterr().out) == {'text': 'Good morning. Everyone.', 'language': 'en',
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
