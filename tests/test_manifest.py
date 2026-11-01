"""The manifest: one row for each kept clip, as JSON Lines or CSV, for programs that read a whole set of clips."""
import csv
import json
import os
from pathlib import Path

import pytest
import soundfile as sf

from helpers import RATE, speech
from recorder import manifest
from recorder.manifest import COLUMNS
from recorder.store import Library, Take, file_sha256

KEEPABLE = {'transcript': 'The harbour lights came on at dusk.', 'origin': 'recorded in my room',
            'allowed_use': 'my own voice references', 'consent_confirmed': True}


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / 'voice-clips', trash=lambda paths: [p.unlink() for p in paths])


def kept(lib, slug, seconds=12.0, **meta):
    take = Take(source='mic', samples=speech(seconds), rate=RATE, device={'name': 'test mic'}, speaker=slug,
                meta={**KEEPABLE, **meta})
    lib.save_master(slug, take)
    lib.keep(slug, take)
    return take


def edit_sidecar(take, **changes):
    data = json.loads(Path(take.sidecar_path).read_text(encoding='utf-8'))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    Path(take.sidecar_path).write_text(json.dumps(data), encoding='utf-8')
    return data


# ---- what is collected --------------------------------------------------------------------------------------------------

def test_a_row_describes_the_clip_as_it_was_kept(lib):
    ann = lib.add_speaker('Ann Lee')
    take = kept(lib, ann, label='Welcome back', transcript='  Welcome back to the show.  ')
    rows, left_out = manifest.collect(lib, [ann], base=lib.folder(ann))
    assert left_out == [] and len(rows) == 1
    [row] = rows
    assert list(row) == list(COLUMNS)
    assert row['audio_filepath'] == take.export_path.name
    assert row['text'] == 'Welcome back to the show.'
    assert row['duration'] == pytest.approx(12.0, abs=0.01) == round(sf.info(str(take.export_path)).duration, 3)
    assert (row['speaker'], row['speaker_id'], row['label']) == ('Ann Lee', ann, 'Welcome back')
    assert (row['sample_rate'], row['source'], row['id'], row['created']) == (24000, 'mic', take.id, take.created)
    assert row['origin'] == 'recorded in my room' and row['allowed_use'] == 'my own voice references'
    assert row['transcript_status'] == 'typed' and row['sha256'] == file_sha256(take.export_path)


def test_only_kept_clips_are_listed_and_other_json_files_are_ignored(lib):
    ann = lib.add_speaker('Ann')
    kept(lib, ann)
    unkept = Take(source='mic', samples=speech(12.0), rate=RATE, speaker=ann)
    lib.save_master(ann, unkept)
    (lib.folder(ann) / 'notes.json').write_text('{"hello": 1}', encoding='utf-8')
    (lib.folder(ann) / 'list.json').write_text('[1, 2]', encoding='utf-8')
    rows, left_out = manifest.collect(lib)
    assert len(rows) == 1 and left_out == []


def test_all_speakers_are_listed_by_name_then_by_when_the_clips_were_made(lib):
    zed, amy = lib.add_speaker('Zed'), lib.add_speaker('amy')
    first, second = kept(lib, zed), kept(lib, zed)
    third = kept(lib, amy)
    first_row = {'created': '2026-10-20T10:00:00+01:00'}
    edit_sidecar(first, **first_row)
    edit_sidecar(second, created='2026-10-21T10:00:00+01:00')
    rows, _ = manifest.collect(lib)
    assert [r['id'] for r in rows] == [third.id, first.id, second.id]                  # amy, then Zed oldest first
    rows, _ = manifest.collect(lib, [zed])
    assert {r['speaker'] for r in rows} == {'Zed'}
    assert manifest.collect(lib, ['nobody']) == ([], [])


def test_a_renamed_speaker_shows_the_new_name_and_the_same_id(lib):
    ann = lib.add_speaker('Ann')
    kept(lib, ann)
    lib.rename_speaker(ann, 'Ann Lee')
    [row], _ = manifest.collect(lib)
    assert (row['speaker'], row['speaker_id']) == ('Ann Lee', ann)


def test_a_clip_moved_to_another_speaker_is_listed_under_the_new_one(lib):
    ann, ben = lib.add_speaker('Ann'), lib.add_speaker('Ben')
    take = kept(lib, ann)
    lib.move_take(take, ben)
    [row], _ = manifest.collect(lib)
    assert (row['speaker'], row['speaker_id']) == ('Ben', ben) and row['id'] == take.id


# ---- paths --------------------------------------------------------------------------------------------------------------

