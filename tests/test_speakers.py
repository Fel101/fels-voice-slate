"""Consent records for each speaker, a real Delete speaker, dataset export, and noise reduction set from the clip."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, bursts, fill, record
from recorder import clean, dataset, manifest
from recorder.core import analysis
from recorder.store import KeepBlocked, TrashUnavailable

TODAY = dt.date(2026, 11, 1)


@pytest.fixture
def signed(tmp_path):
    path = tmp_path / 'Signed Release.pdf'
    path.write_bytes(b'%PDF a signed release')
    return path


def keep_one(session, seconds=12.0):
    take = record(session, seconds)
    fill(session)
    session.keep()
    return take


# ---- the release -----------------------------------------------------------------------------------------------------------

def test_a_speaker_starts_with_no_release(session):
    assert session.lib.release(session.slug) is None and session.lib.release_ends(session.slug) == ('none', '')


def test_the_file_is_copied_into_the_speakers_folder(session, signed):
    session.lib.set_release(session.slug, signed, '2026-09-02', '2027-09-02', 'Covers the game narrator.')
    record_ = session.lib.release(session.slug)
    assert record_['signed'] == '2026-09-02' and record_['expires'] == '2027-09-02' and record_['notes'].startswith('Covers')
    path = session.lib.release_path(session.slug)
    assert path.parent == session.lib.folder(session.slug) / 'release' and path.read_bytes() == signed.read_bytes()
    assert path.name == 'signed-release.pdf' and signed.exists()


def test_the_record_survives_a_damaged_list_of_speakers(session, signed):
    session.lib.set_release(session.slug, signed, '2026-09-02')
    (session.lib.root / 'speakers.json').write_text('{ not json', encoding='utf-8')
    (session.lib.folder(session.slug) / 'masters').mkdir(exist_ok=True)
    assert session.lib.speakers()
    assert session.lib.release(session.slug)['signed'] == '2026-09-02'


def test_a_new_file_does_not_replace_the_old_one(session, signed):
    session.lib.set_release(session.slug, signed)
    first = session.lib.release_path(session.slug)
    session.lib.set_release(session.slug, signed)
    assert first.exists() and session.lib.release_path(session.slug) != first


def test_saving_dates_alone_leaves_the_file_on_record(session, signed):
    session.lib.set_release(session.slug, signed)
    session.lib.set_release(session.slug, None, '2026-09-02', '2027-01-01')
    assert session.lib.release_path(session.slug).name == 'signed-release.pdf'
    assert session.lib.release(session.slug)['expires'] == '2027-01-01'


def test_a_record_can_be_dates_with_no_file(session):
    session.lib.set_release(session.slug, None, '2026-09-02')
    assert session.lib.release(session.slug)['file'] == '' and session.lib.release_path(session.slug) is None


@pytest.mark.parametrize('signed_on, expires', [('next week', ''), ('', '2026-13-01'), ('2027-01-01', '2026-01-01')])
def test_dates_that_cannot_be_are_refused(session, signed_on, expires):
    with pytest.raises(ValueError):
        session.lib.set_release(session.slug, None, signed_on, expires)


def test_a_speaker_who_is_not_there_is_refused(session):
    with pytest.raises(ValueError):
        session.lib.set_release('nobody')


def test_a_record_that_cannot_be_read_is_none(session):
    path = session.lib.folder(session.slug) / 'release' / 'release.json'
    path.parent.mkdir(parents=True)
    path.write_text('not json', encoding='utf-8')
    assert session.lib.release(session.slug) is None
    path.write_text('{"schema": "voice-slate-release-v1", "expires": "soon"}', encoding='utf-8')
    assert session.lib.release(session.slug) is None


@pytest.mark.parametrize('expires, state', [('', 'none'), ('2027-06-01', 'ok'), ('2026-12-01', 'soon'), ('2026-11-01', 'soon'),
                                            ('2026-10-31', 'over')])
def test_how_close_a_release_is_to_running_out(session, expires, state):
    session.lib.set_release(session.slug, None, '', expires)
    assert session.lib.release_ends(session.slug, TODAY)[0] == state


def out_of_date(session):
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    session.lib.set_release(session.slug, None, '2020-01-01', yesterday)
    return yesterday


def test_no_clip_can_be_kept_once_the_release_has_run_out(session):
    day = out_of_date(session)
    record(session, 12.0)
    fill(session)
    with pytest.raises(KeepBlocked) as caught:
        session.keep()
    assert any(f'ran out on {day}' in problem for problem in caught.value.problems)


def test_the_checklist_says_so_too(session):
    out_of_date(session)
    record(session, 12.0)
    fill(session)
    session.measure.derived(session.current)
    session.jobs.drain()
    readiness = session.workspace.readiness(session.measure.derived(session.current))
    assert not readiness.ready and any(step.key == 'release' for step in readiness.steps)


def test_a_release_about_to_run_out_is_only_mentioned(session):
    soon = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    session.lib.set_release(session.slug, None, '', soon)
    record(session, 12.0)
    fill(session)
    readiness = session.workspace.readiness(None)
    assert any(item.key == 'release' for item in readiness.advice)
    assert session.keep() is not None


def test_a_renewed_release_lets_clips_be_kept_again(session):
    out_of_date(session)
    session.lib.set_release(session.slug, None, '2026-01-01', '2099-01-01')
    record(session, 12.0)
    fill(session)
    assert session.keep() is not None


def test_a_clip_records_the_release_it_rests_on(session, signed):
    session.lib.set_release(session.slug, signed, '2026-09-02', '2099-01-01')
    take = keep_one(session)
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))['release']
    assert data['file'] == 'release/signed-release.pdf' and data['signed'] == '2026-09-02' and data['expires'] == '2099-01-01'
    assert len(data['sha256']) == 64


def test_a_clip_with_no_release_has_no_field_for_one(session):
    take = keep_one(session)
    assert 'release' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


def test_the_release_file_is_not_taken_for_a_clip(session, signed):
    session.lib.set_release(session.slug, signed)
    assert session.lib.load_kept(session.slug) == [] and session.lib.skipped == []


def test_a_manifest_leaves_out_clips_whose_release_has_run_out(session):
    keep_one(session)
    day = out_of_date(session)
    rows, left_out = manifest.collect(session.lib)
    assert rows == [] and day in left_out[0][1]


def test_a_manifest_says_when_the_release_runs_out(session):
    session.lib.set_release(session.slug, None, '2026-09-02', '2099-01-01')
    keep_one(session)
    rows, _ = manifest.collect(session.lib)
    assert rows[0]['release_signed'] == '2026-09-02' and rows[0]['release_expires'] == '2099-01-01'
    assert rows[0]['purpose'] == 'reference'


# ---- deleting a speaker ----------------------------------------------------------------------------------------------------

def test_deleting_a_speaker_sends_their_folder_to_the_bin(session, recycle):
    keep_one(session)
    folder = session.lib.folder(session.slug)
    session.workspace.delete_speaker(session.slug)
    assert not folder.exists() and recycle.sent[-1] == [folder.name]
    assert session.slug not in session.lib.speakers() and session.workspace.speakers == []
    assert session.current is None and session.workspace.takes == []


def test_the_next_speaker_comes_forward(session):
    other = session.workspace.add_speaker('Other')
    keep_one(session)
    session.workspace.select_speaker(session.slug)
    session.workspace.delete_speaker(session.slug)
    assert session.workspace.speaker == other


def test_deleting_another_speaker_leaves_the_one_in_front(session):
    first = session.slug
    second = session.workspace.add_speaker('Other')
    session.workspace.select_speaker(first)
    take = keep_one(session)
    session.workspace.delete_speaker(second)
    assert session.workspace.speaker == first and session.current is take


def test_they_do_not_come_back_after_a_restart(session, make_session):
    keep_one(session)
    session.workspace.delete_speaker(session.slug)
    assert make_session().workspace.speakers == []


def test_a_bin_that_cannot_take_them_removes_nothing(session, monkeypatch):
    take = keep_one(session)

    def refuse(paths):
        raise TrashUnavailable('there is no Recycle Bin on this drive')

    monkeypatch.setattr(session.lib, 'trash', refuse)
    with pytest.raises(TrashUnavailable):
        session.workspace.delete_speaker(session.slug)
    assert session.slug in session.lib.speakers() and take.export_path.exists() and session.current is take


def test_permanent_deletion_needs_no_bin(session, monkeypatch):
    keep_one(session)
    folder = session.lib.folder(session.slug)
    monkeypatch.setattr(session.lib, 'trash', lambda paths: pytest.fail('the bin was used'))
    session.workspace.delete_speaker(session.slug, permanent=True)
    assert not folder.exists()


def test_a_take_only_in_memory_stops_it(session, monkeypatch):
    monkeypatch.setattr(session.lib, 'save_master', lambda slug, take: (_ for _ in ()).throw(OSError('disk full')))
    record(session, 12.0)
    with pytest.raises(ValueError, match='only in memory'):
        session.workspace.delete_speaker(session.slug)


def test_not_while_recording(session, open_fake):
    session.capturing.start()
    with pytest.raises(ValueError, match='Stop the recording'):
        session.workspace.delete_speaker(session.slug)


def test_saying_what_a_speaker_holds(session):
    keep_one(session)
    record(session, 12.0)
    assert session.lib.speaker_contents(session.slug) == (1, 1)


# ---- datasets ----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def library_of_clips(session):
    """Two kept clips, one with a cut, and a take that is not kept."""
    first = record(session, 12.0)
    fill(session, transcript='First words | with a bar.')
    session.keep()
    second = record(session, 10.0)
    fill(session, transcript='Second words.')
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    session.workspace.confirm_transcript()
    session.keep()
    record(session, 8.0)
    return first, second


def export(session, folder, layout='clips', rate=24000, originals=False):
    rows, left_out = manifest.collect(session.lib)
    made = dataset.Export(session.lib, folder, layout, rate, originals)
    for row in rows:
        made.add(row)
    assert made.finish() == len(rows)
    return left_out


def test_the_clips_layout_holds_each_clip_with_its_sidecar(session, library_of_clips, tmp_path):
    first, _ = library_of_clips
    folder = tmp_path / 'set'
    export(session, folder)
    wav = folder / session.slug / first.export_path.name
    assert wav.is_file() and wav.with_suffix('.json').is_file() and (folder / 'manifest.jsonl').is_file()
    data = json.loads(wav.with_suffix('.json').read_text(encoding='utf-8'))
    assert data['consent_confirmed'] is True and data['origin'] == 'recorded in my room'


def test_each_clip_in_the_dataset_is_vouched_for_by_its_own_hash(session, library_of_clips, tmp_path):
    from recorder.store import file_sha256
    folder = tmp_path / 'set'
    export(session, folder, rate=16000)
    rows = [json.loads(line) for line in (folder / 'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 2
    for row in rows:
        wav = folder / row['audio_filepath']
        assert file_sha256(wav) == row['sha256'] and sf.info(str(wav)).samplerate == 16000 and row['sample_rate'] == 16000
        assert json.loads(wav.with_suffix('.json').read_text(encoding='utf-8'))['export']['sha256'] == row['sha256']


def test_the_ljspeech_layout(session, library_of_clips, tmp_path):
    first, second = library_of_clips
    folder = tmp_path / 'set'
    export(session, folder, 'ljspeech', 22050)
    lines = (folder / 'metadata.csv').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 2
    name, text, normal = next(line.split('|') for line in lines if line.count('|') == 2 and 'First' in line)
    assert text == normal and text == 'First words with a bar.'
    wav = folder / 'wavs' / f'{name}.wav'
    info = sf.info(str(wav))
    assert info.samplerate == 22050 and info.channels == 1 and info.subtype == 'PCM_16'


def test_the_permission_travels_with_the_ljspeech_layout_too(session, library_of_clips, tmp_path):
    folder = tmp_path / 'set'
    export(session, folder, 'ljspeech', 22050)
    row = json.loads((folder / 'manifest.jsonl').read_text(encoding='utf-8').splitlines()[0])
    assert row['allowed_use'] == 'my own voice references' and row['origin'] == 'recorded in my room'


def test_a_take_that_is_not_kept_is_never_in_a_dataset(session, library_of_clips, tmp_path):
    folder = tmp_path / 'set'
    export(session, folder)
    assert len(list(folder.rglob('*.wav'))) == 2


def test_a_clip_made_from_its_original_keeps_what_the_kept_clip_lost(session, tmp_path):
    """A 15 kHz tone: gone from a 24 kHz clip, there in the original."""
    t = np.arange(int(8 * RATE)) / RATE
    x = bursts(8.0, [(0.2, 7.8)], amp=0.14, f0=137.0) + (0.02 * np.sin(2 * np.pi * 15000 * t)).astype(np.float32)
    record(session, samples=x)
    fill(session)
    session.keep()

    def at_15k(folder):
        wav = next(Path(folder).rglob('*.wav'))
        audio, rate = sf.read(str(wav), dtype='float32')
        spectrum = np.abs(np.fft.rfft(audio))
        return spectrum[np.argmin(np.abs(np.fft.rfftfreq(len(audio), 1 / rate) - 15000))] / np.median(spectrum)

    export(session, tmp_path / 'kept', rate=48000)
    export(session, tmp_path / 'original', rate=48000, originals=True)
    assert at_15k(tmp_path / 'original') > 100 > at_15k(tmp_path / 'kept')


def test_a_clip_made_from_its_original_goes_through_its_edits_again(session, library_of_clips, tmp_path):
    _, second = library_of_clips
    folder = tmp_path / 'set'
    export(session, folder, rate=24000, originals=True)
    wav = folder / session.slug / second.export_path.name
    assert sf.info(str(wav)).duration == pytest.approx(9.0, abs=0.05)
    kept, _ = sf.read(str(second.export_path), dtype='float32')
    again, _ = sf.read(str(wav), dtype='float32')
    assert np.corrcoef(kept[:len(again)], again[:len(kept)])[0, 1] > 0.999


def test_a_clip_whose_wav_has_changed_is_left_out(session, library_of_clips, tmp_path):
    first, _ = library_of_clips
    first.export_path.write_bytes(first.export_path.read_bytes() + b'x')
    left_out = export(session, tmp_path / 'set')
    assert len(left_out) == 1 and 'changed' in left_out[0][1]


@pytest.mark.parametrize('layout, rate', [('other', 24000), ('clips', 12345)])
def test_what_is_not_offered_is_refused(session, tmp_path, layout, rate):
    with pytest.raises(ValueError):
        dataset.Export(session.lib, tmp_path / 'set', layout, rate, False)


def test_a_folder_inside_the_library_is_refused(session):
    with pytest.raises(ValueError, match='outside the output folder'):
        dataset.Export(session.lib, session.lib.root / 'set', 'clips', 24000, False)


def test_a_folder_with_something_in_it_is_refused(session, tmp_path):
    folder = tmp_path / 'set'
    folder.mkdir()
    (folder / 'mine.txt').write_text('keep me', encoding='utf-8')
    with pytest.raises(ValueError, match='empty folder'):
        dataset.Export(session.lib, folder, 'clips', 24000, False)


def test_a_dataset_runs_as_a_job_that_can_be_stopped(session, library_of_clips, tmp_path):
    rows, _ = manifest.collect(session.lib)
    made = dataset.Export(session.lib, tmp_path / 'set', 'clips', 24000, False)
    session.batch.export_dataset(made, rows)
    while session.batch.running:
        session.tick()
    assert 'Wrote 2 of 2 clips' in session.status_text


def test_a_clip_that_fails_is_left_out_and_named(session, library_of_clips, tmp_path, monkeypatch):
    first, _ = library_of_clips
    rows, _ = manifest.collect(session.lib)
    made = dataset.Export(session.lib, tmp_path / 'set', 'clips', 24000, True)
    first.master_path.unlink()
    session.batch.export_dataset(made, rows)
    while session.batch.running:
        session.tick()
    assert 'Wrote 1 of 2 clips' in session.status_text
    assert 'original recording is missing' in session.bus.notices[-1].text


# ---- noise reduction set from the clip ---------------------------------------------------------------------------------------------

def test_the_setting_is_about_5_db_above_the_noise_of_this_clip(session):
    take = record(session, samples=bursts(12.0, [(1.0, 5.0), (6.0, 11.0)], amp=0.14, noise=0.003, f0=137.0))
    floor = analysis.noise_floor_db(take.samples, take.rate)
    nf = session.editor.fit_denoise()
    assert nf == pytest.approx(floor + 5, abs=1) and take.history.chain['denoise'] == {'enabled': True, 'nf': nf}
    assert session.editor.undo() and take.history.chain['denoise']['enabled'] is False


def test_a_quieter_room_gets_a_lower_setting(session):
    quiet = record(session, samples=bursts(12.0, [(1.0, 11.0)], amp=0.14, noise=0.0005, f0=137.0))
    noisy = record(session, samples=bursts(12.0, [(1.0, 11.0)], amp=0.14, noise=0.01, f0=137.0))
    session.workspace.open_take(quiet)
    low = session.editor.fit_denoise()
    session.workspace.open_take(noisy)
    assert low < session.editor.fit_denoise()


def test_it_measures_the_clip_without_the_noise_reduction_that_is_on(session, monkeypatch):
    take = record(session, samples=bursts(12.0, [(1.0, 11.0)], amp=0.14, noise=0.003, f0=137.0))
    monkeypatch.setattr(clean, 'denoise', lambda mono, rate, nf: pytest.fail('it ran noise reduction to measure the noise'))
    session.editor.set_chain('denoise', 'on', enabled=True)
    assert session.editor.fit_denoise() is not None and take.history.chain['denoise']['enabled']


def test_no_take_no_setting(make_session):
    assert make_session().editor.fit_denoise() is None
