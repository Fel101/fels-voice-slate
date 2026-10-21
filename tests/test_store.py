import json

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, speech
from recorder import clean
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
