"""The dataset layouts other tools ask for, Hugging Face's audio folder and Kaldi's data folder, and a split into training
and validation that is the same every time for the same clips.

The file names and columns are the ones the tools' own documentation gives: huggingface.co/docs/datasets/audio_dataset and
repository_structure, and kaldi-asr.org/doc/data_prep.html.
"""
import csv
import fnmatch
import json
import random

import pytest
import soundfile as sf

from helpers import fill, record, finish_batch
from recorder import dataset, manifest
from recorder.store import file_sha256


def keep(session, transcript, seconds=7.0):
    record(session, seconds)
    fill(session, transcript=transcript)
    session.keep()


@pytest.fixture
def voices(session):
    """Kept clips of three speakers whose folder names are the ones that trip the tools up: "test-voice" holds a word the
    datasets library takes for a split, and "voice" and "voice-2" sort the wrong way round for Kaldi once a dash follows."""
    for name, count in (('Voice', 3), ('Test voice', 2), ('Voice 2', 2)):
        session.workspace.add_speaker(name)
        for n in range(count):
            keep(session, f'{name} line {n + 1}, said once.')
    return session


def export(session, folder, layout, rate=None, validation=0, rows=None):
    rows = rows if rows is not None else manifest.collect(session.lib)[0]
    made = dataset.Export(session.lib, folder, layout, rate or dataset.DEFAULT_RATE[layout], False, validation)
    for row in made.split(rows):
        made.add(row)
    assert made.finish() == len(rows)
    return made


def read_csv(path):
    with open(path, encoding='utf-8', newline='') as source:
        return list(csv.DictReader(source))


def lines(path):
    return path.read_text(encoding='utf-8').splitlines()


def test_the_speakers_are_the_ones_the_tests_need(voices):
    assert set(voices.lib.speakers()) >= {'voice', 'voice-2', 'test-voice'}


# ---- Hugging Face's audio folder ---------------------------------------------------------------------------------------

def test_the_audio_folder_has_a_metadata_csv_beside_the_audio(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'audiofolder')
    rows = read_csv(folder / 'train' / 'metadata.csv')
    assert len(rows) == 7 and list(rows[0]) == ['file_name', 'text', 'speaker_id', 'allowed_use']
    for row in rows:
        assert (folder / 'train' / row['file_name']).is_file() and '/' not in row['file_name']
        assert row['allowed_use'] == 'my own voice references'
    assert {row['text'] for row in rows} >= {'Test voice line 1, said once.', 'Voice 2 line 2, said once.'}


def test_the_audio_folder_has_no_file_the_datasets_library_would_take_for_a_split(voices, tmp_path):
    """The library's own patterns: a split folder anywhere is found first, then a file name with a split's word in it. With
    no split, every file must still be found under train."""
    folder = tmp_path / 'set'
    export(voices, folder, 'audiofolder')
    files = [path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file()]
    in_a_split_folder = [name for name in files if name.split('/')[0] in ('train', 'validation')]
    assert sorted(in_a_split_folder) == sorted(name for name in files if name.endswith(('.wav', 'metadata.csv')))
    assert any(fnmatch.fnmatch(name.split('/')[-1], 'test[-._ 0-9]*') for name in files), \
        'the clips of "Test voice" are the case this guards against'


def test_the_audio_folder_keeps_the_clip_as_it_was_kept(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'audiofolder')
    info = sf.info(str(next((folder / 'train').glob('*.wav'))))
    assert info.samplerate == 24000 and info.subtype == 'PCM_16' and info.channels == 1


def test_the_audio_folder_with_a_split(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'audiofolder', validation=20)
    train, held = read_csv(folder / 'train' / 'metadata.csv'), read_csv(folder / 'validation' / 'metadata.csv')
    assert (len(train), len(held)) == (6, 1)
    assert all((folder / 'validation' / row['file_name']).is_file() for row in held)


# ---- Kaldi -----------------------------------------------------------------------------------------------------------

def test_the_kaldi_folder_has_its_four_lists(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'kaldi')
    scp = [line.split(' ', 1) for line in lines(folder / 'wav.scp')]
    assert len(scp) == 7
    for utterance, path in scp:
        assert (folder / path).is_file() and path.startswith('wavs/')
        assert sf.info(str(folder / path)).samplerate == 16000 and sf.info(str(folder / path)).subtype == 'PCM_16'
    texts = dict(line.split(' ', 1) for line in lines(folder / 'text'))
    speakers = dict(line.split(' ') for line in lines(folder / 'utt2spk'))
    assert set(texts) == set(speakers) == {u for u, _ in scp}
    assert 'Voice 2 line 1, said once.' in texts.values()
    assert sorted(set(speakers.values())) == ['test_voice', 'voice', 'voice_2']


def test_each_kaldi_utterance_starts_with_its_speaker_and_a_dash(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'kaldi')
    for line in lines(folder / 'utt2spk'):
        utterance, speaker = line.split(' ')
        assert utterance.startswith(speaker + '-') and ' ' not in utterance


def test_the_kaldi_lists_are_sorted_as_kaldi_checks_them(voices, tmp_path):
    """Kaldi's validate_data_dir.sh: every list sorted by byte, and utt2spk made again from spk2utt in the same order. A
    speaker "voice" next to "voice-2", with a dash after each, would put voice-2's utterances first and break that."""
    folder = tmp_path / 'set'
    export(voices, folder, 'kaldi')
    for name in ('wav.scp', 'text', 'utt2spk', 'spk2utt'):
        raw = (folder / name).read_bytes()
        assert b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf')
        assert lines(folder / name) == sorted(lines(folder / name), key=lambda line: line.encode('utf-8'))
    from_spk2utt = [f'{u} {line.split(" ")[0]}' for line in lines(folder / 'spk2utt') for u in line.split(' ')[1:]]
    assert from_spk2utt == lines(folder / 'utt2spk')


