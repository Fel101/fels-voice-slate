import json
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, speech
from recorder import clean, store
from recorder.store import KeepBlocked, Library, Take, TrashUnavailable


def complete_take(seconds=12.0, source='mic', **meta):
    base = {'transcript': 'The harbour lights came on at dusk.', 'transcript_status': 'typed',
            'origin': 'recorded in my room', 'allowed_use': 'my own voice references', 'consent_confirmed': True}
    base.update(meta)
    return Take(source=source, samples=speech(seconds), rate=RATE, device={'name': 'test mic'}, meta=base)


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


def test_speaker_slugs_are_unique_and_safe(lib):
    a = lib.add_speaker('Ada')
    assert lib.add_speaker('Ada') == a             # same name, same speaker
    c = lib.add_speaker('Ada!')                    # different name, same slug: gets a suffix
    assert a == 'ada' and c == 'ada-2'
    assert lib.add_speaker('A da') == 'a-da'
    assert lib.add_speaker('a-da') == 'a-da-2'
    assert lib.speakers()[a]['name'] == 'Ada'
    with pytest.raises(ValueError):
        lib.add_speaker('   ')


def test_keep_writes_export_master_and_sidecar_with_the_documented_fields(lib, slug):
    take = complete_take()
    paths = lib.keep(slug, take)
    export, sidecar = paths['export'], paths['sidecar']
    info = sf.info(str(export))
    assert (info.samplerate, info.channels, info.subtype) == (24000, 1, 'PCM_16')
    assert 5.9 < info.frames / info.samplerate < 40
    data = json.loads(sidecar.read_text(encoding='utf-8'))
    assert data['schema'] == 'voice-recorder-clip-v2'
    assert data['software']['name'] == 'voice-slate' and data['software']['version']
    assert data['export']['sha256'] and len(data['export']['sha256']) == 64
    assert data['transcript'] == 'The harbour lights came on at dusk.'
    assert data['origin'] and data['allowed_use'] and data['consent_confirmed'] is True
    assert data['master']['sample_rate'] == RATE and data['master']['bit_depth'] == 24
    assert data['edits'] == [] and 'edited' not in data['flags']
    assert data['join_fade_ms'] == 10 and data['chain']['level'] == {'gain_db': 0.0}
    assert data['needs_transcript_review'] is False


def test_keep_is_blocked_with_reasons_and_writes_nothing(lib, slug):
    take = complete_take(consent_confirmed=False, origin='')
    with pytest.raises(KeepBlocked) as err:
        lib.keep(slug, take)
    assert any('permission' in p for p in err.value.problems)
    assert any('origin' in p for p in err.value.problems)
    assert not list(lib.folder(slug).glob('*.wav'))
    assert not list(lib.folder(slug).glob('*.json'))


def test_short_take_is_blocked_by_the_length_rule(lib, slug):
    take = Take(source='mic', samples=speech(3.0), rate=RATE, meta=complete_take().meta)
    with pytest.raises(KeepBlocked, match='Too short'):
        lib.keep(slug, take)


def test_names_are_never_overwritten(lib, slug):
    first = complete_take()
    second = Take(source='mic', samples=first.samples.copy(), rate=RATE, meta=dict(first.meta), id=first.id,
                  created=first.created)
    p1 = lib.keep(slug, first)['export']
    p2 = lib.keep(slug, second)['export']
    assert p1 != p2
    assert p1.exists() and p2.exists()
    assert p2.name.endswith('-2.wav')


def test_keeping_an_edited_take_again_rewrites_only_its_own_files(lib, slug):
    take = complete_take(seconds=14.0)
    first = lib.keep(slug, take)
    take.history.cut(0.0, 1.0)
    take.meta['transcript_status'] = 'edited'
    take.needs_transcript_review = True
    with pytest.raises(KeepBlocked, match='Confirm'):
        lib.keep(slug, take)
    take.needs_transcript_review = False
    again = lib.keep(slug, take)
    assert again['export'] == first['export']
    data = json.loads(again['sidecar'].read_text(encoding='utf-8'))
    assert data['edits'][0]['region'] == 'start'
    assert 'edited' in data['flags']
    assert data['master_seconds'] == pytest.approx(14.0, abs=1e-3)
    assert data['export']['duration_seconds'] == pytest.approx(13.0, abs=0.01)