def test_paths_are_relative_to_the_folder_the_manifest_goes_in(lib, tmp_path):
    ann = lib.add_speaker('Ann')
    take = kept(lib, ann)
    stem = take.export_path.name
    assert manifest.collect(lib, base=lib.folder(ann))[0][0]['audio_filepath'] == stem
    assert manifest.collect(lib, base=lib.root)[0][0]['audio_filepath'] == f'{ann}/{stem}'
    assert manifest.collect(lib, base=tmp_path / 'elsewhere')[0][0]['audio_filepath'] == f'../voice-clips/{ann}/{stem}'


def test_without_a_base_or_on_another_drive_the_path_is_complete(lib, monkeypatch):
    ann = lib.add_speaker('Ann')
    take = kept(lib, ann)
    [row], _ = manifest.collect(lib)
    assert row['audio_filepath'] == take.export_path.resolve().as_posix() and '\\' not in row['audio_filepath']
    monkeypatch.setattr(os.path, 'relpath', lambda *a, **k: (_ for _ in ()).throw(ValueError('path is on mount D:')))
    [row], _ = manifest.collect(lib, base=lib.folder(ann))
    assert row['audio_filepath'] == take.export_path.resolve().as_posix()


# ---- clips that are left out --------------------------------------------------------------------------------------------

def test_a_wav_that_changed_after_it_was_kept_is_left_out_and_named(lib):
    ann = lib.add_speaker('Ann')
    good, changed = kept(lib, ann), kept(lib, ann)
    with open(changed.export_path, 'r+b') as f:
        f.seek(100)
        f.write(b'\x7f\x7f\x7f\x7f')
    rows, left_out = manifest.collect(lib)
    assert [r['id'] for r in rows] == [good.id]
    assert left_out == [(changed.sidecar_path.name, 'its WAV has changed since it was kept (the SHA-256 differs)')]


@pytest.mark.parametrize('damage, reason', [
    (lambda take: take.export_path.unlink(), 'its WAV file is missing'),
    (lambda take: edit_sidecar(take, consent_confirmed=False), 'does not record the permission'),
    (lambda take: edit_sidecar(take, consent_confirmed=None), 'does not record the permission'),
    (lambda take: edit_sidecar(take, transcript=None), 'incomplete'),
    (lambda take: edit_sidecar(take, export=None), 'incomplete'),
    (lambda take: edit_sidecar(take, export={'file': '../outside.wav', 'sha256': 'x'}), 'outside its own folder'),
    (lambda take: edit_sidecar(take, export={'file': 'C:/Windows/x.wav', 'sha256': 'x'}), 'outside its own folder'),
    (lambda take: edit_sidecar(take, export={'file': 'masters/' + take.master_path.name, 'sha256': 'x'}), 'not next to it'),
    (lambda take: edit_sidecar(take, export={'file': take.export_path.name}), 'no SHA-256'),
    (lambda take: take.sidecar_path.write_text('{ not json', encoding='utf-8'), 'could not be read'),
])
def test_a_clip_whose_record_cannot_be_trusted_is_left_out_with_the_reason(lib, damage, reason):
    ann = lib.add_speaker('Ann')
    fine, broken = kept(lib, ann), kept(lib, ann)
    damage(broken)
    rows, left_out = manifest.collect(lib)
    assert [r['id'] for r in rows] == [fine.id]
    [(name, why)] = left_out
    assert name == broken.sidecar_path.name and reason in why


def test_an_older_v1_sidecar_is_listed_too(lib):
    ann = lib.add_speaker('Ann')
    take = kept(lib, ann)
    edit_sidecar(take, schema='voice-recorder-clip-v1')
    assert len(manifest.collect(lib)[0]) == 1
    edit_sidecar(take, schema='voice-recorder-clip-v9')
    assert manifest.collect(lib) == ([], [])                     # a schema this program does not know is not a clip of its own


# ---- the files ----------------------------------------------------------------------------------------------------------

def rows_of(lib, **kw):
    return manifest.collect(lib, **kw)[0]


def test_json_lines_has_one_json_object_on_each_line(lib, tmp_path):
    ann = lib.add_speaker('Ann')
    kept(lib, ann, transcript='Line one.\nLine two with "quotes" and a caf\u00e9.')
    kept(lib, ann, transcript='Second clip.')
    rows = rows_of(lib)
    path = manifest.write(tmp_path / 'm.jsonl', rows)
    raw = path.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf') and raw.endswith(b'\n') and b'\r' not in raw
    lines = raw.decode('utf-8').splitlines()
    assert len(lines) == 2
    back = [json.loads(line) for line in lines]
    assert back == rows and back[0]['text'].count('\n') in (0, 1)
    assert 'caf\u00e9' in raw.decode('utf-8')                    # not escaped to \u00e9


