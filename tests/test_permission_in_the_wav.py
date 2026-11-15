"""The line inside a clip's audio file that says what it may be used for, so that a clip dragged into another program
without its sidecar still says so: in a kept clip, an updated one, a copy and a dataset clip, and never in a clip kept
before, which is not touched."""
import json
import struct
import wave

import pytest
import soundfile as sf

from helpers import record
from recorder import dataset, manifest, share
from recorder.store import file_sha256, permission_line


def chunks(path):
    """The RIFF chunks of a WAV as (id, bytes), and the sub-chunks of a LIST INFO, read byte by byte without SoundFile."""
    raw = path.read_bytes()
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'
    found, at = [], 12
    while at + 8 <= len(raw):
        name, size = raw[at:at + 4], struct.unpack('<I', raw[at + 4:at + 8])[0]
        body = raw[at + 8:at + 8 + size]
        found.append((name, body))
        if name == b'LIST' and body[:4] == b'INFO':
            inner = 4
            while inner + 8 <= len(body):
                sub, length = body[inner:inner + 4], struct.unpack('<I', body[inner + 4:inner + 8])[0]
                found.append((b'INFO/' + sub, body[inner + 8:inner + 8 + length]))
                inner += 8 + length + (length & 1)
        at += 8 + size + (size & 1)
    return found


def info_comment(path):
    texts = [body for name, body in chunks(path) if name == b'INFO/ICMT']
    return texts[0].rstrip(b'\x00').decode('utf-8') if texts else None


def keep(session, allowed='my own voice references', origin='recorded in my room', seconds=8.0):
    take = record(session, seconds)
    session.workspace.set_meta(transcript='The harbour lights came on at dusk.', origin=origin, allowed_use=allowed)
    session.workspace.set_meta(consent_confirmed=True)          # the statement, made after the texts it is about
    session.keep()
    return take


def sidecar_of(take):
    return json.loads(take.sidecar_path.read_text(encoding='utf-8'))


# ---- a kept clip -------------------------------------------------------------------------------------------------------

def test_a_kept_clip_says_what_it_may_be_used_for_inside_its_wav(session):
    take = keep(session)
    data = sidecar_of(take)
    line = info_comment(take.export_path)
    assert line == permission_line(data)
    assert line.startswith(f"Fel's Voice Slate clip {take.id}, described in {take.sidecar_path.name}.")
    assert 'Allowed use: my own voice references.' in line
    assert f"Permission given {data['consent_confirmed_at'][:10]}." in line
    assert sf.SoundFile(str(take.export_path)).comment == line


def test_the_origin_is_not_in_the_line(session):
    take = keep(session, origin='Recorded at Ann Lee\'s flat, 4 Harbour Road')
    assert 'Ann Lee' not in info_comment(take.export_path) and 'Harbour' not in info_comment(take.export_path)


def test_the_sidecars_sha256_is_of_the_finished_file_with_the_line_in_it(session):
    take = keep(session)
    assert sidecar_of(take)['export']['sha256'] == file_sha256(take.export_path)
    rows, left_out = manifest.collect(session.lib)
    assert len(rows) == 1 and left_out == []


def test_another_program_still_opens_the_clip(session):
    """Python's own wave module reads only plain PCM and steps over chunks it does not know, as most programs do."""
    take = keep(session)
    with wave.open(str(take.export_path), 'rb') as clip:
        assert clip.getnchannels() == 1 and clip.getframerate() == 24000 and clip.getsampwidth() == 2
        assert clip.getnframes() == sf.info(str(take.export_path)).frames
    names = [name for name, _ in chunks(take.export_path)]
    assert names.index(b'data') < names.index(b'LIST'), 'Windows shows the comment only from a chunk after the audio'


def test_an_accented_letter_is_written_as_utf8(session):
    take = keep(session, allowed='réunion d’équipe, Señora Núñez')
    raw = take.export_path.read_bytes()
    assert 'réunion d’équipe, Señora Núñez'.encode('utf-8') in raw
    assert 'Allowed use: réunion d’équipe, Señora Núñez.' in info_comment(take.export_path)


def test_updating_a_kept_clip_writes_the_line_again(session):
    take = keep(session)
    session.workspace.set_meta(allowed_use='narration for the museum guide')
    assert not take.meta['consent_confirmed']                   # a new use needs the statement again
    session.workspace.set_meta(consent_confirmed=True)
    session.keep()
    assert 'Allowed use: narration for the museum guide.' in info_comment(take.export_path)
    assert sidecar_of(take)['export']['sha256'] == file_sha256(take.export_path)


