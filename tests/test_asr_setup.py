"""The draft-transcript setup: it downloads, so the tests stand in for pip, Hugging Face and the recognizer."""
import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from recorder import __main__ as entry
from recorder import asr_setup, settings, transcript

MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


class Machine:
    """What the setup's commands do on a pretend computer: make the environment, install, fetch a model, run the worker."""

    def __init__(self, folder):
        self.folder = folder
        self.calls = []
        self.installed = False
        self.fail = None                    # 'venv', 'pip', 'fetch' or 'worker': that step exits with an error
        self.worker_text = ''

    @property
    def python(self):
        return self.folder / asr_setup.VENV / 'Scripts' / 'python.exe'

    def steps(self):
        return [name for name, _ in self.calls]

    def run(self, command, **kwargs):
        command = [str(part) for part in command]
        if command[1:3] == ['-m', 'venv']:
            return self._step('venv', command, lambda: self._make_python(Path(command[3])))
        if command[1:3] == ['-m', 'pip']:
            return self._step('pip', command, lambda: setattr(self, 'installed', True))
        if command[1:3] == ['-I', '-c'] and command[3] == 'import faster_whisper':
            return subprocess.CompletedProcess(command, 0 if self.installed else 1, stdout=b'', stderr=b'')
        if command[1:3] == ['-I', '-c']:
            return self._step('fetch', command, lambda: self.make_model(Path(command[-1])))
        if command[1:3] == ['-I', str(transcript.WORKER)]:
            self.calls.append(('worker', command))
            if self.fail == 'worker':
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='ImportError: no ctranslate2')
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'text': self.worker_text}) + '\n', stderr='')
        raise AssertionError(f'a command the setup should not run: {command}')

    def _step(self, name, command, effect):
        self.calls.append((name, command))
        if self.fail == name:
            return subprocess.CompletedProcess(command, 1)
        effect()
        return subprocess.CompletedProcess(command, 0)

    def _make_python(self, venv):
        python = venv / 'Scripts' / 'python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'')

    @staticmethod
    def make_model(folder):
        folder.mkdir(parents=True, exist_ok=True)
        for name in MODEL_FILES:
            (folder / name).write_bytes(b'x')

    def command(self, step):
        return next(command for name, command in self.calls if name == step)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A program folder with its requirements file, settings kept in a temporary file, and no real commands run."""
    monkeypatch.setenv(settings.ENV_VAR, str(tmp_path / 'settings.json'))
    folder = tmp_path / 'program'
    folder.mkdir()
    (folder / asr_setup.REQUIREMENTS).write_text('faster-whisper==1.2.1\n', encoding='utf-8')
    pretend = Machine(folder)
    monkeypatch.setattr(subprocess, 'run', pretend.run)
    return pretend


def say(monkeypatch, *answers):
    """Answer the questions the setup asks, in order. A question with no answer left fails the test."""
    answers = list(answers)
    monkeypatch.setattr('builtins.input', lambda prompt='': answers.pop(0))
    return answers


def saved():
    return settings.load()


# ---- the answers ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('typed, size', [('', 'base'), ('1', 'tiny'), ('2', 'base'), ('3', 'small'), (' Small ', 'small'),
                                         ('TINY', 'tiny')])
def test_a_model_size_is_read_by_name_or_number(typed, size):
    assert asr_setup.size_choice(typed) == size


@pytest.mark.parametrize('typed', ['huge', '0', '4', 'base.en', 'medium'])
def test_a_size_that_is_not_offered_is_refused(typed):
    with pytest.raises(ValueError, match='Choose one of tiny, base, small'):
        asr_setup.size_choice(typed)


@pytest.mark.parametrize('typed, code', [('', 'en'), ('EN', 'en'), ('English', 'en'), ('de', 'de'), (' Fr ', 'fr'),
                                         ('auto', None), ('yue', 'yue')])
def test_a_language_is_read_as_a_code_and_english_is_the_default(typed, code):
    assert asr_setup.language_code(typed) == code


@pytest.mark.parametrize('typed', ['e', 'german', 'd3', 'en-US', 'x'])
def test_a_language_that_is_not_a_code_is_refused(typed):
    with pytest.raises(ValueError, match='not a language code'):
        asr_setup.language_code(typed)


def test_english_gets_the_english_only_model_and_other_languages_the_multilingual_one():
    assert asr_setup.model_name('base', 'en') == 'base.en'
    assert asr_setup.model_name('small', 'de') == 'small'
    assert asr_setup.model_name('tiny', None) == 'tiny'
    assert asr_setup.describe('base', 'en') == 'the base model for English'
    assert asr_setup.describe('small', 'de') == asr_setup.describe('small', None) == 'the small multilingual model'


def test_the_models_on_offer_are_real_faster_whisper_repositories():
    """These names, with and without .en, are the repositories SYSTRAN publishes (faster-whisper's own list has them)."""
    assert list(asr_setup.SIZES) == ['tiny', 'base', 'small'] and asr_setup.DEFAULT_SIZE in asr_setup.SIZES
    names = [asr_setup.model_name(size, language) for size in asr_setup.SIZES for language in ('en', 'de')]
    assert [asr_setup.REPOSITORY.format(name=name) for name in names] == [
        'Systran/faster-whisper-tiny.en', 'Systran/faster-whisper-tiny', 'Systran/faster-whisper-base.en',
        'Systran/faster-whisper-base', 'Systran/faster-whisper-small.en', 'Systran/faster-whisper-small']


def test_the_download_asks_for_the_files_a_model_needs_and_nothing_else():
    assert asr_setup.MODEL_PATTERNS == ['config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json',
                                        'vocabulary.*']
    assert repr(asr_setup.MODEL_PATTERNS) in asr_setup.FETCH


# ---- what is already there --------------------------------------------------------------------------------------------

def test_a_model_folder_is_complete_only_with_its_weights_settings_tokenizer_and_vocabulary(tmp_path):
    folder = tmp_path / 'model'
    assert not asr_setup.model_complete(folder)
    Machine.make_model(folder)
    assert asr_setup.model_complete(folder)
    (folder / 'vocabulary.txt').rename(folder / 'vocabulary.json')          # some models ship the vocabulary as JSON
    assert asr_setup.model_complete(folder)
    (folder / 'tokenizer.json').unlink()
    assert not asr_setup.model_complete(folder)


def test_the_recognizer_is_ready_only_when_its_python_exists_and_loads_faster_whisper(machine):
    assert not asr_setup.recognizer_ready(machine.python)
    machine._make_python(machine.folder / asr_setup.VENV)
    assert not asr_setup.recognizer_ready(machine.python)                   # the environment is there but empty
    machine.installed = True
    assert asr_setup.recognizer_ready(machine.python)


# ---- the whole setup --------------------------------------------------------------------------------------------------

def test_the_setup_installs_downloads_checks_and_then_writes_the_settings(machine, capsys):
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 0
    assert machine.steps() == ['venv', 'pip', 'fetch', 'worker']
    pip = machine.command('pip')
    assert pip[:3] == [str(machine.python), '-m', 'pip'] and '--only-binary=:all:' in pip
    assert pip[-2:] == ['-r', str(machine.folder / asr_setup.REQUIREMENTS)]
    model = machine.folder / asr_setup.MODELS / 'faster-whisper-base.en'
    fetch = machine.command('fetch')
    assert fetch[:3] == [str(machine.python), '-I', '-c'] and fetch[-2:] == ['Systran/faster-whisper-base.en', str(model)]
    assert 'snapshot_download' in fetch[3] and 'download_model' not in fetch[3]          # the one that shows progress
    assert saved() == {'asr_python': str(machine.python), 'asr_model': str(model), 'asr_language': 'en'}
    out = capsys.readouterr().out
    assert 'Done' in out and 'no account or token is needed' in out          # Hugging Face's anonymous-use notice is normal
    assert transcript.available(saved()['asr_python'], saved()['asr_model'])


def test_a_long_download_says_how_much_has_arrived(machine, monkeypatch, capsys):
    """The bars the download draws count files, so the largest file would sit at one bar for minutes without this."""
    monkeypatch.setattr(asr_setup, 'PROGRESS_SECONDS', 0.05)
    finish = Machine.make_model                                              # the plain version, before it is replaced

    def slow(folder):
        partial = folder / '.cache' / 'huggingface' / 'download' / 'model.bin.incomplete'
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b'x' * 3_000_000)
        time.sleep(0.5)                                                      # long enough for several reports
        partial.unlink()
        finish(folder)

    monkeypatch.setattr(Machine, 'make_model', staticmethod(slow))
    assert asr_setup.run('tiny', 'en', yes=True, folder=machine.folder) == 0
    out = capsys.readouterr().out
    assert '3 of about 80 MB downloaded' in out and out.count('MB downloaded') == 1       # said once, not on every tick