def test_edited_export_is_shorter_by_the_cut_and_is_checked_on_the_result(lib, slug):
    take = complete_take(seconds=14.0)
    take.history.cut(4.0, 6.0)
    take.history.cut(12.0, 14.0)
    paths = lib.keep(slug, take)
    info = sf.info(str(paths['export']))
    assert info.frames / info.samplerate == pytest.approx(10.0, abs=0.02)


def test_an_edit_that_leaves_too_little_is_blocked_after_editing(lib, slug):
    take = complete_take(seconds=10.0)
    take.history.cut(0.0, 6.0)  # 4 s remain
    with pytest.raises(KeepBlocked, match='Too short'):
        lib.keep(slug, take)


def test_original_is_written_when_captured_and_comes_back_unkept(lib, slug):
    take = complete_take(seconds=8.0, source='system')
    path = lib.save_master(slug, take)
    assert path.exists() and path.parent.name == 'masters'
    assert lib.list_unkept(slug) == [path]
    back = lib.load_unkept(slug, path)
    assert back.source == 'system' and back.id == take.id
    assert back.flags == ['recovered']
    assert back.seconds == pytest.approx(8.0, abs=1e-3)
    assert not back.kept


def test_keep_reuses_the_original_written_at_stop(lib, slug):
    take = complete_take()
    master = lib.save_master(slug, take)
    paths = lib.keep(slug, take)
    assert paths['master'] == master and master.exists()
    assert lib.list_unkept(slug) == []


def test_import_copies_a_file_from_anywhere_and_leaves_the_original_alone(lib, slug, tmp_path):
    src = tmp_path / 'elsewhere' / 'my take.wav'
    src.parent.mkdir()
    sf.write(str(src), speech(9.0, rate=44100), 44100, subtype='PCM_16')
    take = lib.import_file(slug, src)
    assert take.source == 'import' and take.master_path.parent.name == 'masters'
    assert take.rate == 44100 and take.seconds == pytest.approx(9.0, abs=0.01)
    assert src.exists()
    assert lib.list_unkept(slug) == [take.master_path]


@pytest.mark.parametrize('where, value', [('master', '../../important.wav'), ('export', '../../important.wav'),
                                          ('export', 'ABSOLUTE'), ('master', '')])
def test_a_sidecar_cannot_point_keep_or_discard_at_a_file_outside_its_folder(lib, slug, tmp_path, where, value):
    """A sidecar can arrive with a clip someone sent. Keep rewrites and Discard removes the files it names."""
    take = complete_take()
    lib.save_master(slug, take)
    paths = lib.keep(slug, take)
    outside = tmp_path / 'important.wav'
    sf.write(str(outside), speech(1.0), RATE)               # real audio, so only the folder check can refuse it
    precious = outside.read_bytes()
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data[where]['file'] = str(outside) if value == 'ABSOLUTE' else value
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert lib.load_kept(slug) == []
    assert len(lib.skipped) == 1 and lib.skipped[0][0] == paths['sidecar'].name
    assert outside.read_bytes() == precious


def test_an_imported_clips_sidecar_names_the_file_but_not_where_it_was(lib, slug, tmp_path):
    src = tmp_path / 'private folder' / 'my take.wav'
    src.parent.mkdir()
    sf.write(str(src), speech(9.0, rate=44100), 44100, subtype='PCM_16')
    take = lib.import_file(slug, src)
    take.meta.update(transcript='The harbour lights came on at dusk.', origin='a phone memo',
                     allowed_use='testing', consent_confirmed=True)
    sidecar = json.loads(lib.keep(slug, take)['sidecar'].read_text(encoding='utf-8'))
    assert sidecar['source'] == 'import' and sidecar['device'] == {'name': 'my take.wav'}
    assert 'private folder' not in json.dumps(sidecar)