def test_a_long_allowed_use_is_shortened_and_says_where_the_rest_is(session):
    take = keep(session, allowed='word ' * 200)
    line = info_comment(take.export_path)
    assert line.count('word') < 100 and '... (the rest is in the sidecar).' in line
    assert sidecar_of(take)['allowed_use'] == ('word ' * 200).strip()


def test_a_clip_kept_before_the_date_was_recorded_gets_no_date():
    line = permission_line({'id': '3f9a1c2e', 'export': {'file': '20261020-161204_mic_3f9a1c2e.wav'},
                            'allowed_use': 'my own voice references'})
    assert line == ("Fel's Voice Slate clip 3f9a1c2e, described in 20261020-161204_mic_3f9a1c2e.json. "
                    "Allowed use: my own voice references.")


# ---- clips kept before ---------------------------------------------------------------------------------------------------

@pytest.fixture
def kept_before(session):
    """A clip kept by an older version: its WAV has no line, and its sidecar vouches for that WAV."""
    take = keep(session)
    samples, rate = sf.read(str(take.export_path), dtype='int16')
    sf.write(str(take.export_path), samples, rate, subtype='PCM_16', format='WAV')
    data = sidecar_of(take)
    data['export']['sha256'] = file_sha256(take.export_path)
    take.sidecar_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    assert info_comment(take.export_path) is None
    return take


def test_a_clip_kept_before_is_not_touched(make_session, session, kept_before, tmp_path):
    before = kept_before.export_path.read_bytes()
    again = make_session(root=session.lib.root)
    again.workspace.select_speaker(session.slug)
    rows, left_out = manifest.collect(again.lib)
    assert len(rows) == 1 and left_out == []
    share.save_copy(kept_before.export_path, tmp_path / 'copy.wav', 'wav', 24000)
    made = dataset.Export(again.lib, tmp_path / 'set', 'clips', 24000, False)
    made.add(rows[0])
    made.finish()
    assert kept_before.export_path.read_bytes() == before


def test_a_copy_of_a_clip_kept_before_carries_the_line(session, kept_before, tmp_path):
    copy = share.save_copy(kept_before.export_path, tmp_path / 'copy.wav', 'wav', 48000)
    assert info_comment(copy) == permission_line(sidecar_of(kept_before))


# ---- copies and datasets ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['wav', 'flac', 'ogg'])
def test_a_copy_carries_the_line(session, tmp_path, kind):
    take = keep(session, allowed='réunion d’équipe')
    copy = share.save_copy(take.export_path, tmp_path / f'copy{share.FORMATS[kind][1]}', kind, 24000)
    assert sf.SoundFile(str(copy)).comment == permission_line(sidecar_of(take))
    if kind == 'wav':
        assert info_comment(copy) == permission_line(sidecar_of(take))
        names = [name for name, _ in chunks(copy)]
        assert names.index(b'data') < names.index(b'LIST')


def test_an_mp3_copy_carries_the_line_in_plain_letters(session, tmp_path):
    """An MP3's tag holds Latin-1 and libsndfile hands it UTF-8, so "réunion" came back as "rÃ©union"."""
    take = keep(session, allowed='réunion d’équipe – Señora')
    copy = share.save_copy(take.export_path, tmp_path / 'copy.mp3', 'mp3', 24000)
    comment = sf.SoundFile(str(copy)).comment
    assert "Allowed use: reunion d'equipe - Senora." in comment and comment.isascii()
    assert comment == share.plain_letters(permission_line(sidecar_of(take)))


def test_a_copy_without_a_sidecar_beside_its_source_is_still_made(session, tmp_path):
    take = keep(session)
    loose = tmp_path / 'loose.wav'
    loose.write_bytes(take.export_path.read_bytes())
    copy = share.save_copy(loose, tmp_path / 'copy.flac', 'flac', 24000)
    assert sf.info(str(copy)).frames > 0 and sf.SoundFile(str(copy)).comment == ''


@pytest.mark.parametrize('layout', list(dataset.LAYOUTS))
def test_every_dataset_clip_carries_the_line(session, tmp_path, layout):
    take = keep(session)
    rows, _ = manifest.collect(session.lib)
    made = dataset.Export(session.lib, tmp_path / 'set', layout, dataset.DEFAULT_RATE[layout], False)
    made.add(rows[0])
    made.finish()
    row = json.loads((tmp_path / 'set' / 'manifest.jsonl').read_text(encoding='utf-8'))
    wav = tmp_path / 'set' / row['audio_filepath']
    assert info_comment(wav) == permission_line(sidecar_of(take))
    assert file_sha256(wav) == row['sha256']
