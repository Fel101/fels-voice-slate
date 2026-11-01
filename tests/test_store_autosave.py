"""The autosave file (the work on a take that is not kept yet, saved beside its original and read back after a restart),
the label of a take, and the new name of a speaker. All of it is about files, so no session or window is involved."""
import json

import pytest
import soundfile as sf

from helpers import RATE, speech
from recorder import store
from recorder.store import Library, Take, TrashUnavailable, clean_label


class Bin:
    """Stands in for the Recycle Bin: remembers what was sent there, and removes the files."""

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, paths):
        if self.fail:
            raise TrashUnavailable(self.fail)
        self.sent.append([p.name for p in paths])
        for path in paths:
            path.unlink()


@pytest.fixture
def recycle():
    return Bin()


@pytest.fixture
def lib(tmp_path, recycle):
    return Library(tmp_path / 'voice-clips', trash=recycle)


@pytest.fixture
def slug(lib):
    return lib.add_speaker('Test Speaker')


KEEPABLE = {'transcript': 'The harbour lights came on at dusk.', 'origin': 'recorded in my room',
            'allowed_use': 'my own voice references', 'consent_confirmed': True}


def saved_take(lib, slug, seconds=12.0, **meta):
    """A take whose original is on disk, as it is the moment a recording stops."""
    take = Take(source='mic', samples=speech(seconds), rate=RATE, device={'name': 'test mic'}, speaker=slug, meta=dict(meta))
    lib.save_master(slug, take)
    return take


def restart(lib, slug):
    """What the next start sees: a library on the same folder, with its takes read from disk."""
    return Library(lib.root, trash=lib.trash).load_takes(slug)


def written(lib, take):
    return json.loads(lib.autosave_file(take).read_text(encoding='utf-8'))


# ---- what comes back ---------------------------------------------------------------------------------------------------

def test_a_take_comes_back_with_its_edits_settings_and_text(lib, slug):
    take = saved_take(lib, slug, transcript='The harbour lights came on at dusk.', origin='my room',
                      allowed_use='my own voice', notes='second try', source_url='https://example.org/a',
                      label='  Welcome   back ')
    take.history.cut(1.0, 2.0)
    take.history.fade('in', 0.0, 0.5)
    take.history.set_chain('highpass', 'High-pass on', enabled=True)
    take.history.set_fade_ms(20)
    take.needs_transcript_review = True
    take.flags = ['interrupted']
    assert lib.autosave(take) is True
    assert lib.autosave_file(take) == take.master_path.parent / f'{take.stem}.autosave.json'

    [back] = restart(lib, slug)
    assert back.id == take.id and back.restored and not back.autosave_problem
    assert back.history.edits == take.history.edits
    assert back.history.chain == take.history.chain and back.history.fade_ms == 20
    assert back.needs_transcript_review is True
    assert back.flags == ['recovered', 'interrupted'] and back.device == {'name': 'test mic'}
    for key in ('transcript', 'origin', 'allowed_use', 'notes', 'source_url'):
        assert back.meta[key] == take.meta[key], key
    assert back.meta['label'] == 'Welcome back'
    assert back.meta['transcript_status'] == 'typed'


def test_a_take_nobody_changed_has_no_autosave_file_and_loads_as_recorded(lib, slug):
    take = saved_take(lib, slug)
    assert not lib.autosave_file(take).exists()
    [back] = restart(lib, slug)
    assert not back.restored and not back.autosave_problem and back.flags == ['recovered']


def test_the_script_line_and_the_draft_text_come_back_too(lib, slug):
    take = saved_take(lib, slug, transcript='A draft from the audio.', transcript_status='asr_draft',
                      draft_text='A draft from the audio.', script_line='Read this line.')
    take.history.silence(1.0, 2.0)
    take.draft_key = '[["silence", 1.0, 2.0]]'
    lib.autosave(take)
    [back] = restart(lib, slug)
    assert back.meta['draft_text'] == 'A draft from the audio.' and back.meta['script_line'] == 'Read this line.'
    assert back.meta['transcript_status'] == 'asr_draft' and back.draft_key == '[["silence", 1.0, 2.0]]'


