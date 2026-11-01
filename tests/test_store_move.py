"""Moving a take to another speaker: the files are copied and checked before the old ones are removed, so a whole copy
of the take exists at every moment. All of it is about files, so no session or window is involved."""
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import RATE, speech
from recorder import store
from recorder.store import Library, Take, TrashUnavailable

KEEPABLE = {'transcript': 'The harbour lights came on at dusk.', 'origin': 'recorded in my room',
            'allowed_use': 'my own voice references', 'consent_confirmed': True}


class Bin:
    def __init__(self):
        self.sent = []

    def __call__(self, paths):
        self.sent.append([p.name for p in paths])
        for path in paths:
            path.unlink()


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / 'voice-clips', trash=Bin())


@pytest.fixture
def pair(lib):
    """Two speakers, with Ann's remembered origin and allowed use different from Ben's."""
    ann, ben = lib.add_speaker('Ann'), lib.add_speaker('Ben')
    lib.remember(ann, 'Ann at home', 'Ann only')
    lib.remember(ben, 'Ben at the studio', 'Ben only')
    return ann, ben


def saved_take(lib, slug, seconds=12.0, **meta):
    take = Take(source='mic', samples=speech(seconds), rate=RATE, device={'name': 'test mic'}, speaker=slug,
                meta={**lib.speaker_defaults(slug), **meta})
    lib.save_master(slug, take)
    return take


def kept_take(lib, slug, **meta):
    take = saved_take(lib, slug, **{**KEEPABLE, **meta})
    lib.keep(slug, take)
    return take


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sidecar_of(take):
    return json.loads(Path(take.sidecar_path).read_text(encoding='utf-8'))


def restart(lib, slug):
    return Library(lib.root, trash=lib.trash).load_takes(slug)


# ---- a take that is not kept -------------------------------------------------------------------------------------------

def test_an_unkept_take_moves_with_its_original_and_its_unsaved_work(lib, pair):
    ann, ben = pair
    take = saved_take(lib, ann, transcript='Words typed so far.', label='Draft one')
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    old_master, old_autosave = take.master_path, lib.autosave_file(take)
    original = sha(old_master)
    assert old_autosave.is_file()

    assert lib.move_take(take, ben) == []

    new_master = lib.folder(ben) / 'masters' / old_master.name
    assert take.speaker == ben and take.master_path == new_master and sha(new_master) == original
    assert not old_master.exists() and not old_autosave.exists()
    assert lib.autosave_file(take).is_file() and lib.autosave_file(take).parent == new_master.parent
    assert restart(lib, ann) == []
    [back] = restart(lib, ben)
    assert back.id == take.id and back.speaker == ben and back.restored
    assert back.history.edits == take.history.edits and back.meta['transcript'] == 'Words typed so far.'
    assert back.meta['label'] == 'Draft one'


def test_a_take_nobody_changed_still_gets_a_file_where_it_arrives(lib, pair):
    ann, ben = pair
    take = saved_take(lib, ann)
    assert not lib.autosave_file(take).exists()
    lib.move_take(take, ben)
    assert lib.autosave_file(take).is_file()                  # what the take holds now is saved in its new place
    [back] = restart(lib, ben)
    assert back.id == take.id and not back.autosave_problem


def test_a_take_with_the_old_speakers_origin_and_use_takes_the_new_speakers(lib, pair):
    ann, ben = pair
    take = saved_take(lib, ann)
    assert (take.meta['origin'], take.meta['allowed_use']) == ('Ann at home', 'Ann only')
    lib.move_take(take, ben)
    assert (take.meta['origin'], take.meta['allowed_use']) == ('Ben at the studio', 'Ben only')
    [back] = restart(lib, ben)
    assert (back.meta['origin'], back.meta['allowed_use']) == ('Ben at the studio', 'Ben only')


def test_an_origin_or_use_the_person_wrote_for_this_take_goes_with_it(lib, pair):
    ann, ben = pair
    take = saved_take(lib, ann, origin='Recorded on the ferry')
    lib.move_take(take, ben)
    assert take.meta['origin'] == 'Recorded on the ferry'            # their own words are kept
    assert take.meta['allowed_use'] == 'Ben only'                    # the remembered text moves to Ben's