def test_the_megabytes_so_far_count_partial_files_in_every_subfolder(tmp_path):
    assert asr_setup._megabytes_in(tmp_path / 'not-there-yet') == 0
    (tmp_path / 'a' / 'b').mkdir(parents=True)
    (tmp_path / 'model.bin').write_bytes(b'x' * 2_000_000)
    (tmp_path / 'a' / 'b' / 'part.incomplete').write_bytes(b'x' * 1_500_000)
    assert asr_setup._megabytes_in(tmp_path) == 3


def test_the_questions_are_asked_when_no_answers_were_given(machine, monkeypatch):
    say(monkeypatch, '3', 'de', '')
    assert asr_setup.run(folder=machine.folder) == 0
    assert machine.command('fetch')[-2:] == ['Systran/faster-whisper-small',
                                             str(machine.folder / asr_setup.MODELS / 'faster-whisper-small')]
    assert saved()['asr_language'] == 'de' and saved()['asr_model'].endswith('faster-whisper-small')


def test_a_question_asked_twice_when_the_answer_is_no_good(machine, monkeypatch, capsys):
    say(monkeypatch, 'huge', '2', 'german', '', 'y')
    assert asr_setup.run(folder=machine.folder) == 0
    out = capsys.readouterr().out
    assert 'Choose one of tiny, base, small' in out and 'not a language code' in out
    assert saved()['asr_language'] == 'en' and saved()['asr_model'].endswith('faster-whisper-base.en')