def test_discard_sends_every_file_for_the_take_to_the_recycle_bin(lib, slug, recycle):
    take = complete_take()
    lib.save_master(slug, take)
    paths = lib.keep(slug, take)
    lib.discard(take)
    assert len(recycle.sent) == 1 and len(recycle.sent[0]) == 3
    for p in paths.values():
        assert not p.exists()
    assert take.master_path is None and take.export_path is None and not take.kept


def test_discard_can_delete_for_good_without_the_bin(lib, slug, recycle):
    take = complete_take()
    paths = lib.keep(slug, take)
    lib.discard(take, permanent=True)
    assert recycle.sent == [] and not any(p.exists() for p in paths.values())


def test_a_recycle_bin_failure_deletes_nothing_and_leaves_the_take_as_it_was(tmp_path):
    broken = Bin(fail='no recycle bin on a network folder')
    lib = Library(tmp_path / 'clips', trash=broken)
    slug = lib.add_speaker('Test Speaker')
    take = complete_take()
    paths = lib.keep(slug, take)
    with pytest.raises(TrashUnavailable, match='network folder'):
        lib.discard(take)
    assert all(p.exists() for p in paths.values()) and take.kept


def test_discarding_a_take_that_was_never_written_is_fine(lib):
    take = complete_take()
    lib.discard(take)
    assert take.master_path is None


def test_speaker_origin_and_allowed_use_are_remembered_but_consent_is_not(lib, slug):
    lib.keep(slug, complete_take(origin='studio, 2026', allowed_use='my audiobooks'))
    stored = lib.speakers()[slug]
    assert stored['origin'] == 'studio, 2026' and stored['allowed_use'] == 'my audiobooks'
    assert 'consent' not in json.dumps(stored)


def test_a_keep_passes_the_common_reference_checks_on_the_exported_file(lib, slug):
    """The checks a speech tool typically applies to a reference clip, run on the exported file."""
    paths = lib.keep(slug, complete_take(seconds=15.0))
    x, rate = sf.read(str(paths['export']), dtype='float32')
    assert rate == 24000 and x.ndim == 1
    seconds = len(x) / rate
    rms_dbfs = 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    clipped = float(np.mean(np.abs(x) >= 0.995))
    assert 3 <= seconds <= 40
    assert rms_dbfs > -50                # not silent
    assert clipped <= 0.001              # fewer than 0.1% clipped samples
    assert seconds >= 5.01


def test_takes_compare_by_identity_not_by_their_samples():
    a = Take(source='mic', samples=np.zeros(10, np.float32), rate=10)
    b = Take(source='mic', samples=np.zeros(20, np.float32), rate=10)
    assert a == a and a != b
    assert [b, a].index(a) == 1


def test_a_damaged_speakers_file_is_moved_aside_and_the_speakers_are_rebuilt(tmp_path):
    root = tmp_path / 'clips'
    (root / 'bot' / 'masters').mkdir(parents=True)
    (root / 'speakers.json').write_text('{"bot": ', encoding='utf-8')
    lib = Library(root)
    assert list(lib.speakers()) == ['bot']
    backups = list(root.glob('speakers.json.damaged-*'))
    assert len(backups) == 1 and backups[0].read_text(encoding='utf-8') == '{"bot": '
    assert not (root / 'speakers.json').exists()
    assert 'moved to' in lib.recovery_note
    lib.add_speaker('Alice')
    assert set(json.loads((root / 'speakers.json').read_text(encoding='utf-8'))) == {'bot', 'alice'}


@pytest.mark.parametrize('text', ['[]', '"text"', '{"bot": 5}', '{"bot": {"origin": "x"}}',
                                  '{"../../elsewhere": {"name": "Not a folder here"}}',
                                  '{"C:\\\\elsewhere": {"name": "Absolute"}}', '{"..": {"name": "Parent"}}'])
def test_a_speakers_file_with_the_wrong_shape_counts_as_damaged(tmp_path, text):
    """Valid JSON is not enough: a speaker is a folder in the output folder, and a registry can arrive from elsewhere."""
    root = tmp_path / 'clips'
    (root / 'bot' / 'masters').mkdir(parents=True)
    (root / 'speakers.json').write_text(text, encoding='utf-8')
    lib = Library(root)
    assert list(lib.speakers()) == ['bot']
    assert len(list(root.glob('speakers.json.damaged-*'))) == 1 and 'moved to' in lib.recovery_note