def test_a_take_from_an_older_build_in_pending_moves_into_masters(lib, pair):
    ann, ben = pair
    take = saved_take(lib, ann)
    pending = lib.folder(ann) / '_pending'
    pending.mkdir()
    legacy = pending / take.master_path.name
    shutil.move(take.master_path, legacy)
    [back] = restart(lib, ann)
    assert back.device.get('legacy_pending') and back.master_path is None
    lib.move_take(back, ben)
    assert not legacy.exists() and back.master_path == lib.folder(ben) / 'masters' / legacy.name
    assert 'legacy_pending' not in back.device and 'file' not in back.device
    assert [t.id for t in restart(lib, ben)] == [take.id]


# ---- a kept clip --------------------------------------------------------------------------------------------------------

def test_a_kept_clip_moves_whole_and_its_record_says_where_it_came_from(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann, label='Welcome back')
    before = sidecar_of(take)
    old_files = [take.export_path, take.sidecar_path, take.master_path]
    export_sha, master_sha = sha(take.export_path), sha(take.master_path)

    assert lib.move_take(take, ben) == []

    assert not any(p.exists() for p in old_files)
    folder = lib.folder(ben)
    assert take.speaker == ben and take.export_path.parent == folder and take.sidecar_path.parent == folder
    assert take.master_path.parent == folder / 'masters'
    assert sha(take.export_path) == export_sha == sidecar_of(take)['export']['sha256']
    assert sha(take.master_path) == master_sha
    after = sidecar_of(take)
    assert after['speaker'] == ben and after['export']['file'] == take.export_path.name
    assert after['master']['file'] == f'masters/{take.master_path.name}'
    assert len(after['moves']) == 1 and after['moves'][0]['from'] == ann and after['moves'][0]['to'] == ben
    assert after['moves'][0]['at']
    for key in ('id', 'created', 'transcript', 'transcript_status', 'origin', 'allowed_use', 'consent_confirmed', 'label',
                'edits', 'chain', 'measurements', 'notes', 'flags', 'software'):
        assert after[key] == before[key], key                    # nothing but the places changed
    assert restart(lib, ann) == []
    [back] = restart(lib, ben)
    assert back.kept and back.id == take.id and back.speaker == ben and back.meta['label'] == 'Welcome back'
    assert back.meta['consent_confirmed'] is True


def test_a_kept_clip_keeps_its_own_origin_and_use_whatever_the_new_speaker_has(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann)
    lib.move_take(take, ben)
    after = sidecar_of(take)
    assert after['origin'] == 'recorded in my room' and after['allowed_use'] == 'my own voice references'
    assert lib.speaker_defaults(ben) == {'origin': 'Ben at the studio', 'allowed_use': 'Ben only'}   # and Ben's are untouched


def test_each_move_adds_to_the_record_so_the_whole_path_can_be_read(lib, pair):
    ann, ben = pair
    cat = lib.add_speaker('Cat')
    take = kept_take(lib, ann)
    lib.move_take(take, ben)
    lib.move_take(take, cat)
    moves = sidecar_of(take)['moves']
    assert [(m['from'], m['to']) for m in moves] == [(ann, ben), (ben, cat)]
    assert [t.id for t in restart(lib, cat)] == [take.id] and restart(lib, ben) == [] and restart(lib, ann) == []


def test_a_clip_moves_back_and_forth_without_losing_anything(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann)
    audio = sha(take.export_path)
    for slug in (ben, ann, ben, ann):
        lib.move_take(take, slug)
    assert sha(take.export_path) == audio and take.speaker == ann
    assert [t.id for t in restart(lib, ann)] == [take.id] and restart(lib, ben) == []


def test_a_name_already_used_by_the_new_speaker_gets_a_suffix_and_both_clips_stay(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann)
    stem = take.export_path.stem
    clash = lib.folder(ben)
    (clash / 'masters').mkdir(parents=True)
    (clash / f'{stem}.wav').write_bytes(b'someone else')                  # a file with the clip's own name
    lib.move_take(take, ben)
    assert take.export_path.name == f'{stem}-2.wav' and take.master_path.name == f'{stem}-2.raw.wav'
    assert (clash / f'{stem}.wav').read_bytes() == b'someone else'        # what was there is untouched
    after = sidecar_of(take)
    assert after['export']['file'] == f'{stem}-2.wav' and after['master']['file'] == f'masters/{stem}-2.raw.wav'
    [back] = restart(lib, ben)
    assert back.kept and back.id == take.id


