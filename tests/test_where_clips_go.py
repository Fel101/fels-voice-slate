"""Where recordings are kept: in the person's own folder, so that updating the program cannot touch them."""
import json
from pathlib import Path

import pytest

from helpers import fill, record
from recorder import settings
from recorder.store import Relocation


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program folder and a home folder of their own."""
    folder = tmp_path / 'program'
    folder.mkdir()
    monkeypatch.setattr(settings, 'program_dir', lambda: folder)
    monkeypatch.setattr(settings, 'USER_ROOT', tmp_path / 'home' / 'Recordings' / 'voice-clips')
    for name in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        monkeypatch.delenv(name, raising=False)
    return folder


def with_recordings(folder):
    (folder / 'voice-clips' / 'my-voice' / 'masters').mkdir(parents=True)
    return folder / 'voice-clips'


def test_a_new_copy_keeps_recordings_in_the_persons_own_folder(program):
    assert settings.default_root() == settings.USER_ROOT
    assert not (program / 'voice-clips').exists()


def test_an_older_copy_goes_on_using_the_folder_it_has(program):
    folder = with_recordings(program)
    assert settings.default_root() == folder


def test_the_folder_it_has_is_still_kept_out_of_git(program):
    folder = with_recordings(program)
    settings.default_root()
    assert (folder / '.gitignore').is_file()


def test_a_list_of_speakers_counts_as_recordings(program):
    folder = program / 'voice-clips'
    folder.mkdir()
    (folder / 'speakers.json').write_text('{"me": {"name": "Me"}}', encoding='utf-8')
    assert settings.default_root() == folder


def test_an_empty_list_of_speakers_does_not(program):
    folder = program / 'voice-clips'
    folder.mkdir()
    (folder / 'speakers.json').write_text('{}', encoding='utf-8')
    assert settings.default_root() == settings.USER_ROOT


def test_a_synced_program_folder_is_not_used_even_with_recordings_in_it(tmp_path, monkeypatch):
    folder = tmp_path / 'OneDrive' / 'program'
    folder.mkdir(parents=True)
    monkeypatch.setattr(settings, 'program_dir', lambda: folder)
    monkeypatch.setattr(settings, 'USER_ROOT', tmp_path / 'home')
    with_recordings(folder)
    assert settings.default_root() == tmp_path / 'home'


# ---- moving them ----------------------------------------------------------------------------------------------------------------

def tree(tmp_path):
    source = tmp_path / 'old'
    (source / 'me' / 'masters').mkdir(parents=True)
    (source / 'speakers.json').write_text('{"me": {"name": "Me"}}', encoding='utf-8')
    (source / 'me' / 'clip.wav').write_bytes(b'kept audio')
    (source / 'me' / 'masters' / 'clip.raw.wav').write_bytes(b'original audio')
    return source


def test_every_file_arrives_and_the_old_ones_go_only_at_the_end(tmp_path):
    source, target = tree(tmp_path), tmp_path / 'new'
    moving = Relocation(source, target)
    assert len(moving.files) == 3
    for path in moving.files:
        moving.copy(path)
    assert (source / 'me' / 'clip.wav').exists() and (target / 'me' / 'clip.wav').read_bytes() == b'kept audio'
    assert moving.finish() == []
    assert not (source / 'me').exists() and (target / 'me' / 'masters' / 'clip.raw.wav').read_bytes() == b'original audio'


def test_stopping_on_the_way_leaves_the_old_folder_as_it_was(tmp_path):
    source, target = tree(tmp_path), tmp_path / 'new'
    moving = Relocation(source, target)
    moving.copy(moving.files[0])
    moving.undo()
    assert not target.exists() and len(list(source.rglob('*.wav'))) == 2


def test_a_copy_that_does_not_match_stops_it(tmp_path, monkeypatch):
    from recorder import store
    source, target = tree(tmp_path), tmp_path / 'new'
    moving = Relocation(source, target)
    real, calls = store.file_sha256, []

    def wrong(path):
        calls.append(path)
        return 'different' if len(calls) == 1 else real(path)

    monkeypatch.setattr(store, 'file_sha256', wrong)
    with pytest.raises(OSError, match='copied exactly'):
        moving.copy(moving.files[0])
    assert not any(path.name.endswith('.tmp') for path in target.rglob('*'))


@pytest.mark.parametrize('inside', ['old/inner', 'old', '.'])
def test_folders_inside_each_other_are_refused(tmp_path, inside):
    source = tree(tmp_path)
    with pytest.raises(ValueError, match='inside each other'):
        Relocation(source, tmp_path / inside)


def test_a_folder_with_something_in_it_is_refused(tmp_path):
    source, target = tree(tmp_path), tmp_path / 'new'
    target.mkdir()
    (target / 'mine.txt').write_text('keep me', encoding='utf-8')
    with pytest.raises(ValueError, match='empty folder'):
        Relocation(source, target)


def run(session):
    while session.batch.running:
        session.tick()


def test_the_window_offers_to_move_them_and_keeps_going_from_the_new_place(window, session, dialogs, tmp_path):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    name = take.export_path.name
    old = session.lib.root
    new = tmp_path / 'moved'
    dialogs.settings_answer = {'root': str(new), 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None}
    window.open_settings()
    assert dialogs.confirmations[-1].title == 'Move your recordings?'
    run(session)
    window.flush()
    assert session.lib.root == new and (new / session.slug / name).is_file() and not (old / session.slug / name).exists()
    assert session.current.kept and Path(session.current.export_path).parent == new / session.slug
    assert len(session.current.samples) == 12 * 48000
    assert json.loads(settings.settings_path().read_text(encoding='utf-8'))['root'] == str(new)


def test_saying_no_leaves_them_and_uses_the_new_folder(window, session, dialogs, tmp_path):
    record(session, 12.0)
    old = session.lib.root
    new = tmp_path / 'other'
    dialogs.confirm_answer = False
    dialogs.settings_answer = {'root': str(new), 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None}
    window.open_settings()
    assert not session.batch.running and session.lib.root == new and any(old.rglob('*.raw.wav'))


def test_a_move_that_fails_stays_on_the_old_folder_and_says_so(window, session, dialogs, tmp_path, monkeypatch):
    record(session, 12.0)
    old = session.lib.root

    def fail(self, path):
        raise OSError('the disk is full')

    monkeypatch.setattr(Relocation, 'copy', fail)
    dialogs.settings_answer = {'root': str(tmp_path / 'moved'), 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None,
                               'asr_model': None}
    window.open_settings()
    run(session)
    window.flush()
    assert session.lib.root == old and 'the disk is full' in session.bus.notices[-1].text
    assert 'not moved' in session.status_text and not (tmp_path / 'moved').exists()


def test_nothing_is_asked_when_there_is_nothing_to_move(window, session, dialogs, tmp_path):
    (session.lib.root / 'speakers.json').write_text('{}', encoding='utf-8')
    dialogs.settings_answer = {'root': str(tmp_path / 'other'), 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None,
                               'asr_model': None}
    window.open_settings()
    assert dialogs.confirmations == []