def test_a_missing_speakers_file_is_rebuilt_from_the_speaker_folders(tmp_path):
    root = tmp_path / 'clips'
    (root / 'alice' / 'masters').mkdir(parents=True)
    (root / 'notes').mkdir()                                  # not a speaker: it has no masters folder
    assert list(Library(root).speakers()) == ['alice']


def test_a_sidecar_that_cannot_be_used_is_skipped_with_its_reason_and_left_alone(lib, slug):
    lib.keep(slug, complete_take())
    folder = lib.folder(slug)
    good = next(folder.glob('*.json'))
    data = json.loads(good.read_text(encoding='utf-8'))
    data['id'] = 'badedits'
    data['edits'] = [{'op': 'cut', 'region': 'end', 'start_seconds': 2.0, 'end_seconds': 99.0}]
    bad = folder / 'bad-edits.json'
    bad.write_text(json.dumps(data), encoding='utf-8')
    junk = folder / 'junk.json'
    junk.write_text('{"schema": ', encoding='utf-8')
    before = (bad.read_text(encoding='utf-8'), junk.read_text(encoding='utf-8'))
    takes = lib.load_kept(slug)
    assert len(takes) == 1 and takes[0].id != 'badedits'
    reasons = dict(lib.skipped)
    assert 'its edit list is invalid' in reasons['bad-edits.json']
    assert reasons['junk.json'] == 'it could not be read'
    assert (bad.read_text(encoding='utf-8'), junk.read_text(encoding='utf-8')) == before


def test_a_sidecar_with_unusable_processing_settings_is_skipped(lib, slug):
    paths = lib.keep(slug, complete_take())
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data['chain']['level']['gain_db'] = 500
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert lib.load_kept(slug) == []
    assert 'its processing settings are invalid' in lib.skipped[0][1]


def test_a_sidecar_whose_master_is_missing_is_reported(lib, slug):
    lib.keep(slug, complete_take())
    folder = lib.folder(slug)
    sidecar = next(folder.glob('*.json'))
    data = json.loads(sidecar.read_text(encoding='utf-8'))
    (folder / data['master']['file']).unlink()
    assert lib.load_kept(slug) == []
    assert lib.skipped == [(sidecar.name, 'its master file is missing')]


def test_an_empty_unkept_master_is_reported_and_not_loaded(lib, slug):
    path = lib.folder(slug) / 'masters' / '20261020-120000_mic_deadbeef.raw.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(0, np.float32), RATE)
    before = path.read_bytes()
    assert lib.load_takes(slug) == []
    assert 'no audio' in dict(lib.skipped)[path.name]
    assert path.read_bytes() == before


def test_sidecar_cuts_that_cover_every_sample_are_skipped(lib, slug):
    paths = lib.keep(slug, complete_take())
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data['edits'] = [{'op': 'cut', 'region': 'start', 'start_seconds': 0, 'end_seconds': 6},
                     {'op': 'cut', 'region': 'end', 'start_seconds': 6, 'end_seconds': 12}]
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert lib.load_kept(slug) == []
    assert 'whole clip' in lib.skipped[0][1]


@pytest.mark.parametrize('fade', [0, 50])
def test_the_saved_join_fade_is_restored_and_used_when_the_take_is_kept_again(lib, slug, fade):
    take = complete_take()
    take.history.cut(3, 4)
    take.history.set_fade_ms(fade)
    paths = lib.keep(slug, take)
    loaded = lib.load_kept(slug)[0]
    assert loaded.join_fade_ms == fade
    expected = lib.export_for(loaded)
    lib.keep(slug, loaded)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['join_fade_ms'] == fade
    assert sf.info(str(paths['export'])).frames == len(expected)


def test_old_sidecars_without_join_fade_keep_the_original_default(lib, slug):
    paths = lib.keep(slug, complete_take())
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    del data['join_fade_ms']
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert lib.load_kept(slug)[0].join_fade_ms == 10