@pytest.mark.parametrize('slugs', [['voice', 'voice-2'], ['voice', 'voice-1', 'voice-10'], ['a', 'a0', 'a-0', 'a-b']])
def test_a_speakers_kaldi_id_keeps_the_order(slugs):
    stem = '20261020-161204_mic_3f9a1c2e'
    pairs = [(f'{dataset.kaldi_speaker(slug)}-{stem}', dataset.kaldi_speaker(slug)) for slug in slugs]
    assert sorted(pairs) == sorted(pairs, key=lambda pair: (pair[1], pair[0]))
    assert len({speaker for _, speaker in pairs}) == len(slugs)


def test_the_kaldi_folder_with_a_split(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'kaldi', validation=20)
    assert not (folder / 'wav.scp').exists()
    train, held = lines(folder / 'train' / 'wav.scp'), lines(folder / 'validation' / 'wav.scp')
    assert (len(train), len(held)) == (6, 1)
    for line in train + held:
        assert (folder / line.split(' ', 1)[1]).is_file()
    assert (folder / 'validation' / 'spk2utt').is_file() and (folder / 'validation' / 'text').is_file()


# ---- the manifest goes with every layout -------------------------------------------------------------------------------

@pytest.mark.parametrize('layout', list(dataset.LAYOUTS))
def test_every_layout_has_the_manifest_with_the_permission(voices, tmp_path, layout):
    folder = tmp_path / 'set'
    export(voices, folder, layout)
    rows = [json.loads(line) for line in lines(folder / 'manifest.jsonl')]
    assert len(rows) == 7
    for row in rows:
        assert row['allowed_use'] == 'my own voice references' and row['origin'] == 'recorded in my room'
        assert file_sha256(folder / row['audio_filepath']) == row['sha256'] and 'split' not in row


# ---- the split -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('count, percent, held', [(7, 10, 1), (20, 10, 2), (25, 10, 3), (25, 20, 5), (40, 5, 2), (1, 10, 0),
                                                  (2, 5, 1), (3, 20, 1)])
def test_how_many_clips_a_split_puts_aside(count, percent, held):
    rows = [{'speaker_id': 'voice', 'audio_filepath': f'/x/2026_mic_{n:08x}.wav'} for n in range(count)]
    parts = [row['split'] for row in dataset.split(rows, percent)]
    assert parts.count(dataset.VALIDATION) == held and parts.count(dataset.TRAIN) == count - held


def test_a_split_is_the_same_whatever_order_the_clips_come_in():
    rows = [{'speaker_id': 'voice', 'audio_filepath': f'/x/2026_mic_{n:08x}.wav'} for n in range(50)]
    first = {row['audio_filepath'] for row in dataset.split(rows, 10) if row['split'] == dataset.VALIDATION}
    shuffled = rows[:]
    random.Random(4).shuffle(shuffled)
    again = {row['audio_filepath'] for row in dataset.split(shuffled, 10) if row['split'] == dataset.VALIDATION}
    assert first == again and len(first) == 5
    other = {row['audio_filepath'] for row in dataset.split(rows, 20) if row['split'] == dataset.VALIDATION}
    assert first < other, 'a larger split takes the same clips and more'


def test_no_split_leaves_the_rows_alone():
    rows = [{'speaker_id': 'voice', 'audio_filepath': '/x/a.wav'}]
    assert dataset.split(rows, 0) == rows and 'split' not in dataset.split(rows, 0)[0]


def test_the_same_clips_go_aside_in_every_export(voices, tmp_path):
    held = []
    for n, layout in enumerate(('clips', 'audiofolder', 'clips')):
        folder = tmp_path / f'set{n}'
        export(voices, folder, layout, validation=20)
        rows = [json.loads(line) for line in lines(folder / 'manifest.jsonl')]
        held.append({(row['speaker_id'], row['id']) for row in rows if row['split'] == dataset.VALIDATION})
    assert held[0] == held[1] == held[2] and len(held[0]) == 1


def test_the_clips_layout_with_a_split(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'clips', validation=20)
    held = list((folder / 'validation').rglob('*.wav'))
    assert len(held) == 1 and len(list((folder / 'train').rglob('*.wav'))) == 6
    sidecar = json.loads(held[0].with_suffix('.json').read_text(encoding='utf-8'))
    assert sidecar['dataset'] == {'made_from': 'kept clip', 'split': 'validation'}
    assert sidecar['consent_confirmed'] is True


def test_the_ljspeech_layout_with_a_split(voices, tmp_path):
    folder = tmp_path / 'set'
    export(voices, folder, 'ljspeech', validation=20)
    everything = lines(folder / 'metadata.csv')
    train, held = lines(folder / 'metadata_train.csv'), lines(folder / 'metadata_validation.csv')
    assert len(everything) == 7 and (len(train), len(held)) == (6, 1) and sorted(train + held) == sorted(everything)


def test_a_split_is_offered_only_as_listed(session, tmp_path):
    with pytest.raises(ValueError, match='not offered'):
        dataset.Export(session.lib, tmp_path / 'set', 'clips', 24000, False, 50)


def test_the_job_says_how_the_clips_were_split(voices, tmp_path):
    rows, _ = manifest.collect(voices.lib)
    made = dataset.Export(voices.lib, tmp_path / 'set', 'kaldi', 16000, False, 20)
    voices.batch.export_dataset(made, made.split(rows))
    finish_batch(voices)
    assert voices.status_text.endswith('Wrote 7 of 7 clips to ' + str(tmp_path / 'set') +
                                       ': 6 for training and 1 for validation')
