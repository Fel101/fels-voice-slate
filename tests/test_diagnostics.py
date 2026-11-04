"""Export diagnostics: a zip for a bug report, with what would name the person or their speakers replaced."""
import json
import zipfile

import pytest

from recorder import diagnostics, launch, settings


@pytest.fixture
def home(tmp_path, isolated):
    """A home folder with a recordings folder of two speakers, and a log that names all of it in every way it can."""
    home = tmp_path / 'Users' / 'Pat Smith'
    root = home / 'Recordings' / 'voice-clips'
    for slug in ('jane-doe', 'will'):
        (root / slug / 'masters').mkdir(parents=True)
    (root / 'speakers.json').write_text(json.dumps({'jane-doe': {'name': 'Jane Doe'}, 'will': {'name': 'Will'}}),
                                        encoding='utf-8')
    take = root / 'jane-doe' / 'masters' / '20261104-101500_mic_1a2b3c4d.raw.wav'
    settings.update({'root': str(root), 'ffmpeg': str(home / 'tools' / 'ffmpeg.exe'), 'window': 'AdnQywADAAAAAA==',
                     'input_device': {'mic': 'Microphone (USB Audio)'}})
    launch.log_path().write_text('\n'.join([
        f'Fel\'s Voice Slate starting. Settings: {settings.settings_path()}',
        f"PermissionError: [Errno 13] Permission denied: '{str(take)}'".replace('\\', '\\\\'),     # as repr() writes it
        f'Opened {str(take).replace(chr(92), "/")}',
        f'opened {str(home).lower()}\\desktop\\notes.txt',
        'D:\\Users\\Pat Smith\\Music\\x.wav and \\\\server\\pat smith\\share',
        'Kept a clip for Jane Doe. Will was next.',
        'It will retry. A background task failed.',
    ]) + '\n', encoding='utf-8')
    return home


def contents(path):
    with zipfile.ZipFile(path) as bundle:
        return {name: bundle.read(name).decode('utf-8') for name in bundle.namelist()}


def test_the_zip_holds_what_is_installed_the_settings_and_the_end_of_the_log(home, tmp_path):
    made = diagnostics.collect(tmp_path / 'diagnostics.zip', home=home)
    files = contents(made)
    assert set(files) == {'summary.txt', 'voice-slate.log'}
    summary = files['summary.txt']
    assert 'What is installed:' in summary and 'the window' in summary and 'draft transcripts (optional)' in summary
    assert 'input_device: {"mic": "Microphone (USB Audio)"}' in summary
    assert 'window:' not in summary                           # where the window was says nothing
    assert 'A background task failed.' in files['voice-slate.log']


def test_folders_the_user_name_and_speakers_are_hidden_however_a_path_is_written(home, tmp_path):
    text = '\n'.join(contents(diagnostics.collect(tmp_path / 'diagnostics.zip', home=home)).values())
    for personal in ('Pat Smith', 'pat smith', 'jane-doe', 'Jane Doe', 'Will was'):
        assert personal not in text, personal
    assert "Permission denied: '<recordings>\\\\<speaker 1>\\\\masters\\\\" in text
    assert 'Opened <recordings>/<speaker 1>/masters/' in text
    assert 'opened <home>\\desktop' in text
    assert 'D:\\Users\\<user>\\Music' in text and '\\\\server\\<user>\\share' in text
    assert 'Kept a clip for <speaker 1>. <speaker 2> was next.' in text
    assert 'It will retry.' in text                           # a name is hidden as it is written, not every word like it
    assert 'ffmpeg: <home>\\tools\\ffmpeg.exe' in text


def test_a_longer_name_that_begins_like_the_user_is_not_cut_in_half(tmp_path, isolated):
    hide = diagnostics.hider(home=tmp_path / 'Users' / 'pat', root=tmp_path / 'clips')
    assert hide('C:\\Users\\patricia\\x and C:\\Users\\pat\\y') == 'C:\\Users\\patricia\\x and C:\\Users\\<user>\\y'


def test_making_the_zip_changes_nothing_in_the_recordings_folder(home, tmp_path):
    """Not even a damaged speakers.json, which opening the library would move aside and rebuild."""
    root = home / 'Recordings' / 'voice-clips'
    (root / 'speakers.json').write_text('{ damaged', encoding='utf-8')

    def snapshot():
        return sorted((str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns) for path in root.rglob('*'))

    before = snapshot()
    text = '\n'.join(contents(diagnostics.collect(tmp_path / 'diagnostics.zip', home=home)).values())
    assert snapshot() == before
    assert 'jane-doe' not in text                             # the speaker folders still name the speakers


def test_with_no_log_yet_the_zip_holds_the_summary(tmp_path, isolated):
    files = contents(diagnostics.collect(tmp_path / 'diagnostics.zip', home=tmp_path / 'home', root=tmp_path / 'clips'))
    assert list(files) == ['summary.txt'] and 'Settings:' in files['summary.txt']


def test_the_check_and_the_zip_say_the_same_about_what_is_installed(tmp_path, isolated, capsys):
    from recorder import __main__ as entry

    entry.check()
    printed = capsys.readouterr().out
    for line in diagnostics.installed(settings.load())[0]:
        assert line in printed