def test_the_whole_recipe_survives_keep_and_load(lib, slug):
    take = complete_take(seconds=16.0)
    take.history.cut(0.0, 1.0)
    take.history.silence(5.0, 5.4)
    take.history.fade('out', 14.0, 15.0)
    take.history.gain(-3.0, 8.0, 9.0)
    take.history.set_chain('highpass', 'High-pass', enabled=True, hz=100.0)
    take.history.set_chain('level', 'Level', gain_db=4.5)
    take.history.set_chain('limiter', 'Limiter', enabled=True, ceiling_db=-2.0)
    take.history.set_fade_ms(20)
    take.needs_transcript_review = False
    paths = lib.keep(slug, take)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert {'edited', 'filtered', 'level_changed', 'limited'} <= set(data['flags'])
    back = lib.load_kept(slug)[0]
    assert back.history.edits == take.history.edits
    assert back.history.chain == take.history.chain and back.history.fade_ms == 20.0
    assert np.array_equal(lib.export_for(back), lib.export_for(take))


def test_a_version_1_sidecar_loads_and_is_rewritten_as_version_2_when_kept_again(lib, slug, monkeypatch):
    take = complete_take()
    take.history.cut(2.0, 3.0)
    take.needs_transcript_review = False
    paths = lib.keep(slug, take)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data['schema'] = 'voice-recorder-clip-v1'
    del data['chain'], data['software']
    data['gain_db'] = 6.0
    data['denoise'] = {'filter': 'afftdn=nf=-30', 'nf': -30.0, 'identity_checked': False}
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    old = lib.load_kept(slug)[0]
    assert old.history.chain['level']['gain_db'] == 6.0
    assert old.history.chain['denoise'] == {'enabled': True, 'nf': -30.0}
    assert old.history.edits[0]['op'] == 'cut'
    cleaned = []
    monkeypatch.setattr(clean, 'denoise', lambda mono, rate, nf=-25.0: cleaned.append(nf) or mono)
    lib.keep(slug, old)
    again = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert again['schema'] == 'voice-recorder-clip-v2' and again['chain']['level']['gain_db'] == 6.0
    assert again['chain']['denoise']['identity_checked'] is False and cleaned == [-30.0]


def test_the_masters_stay_untouched_by_every_edit(lib, slug):
    take = complete_take()
    master = lib.save_master(slug, take)
    before = master.read_bytes()
    take.history.cut(1.0, 2.0)
    take.history.silence(4.0, 5.0)
    take.history.set_chain('level', 'Level', gain_db=6.0)
    take.needs_transcript_review = False
    lib.keep(slug, take)
    assert master.read_bytes() == before


# ---- a Keep that cannot write ------------------------------------------------------------------------------------------
# A folder standing where a file has to go makes the real write fail with the real error. A stand-in that raised OSError is
# how a failed Keep came to look like a kept clip: SoundFile raises an error of its own, which nothing was catching.

@pytest.mark.parametrize('blocked', ['{stem}.wav.tmp', '{stem}.json.tmp', '{stem}.wav', '{stem}.json'])
def test_a_keep_that_cannot_write_leaves_the_take_not_kept_and_nothing_half_done(lib, slug, blocked):
    take = complete_take()
    lib.save_master(slug, take)
    obstacle = lib.folder(slug) / blocked.format(stem=take.stem)
    obstacle.mkdir()
    with pytest.raises(OSError):
        lib.keep(slug, take)
    assert not take.kept and take.export_path is None and take.sidecar_path is None
    assert [p.name for p in lib.folder(slug).iterdir() if p.is_file()] == []
    obstacle.rmdir()
    paths = lib.keep(slug, take)                                 # and it goes through once the problem is gone
    assert take.kept and paths['export'].is_file() and paths['sidecar'].is_file()


def test_a_keep_that_fails_after_writing_the_original_counts_the_original_as_saved(lib, slug):
    take = complete_take()
    take.unsaved = True
    (lib.folder(slug) / f'{take.stem}.wav.tmp').mkdir(parents=True)
    with pytest.raises(OSError):
        lib.keep(slug, take)
    assert take.master_path.is_file() and not take.unsaved and not take.kept