def test_a_moved_clip_can_be_kept_again_and_the_record_of_the_move_stays(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann)
    lib.move_take(take, ben)
    [back] = restart(lib, ben)
    assert back.moves == take.moves and len(back.moves) == 1                  # read back from the sidecar
    back.meta['notes'] = 'second thoughts'
    lib.keep(ben, back)                                                       # which writes the sidecar afresh
    after = sidecar_of(back)
    assert after['notes'] == 'second thoughts' and after['speaker'] == ben
    assert [(m['from'], m['to']) for m in after['moves']] == [(ann, ben)]
    lib.discard(back)
    assert restart(lib, ben) == [] and lib.trash.sent


def test_a_clip_that_was_never_moved_has_no_moves_in_its_record(lib, pair):
    ann, _ = pair
    assert 'moves' not in sidecar_of(kept_take(lib, ann))


def test_odd_entries_in_the_moves_of_a_sidecar_are_ignored(lib, pair):
    ann, _ = pair
    take = kept_take(lib, ann)
    data = sidecar_of(take)
    data['moves'] = [{'from': 'a', 'to': 'b', 'at': 'noon', 'extra': 1}, {'from': 1, 'to': 'b', 'at': 'x'}, 'text', None]
    take.sidecar_path.write_text(json.dumps(data), encoding='utf-8')
    [back] = restart(lib, ann)
    assert back.moves == [{'from': 'a', 'to': 'b', 'at': 'noon'}]
    take.sidecar_path.write_text(json.dumps({**data, 'moves': 'nonsense'}), encoding='utf-8')
    assert restart(lib, ann)[0].moves == []


# ---- refusals ----------------------------------------------------------------------------------------------------------

def test_a_speaker_that_is_not_in_the_folder_is_refused(lib, pair):
    ann, _ = pair
    take = saved_take(lib, ann)
    with pytest.raises(ValueError, match='not in this folder'):
        lib.move_take(take, 'nobody')
    assert take.speaker == ann and take.master_path.is_file()


def test_moving_to_the_speaker_it_is_already_under_is_refused(lib, pair):
    ann, _ = pair
    take = saved_take(lib, ann)
    with pytest.raises(ValueError, match='already filed'):
        lib.move_take(take, ann)


def test_a_take_whose_original_is_only_in_memory_cannot_be_moved(lib, pair):
    ann, ben = pair
    take = Take(source='mic', samples=speech(12.0), rate=RATE, speaker=ann, unsaved=True)
    with pytest.raises(ValueError, match='not saved on disk'):
        lib.move_take(take, ben)
    gone = saved_take(lib, ann)
    gone.master_path.unlink()
    with pytest.raises(ValueError, match='not saved on disk'):
        lib.move_take(gone, ben)


def test_a_kept_clip_whose_record_is_damaged_or_missing_stays_where_it_is(lib, pair):
    ann, ben = pair
    take = kept_take(lib, ann)
    take.sidecar_path.write_text('not json', encoding='utf-8')
    with pytest.raises(ValueError, match='cannot be read'):
        lib.move_take(take, ben)
    take.sidecar_path.write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='cannot be read'):
        lib.move_take(take, ben)
    take.sidecar_path.unlink()
    with pytest.raises(ValueError, match='sidecar'):
        lib.move_take(take, ben)
    assert take.speaker == ann and take.export_path.is_file() and not lib.folder(ben).exists()


# ---- when something fails -----------------------------------------------------------------------------------------------

def test_a_copy_that_does_not_match_is_refused_and_nothing_is_left_behind(lib, pair, monkeypatch):
    ann, ben = pair
    take = kept_take(lib, ann)
    real = store.file_sha256
    monkeypatch.setattr(store, 'file_sha256', lambda path: 'other' if str(path).endswith('.tmp') else real(path))
    with pytest.raises(OSError, match='copied exactly'):
        lib.move_take(take, ben)
    assert take.speaker == ann and take.export_path.is_file() and take.master_path.is_file()
    assert [p.name for p in lib.folder(ben).rglob('*') if p.is_file()] == []
    assert [t.id for t in restart(lib, ann)] == [take.id]