def test_automatic_language_detection_uses_the_multilingual_model_and_leaves_the_language_unset(machine):
    settings.update({'asr_language': 'en'})
    assert asr_setup.run('tiny', 'auto', yes=True, folder=machine.folder) == 0
    assert saved()['asr_model'].endswith('faster-whisper-tiny') and 'asr_language' not in saved()


def test_nothing_is_downloaded_or_changed_unless_the_person_says_go_ahead(machine, monkeypatch, capsys):
    say(monkeypatch, 'n')
    assert asr_setup.run('base', 'en', folder=machine.folder) == 0
    assert machine.calls == [] and not (machine.folder / asr_setup.VENV).exists() and saved() == {}
    assert 'Nothing was downloaded or changed' in capsys.readouterr().out


def test_what_will_be_downloaded_is_said_before_asking(machine, monkeypatch, capsys):
    say(monkeypatch, 'n')
    asr_setup.run('small', 'de', folder=machine.folder)
    out = capsys.readouterr().out
    assert 'from PyPI' in out and 'from Hugging Face' in out and 'the small multilingual model' in out
    assert 'about 90 MB' in out and 'about 490 MB' in out and 'nothing about you' in out.lower().replace('\n', ' ')


def test_an_answer_the_setup_cannot_use_on_the_command_line_stops_it_at_once(machine, capsys):
    assert asr_setup.run('huge', 'en', yes=True, folder=machine.folder) == 2
    assert asr_setup.run('base', 'german', yes=True, folder=machine.folder) == 2
    assert machine.calls == [] and saved() == {}
    assert 'not a language code' in capsys.readouterr().out


@pytest.mark.parametrize('step', ['venv', 'pip', 'fetch', 'worker'])
def test_a_step_that_fails_leaves_the_settings_alone_and_says_how_to_try_again(machine, capsys, step):
    settings.update({'ffmpeg': 'C:/tools/ffmpeg.exe'})
    machine.fail = step
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 1
    assert saved() == {'ffmpeg': 'C:/tools/ffmpeg.exe'}
    out = capsys.readouterr().out
    assert 'Nothing in your settings was changed' in out and 'setup-transcripts.bat' in out


def test_a_recognizer_that_cannot_run_is_reported_with_its_own_error(machine, capsys):
    machine.fail = 'worker'
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 1
    assert 'did not run' in capsys.readouterr().out and saved() == {}


def test_a_download_that_leaves_a_model_incomplete_is_not_trusted(machine, monkeypatch, capsys):
    monkeypatch.setattr(Machine, 'make_model', staticmethod(lambda folder: folder.mkdir(parents=True, exist_ok=True)))
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 1
    assert 'missing some of its files' in capsys.readouterr().out and saved() == {}


def test_what_is_already_installed_is_only_checked_and_connected(machine, capsys):
    machine._make_python(machine.folder / asr_setup.VENV)
    machine.installed = True
    machine.make_model(machine.folder / asr_setup.MODELS / 'faster-whisper-base.en')
    assert asr_setup.run('base', 'en', folder=machine.folder) == 0          # no question: there is nothing to download
    assert machine.steps() == ['worker']
    assert 'already here' in capsys.readouterr().out and saved()['asr_model'].endswith('faster-whisper-base.en')