def test_a_keep_that_cannot_write_the_original_leaves_the_take_in_memory(lib, slug):
    take = complete_take()
    take.unsaved = True
    lib.folder(slug).mkdir(parents=True)
    (lib.folder(slug) / 'masters').write_text('a file where the folder should be', encoding='utf-8')
    with pytest.raises(OSError):
        lib.keep(slug, take)
    assert take.unsaved and not take.kept and take.master_path is None


def test_an_update_that_cannot_write_leaves_the_kept_clip_and_the_changes_alone(lib, slug):
    take = complete_take(seconds=14.0)
    paths = lib.keep(slug, take)
    before = paths['export'].read_bytes(), paths['sidecar'].read_bytes()
    take.history.cut(0.0, 1.0)
    (lib.folder(slug) / (paths['export'].name + '.tmp')).mkdir()
    with pytest.raises(OSError):
        lib.keep(slug, take)
    assert (paths['export'].read_bytes(), paths['sidecar'].read_bytes()) == before
    assert take.kept and take.changed


def test_a_keep_survives_a_speaker_list_that_cannot_be_written(lib, slug, monkeypatch):
    """The clip is whole by then: only the texts offered for the next take are lost, which is no reason to report a failure."""
    monkeypatch.setattr(lib, 'remember', lambda *a: (_ for _ in ()).throw(OSError('speakers.json is locked')))
    take = complete_take()
    assert lib.keep(slug, take)['export'].is_file() and take.kept


def test_files_reach_the_disk_before_they_are_renamed_into_place(lib, slug, monkeypatch):
    events, sync, replace = [], store.sync_file, os.replace

    def noted_sync(path):
        events.append(('sync', Path(path).name))
        sync(path)

    def noted_replace(source, target):
        events.append(('replace', Path(source).name))
        replace(source, target)

    monkeypatch.setattr(store, 'sync_file', noted_sync)
    monkeypatch.setattr(os, 'replace', noted_replace)
    take = complete_take()
    lib.keep(slug, take)
    take.history.cut(1.0, 2.0)
    lib.autosave(take)
    renamed = [name for kind, name in events if kind == 'replace']
    assert len(renamed) == 5                                     # the original, the WAV, the sidecar, the speakers, the autosave
    assert all(events.index(('sync', name)) < events.index(('replace', name)) for name in renamed)


# ---- what a sidecar records --------------------------------------------------------------------------------------------

def test_flags_describe_the_clip_as_it_is_kept_now_and_not_as_it_was(lib, slug):
    take = complete_take(seconds=14.0)
    take.flags = ['interrupted']
    take.history.cut(0.0, 1.0)
    take.history.set_chain('level', 'Level', gain_db=3.0)
    take.history.set_chain('limiter', 'Limiter', enabled=True)
    paths = lib.keep(slug, take)
    assert json.loads(paths['sidecar'].read_text(encoding='utf-8'))['flags'] == ['edited', 'interrupted', 'level_changed',
                                                                                 'limited']
    back = lib.load_kept(slug)[0]
    back.history.remove(0)
    back.history.set_chain('level', 'Level', gain_db=0.0)
    back.history.set_chain('limiter', 'Limiter', enabled=False)
    lib.keep(slug, back)
    assert json.loads(paths['sidecar'].read_text(encoding='utf-8'))['flags'] == ['interrupted']


def clipped_take():
    return Take(source='mic', samples=np.clip(speech(12.0, amp=3.0), -1.0, 1.0), rate=RATE, meta=complete_take().meta)


def test_gain_and_the_limiter_cannot_hide_clipping_from_the_sidecar(lib, slug):
    take = clipped_take()
    take.history.set_chain('level', 'Level', gain_db=-12.0)
    take.history.set_chain('limiter', 'Limiter', enabled=True)
    data = json.loads(lib.keep(slug, take)['sidecar'].read_text(encoding='utf-8'))
    assert 'clipped' in data['flags'] and data['measurements']['clipped_fraction'] == 0.0
    assert data['measurements']['recording_clipped_fraction'] > 0.2


