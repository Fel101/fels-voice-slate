"""A take reads its audio when it is asked for, and lets it go again, so a big library opens quickly and fits in memory."""
import gc
import json
import time

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, Bin, fill, record, speech
from recorder import store
from recorder.store import Library, Take


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'clips', trash=Bin())


@pytest.fixture
def room(monkeypatch):
    """A fresh count of what is held in memory, with this much room in it, untouched by the takes of other tests."""
    def limit(max_bytes):
        resident = store._Resident()
        resident.MAX_BYTES = max_bytes
        monkeypatch.setattr(store, '_resident', resident)

    return limit


def many_masters(library, slug, count, seconds=2.0):
    """`count` takes on disk, none of them kept, written directly so that the test is quick."""
    folder = library.folder(slug) / 'masters'
    folder.mkdir(parents=True)
    x = speech(seconds)
    for number in range(count):
        sf.write(str(folder / f'20261020-1612{number % 60:02d}_mic_{number:08x}.raw.wav'), x, RATE, subtype='PCM_24')


def test_loading_a_list_of_takes_reads_none_of_their_audio(library, monkeypatch):
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 3)

    def refuse(*args, **kwargs):
        raise AssertionError('the audio was read')

    monkeypatch.setattr(sf, 'read', refuse)
    takes = library.load_takes(slug)
    assert len(takes) == 3
    assert all(take.seconds == pytest.approx(2.0) and take.rate == RATE and take.channels == 1 for take in takes)


def test_a_thousand_takes_load_in_about_a_second(library):
    """They froze the start for about 16 s, and held over a gigabyte. The first look at new files is left out: Windows'
    virus scanner reads each one then, which takes it 8 ms a file and is not something a library that exists meets."""
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 1000, seconds=0.5)
    library.load_takes(slug)
    started = time.perf_counter()
    takes = library.load_takes(slug)
    took = time.perf_counter() - started
    assert len(takes) == 1000 and took < 3.0
    assert not any(take.__dict__.get('_samples') is not None for take in takes)


def test_a_kept_clip_loads_without_reading_its_audio(library, monkeypatch):
    slug = library.add_speaker('Someone')
    take = Take(source='mic', samples=speech(8.0), rate=RATE, speaker=slug,
                meta={'transcript': 'Words.', 'origin': 'here', 'allowed_use': 'tests', 'consent_confirmed': True})
    library.save_master(slug, take)
    library.keep(slug, take)
    monkeypatch.setattr(sf, 'read', lambda *a, **k: pytest.fail('the audio was read'))
    again = library.load_kept(slug)[0]
    assert again.seconds == pytest.approx(8.0) and again.kept


def test_the_audio_arrives_when_it_is_asked_for(library):
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 1)
    take = library.load_takes(slug)[0]
    assert take.samples.shape == (2 * RATE,)
    assert np.allclose(take.samples, speech(2.0), atol=1e-5)


def test_a_stereo_original_keeps_its_shape(library):
    slug = library.add_speaker('Someone')
    folder = library.folder(slug) / 'masters'
    folder.mkdir(parents=True)
    x = speech(1.0)
    sf.write(str(folder / '20261020-161200_mic_00000001.raw.wav'), np.column_stack([x, x]), RATE, subtype='PCM_24')
    take = library.load_takes(slug)[0]
    assert take.channels == 2 and take.samples.shape == (RATE, 2)


def test_a_file_that_has_gone_says_so(library):
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 1)
    take = library.load_takes(slug)[0]
    take.master_path.unlink()
    with pytest.raises(OSError):
        take.samples


def test_memory_stays_bounded_however_many_takes_are_opened(library, room):
    room(3 * 2 * RATE * 4)                                      # three takes of 2 s
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 12)
    takes = library.load_takes(slug)
    for take in takes:
        take.samples
    held = [take for take in takes if take.__dict__.get('_samples') is not None]
    assert len(held) == 3 and takes[-1] in held


def test_a_take_nobody_holds_does_not_count_against_the_others(library, room):
    room(2 * 2 * RATE * 4)
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 2)
    gone = Take(source='mic', samples=speech(60.0), rate=RATE)          # no file behind it, so it can never let go
    gone.samples
    del gone
    gc.collect()
    first, second = library.load_takes(slug)
    first.samples
    second.samples
    assert first.__dict__.get('_samples') is not None and second.__dict__.get('_samples') is not None


def test_what_was_worked_out_from_the_audio_goes_with_it(library, room):
    room(1)
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 2)
    first, second = library.load_takes(slug)
    first.mono
    first.cache['thumb'] = 'kept'
    second.samples
    assert 'mono' not in first.cache and first.cache['thumb'] == 'kept'


def test_the_take_in_use_stays_however_large_it_is(library, room):
    room(1)
    slug = library.add_speaker('Someone')
    many_masters(library, slug, 1)
    take = library.load_takes(slug)[0]
    take.samples
    assert take.__dict__.get('_samples') is not None


def test_audio_that_is_only_in_memory_is_never_let_go(session, monkeypatch, room):
    room(1)
    real = session.lib.save_master
    monkeypatch.setattr(session.lib, 'save_master', lambda slug, take: (_ for _ in ()).throw(OSError('disk full')))
    alone = record(session, 8.0)
    assert alone.unsaved
    monkeypatch.setattr(session.lib, 'save_master', real)
    record(session, 8.0)
    assert alone.__dict__.get('_samples') is not None and len(alone.samples) == 8 * RATE


def test_a_take_works_the_same_after_its_audio_was_let_go(session):
    take = record(session, 8.0)
    fill(session)
    before = session.lib.export_for(take).copy()
    take.let_go()
    assert take.__dict__.get('_samples') is None
    assert np.allclose(session.lib.export_for(take), before, atol=1e-4)


def test_opening_a_take_whose_original_has_gone_says_so_and_stays_put(session):
    first = record(session, 8.0)
    second = record(session, 8.0)
    first.let_go()
    first.master_path.unlink()
    assert session.workspace.open_take(first) is False
    assert session.current is second and session.bus.notices[-1].title == 'Could not open the take'


def test_a_sidecar_that_cuts_the_whole_clip_is_still_refused(library):
    slug = library.add_speaker('Someone')
    take = Take(source='mic', samples=speech(8.0), rate=RATE, speaker=slug,
                meta={'transcript': 'Words.', 'origin': 'here', 'allowed_use': 'tests', 'consent_confirmed': True})
    library.save_master(slug, take)
    paths = library.keep(slug, take)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data['edits'] = [{'op': 'cut', 'start_seconds': 0.0, 'end_seconds': 8.0, 'region': 'start'}]
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    assert library.load_kept(slug) == [] and 'invalid' in library.skipped[0][1]
