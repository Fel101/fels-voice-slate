"""Voice Lab handoffs carry the exact checked files and work offline."""
import hashlib
import json
import re
import socket
import zipfile

import pytest

from helpers import fill, record
from recorder import fels_suite, handoff


@pytest.fixture
def kept(session):
    take = record(session, 12.0)
    fill(session)
    session.workspace.set_meta(label='First line')
    session.keep()
    return take


def edit_details(take, **changes):
    data = json.loads(take.sidecar_path.read_bytes())
    data.update(changes)
    take.sidecar_path.write_text(json.dumps(data), encoding='utf-8')


def test_bundle_is_exactly_the_kept_files_and_works_with_sockets_disabled(kept, session, tmp_path, monkeypatch):
    original = {path.relative_to(session.lib.root): path.read_bytes()
                for path in session.lib.root.rglob('*') if path.is_file()}
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)

    def blocked(*args, **kwargs):
        pytest.fail('Voice Slate tried to use a network socket')

    monkeypatch.setattr(socket, 'socket', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    clip = handoff.Clip.from_take(kept).check()
    assert handoff.inbox_exists(session.lib.root)
    path = clip.write(session.lib.root)
    assert path.parent == inbox
    assert re.fullmatch(r'\d{8}T\d{6}Z-voice-slate-smoke-speaker-First-line\.voiceslate', path.name)
    assert list(inbox.iterdir()) == [path]
    with zipfile.ZipFile(path) as bundle:
        # The suite clip format in docs/clip-format.md fixes the entry names and allows either order.
        assert sorted(bundle.namelist()) == ['clip.json', 'clip.wav']
        assert all(member.compress_type == zipfile.ZIP_STORED and not member.flag_bits & 1 for member in bundle.infolist())
        assert bundle.read('clip.wav') == kept.export_path.read_bytes()
        assert bundle.read('clip.json') == kept.sidecar_path.read_bytes()
        data = json.loads(bundle.read('clip.json').decode('utf-8'))
        assert data['export']['sha256'] == hashlib.sha256(bundle.read('clip.wav')).hexdigest()
    assert {path.relative_to(session.lib.root): path.read_bytes()
            for path in session.lib.root.rglob('*') if path.is_file()} == original


@pytest.mark.parametrize(('changes', 'problem'), [
    ({'consent_confirmed': 1}, 'confirmed permission'),
    ({'transcript': ''}, 'no transcript'),
    ({'origin': ' '}, 'no origin'),
    ({'allowed_use': None}, 'no allowed use'),
    ({'transcript_status': []}, 'could not be read'),
    ({'master': {'file': '../original.wav'}}, 'original file reference'),
    ({'master': {'file': ' '}}, 'original file reference'),
    ({'schema': 'voice-recorder-clip-v3'}, 'could not be read'),
])
def test_invalid_sidecar_is_refused(kept, changes, problem):
    edit_details(kept, **changes)
    with pytest.raises(handoff.HandoffError, match=problem):
        handoff.Clip.from_take(kept).check()


@pytest.mark.parametrize(('member', 'limit', 'problem'), [
    ('export_path', handoff.MAX_CLIP_BYTES, 'audition is too long'),
    ('sidecar_path', handoff.MAX_SIDECAR_BYTES, '1 MiB limit'),
])
def test_size_limits_are_enforced_before_packaging(kept, member, limit, problem):
    with getattr(kept, member).open('ab') as target:
        target.truncate(limit + 1)
    with pytest.raises(handoff.HandoffError, match=problem):
        handoff.Clip.from_take(kept).check()


@pytest.mark.parametrize(('field', 'value', 'problem'), [
    ('sha256', '0' * 64, 'WAV does not match'),
    ('sample_rate', 11025, 'WAV format'),
    ('file', 'CON.wav', 'invalid WAV name'),
])
def test_wav_declarations_must_describe_the_kept_file(kept, field, value, problem):
    export = json.loads(kept.sidecar_path.read_bytes())['export']
    edit_details(kept, export={**export, field: value})
    with pytest.raises(handoff.HandoffError, match=problem):
        handoff.Clip.from_take(kept).check()


def test_legacy_sidecar_remains_unchanged_in_the_bundle(kept, session, tmp_path):
    edit_details(kept, schema='voice-recorder-clip-v1')
    path = handoff.Clip.from_take(kept).check().write(session.lib.root, tmp_path / 'legacy.voiceslate')
    with zipfile.ZipFile(path) as bundle:
        assert bundle.read('clip.json') == kept.sidecar_path.read_bytes()


def test_handoff_refuses_files_that_changed_after_the_check(kept, session, tmp_path):
    clip = handoff.Clip.from_take(kept).check()
    edit_details(kept, transcript_status='asr_draft')
    destination = tmp_path / 'draft.voiceslate'
    with pytest.raises(handoff.HandoffError, match='changed during the handoff'):
        clip.write(session.lib.root, destination)
    assert not destination.exists()


def test_save_keeps_existing_files_and_never_writes_in_the_library(kept, session, tmp_path, monkeypatch):
    clip = handoff.Clip.from_take(kept).check()
    destination = tmp_path / 'existing.voiceslate'
    destination.write_bytes(b'existing')
    with pytest.raises(handoff.HandoffError, match='already exists'):
        clip.write(session.lib.root, destination)
    assert destination.read_bytes() == b'existing'
    assert list(tmp_path.glob('.voiceslate-*')) == []
    with pytest.raises(handoff.HandoffError, match='outside the output folder'):
        clip.write(session.lib.root, session.lib.root / 'copy.voiceslate')
    configured = session.lib.root / 'suite'
    monkeypatch.setenv('FELS_SUITE_DIR', str(configured))
    assert not handoff.inbox_exists(session.lib.root)
    assert not configured.exists()


def test_an_unusable_suite_folder_is_logged_at_startup(kept, session, tmp_path, monkeypatch, caplog):
    configured = tmp_path / 'suite-is-a-file'
    configured.touch()
    monkeypatch.setenv('FELS_SUITE_DIR', str(configured))
    default = tmp_path / 'default-suite-folder'
    monkeypatch.setattr(fels_suite, 'default_suite_dir', lambda **kwargs: default)
    inbox = default / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)

    handoff.log_suite_folder_fallback(session.lib.root)
    assert handoff.inbox_exists(session.lib.root)
    clip = handoff.Clip.from_take(kept).check()
    assert clip.write(session.lib.root).parent == inbox
    assert [message for message in caplog.messages if 'FELS_SUITE_DIR' in message] == [
        "Could not use FELS_SUITE_DIR. Using the default Fel's Studio folder."]