def test_a_clip_with_the_clipped_part_cut_out_is_not_flagged(lib, slug):
    take = complete_take(seconds=12.0)
    samples = take.samples.copy()
    samples[int(RATE * 5.0):int(RATE * 6.0)] = 1.0
    take = Take(source='mic', samples=samples, rate=RATE, meta=take.meta)
    take.history.cut(4.9, 6.1)
    data = json.loads(lib.keep(slug, take)['sidecar'].read_text(encoding='utf-8'))
    assert 'clipped' not in data['flags'] and data['measurements']['recording_clipped_fraction'] == 0.0


def test_the_time_of_the_permission_is_kept_with_it_and_none_is_made_up(lib, slug):
    take = complete_take(consent_confirmed_at='2026-10-31T10:00:00+00:00')
    paths = lib.keep(slug, take)
    assert json.loads(paths['sidecar'].read_text(encoding='utf-8'))['consent_confirmed_at'] == '2026-10-31T10:00:00+00:00'
    back = lib.load_kept(slug)[0]
    lib.keep(slug, back)
    assert json.loads(paths['sidecar'].read_text(encoding='utf-8'))['consent_confirmed_at'] == '2026-10-31T10:00:00+00:00'
    older = complete_take()                                      # a clip kept before the time was recorded has none
    data = json.loads(lib.keep(slug, older)['sidecar'].read_text(encoding='utf-8'))
    assert 'consent_confirmed_at' not in data


# ---- what cannot come in -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize('field, value, reason', [
    ('created', None, 'incomplete'), ('created', 12345, 'incomplete'), ('created', 'last Tuesday', 'creation time'),
    ('id', None, 'incomplete'), ('source', 5, 'incomplete'),
    ('transcript', 123, 'transcript is not text'), ('origin', ['a'], 'origin is not text'),
    ('allowed_use', {'a': 1}, 'allowed_use is not text'), ('notes', 7, 'notes is not text'),
    ('source_url', 9, 'source_url is not text'),
    ('flags', 'edited', 'flags'), ('flags', [1], 'flags'), ('device', 'a microphone', 'device'),
])
def test_a_sidecar_with_a_value_of_the_wrong_kind_costs_that_clip_and_no_other(lib, slug, field, value, reason):
    good, bad = complete_take(), complete_take()
    lib.keep(slug, good)
    paths = lib.keep(slug, bad)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data[field] = value
    text = json.dumps(data)
    paths['sidecar'].write_text(text, encoding='utf-8')
    assert [take.id for take in lib.load_kept(slug)] == [good.id]
    assert lib.skipped[0][0] == paths['sidecar'].name and reason in lib.skipped[0][1]
    assert paths['sidecar'].read_text(encoding='utf-8') == text


def test_an_unknown_transcript_status_is_read_as_typed(lib, slug):
    paths = lib.keep(slug, complete_take())
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data['transcript_status'] = ['not', 'a', 'status']
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert lib.load_kept(slug)[0].meta['transcript_status'] == 'typed'


def test_remembered_texts_that_are_not_text_are_left_out(lib, slug):
    data = json.loads((lib.root / 'speakers.json').read_text(encoding='utf-8'))
    data[slug].update(origin=5, allowed_use=['a'])
    (lib.root / 'speakers.json').write_text(json.dumps(data), encoding='utf-8')
    assert lib.speaker_defaults(slug) == {'origin': '', 'allowed_use': ''}


def test_a_file_with_no_audio_in_it_is_not_imported(lib, slug, tmp_path):
    path = tmp_path / 'empty.wav'
    sf.write(str(path), np.zeros(0, np.float32), RATE)
    with pytest.raises(ValueError, match='no audio'):
        lib.import_file(slug, path)
    assert lib.list_unkept(slug) == []


@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_a_file_whose_samples_are_not_numbers_is_not_imported(lib, slug, tmp_path, bad):
    path = tmp_path / 'damaged.wav'
    samples = speech(8.0)
    samples[100] = bad
    sf.write(str(path), samples, RATE, subtype='FLOAT')
    with pytest.raises(ValueError, match='damaged'):
        lib.import_file(slug, path)
    assert lib.list_unkept(slug) == []


@pytest.mark.parametrize('name, folder', [('Nul', 'speaker-nul'), ('CON', 'speaker-con'), ('aux', 'speaker-aux'),
                                          ('Com1', 'speaker-com1'), ('LPT9', 'speaker-lpt9'), ('Nula', 'nula')])