def test_the_permission_tick_is_never_saved(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    assert 'consent_confirmed' not in written(lib, take)
    data = written(lib, take)
    data['consent_confirmed'] = True                        # even a file that claims it is not believed
    lib.autosave_file(take).write_text(json.dumps(data), encoding='utf-8')
    [back] = restart(lib, slug)
    assert back.meta['consent_confirmed'] is False


def test_a_review_flag_cannot_outlive_the_edits_that_asked_for_it(lib, slug):
    take = saved_take(lib, slug)
    take.history.fade('in', 0.0, 0.5)                       # a fade removes no words
    lib.autosave(take)
    data = written(lib, take)
    data['needs_transcript_review'] = True
    lib.autosave_file(take).write_text(json.dumps(data), encoding='utf-8')
    assert restart(lib, slug)[0].needs_transcript_review is False


def test_text_read_back_is_cut_to_a_sane_length_and_an_unknown_status_becomes_typed(lib, slug):
    take = saved_take(lib, slug)
    take.history.cut(0.5, 1.0)
    lib.autosave(take)
    data = written(lib, take)
    data['transcript'] = 'x' * (store.AUTOSAVE_TEXT_MAX + 5000)
    data['transcript_status'] = 'invented'
    lib.autosave_file(take).write_text(json.dumps(data), encoding='utf-8')
    [back] = restart(lib, slug)
    assert len(back.meta['transcript']) == store.AUTOSAVE_TEXT_MAX and back.meta['transcript_status'] == 'typed'


def test_autosave_files_are_not_takes_and_not_sidecars(lib, slug):
    take = saved_take(lib, slug)
    take.history.cut(0.5, 1.0)
    lib.autosave(take)
    assert [p.name for p in lib.list_unkept(slug)] == [take.master_path.name]
    assert lib.load_kept(slug) == [] and len(restart(lib, slug)) == 1


def test_a_capture_from_an_older_build_in_pending_is_saved_beside_itself(lib, slug):
    pending = lib.folder(slug) / '_pending'
    pending.mkdir(parents=True)
    master = pending / '20261001-101500_mic_0a1b2c3d.raw.wav'
    sf.write(str(master), speech(12.0), RATE, subtype='PCM_24', format='WAV')
    [take] = lib.load_takes(slug)
    assert take.master_path is None
    take.history.cut(1.0, 2.0)
    assert lib.autosave(take) is True
    assert (pending / '20261001-101500_mic_0a1b2c3d.autosave.json').is_file()
    [back] = restart(lib, slug)
    assert back.restored and back.history.edits == take.history.edits
    assert back.device == {'legacy_pending': True, 'file': str(master)}


# ---- what is never written ---------------------------------------------------------------------------------------------

def test_nothing_is_written_while_the_original_is_not_on_disk(lib, slug):
    take = Take(source='mic', samples=speech(12.0), rate=RATE, speaker=slug, unsaved=True)
    take.history.cut(1.0, 2.0)
    assert lib.autosave(take) is False
    assert not list(lib.root.rglob('*.autosave.json'))


def test_a_discarded_takes_autosave_is_not_written_again(lib, slug):
    take = saved_take(lib, slug)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    lib.discard(take)
    assert lib.autosave(take) is False
    assert not list(lib.root.rglob('*.autosave.json'))


def test_discard_sends_the_autosave_file_to_the_recycle_bin_with_the_rest(lib, slug, recycle):
    take = saved_take(lib, slug)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    name = lib.autosave_file(take).name
    lib.discard(take)
    assert any(name in sent for sent in recycle.sent) and not list(lib.root.rglob('*.autosave.json'))


def test_keep_removes_the_autosave_file_and_the_sidecar_holds_the_work(lib, slug):
    take = saved_take(lib, slug, label='Greeting', **KEEPABLE)
    take.history.cut(0.5, 1.5)
    lib.autosave(take)
    path = lib.autosave_file(take)
    assert path.is_file()
    paths = lib.keep(slug, take)
    assert not path.exists()
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['label'] == 'Greeting' and data['edits'] and data['consent_confirmed'] is True
    assert lib.autosave(take) is False and not path.exists()           # nothing has changed since


# ---- a file that cannot be used ----------------------------------------------------------------------------------------

def damaged(mutate):
    """A parametrised way to spoil the autosave file of a take with one cut."""
    return mutate


def spoil_text(text):
    return lambda data: text


def spoil_schema(data):
    data['schema'] = 'something-else'
    return data


def spoil_frames(data):
    data['master']['frames'] += 1
    return data


def spoil_name(data):
    data['master']['file'] = 'another.raw.wav'
    return data


def spoil_edit(data):
    data['edits'][0]['end_seconds'] = 90.0
    return data


def spoil_everything_cut(data):
    data['edits'][0].update(start_seconds=0.0, end_seconds=12.0)
    return data


def spoil_chain(data):
    data['chain']['denoise'] = {'enabled': True, 'nf': 500}
    return data


def spoil_fade(data):
    data['join_fade_ms'] = 5000
    return data


@pytest.mark.parametrize('spoil, reason', [
    (spoil_text('{"schema": '), 'damaged'),
    (spoil_text('[1, 2]'), 'not an autosave file'),
    (spoil_schema, 'not an autosave file'),
    (spoil_frames, 'different recording'),
    (spoil_name, 'different recording'),
    (spoil_edit, 'edit list is invalid'),
    (spoil_everything_cut, 'remove the whole clip'),
    (spoil_chain, 'processing settings'),
    (spoil_fade, 'join fade'),
])
def test_an_autosave_file_that_cannot_be_used_leaves_the_take_as_recorded_and_the_file_alone(lib, slug, spoil, reason):
    take = saved_take(lib, slug, transcript='typed words')
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    path = lib.autosave_file(take)
    data = json.loads(path.read_text(encoding='utf-8'))
    spoiled = spoil(data)
    text = spoiled if isinstance(spoiled, str) else json.dumps(spoiled)
    path.write_text(text, encoding='utf-8')
    fresh = Library(lib.root, trash=lib.trash)
    [back] = fresh.load_takes(slug)
    assert reason in back.autosave_problem and path.name in back.autosave_problem
    assert not back.restored and back.history.edits == [] and back.meta['transcript'] == ''
    assert back.flags == ['recovered'] and fresh.skipped == []
    assert path.read_text(encoding='utf-8') == text                       # the file is left exactly as it was


# ---- labels ------------------------------------------------------------------------------------------------------------

def test_a_label_is_one_clean_line_of_limited_length():
    assert clean_label('  Welcome \t back\n now ') == 'Welcome back now'
    assert clean_label('bell\x07 ring\x00') == 'bell ring'
    assert clean_label(None) == '' and clean_label('   ') == ''
    assert len(clean_label('x' * 200)) == store.LABEL_MAX
    assert clean_label('a' * 79 + ' b') == 'a' * 79                      # cut inside the limit, then trimmed
    assert clean_label('Grüße, 你好') == 'Grüße, 你好'


def test_a_kept_take_without_a_label_has_no_label_field(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    data = json.loads(lib.keep(slug, take)['sidecar'].read_text(encoding='utf-8'))
    assert 'label' not in data


def test_a_label_is_written_to_the_sidecar_and_read_back(lib, slug):
    take = saved_take(lib, slug, label='Greeting two', **KEEPABLE)
    lib.keep(slug, take)
    [back] = lib.load_kept(slug)
    assert back.meta['label'] == 'Greeting two' and back.meta['saved_label'] == 'Greeting two'


def test_a_kept_takes_label_changes_alone_in_the_sidecar(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    sidecar = lib.keep(slug, take)['sidecar']
    before = json.loads(sidecar.read_text(encoding='utf-8'))
    take.meta['label'] = '  Greeting   one '
    assert lib.autosave(take) is True
    after = json.loads(sidecar.read_text(encoding='utf-8'))
    assert after.pop('label') == 'Greeting one' and after == before       # every other field is as Keep wrote it
    assert lib.autosave(take) is False                                      # nothing new to write
    take.meta['label'] = ''
    assert lib.autosave(take) is True and 'label' not in json.loads(sidecar.read_text(encoding='utf-8'))
    assert not lib.autosave_file(take).exists() and not list(sidecar.parent.glob('*.tmp'))


def test_a_label_that_cannot_be_written_into_a_damaged_sidecar_raises(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    sidecar = lib.keep(slug, take)['sidecar']
    sidecar.write_text('{"schema": ', encoding='utf-8')
    take.meta['label'] = 'Greeting'
    with pytest.raises(ValueError):
        lib.autosave(take)
    assert sidecar.read_text(encoding='utf-8') == '{"schema": '


# ---- speakers ----------------------------------------------------------------------------------------------------------

def test_renaming_a_speaker_changes_only_the_name_shown(lib, slug):
    lib.keep(slug, saved_take(lib, slug, **KEEPABLE))
    lib.remember(slug, 'my room', 'voice references')
    files = sorted(p.relative_to(lib.root).as_posix() for p in lib.root.rglob('*') if p.is_file() and p.name != 'speakers.json')
    lib.rename_speaker(slug, '  Ada Lovelace ')
    speaker = lib.speakers()[slug]
    assert speaker == {'name': 'Ada Lovelace', 'origin': 'my room', 'allowed_use': 'voice references'}
    assert sorted(p.relative_to(lib.root).as_posix() for p in lib.root.rglob('*')
                  if p.is_file() and p.name != 'speakers.json') == files           # no folder and no clip moved
    assert lib.add_speaker('Ada Lovelace') == slug and len(lib.speakers()) == 1     # found by the name it has now
    assert len(lib.load_kept(slug)) == 1


@pytest.mark.parametrize('name, slug_name, message', [
    ('   ', None, 'Enter a speaker name'),
    ('Other Voice', 'nobody', 'not in this folder'),
    ('other voice', None, 'already a speaker called Other Voice'),
])
def test_a_speaker_rename_that_cannot_be_done_says_why_and_changes_nothing(lib, slug, name, slug_name, message):
    lib.add_speaker('Other Voice')
    before = (lib.root / 'speakers.json').read_text(encoding='utf-8')
    with pytest.raises(ValueError, match=message):
        lib.rename_speaker(slug_name or slug, name)
    assert (lib.root / 'speakers.json').read_text(encoding='utf-8') == before


def test_renaming_a_speaker_to_the_name_it_has_writes_nothing(lib, slug):
    path = lib.root / 'speakers.json'
    stamp = path.stat().st_mtime_ns
    lib.rename_speaker(slug, 'Test Speaker')
    assert path.stat().st_mtime_ns == stamp


# ---- a kept take changed again -----------------------------------------------------------------------------------------

def test_a_kept_take_has_changed_when_its_edits_or_text_differ_from_its_sidecar(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    assert not take.changed                                               # not kept
    lib.keep(slug, take)
    assert not take.changed
    take.meta['notes'] = 'a note'
    assert take.changed
    take.meta['notes'] = ''
    take.history.set_chain('highpass', 'High-pass on', enabled=True)
    assert take.changed
    take.history.undo()
    assert not take.changed
    assert not restart(lib, slug)[0].changed


def test_a_label_alone_does_not_make_a_kept_take_changed(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    lib.keep(slug, take)
    take.meta['label'] = 'Greeting'
    assert not take.changed


def test_a_kept_take_keeps_its_flags_and_device_from_the_sidecar(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    take.flags = ['interrupted']
    lib.keep(slug, take)
    take.history.cut(1.0, 2.0)
    take.transcript_checked()
    lib.autosave(take)
    [back] = restart(lib, slug)
    assert back.changed and 'recovered' not in back.flags and 'interrupted' in back.flags
    assert back.device == {'name': 'test mic'} and back.needs_transcript_review is False


def test_a_damaged_autosave_file_leaves_a_kept_take_as_it_was_kept(lib, slug):
    take = saved_take(lib, slug, **KEEPABLE)
    lib.keep(slug, take)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    lib.autosave_file(take).write_text('{"schema": ', encoding='utf-8')
    [back] = restart(lib, slug)
    assert not back.changed and 'damaged' in back.autosave_problem


def test_a_kept_take_with_no_label_written_keeps_the_sidecars_label(lib, slug):
    take = saved_take(lib, slug, label='Greeting', **KEEPABLE)
    lib.keep(slug, take)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    data = written(lib, take)
    data['label'] = 'An older name'
    lib.autosave_file(take).write_text(json.dumps(data), encoding='utf-8')
    assert restart(lib, slug)[0].meta['label'] == 'Greeting'