def test_characters_some_programs_treat_as_line_ends_are_escaped_in_json_lines(lib, tmp_path):
    ann = lib.add_speaker('Ann')
    kept(lib, ann, transcript='one\u2028two\u2029three\x85four\x0bfive\x1csix.')
    path = manifest.write(tmp_path / 'm.jsonl', rows_of(lib))
    text = path.read_text(encoding='utf-8')
    assert len(text.splitlines()) == 1 and len(text.split('\n')) == 2
    assert json.loads(text)['text'] == 'one\u2028two\u2029three\x85four\x0bfive\x1csix.'


def test_csv_has_a_header_and_reads_back_with_any_csv_reader(lib, tmp_path):
    ann = lib.add_speaker('Ann')
    kept(lib, ann, transcript='Comma, "quote", and a new\nline.', label='A, B')
    rows = rows_of(lib)
    path = manifest.write(tmp_path / 'm.csv', rows)
    raw = path.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf')
    with open(path, encoding='utf-8', newline='') as f:
        table = list(csv.reader(f))
    assert tuple(table[0]) == COLUMNS and len(table) == 2
    cells = dict(zip(COLUMNS, table[1]))
    assert cells['text'] == 'Comma, "quote", and a new\nline.' and cells['label'] == 'A, B'
    assert float(cells['duration']) == rows[0]['duration'] and cells['sample_rate'] == '24000'


@pytest.mark.parametrize('start', ['=', '+', '-', '@'])
def test_csv_text_that_a_spreadsheet_would_run_as_a_formula_gets_an_apostrophe(lib, tmp_path, start):
    ann = lib.add_speaker('Ann')
    kept(lib, ann, transcript=f'{start}1+1 is two, said the teacher.', origin=f'{start}HYPERLINK("x")')
    rows = rows_of(lib)
    csv_path = manifest.write(tmp_path / 'm.csv', rows)
    with open(csv_path, encoding='utf-8', newline='') as f:
        cells = dict(zip(COLUMNS, list(csv.reader(f))[1]))
    assert cells['text'] == f"'{start}1+1 is two, said the teacher." and cells['origin'] == f"'{start}HYPERLINK(\"x\")"
    assert cells['audio_filepath'] == rows[0]['audio_filepath'] and cells['sha256'] == rows[0]['sha256']
    json_path = manifest.write(tmp_path / 'm.jsonl', rows)                  # while JSON Lines keeps the exact words
    assert json.loads(json_path.read_text(encoding='utf-8'))['text'] == f'{start}1+1 is two, said the teacher.'


def test_the_kind_comes_from_the_file_ending_unless_it_is_given(lib, tmp_path):
    ann = lib.add_speaker('Ann')
    kept(lib, ann)
    rows = rows_of(lib)
    assert manifest.kind_for('a.JSONL') == 'jsonl' and manifest.kind_for('a.csv') == 'csv' and manifest.kind_for('a.txt') is None
    assert manifest.write(tmp_path / 'upper.CSV', rows).read_text(encoding='utf-8').startswith('audio_filepath,')
    assert manifest.write(tmp_path / 'noext', rows, 'jsonl').read_text(encoding='utf-8').startswith('{')
    with pytest.raises(ValueError, match='.jsonl or a .csv'):
        manifest.write(tmp_path / 'm.txt', rows)
    assert not (tmp_path / 'm.txt').exists()


def test_an_empty_list_makes_an_empty_json_lines_file_and_a_header_only_csv(tmp_path):
    assert manifest.write(tmp_path / 'e.jsonl', []).read_bytes() == b''
    assert manifest.write(tmp_path / 'e.csv', []).read_bytes() == (','.join(COLUMNS) + '\r\n').encode('ascii')


def test_a_failed_write_leaves_the_earlier_file_and_no_leftover(lib, tmp_path, monkeypatch):
    ann = lib.add_speaker('Ann')
    kept(lib, ann)
    rows = rows_of(lib)
    target = tmp_path / 'm.jsonl'
    target.write_text('earlier manifest', encoding='utf-8')
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError(13, 'in use')))
    with pytest.raises(PermissionError):
        manifest.write(target, rows)
    monkeypatch.undo()
    assert target.read_text(encoding='utf-8') == 'earlier manifest' and not list(tmp_path.glob('*.tmp'))


def test_the_fields_of_a_row_match_the_documented_columns():
    assert COLUMNS[:3] == ('audio_filepath', 'text', 'duration')            # the names speech toolkits look for
    assert len(set(COLUMNS)) == len(COLUMNS) == 14