def test_a_speaker_called_what_windows_keeps_for_a_device_gets_a_folder_that_works(lib, name, folder):
    slug = lib.add_speaker(name)
    assert slug == folder and lib.save_master(slug, complete_take()).is_file()


# ---- a recording cut short ---------------------------------------------------------------------------------------------

def spooled(lib, slug, seconds=3.0, channels=1, line=None):
    """What is left on disk when the program stops in the middle of a take."""
    spool = lib.open_spool(slug, RATE, channels, 'mic', {'name': 'test mic'}, line)
    block = speech(seconds)
    spool.write([block if channels == 1 else np.stack([block] * channels, axis=1)])
    spool.close()
    return spool.path


def test_a_recording_cut_short_becomes_a_take_that_can_be_kept(lib, slug):
    lib.remember(slug, 'my room', 'my own voice')
    path = spooled(lib, slug, 8.0, line='A line to read aloud.')
    [take] = lib.load_takes(slug)
    assert lib.recovered == 1 and not path.exists() and take.master_path.is_file()
    assert take.seconds == pytest.approx(8.0) and np.allclose(take.samples, speech(8.0), atol=1e-6)
    assert take.flags == ['recovered', 'interrupted'] and take.device == {'name': 'test mic'}
    assert take.meta['transcript'] == 'A line to read aloud.' and take.meta['transcript_status'] == 'read_from_script'
    assert take.meta['origin'] == 'my room' and take.meta['allowed_use'] == 'my own voice'
    take.meta['consent_confirmed'] = True
    data = json.loads(lib.keep(slug, take)['sidecar'].read_text(encoding='utf-8'))
    assert 'interrupted' in data['flags'] and data['master_seconds'] == pytest.approx(8.0)
    assert Library(lib.root, trash=lib.trash).load_takes(slug)[0].id == take.id          # once, and not again


def test_a_spool_cut_off_in_the_middle_of_a_frame_keeps_the_whole_frames(lib, slug):
    path = spooled(lib, slug, 2.0, channels=2)
    with open(path, 'ab') as f:
        f.write(b'\x01\x02\x03')
    [take] = lib.load_takes(slug)
    assert take.samples.shape == (2 * RATE, 2)


def test_a_spool_that_ended_before_any_audio_arrived_leaves_nothing(lib, slug):
    spool = lib.open_spool(slug, RATE, 1, 'mic', {})
    spool.close()
    assert lib.load_takes(slug) == [] and lib.recovered == 0 and not spool.path.exists()


@pytest.mark.parametrize('first_line', [b'not json at all', b'[1, 2]', b'{"schema": "something-else"}',
                                        b'{"schema": "voice-slate-spool-v1"}',
                                        b'{"schema": "voice-slate-spool-v1", "rate": 0, "channels": 1}',
                                        b'{"schema": "voice-slate-spool-v1", "rate": 48000, "channels": 1, "created": "?"}'])
def test_a_spool_that_cannot_be_read_is_reported_and_left_alone(lib, slug, first_line):
    path = lib.folder(slug) / 'masters' / f'20261031-120000_mic_0a1b2c3d{store.SPOOL_SUFFIX}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(first_line + b'\n' + b'\x00' * 400)
    assert lib.load_takes(slug) == [] and path.is_file() and path.read_bytes().startswith(first_line)
    assert 'could not be recovered' in dict(lib.skipped)[path.name]


def test_the_recording_being_made_is_not_taken_for_one_cut_short(lib, slug):
    path = spooled(lib, slug)
    assert lib.load_takes(slug, recover=False) == [] and path.is_file() and lib.recovered == 0


def test_writing_the_original_ends_the_need_for_the_spool(lib, slug):
    path = spooled(lib, slug)
    take = complete_take()
    take.spool = path
    lib.save_master(slug, take)
    assert not path.exists() and take.spool is None


def test_discarding_a_take_takes_its_spool_with_it(lib, slug, recycle):
    path = spooled(lib, slug)
    take = complete_take()
    take.spool = path
    lib.discard(take)
    assert recycle.sent == [[path.name]] and not path.exists()
