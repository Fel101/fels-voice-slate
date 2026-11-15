"""Backing up a whole output folder, and bringing a backup into an empty one: each file is read back and checked, a long
recording is copied a block at a time, and a job that is stopped or fails removes what it copied and changes nothing else.

The failures are real ones: a byte changed in a backup, a drive letter with nothing on it, a file the program removes while
the job runs."""
import itertools
import json

import pytest

from helpers import fill, finish_batch, record
from recorder import manifest, store
from recorder.store import BACKUP_FILE, Backup, BringIn, file_sha256
from test_folder_out_of_reach import free_drive


def keep(session, transcript='The harbour lights came on at dusk.', seconds=8.0):
    take = record(session, seconds)
    fill(session, transcript=transcript)
    session.keep()
    return take


@pytest.fixture
def library(session):
    """Two speakers with kept clips, and a take that is not kept: its original is on disk and nothing else."""
    keep(session, 'First line.')
    keep(session, 'Second line.')
    record(session, 6.0)
    session.workspace.add_speaker('Ben')
    keep(session, 'Ben reads a line.')
    return session


def one_step_a_tick(session):
    """A clock that moves on with each look at it, as the real one does, so that a tick of the job does one step."""
    session.batch.clock = itertools.count(0, 0.025).__next__


def snapshot(folder):
    """Every file in the folder, by its name relative to it, with its bytes."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob('*')) if path.is_file()}


def back_up(session, target):
    backup = Backup(session.lib.root, target)
    session.batch.back_up(backup)
    finish_batch(session)
    return backup


# ---- backing up ------------------------------------------------------------------------------------------------------------

def test_a_backup_holds_every_file_each_one_checked_and_the_output_folder_is_not_changed(library, tmp_path):
    root = library.lib.root
    before = snapshot(root)
    back_up(library, tmp_path / 'backup')
    after = snapshot(tmp_path / 'backup')
    listed = json.loads(after.pop(BACKUP_FILE).decode('utf-8'))
    assert after == before and snapshot(root) == before
    assert listed['schema'] == 'voice-slate-backup-v1' and listed['made'] and listed['software']['name'] == 'voice-slate'
    assert listed['files'] == {name: file_sha256(tmp_path / 'backup' / name) for name in before}
    assert any(name.endswith('.raw.wav') for name in before) and 'speakers.json' in before
    assert library.status_text.startswith(f'Backed up {len(before)} files to {tmp_path / "backup"}. Each one was read back')


def test_a_long_recording_is_copied_a_block_at_a_time(library, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'COPY_BLOCK', 64 * 1024)
    backup = Backup(library.lib.root, tmp_path / 'backup')
    master = next(path for path in backup.files if path.name.endswith('.raw.wav'))
    parts = []
    while not backup.step(master):
        parts.append(backup.part)
    assert len(parts) > 10 and parts == sorted(parts) and parts[0] < 0.1 and parts[-1] > 0.9
    assert (tmp_path / 'backup' / backup.name_of(master)).read_bytes() == master.read_bytes()


def test_the_job_says_how_far_a_long_file_has_got(library, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'COPY_BLOCK', 16 * 1024)
    one_step_a_tick(library)
    library.batch.back_up(Backup(library.lib.root, tmp_path / 'backup'))
    seen = set()
    while library.batch.running:
        library.batch.tick()
        seen.add(library.batch.progress_text)
    assert any(text.startswith('Backing up your recordings: ') and text.endswith('%') for text in seen)


def test_stopping_a_backup_removes_what_it_had_copied(library, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'COPY_BLOCK', 16 * 1024)
    before = snapshot(library.lib.root)
    one_step_a_tick(library)
    library.batch.back_up(Backup(library.lib.root, tmp_path / 'backup'))
    for _ in range(3):
        library.batch.tick()
    assert library.batch.running and (tmp_path / 'backup').exists()
    library.batch.cancel()
    assert not (tmp_path / 'backup').exists() and snapshot(library.lib.root) == before
    assert 'stopped, and the files it had copied were removed' in library.status_text


def test_stopping_leaves_a_folder_the_person_made_and_nothing_in_it(library, tmp_path):
    target = tmp_path / 'backup'
    target.mkdir()
    one_step_a_tick(library)
    library.batch.back_up(Backup(library.lib.root, target))
    library.batch.tick()
    assert library.batch.running and list(target.iterdir())
    library.batch.cancel()
    assert target.is_dir() and list(target.iterdir()) == []


def test_a_backup_onto_a_drive_that_is_not_there_fails_and_says_so(library):
    library.batch.back_up(Backup(library.lib.root, free_drive() / 'backup'))
    finish_batch(library)
    assert 'could not be made' in library.status_text
    assert library.bus.notices[-1].title == 'Backing up your recordings'


def test_a_file_the_program_removes_while_it_runs_is_passed_over(library, tmp_path):
    backup = Backup(library.lib.root, tmp_path / 'backup')
    removed = next(path for path in backup.files if path.name.endswith('.raw.wav'))
    library.batch.back_up(backup)
    removed.unlink()                                  # as the program does with a take that is discarded meanwhile
    finish_batch(library)
    listed = json.loads((tmp_path / 'backup' / BACKUP_FILE).read_text(encoding='utf-8'))['files']
    assert backup.name_of(removed) not in listed and len(listed) == len(backup.files) - 1
    assert library.status_text.endswith('1 file was removed by the program while it ran, and is not in it.')


def test_a_folder_with_something_in_it_is_refused(library, tmp_path):
    (tmp_path / 'backup').mkdir()
    (tmp_path / 'backup' / 'mine.txt').write_text('keep me', encoding='utf-8')
    with pytest.raises(ValueError, match='empty folder'):
        Backup(library.lib.root, tmp_path / 'backup')
    assert (tmp_path / 'backup' / 'mine.txt').read_text(encoding='utf-8') == 'keep me'


@pytest.mark.parametrize('where', ['inside', 'itself'])
def test_a_backup_inside_the_output_folder_is_refused(library, where):
    target = library.lib.root / 'backup' if where == 'inside' else library.lib.root
    with pytest.raises(ValueError, match='inside each other'):
        Backup(library.lib.root, target)


# ---- bringing one in ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def backup(library, tmp_path):
    back_up(library, tmp_path / 'backup')
    return tmp_path / 'backup'


@pytest.fixture
def empty(make_session, tmp_path):
    """The program on another computer: an output folder with nothing in it yet."""
    return make_session(root=tmp_path / 'new-pc' / 'voice-clips')


def bring_in(session, source):
    bringing = BringIn(source, session.lib.root)
    session.batch.bring_in(bringing, lambda: session.workspace.set_root(session.lib.root, moved=True))
    finish_batch(session)
    return bringing


def test_a_backup_brought_in_is_the_library_it_was_made_from(library, backup, empty):
    before = snapshot(library.lib.root)
    bring_in(empty, backup)
    assert snapshot(empty.lib.root) == before
    assert [name for _, name in empty.workspace.speakers] == ['Ben', 'Smoke Speaker']
    rows, left_out = manifest.collect(empty.lib)
    assert len(rows) == 3 and left_out == []
    empty.workspace.select_speaker('smoke-speaker')
    assert len(empty.workspace.takes) == 3 and sum(take.kept for take in empty.workspace.takes) == 2
    assert empty.status_text.startswith(f'Brought in {len(before)} files from {backup}. Each one was read back')


def test_the_backup_is_not_changed_by_bringing_it_in(backup, empty):
    before = snapshot(backup)
    bring_in(empty, backup)
    assert snapshot(backup) == before


def test_a_file_damaged_since_the_backup_was_made_stops_it_and_nothing_stays(backup, empty):
    wav = next(backup.rglob('*_mic_*.wav'))
    damaged = bytearray(wav.read_bytes())
    damaged[-10] ^= 0xFF
    wav.write_bytes(bytes(damaged))
    before = snapshot(backup)
    bring_in(empty, backup)
    assert snapshot(empty.lib.root) == {} and snapshot(backup) == before
    assert empty.workspace.speakers == []
    assert 'not brought in' in empty.status_text and 'itself was not changed' in empty.status_text
    assert 'has changed or been damaged since' in empty.bus.notices[-1].text and wav.name in empty.bus.notices[-1].text


def test_stopping_a_bring_in_leaves_the_output_folder_empty(backup, empty, monkeypatch):
    monkeypatch.setattr(store, 'COPY_BLOCK', 16 * 1024)
    one_step_a_tick(empty)
    empty.batch.bring_in(BringIn(backup, empty.lib.root), lambda: empty.workspace.set_root(empty.lib.root, moved=True))
    for _ in range(4):
        empty.batch.tick()
    assert empty.batch.running and snapshot(empty.lib.root)
    empty.batch.cancel()
    assert snapshot(empty.lib.root) == {} and empty.workspace.speakers == []


def test_a_copy_made_some_other_way_is_brought_in_and_checked_as_it_is_read(library, empty, tmp_path):
    import shutil
    shutil.copytree(library.lib.root, tmp_path / 'by-hand')
    bring_in(empty, tmp_path / 'by-hand')
    assert snapshot(empty.lib.root) == snapshot(library.lib.root)


def test_an_empty_list_of_speakers_is_replaced(backup, empty):
    empty.lib.root.mkdir(parents=True, exist_ok=True)
    (empty.lib.root / 'speakers.json').write_text('{}', encoding='utf-8')
    bring_in(empty, backup)
    assert json.loads((empty.lib.root / 'speakers.json').read_text(encoding='utf-8')).keys() == {'smoke-speaker', 'ben'}


def test_an_output_folder_with_recordings_is_refused(library, backup):
    before = snapshot(library.lib.root)
    with pytest.raises(ValueError, match='no recordings yet'):
        BringIn(backup, library.lib.root)
    assert snapshot(library.lib.root) == before


def test_an_output_folder_with_other_files_is_refused(backup, empty):
    empty.lib.root.mkdir(parents=True, exist_ok=True)
    (empty.lib.root / 'notes.txt').write_text('mine', encoding='utf-8')
    with pytest.raises(ValueError, match='no recordings yet'):
        BringIn(backup, empty.lib.root)


def test_a_folder_that_is_not_a_backup_is_refused(empty, tmp_path):
    (tmp_path / 'holiday').mkdir()
    (tmp_path / 'holiday' / 'photo.jpg').write_bytes(b'jpeg')
    with pytest.raises(ValueError, match='not a backup of recordings'):
        BringIn(tmp_path / 'holiday', empty.lib.root)


def test_a_backup_missing_a_file_it_was_made_with_is_refused(backup, empty):
    next(backup.rglob('*.raw.wav')).unlink()
    with pytest.raises(ValueError, match='missing 1 of the files it was made with'):
        BringIn(backup, empty.lib.root)


def test_a_backup_whose_list_cannot_be_read_is_refused(backup, empty):
    (backup / BACKUP_FILE).write_text('{"schema": "voice-slate-backup-v1", "files": ', encoding='utf-8')
    with pytest.raises(ValueError, match='cannot be read'):
        BringIn(backup, empty.lib.root)


def test_the_backups_own_list_does_not_come_into_the_output_folder(backup, empty):
    bring_in(empty, backup)
    assert not (empty.lib.root / BACKUP_FILE).exists()


def test_the_job_speaks_the_language_of_the_window(library, tmp_path):
    """Every long job's line in the status bar was English whatever the language: its title is now translated with it."""
    from recorder import i18n
    i18n.use('de')
    try:
        library.batch.back_up(Backup(library.lib.root, tmp_path / 'backup'))
        assert library.batch.progress_text == f'Ihre Aufnahmen sichern: 0 von {len(library.batch.items)}'
        finish_batch(library)
        assert library.status_text.endswith('gesichert. Jede wurde zurückgelesen und geprüft.')
    finally:
        i18n.use('en')
