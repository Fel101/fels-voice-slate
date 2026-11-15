"""How much there is: the kept clips of a speaker and their length, the length of the takes selected, how far a script has
got, and what a manifest or dataset would hold. Every total comes from what the takes and sidecars already say: no audio
is read to add one up."""
import json
from pathlib import Path

import pytest
import soundfile as sf

from helpers import RATE, fill, record, speech
from recorder import manifest
from recorder.session.workspace import kept_total, length_total
from recorder.store import Library, Take

KEEPABLE = {'transcript': 'The harbour lights came on at dusk.', 'origin': 'recorded in my room',
            'allowed_use': 'my own voice references', 'consent_confirmed': True}


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / 'voice-clips', trash=lambda paths: [p.unlink() for p in paths])


def kept(lib, slug, seconds=12.0, **meta):
    take = Take(source='mic', samples=speech(seconds), rate=RATE, speaker=slug, meta={**KEEPABLE, **meta})
    lib.save_master(slug, take)
    lib.keep(slug, take)
    return take


@pytest.fixture
def no_audio_read(monkeypatch):
    """Call it once the clips are written: reading audio, or hashing a clip, then fails the test. Reading a file's header
    is allowed."""
    def refuse(*args, **kwargs):
        raise AssertionError('audio was read to add up a total')

    def from_here_on():
        monkeypatch.setattr(sf, 'read', refuse)
        monkeypatch.setattr(sf, 'blocks', refuse)
        for name in ('read', 'buffer_read', 'blocks'):          # opening a file to read its header is allowed
            monkeypatch.setattr(sf.SoundFile, name, refuse)
        monkeypatch.setattr(manifest, 'file_sha256', refuse)
    return from_here_on


# ---- the length of a clip, without its audio ---------------------------------------------------------------------------

def test_a_kept_clip_knows_its_length_from_its_sidecar_when_the_list_loads(lib, no_audio_read):
    ann = lib.add_speaker('Ann')
    kept(lib, ann, 12.0)
    kept(lib, ann, 9.5)
    no_audio_read()
    takes = lib.load_takes(ann)
    assert sorted(take.kept_seconds for take in takes) == pytest.approx([9.5, 12.0], abs=0.01)
    assert kept_total(takes) == (2, pytest.approx(21.5, abs=0.02))


def test_keep_sets_the_length_as_the_sidecar_has_it(lib):
    ann = lib.add_speaker('Ann')
    take = kept(lib, ann, 12.0)
    data = json.loads(Path(take.sidecar_path).read_text(encoding='utf-8'))
    assert take.kept_seconds == data['export']['duration_seconds'] == pytest.approx(12.0, abs=0.01)


def test_a_sidecar_with_no_length_is_measured_from_the_header_of_its_wav(lib):
    ann = lib.add_speaker('Ann')
    take = kept(lib, ann, 8.0)
    data = json.loads(Path(take.sidecar_path).read_text(encoding='utf-8'))
    data['export'].pop('duration_seconds')
    Path(take.sidecar_path).write_text(json.dumps(data), encoding='utf-8')
    [loaded] = lib.load_takes(ann)
    assert loaded.kept_seconds == pytest.approx(8.0, abs=0.01)


def test_a_take_not_kept_is_its_original_less_its_cuts():
    take = Take(source='mic', samples=speech(12.0), rate=RATE)
    assert take.clip_seconds == pytest.approx(12.0)
    take.history._states[0]['edits'] = [{'op': 'cut', 'start_seconds': 2.0, 'end_seconds': 4.5},
                                        {'op': 'cut', 'start_seconds': 4.0, 'end_seconds': 5.0},       # overlaps the first
                                        {'op': 'fade_in', 'start_seconds': 0.0, 'end_seconds': 1.0}]   # not a cut
    assert take.clip_seconds == pytest.approx(9.0, abs=0.001)
    assert length_total([take, Take(source='mic', samples=speech(3.0), rate=RATE)]) == (2, pytest.approx(12.0, abs=0.001))


def test_a_kept_clip_changed_since_is_counted_as_it_is_now_when_selected(session):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    assert take.clip_seconds == pytest.approx(take.kept_seconds)
    session.editor.set_selection(1.0, 3.0)
    session.editor.cut_selection()
    assert take.changed and take.clip_seconds == pytest.approx(10.0, abs=0.01)
    assert kept_total([take]) == (1, pytest.approx(12.0, abs=0.01))          # the kept clip on disk has not changed


# ---- what a manifest or dataset would hold ------------------------------------------------------------------------------

def test_the_tally_counts_the_kept_clips_of_one_speaker_or_of_all_from_their_sidecars(lib, no_audio_read):
    ann, ben = lib.add_speaker('Ann'), lib.add_speaker('Ben')
    kept(lib, ann, 12.0)
    kept(lib, ann, 10.0)
    kept(lib, ben, 9.0)
    unkept = Take(source='mic', samples=speech(12.0), rate=RATE, speaker=ann)
    lib.save_master(ann, unkept)
    (lib.folder(ann) / 'notes.json').write_text('{"hello": 1}', encoding='utf-8')
    no_audio_read()
    assert manifest.tally(lib, [ann]) == (2, pytest.approx(22.0, abs=0.02))
    assert manifest.tally(lib) == (3, pytest.approx(31.0, abs=0.03))


def test_the_tally_leaves_out_what_an_export_leaves_out_without_reading_it(lib, no_audio_read):
    ann, ben = lib.add_speaker('Ann'), lib.add_speaker('Ben')
    kept(lib, ann, 12.0)
    gone = kept(lib, ann, 10.0)
    kept(lib, ben, 9.0)
    gone.export_path.unlink()
    lib.set_release(ben, signed='2020-01-01', expires='2021-01-01')
    no_audio_read()
    assert manifest.tally(lib) == (1, pytest.approx(12.0, abs=0.01))


def test_the_tally_matches_the_manifest_written(lib):
    ann = lib.add_speaker('Ann')
    for seconds in (12.0, 10.5, 9.25):
        kept(lib, ann, seconds)
    rows, left_out = manifest.collect(lib, [ann])
    count, seconds = manifest.tally(lib, [ann])
    assert left_out == [] and count == len(rows) and seconds == pytest.approx(sum(row['duration'] for row in rows), abs=0.01)