def test_a_full_disk_while_writing_the_new_record_leaves_the_clip_where_it_was(lib, pair, monkeypatch):
    ann, ben = pair
    take = kept_take(lib, ann)

    def full(path, data):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(Library, '_write_json', staticmethod(full))
    with pytest.raises(OSError, match='No space'):
        lib.move_take(take, ben)
    assert take.speaker == ann and take.sidecar_path.parent == lib.folder(ann)
    assert [p for p in lib.folder(ben).rglob('*') if p.is_file()] == []
    monkeypatch.undo()
    assert [t.id for t in restart(lib, ann)] == [take.id]


def test_a_failure_while_saving_an_unkept_take_in_its_new_place_puts_everything_back(lib, pair, monkeypatch):
    ann, ben = pair
    take = saved_take(lib, ann, transcript='Typed before the move.')
    take.meta['label'] = 'Keep me'
    old_master, before = take.master_path, dict(take.meta)

    def refuse(self, take):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(Library, 'autosave', refuse)
    with pytest.raises(OSError, match='No space'):
        lib.move_take(take, ben)
    assert take.speaker == ann and take.master_path == old_master and old_master.is_file()
    assert take.meta == before                                           # even the origin and use are as they were
    assert [p for p in lib.folder(ben).rglob('*') if p.is_file()] == []


def test_old_files_that_cannot_be_removed_are_reported_and_leave_a_whole_duplicate(lib, pair, monkeypatch):
    ann, ben = pair
    take = kept_take(lib, ann)
    old_sidecar, old_export, old_master = take.sidecar_path, take.export_path, take.master_path
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self == old_sidecar:
            raise PermissionError(13, 'The process cannot access the file')
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', locked)
    left = lib.move_take(take, ben)
    monkeypatch.undo()
    assert old_sidecar in left and old_export in left and old_master in left      # nothing half-removed
    assert old_sidecar.is_file() and old_export.is_file() and old_master.is_file()
    assert take.speaker == ben and take.export_path.is_file()
    assert [t.id for t in restart(lib, ann)] == [take.id] and [t.id for t in restart(lib, ben)] == [take.id]


def test_an_old_file_that_stays_after_the_sidecar_went_is_reported(lib, pair, monkeypatch):
    ann, ben = pair
    take = kept_take(lib, ann)
    old_export = take.export_path
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self == old_export:
            raise PermissionError(13, 'in use')
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', locked)
    left = lib.move_take(take, ben)
    monkeypatch.undo()
    assert left == [old_export] and old_export.is_file()
    assert restart(lib, ann) == []                                     # no clip is listed in the old place any more
    assert [t.id for t in restart(lib, ben)] == [take.id]


class Interrupt:
    """Makes the Nth file operation of a move fail, to show what is left when it stops there."""

    def __init__(self, monkeypatch, fail_at=None):
        self.count, self.fail_at = 0, fail_at
        real_copy, real_replace, real_unlink = shutil.copyfile, store.os.replace, Path.unlink

        def step(name):
            self.count += 1
            if self.count == self.fail_at:
                raise OSError(5, f'interrupted at {name}')

        def copyfile(source, destination, **kw):
            step('copy')
            return real_copy(source, destination, **kw)

        def replace(source, destination):
            step('rename')
            return real_replace(source, destination)

        def unlink(path, missing_ok=False):
            step('remove')
            return real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(store.shutil, 'copyfile', copyfile)
        monkeypatch.setattr(store.os, 'replace', replace)
        monkeypatch.setattr(Path, 'unlink', unlink)


def whole_kept(lib, slug, take_id, audio_sha):
    """True if the speaker has the kept clip complete: it loads, and its WAV is the bytes that were kept."""
    for back in restart(lib, slug):
        if back.id == take_id and back.kept and sha(back.export_path) == audio_sha:
            return True
    return False