def test_another_model_can_be_added_beside_one_that_is_there(machine):
    asr_setup.run('base', 'en', yes=True, folder=machine.folder)
    machine.calls.clear()
    asr_setup.run('small', 'en', yes=True, folder=machine.folder)
    assert machine.steps() == ['fetch', 'worker']                           # the recognizer is reused, only the model is new
    assert saved()['asr_model'].endswith('faster-whisper-small.en')
    assert (machine.folder / asr_setup.MODELS / 'faster-whisper-base.en').is_dir()


def test_not_enough_free_space_stops_before_anything_is_downloaded(machine, monkeypatch, capsys):
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: SimpleNamespace(total=10**9, used=0, free=100 * 1024 * 1024))
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 1
    assert 'not enough free space' in capsys.readouterr().out and machine.calls == [] and saved() == {}


def test_a_missing_requirements_file_is_explained(machine, capsys):
    (machine.folder / asr_setup.REQUIREMENTS).unlink()
    assert asr_setup.run('base', 'en', yes=True, folder=machine.folder) == 1
    assert 'requirements-asr.txt is missing' in capsys.readouterr().out and saved() == {}


def test_cancelling_leaves_everything_as_it_was(machine, monkeypatch, capsys):
    def interrupt(prompt=''):
        raise KeyboardInterrupt

    monkeypatch.setattr('builtins.input', interrupt)
    assert asr_setup.run(folder=machine.folder) == 1
    assert 'Cancelled' in capsys.readouterr().out and machine.calls == [] and saved() == {}


def test_a_closed_console_cancels_instead_of_crashing(machine, monkeypatch, capsys):
    def closed(prompt=''):
        raise EOFError

    monkeypatch.setattr('builtins.input', closed)
    assert asr_setup.run(folder=machine.folder) == 1
    assert 'Cancelled' in capsys.readouterr().out


# ---- how people reach it ----------------------------------------------------------------------------------------------

def test_the_command_line_hands_its_answers_to_the_setup(monkeypatch):
    seen = []
    monkeypatch.setattr(asr_setup, 'run', lambda size, language, yes: seen.append((size, language, yes)) or 0)
    assert entry.main(['--setup-transcripts', 'small', '--language', 'de', '--yes']) == 0
    assert entry.main(['--setup-transcripts']) == 0
    assert seen == [('small', 'de', True), (None, None, False)]


def test_the_check_says_whether_draft_transcripts_are_set_up(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv(settings.ENV_VAR, str(tmp_path / 'settings.json'))
    entry.check()
    assert 'not set up: run setup-transcripts.bat' in capsys.readouterr().out
    python = tmp_path / 'python.exe'
    python.write_bytes(b'')
    model = tmp_path / 'model'
    model.mkdir()
    settings.update({'asr_python': str(python), 'asr_model': str(model)})
    entry.check()
    out = capsys.readouterr().out
    assert 'draft transcripts (optional)' in out and 'ready' in out.split('draft transcripts (optional)')[1].splitlines()[0]


def test_the_setup_scripts_are_there_and_keep_a_double_clicked_window_open():
    folder = settings.program_dir()
    script = (folder / transcript.SETUP_SCRIPT).read_text(encoding='utf-8')
    assert '-m recorder --setup-transcripts %*' in script
    assert script.rstrip().splitlines()[-2:] == ['pause', 'exit /b %CODE%']          # it pauses whether it worked or not
    assert 'Run setup.bat first' in script
    setup = (folder / 'setup.bat').read_text(encoding='utf-8')
    assert '-m recorder --setup-transcripts' in setup and 'setup-transcripts.bat' in setup
    assert (folder / asr_setup.REQUIREMENTS).is_file()


def test_the_requirements_pin_every_package_the_recognizer_needs_directly():
    lines = [line.split('#')[0].strip() for line in (settings.program_dir() / asr_setup.REQUIREMENTS)
             .read_text(encoding='utf-8').splitlines()]
    pins = {line.split('==')[0] for line in lines if line}
    assert pins == {'faster-whisper', 'ctranslate2', 'onnxruntime', 'av', 'huggingface_hub', 'tokenizers', 'numpy'}
    assert all('==' in line for line in lines if line)


def test_the_setup_leaves_no_trace_in_git(tmp_path):
    ignored = (settings.program_dir() / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert '/.venv-asr/' in ignored and '/models/' in ignored