def run_move_stopping_at(tmp_path, monkeypatch, name, make, fail_at):
    """Build a library with one take (made by `make`) and move it, the `fail_at`th file operation failing (none if None).
    Returns what is needed to look at the result."""
    lib = Library(tmp_path / name, trash=Bin())
    ann, ben = lib.add_speaker('Ann'), lib.add_speaker('Ben')
    take = make(lib, ann)
    audio = sha(take.export_path if take.kept else take.master_path)
    old_paths = (take.export_path, take.sidecar_path, take.master_path)
    with monkeypatch.context() as patch:
        counter = Interrupt(patch, fail_at)
        try:
            left, moved = lib.move_take(take, ben), True
        except OSError:
            left, moved = [], False
    return SimpleNamespace(lib=lib, ann=ann, ben=ben, take=take, steps=counter.count, moved=moved, left=left,
                           audio=audio, old_paths=old_paths)


def test_whatever_step_fails_a_whole_copy_of_a_kept_clip_exists(tmp_path, monkeypatch):
    """Stop the move at each of its file operations in turn. The clip is whole in the old place or in the new one."""
    clean = run_move_stopping_at(tmp_path, monkeypatch, 'count', kept_take, None)
    assert clean.moved and clean.left == [] and clean.steps >= 8      # two copies, a sidecar, and the removal of old files
    for fail_at in range(1, clean.steps + 1):
        r = run_move_stopping_at(tmp_path, monkeypatch, f'run-{fail_at}', kept_take, fail_at)
        in_old, in_new = (whole_kept(r.lib, slug, r.take.id, r.audio) for slug in (r.ann, r.ben))
        assert in_old or in_new, f'step {fail_at} of {clean.steps} left no whole clip'
        if not r.moved:
            assert in_old and r.take.speaker == r.ann and (r.take.export_path, r.take.sidecar_path,
                                                          r.take.master_path) == r.old_paths
            assert not [p for p in r.lib.folder(r.ben).rglob('*') if p.is_file()], f'step {fail_at} left files behind'
        else:
            assert in_new and r.take.speaker == r.ben, f'step {fail_at}'
            assert all(Path(p).exists() for p in r.left)              # what is reported as left behind is really there
            assert in_old == (r.old_paths[1] in r.left)               # the old clip stays whole only if its sidecar stayed


def test_whatever_step_fails_a_whole_copy_of_an_unkept_take_exists(tmp_path, monkeypatch):
    def make(lib, slug):
        take = saved_take(lib, slug, transcript='Typed words.')
        take.history.cut(1.0, 2.0)
        lib.autosave(take)
        return take

    clean = run_move_stopping_at(tmp_path, monkeypatch, 'count', make, None)
    assert clean.moved and clean.left == [] and clean.steps >= 4
    for fail_at in range(1, clean.steps + 1):
        r = run_move_stopping_at(tmp_path, monkeypatch, f'run-{fail_at}', make, fail_at)
        copies = {slug: [t for t in restart(r.lib, slug) if t.id == r.take.id] for slug in (r.ann, r.ben)}
        assert copies[r.ann] or copies[r.ben], f'step {fail_at} of {clean.steps} left no copy'
        for found in copies.values():
            assert all(sha(back.master_path) == r.audio for back in found)
        if not r.moved:
            [back] = copies[r.ann]
            assert r.take.speaker == r.ann and back.meta['transcript'] == 'Typed words.'
            assert back.history.edits == r.take.history.edits
            assert not [p for p in r.lib.folder(r.ben).rglob('*') if p.is_file()], f'step {fail_at} left files behind'
        else:
            [back] = copies[r.ben]
            assert r.take.speaker == r.ben and back.meta['transcript'] == 'Typed words.' and back.restored


def test_the_trash_is_not_used_by_a_move(lib, pair):
    ann, ben = pair
    kept_take(lib, ann)
    take = kept_take(lib, ann)
    lib.move_take(take, ben)
    assert lib.trash.sent == []                                         # a move is not a discard


def test_trash_unavailable_is_not_raised_by_a_move(lib, pair, monkeypatch):
    ann, ben = pair
    take = kept_take(lib, ann)
    monkeypatch.setattr(lib, 'trash', lambda paths: (_ for _ in ()).throw(TrashUnavailable('no bin')))
    assert lib.move_take(take, ben) == []
