"""Speakers, takes, keep and discard, restarts, and the cases where a take must never be lost."""
import json
from datetime import datetime

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, FakeCapture, bursts, fill, record, speech
from recorder import store
from recorder.store import KeepBlocked, TrashUnavailable


def fail_writes(monkeypatch, library):
    """Make writing originals to this library fail, as a full disk does. Returns nothing; undone with the monkeypatch."""
    monkeypatch.setattr(library, 'save_master', lambda *a: (_ for _ in ()).throw(OSError('disk full')))


# ---- takes ------------------------------------------------------------------------------------------------------------

def test_a_finished_capture_becomes_a_pending_take_and_is_opened(session):
    take = record(session)
    assert session.current is take and take in session.workspace.takes
    assert take.master_path.exists() and take.master_path.parent.name == 'masters'
    assert take.meta['origin'] == '' and take.meta['consent_confirmed'] is False


def test_an_empty_capture_is_refused_with_a_notice(session):
    assert session.workspace.add_take(np.zeros(0, np.float32), RATE, 'system', {}) is None
    notice = session.bus.notices[-1]
    assert notice.level == 'warning' and 'No audio' in notice.text


def test_a_capture_longer_than_the_limit_is_cut_to_it(session):
    take = record(session, samples=np.zeros(41 * RATE, np.float32) + 0.1)
    assert take.seconds == pytest.approx(40.0, abs=0.01)


def test_a_second_take_while_reviewing_the_first_opens_the_new_one(session):
    record(session, 10.0)
    second = record(session, 2.0)
    assert session.current is second and session.workspace.takes[0] is second


def test_opening_an_older_take_makes_it_current(session):
    first = record(session, 10.0)
    record(session, 2.0)
    session.workspace.open_take(first)
    assert session.current is first


def test_the_automatic_speaker_is_called_my_voice_and_is_selected(make_session):
    s = make_session()
    assert s.workspace.speaker is None
    assert s.workspace.ensure_speaker() == 'my-voice'
    assert s.workspace.speaker == 'my-voice' and s.workspace.name_of('my-voice') == 'My voice'


def test_a_blank_speaker_name_is_refused(session):
    with pytest.raises(ValueError):
        session.workspace.add_speaker('   ')


def test_the_text_fields_belong_to_the_take_and_unknown_fields_are_refused(session):
    take = record(session)
    session.workspace.set_meta(transcript='Hello there friend.', consent_confirmed=1, notes=None)
    assert take.meta['transcript'] == 'Hello there friend.'
    assert take.meta['consent_confirmed'] is True and take.meta['notes'] == ''
    with pytest.raises(KeyError):
        session.workspace.set_meta(favourite_colour='green')


# ---- keep -------------------------------------------------------------------------------------------------------------

def test_keep_writes_the_export_and_sidecar_after_a_cut(session):
    record(session, 14.0)
    fill(session)
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    session.workspace.confirm_transcript()
    session.keep()
    take = session.current
    assert take.export_path.exists() and take.sidecar_path.exists()
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    assert data['schema'] == store.SCHEMA
    assert data['edits'][0]['region'] == 'start'
    assert data['needs_transcript_review'] is False
    assert data['transcript_status'] == 'typed'


def test_keep_after_a_cut_waits_until_the_transcript_is_confirmed(session):
    record(session, 14.0)
    fill(session)
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    with pytest.raises(KeepBlocked) as blocked:
        session.keep()
    assert 'transcript still matches' in blocked.value.problems[0]
    assert not list(session.lib.folder(session.slug).glob('*.wav'))
    session.workspace.confirm_transcript()
    assert session.keep()['export'].exists()


def test_keep_with_missing_permission_shows_the_reasons_and_writes_nothing(session):
    record(session)
    fill(session, consent=False)
    with pytest.raises(KeepBlocked) as blocked:
        session.keep()
    assert any('permission' in problem for problem in blocked.value.problems)
    assert not list(session.lib.folder(session.slug).glob('*.wav'))


def test_script_line_is_saved_as_read_from_script_when_unchanged(session):
    line = 'A line read from the script.'
    session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line=line)
    assert session.current.meta['transcript'] == line
    fill(session, transcript=line)
    session.keep()
    data = json.loads(session.current.sidecar_path.read_text(encoding='utf-8'))
    assert data['transcript_status'] == 'read_from_script'


def test_script_line_changed_by_hand_is_saved_as_edited(session):
    session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line='A line read from the script.')
    fill(session, transcript='A slightly different line.')
    session.keep()
    data = json.loads(session.current.sidecar_path.read_text(encoding='utf-8'))
    assert data['transcript_status'] == 'edited'


def test_a_draft_that_is_kept_unchanged_is_saved_as_a_draft(session):
    take = record(session, 10.0)
    session.drafts.receive(take, 'A draft from the audio.', None)
    assert take.meta['transcript'] == 'A draft from the audio.'
    fill(session, transcript='A draft from the audio.')
    session.keep()
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    assert data['transcript_status'] == 'asr_draft'


def test_a_draft_that_was_corrected_is_saved_as_edited(session):
    take = record(session, 10.0)
    session.drafts.receive(take, 'A draft from the audio.', None)
    fill(session, transcript='A draft from the audio, corrected.')
    session.keep()
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['transcript_status'] == 'edited'


def test_a_transcript_stays_edited_when_the_clip_is_kept_again(session):
    take = record(session, 10.0)
    session.drafts.receive(take, 'A draft from the audio.', None)
    fill(session, transcript='A draft from the audio, corrected.')
    session.keep()
    session.keep()
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['transcript_status'] == 'edited'


def test_a_kept_draft_corrected_after_a_restart_is_saved_as_edited(session, make_session):
    take = record(session, 10.0)
    session.drafts.receive(take, 'A draft from the audio.', None)
    fill(session, transcript='A draft from the audio.')
    session.keep()
    fresh = make_session()
    assert fresh.current.meta['transcript_status'] == 'asr_draft'
    fresh.workspace.set_meta(transcript='A draft that somebody corrected.')
    fresh.keep()
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['transcript_status'] == 'edited'


def test_typed_text_stays_typed(session):
    take = record(session, 10.0)
    fill(session)
    session.keep()
    session.workspace.set_meta(transcript='Different words, typed by hand.')
    session.keep()
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['transcript_status'] == 'typed'


def test_keeping_the_script_take_steps_to_the_next_line(session):
    session.script.load_text('First line to read aloud.\nSecond line to read aloud.')
    session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    assert session.script.index == 1


def test_updating_a_kept_clip_does_not_skip_a_line_of_the_script(session):
    session.script.load_text('Line one to read.\nLine two to read.\nLine three to read.')
    first = session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    assert session.script.label == '2 of 3'
    session.workspace.open_take(first)
    session.keep()
    assert session.script.label == '2 of 3'


def test_keeping_a_take_of_an_earlier_line_does_not_move_the_script(session):
    session.script.load_text('Line one to read.\nLine two to read.\nLine three to read.')
    older = session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.workspace.add_take(speech(10.0), RATE, 'mic', {}, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    assert session.script.index == 1
    session.workspace.open_take(older)
    session.keep()
    assert session.script.index == 1


# ---- the permission --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('field', ['allowed_use', 'origin'])
def test_new_words_for_what_the_permission_covers_need_the_box_ticked_again(session, field):
    take = record(session, 10.0)
    fill(session)
    session.workspace.set_meta(**{field: 'something else entirely'})
    assert take.meta['consent_confirmed'] is False and take.meta['consent_confirmed_at'] == ''
    assert 'tick the box again' in session.status_text
    with pytest.raises(KeepBlocked, match='permission'):
        session.keep()


def test_a_kept_clip_cannot_change_its_allowed_use_without_the_box_being_ticked_again(session):
    take = record(session, 10.0)
    fill(session)
    session.keep()
    session.workspace.set_meta(allowed_use='sold to anyone who asks')
    assert take.meta['consent_confirmed'] is False
    with pytest.raises(KeepBlocked, match='permission'):
        session.keep()
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    assert data['allowed_use'] == 'my own voice references'
    session.workspace.set_meta(consent_confirmed=True)
    assert json.loads(session.keep()['sidecar'].read_text(encoding='utf-8'))['allowed_use'] == 'sold to anyone who asks'


def test_spaces_around_the_words_change_nothing_the_permission_covers(session):
    take = record(session, 10.0)
    fill(session)
    session.workspace.set_meta(allowed_use='my own voice references  ')
    assert take.meta['consent_confirmed'] is True


def test_other_fields_leave_the_permission_alone(session):
    take = record(session, 10.0)
    fill(session)
    session.workspace.set_meta(transcript='Different words.', notes='a note', label='A name', source_url='https://a.example')
    assert take.meta['consent_confirmed'] is True


def test_the_sidecar_says_when_the_box_was_ticked(session):
    take = record(session, 10.0)
    fill(session, consent=False)
    assert take.meta.get('consent_confirmed_at', '') == ''
    session.workspace.set_meta(consent_confirmed=True)
    moment = take.meta['consent_confirmed_at']
    assert datetime.fromisoformat(moment).tzinfo is not None
    assert json.loads(session.keep()['sidecar'].read_text(encoding='utf-8'))['consent_confirmed_at'] == moment


# ---- what a load can survive --------------------------------------------------------------------------------------------

@pytest.mark.parametrize('field, value', [('created', None), ('created', 12345), ('created', 'not a date'),
                                          ('transcript', 123), ('flags', 7), ('device', 'mic')])
def test_one_damaged_sidecar_does_not_stop_the_program_opening(session, make_session, field, value):
    good, bad = record(session, 10.0), record(session, 10.0)
    session.workspace.open_take(good)
    fill(session)
    session.keep()
    session.workspace.open_take(bad)
    fill(session)
    paths = session.keep()
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    data[field] = value
    paths['sidecar'].write_text(json.dumps(data), encoding='utf-8')
    fresh = make_session()
    assert [take.id for take in fresh.workspace.takes] == [good.id]
    assert '1 saved clip(s) not loaded' in fresh.status_text and paths['sidecar'].name in fresh.status_text


def test_a_file_with_no_audio_is_refused_when_it_is_imported(session, tmp_path):
    path = tmp_path / 'empty.wav'
    sf.write(str(path), np.zeros(0, np.float32), RATE)
    assert session.workspace.import_file(path) is None
    assert session.bus.notices[-1].title == 'Could not import' and 'empty.wav: the file holds no audio' in session.bus.notices[-1].text
    assert session.workspace.takes == []


# ---- discard ----------------------------------------------------------------------------------------------------------

def test_discard_sends_the_files_to_the_recycle_bin(session, recycle):
    record(session, 10.0)
    fill(session)
    session.keep()
    take = session.current
    files = [take.master_path, take.export_path, take.sidecar_path]
    assert session.workspace.discard() is True
    assert not any(path.exists() for path in files)
    assert sorted(recycle.sent[0]) == sorted(path.name for path in files)
    assert session.current is None and session.workspace.takes == []


def test_discarding_one_of_two_takes_removes_only_that_take(session):
    first = record(session, 10.0)
    second = record(session, 2.0)
    assert session.current is second
    session.workspace.discard()
    assert session.workspace.takes == [first]


def test_when_the_recycle_bin_is_unavailable_nothing_is_deleted_until_asked(session, recycle):
    take = record(session, 10.0)
    master = take.master_path
    recycle.fail = 'The Recycle Bin is not available on a network folder.'
    with pytest.raises(TrashUnavailable):
        session.workspace.discard()
    assert master.exists() and session.current is take
    assert session.workspace.discard(permanent=True) is True
    assert not master.exists() and session.workspace.takes == []


def test_re_record_replaces_the_take_and_starts_again(session, monkeypatch):
    from recorder import capture as cap
    fake = FakeCapture(speech(1.0))
    fake.recording = False
    monkeypatch.setattr(cap, 'open_capture', lambda source, device, pre_roll: fake)
    take = record(session, 5.0)
    assert session.re_record() is True
    assert take not in session.workspace.takes and session.capturing.recording


# ---- restarts ---------------------------------------------------------------------------------------------------------

def test_a_kept_take_comes_back_after_a_restart_with_its_edits_and_transcript(session, make_session):
    record(session, 14.0)
    fill(session)
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    session.workspace.confirm_transcript()
    session.keep()
    kept_id = session.current.id

    fresh = make_session()
    assert [t.id for t in fresh.workspace.takes] == [kept_id]
    loaded = fresh.workspace.takes[0]
    assert loaded.export_path.exists()
    assert loaded.history.edits[0]['region'] == 'start'
    assert loaded.meta['transcript'] == 'The harbour lights came on at dusk.'
    assert loaded.seconds == pytest.approx(14.0, abs=0.01)


def test_a_capture_that_was_never_kept_is_listed_again_from_masters(session, make_session):
    take = record(session, 10.0, source='system')
    fresh = make_session()
    assert [t.id for t in fresh.workspace.takes] == [take.id]
    assert fresh.workspace.takes[0].master_path.exists() and fresh.workspace.takes[0].source == 'system'


def test_reloading_the_list_does_not_list_a_take_twice(session):
    take = record(session, 10.0)
    session.workspace.load_speaker_takes()
    assert session.workspace.takes == [take]


def test_a_clip_that_cannot_be_loaded_is_named_in_the_status_and_left_alone(session):
    folder = session.lib.folder(session.slug)
    (folder / 'masters').mkdir(parents=True, exist_ok=True)
    broken = folder / 'broken.json'
    broken.write_text(json.dumps({'schema': 'voice-recorder-clip-v1', 'id': 'x', 'created': '2026-10-19T12:00:00-04:00',
                                  'source': 'mic', 'master': {'file': 'masters/none.raw.wav'},
                                  'export': {'file': 'none.wav'}}), encoding='utf-8')
    before = broken.read_text(encoding='utf-8')
    session.workspace.load_speaker_takes()
    assert 'saved clip(s) not loaded' in session.status_text
    assert 'broken.json: its master file is missing' in session.status_text
    assert broken.read_text(encoding='utf-8') == before


def test_a_damaged_speakers_file_does_not_stop_the_session_opening(make_session, tmp_path):
    clips = tmp_path / 'clips'
    (clips / 'bot' / 'masters').mkdir(parents=True)
    (clips / 'speakers.json').write_text('{"bot": ', encoding='utf-8')
    s = make_session(clips)
    assert s.workspace.speaker == 'bot'
    assert 'moved to' in s.lib.recovery_note and 'moved to' in s.status_text


def test_a_corrupt_original_is_skipped_and_valid_takes_load(session):
    take = record(session, 8.0)
    bad = take.master_path.with_name('20261020-120000_mic_deadbeef.raw.wav')
    bad.write_bytes(b'RIFF truncated WAV')
    session.workspace.load_speaker_takes()
    assert session.workspace.takes == [take]
    assert bad.name in session.status_text
    assert bad.read_bytes() == b'RIFF truncated WAV'


def test_a_corrupt_original_does_not_prevent_startup(session, make_session):
    folder = session.lib.folder(session.slug) / 'masters'
    folder.mkdir(parents=True, exist_ok=True)
    bad = folder / '20261020-120000_mic_deadbeef.raw.wav'
    bad.write_bytes(b'truncated')
    fresh = make_session()
    assert fresh.workspace.takes == [] and bad.name in fresh.status_text


# ---- importing --------------------------------------------------------------------------------------------------------

def test_importing_a_recording_copies_it_in_as_a_take(session, tmp_path):
    source = tmp_path / 'somewhere' / 'phone memo.wav'
    source.parent.mkdir()
    sf.write(str(source), speech(9.0), RATE)
    take = session.workspace.import_file(source)
    assert take is not None and take.source == 'import' and session.current is take
    assert take.master_path.exists() and take.master_path.parent.name == 'masters'
    assert source.exists()                              # the file it came from is left alone
    assert 'Imported phone memo.wav' in session.status_text


def test_a_file_that_is_not_audio_is_reported_not_raised(session, tmp_path):
    bad = tmp_path / 'notes.wav'
    bad.write_text('this is not audio', encoding='utf-8')
    assert session.workspace.import_file(bad) is None
    assert session.bus.notices[-1].level == 'error' and 'notes.wav' in session.bus.notices[-1].text


# ---- "ready to keep?" -------------------------------------------------------------------------------------------------

def test_the_checklist_lists_what_is_still_missing(session):
    record(session, 10.0)
    steps = {step.key: step for step in session.workspace.readiness().steps}
    assert not session.workspace.readiness().ready
    assert steps['length'].done is False and 'measuring' in steps['length'].detail
    for key in ('transcript', 'origin', 'allowed_use', 'permission'):
        assert steps[key].done is False


def test_the_checklist_is_ready_when_every_rule_is_met(session):
    take = record(session, 10.0)
    fill(session)
    assert session.measure.derived(take) is None        # measuring starts in the background
    session.tick()
    ready = session.workspace.readiness(session.measure.derived(take))
    assert ready.ready, [step for step in ready.steps if not step.done]


def test_a_clip_that_is_too_short_is_not_ready(session):
    take = record(session, 3.0)
    fill(session)
    session.measure.derived(take)
    session.tick()
    ready = session.workspace.readiness(session.measure.derived(take))
    assert not ready.ready
    assert [step.key for step in ready.steps if not step.done] == ['length']


def test_the_checklist_carries_advice_that_does_not_block_keep(session):
    take = record(session, samples=bursts(30.0, [(1.0, 29.0)], amp=0.02))
    fill(session)
    session.measure.derived(take)
    session.tick()
    ready = session.workspace.readiness(session.measure.derived(take))
    assert ready.ready
    keys = [item.key for item in ready.advice]
    assert 'target' in keys and 'level' in keys                      # long, and well below the speech target


def test_a_draft_and_noise_cleaning_are_advice(session):
    take = record(session, 12.0)
    session.drafts.receive(take, 'The harbour lights came on at dusk.', None)
    session.editor.set_denoise(True)
    keys = [item.key for item in session.workspace.readiness().advice]
    assert 'draft' in keys and 'denoise' in keys
    session.workspace.set_meta(transcript='I rewrote the draft myself.')
    assert 'draft' not in [item.key for item in session.workspace.readiness().advice]


def test_removing_words_asks_for_the_transcript_to_be_checked(session):
    take = record(session, 12.0)
    fill(session)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    session.measure.derived(take)
    session.tick()
    steps = {step.key: step for step in session.workspace.readiness(session.measure.derived(take)).steps}
    assert steps['checked'].done is False
    session.workspace.confirm_transcript()
    steps = {step.key: step for step in session.workspace.readiness(session.measure.derived(take)).steps}
    assert steps['checked'].done is True


# ---- a take must never be lost ----------------------------------------------------------------------------------------

def test_a_failed_write_on_stop_keeps_the_take_and_keep_writes_it_later(session, monkeypatch):
    state = {'disk_full': True}
    write_master = session.lib.save_master

    def save_master(slug, take):
        if state['disk_full']:
            raise OSError(28, 'No space left on device')
        return write_master(slug, take)

    monkeypatch.setattr(session.lib, 'save_master', save_master)
    session.capturing.capture = FakeCapture(speech(8.0))
    session.capturing.finish_take()
    take = session.workspace.takes[0]
    assert take.unsaved and 'No space left' in take.save_error
    assert 'could not be written' in session.status_text
    session.workspace.load_speaker_takes()
    assert take in session.workspace.takes
    state['disk_full'] = False
    session.workspace.open_take(take)
    fill(session)
    session.keep()
    assert not take.unsaved and take.master_path.exists() and take.export_path.exists()


def test_close_saves_a_running_take(session):
    session.capturing.capture = FakeCapture(speech(2.0))
    assert session.close() == []
    assert session.closed and len(session.workspace.takes) == 1
    assert session.workspace.takes[0].master_path.is_file()


def test_a_failed_save_keeps_the_session_open_and_a_later_close_retries(session, monkeypatch):
    save_master = session.lib.save_master
    fail_writes(monkeypatch, session.lib)
    session.capturing.capture = FakeCapture(speech(2.0))
    failures = session.close()
    assert failures and 'disk full' in failures[0] and not session.closed
    take = session.workspace.takes[0]
    assert take.unsaved
    monkeypatch.setattr(session.lib, 'save_master', save_master)
    assert session.close() == []
    assert session.closed and not take.unsaved and take.master_path.is_file()


def test_closing_twice_is_harmless(session):
    assert session.close() == [] and session.close() == []


def test_an_unsaved_take_and_its_edits_survive_switching_speakers(session, monkeypatch):
    with monkeypatch.context() as failure:
        fail_writes(failure, session.lib)
        take = record(session, 12.0)
    fill(session)
    session.editor.set_selection(0, 1)
    session.editor.cut_selection()
    session.workspace.add_speaker('Other Speaker')
    assert take not in session.workspace.takes
    session.workspace.select_speaker(session.slug)
    assert session.workspace.takes == [take] and session.current is take and take.unsaved
    assert take.history.edits and take.meta['origin'] == 'recorded in my room'
    session.workspace.confirm_transcript()
    session.keep()
    assert take.master_path.is_file() and take.export_path.is_file() and not take.unsaved


def test_close_recovers_unsaved_takes_from_a_hidden_speaker(session, monkeypatch):
    with monkeypatch.context() as failure:
        fail_writes(failure, session.lib)
        take = record(session, 2.0)
    session.workspace.add_speaker('Other Speaker')
    assert session.close() == []
    assert session.closed and not take.unsaved and take.master_path.is_file()


def test_changing_folder_and_back_keeps_each_folders_session_takes_apart(session, tmp_path, monkeypatch):
    original_root = session.lib.root
    with monkeypatch.context() as failure:
        fail_writes(failure, session.lib)
        first = record(session, 10.0)
    assert session.workspace.set_root(tmp_path / 'other-clips')
    session.workspace.add_speaker('Smoke Speaker')
    second = record(session, 8.0)
    assert first not in session.workspace.takes
    assert session.workspace.set_root(original_root)
    assert session.workspace.takes == [first] and first.unsaved and second not in session.workspace.takes


def test_a_failed_close_also_protects_an_unsaved_take_in_another_library(session, tmp_path, monkeypatch):
    original = session.lib.root.resolve()
    write_master = store.Library._write_master
    blocked = [True]

    def write(path, samples, rate):
        if blocked[0] and path.is_relative_to(original):
            raise OSError('original folder unavailable')
        return write_master(path, samples, rate)

    monkeypatch.setattr(store.Library, '_write_master', staticmethod(write))
    take = record(session, 2.0)
    assert take.unsaved
    session.workspace.set_root(tmp_path / 'other-library')
    failures = session.close()
    assert failures and 'original folder unavailable' in failures[0]
    assert not session.closed and take.unsaved
    blocked[0] = False
    assert session.close() == []
    assert session.closed and not take.unsaved and take.master_path.is_relative_to(original)


def test_a_registry_read_failure_keeps_the_recording_recoverable(session, monkeypatch):
    with monkeypatch.context() as failure:
        failure.setattr(session.lib, 'speaker_defaults', lambda *a: (_ for _ in ()).throw(OSError('registry locked')))
        session.capturing.capture = FakeCapture(speech(2.0))
        session.capturing.finish_take()
    take = session.workspace.takes[0]
    assert take.unsaved and 'registry locked' in take.save_error
    assert session.close() == []
    assert session.closed and take.master_path.is_file()


def test_a_recording_keeps_its_speaker_and_output_folder(session, tmp_path):
    other = session.lib.add_speaker('Other Speaker')
    session.workspace.refresh_speakers(select=session.slug)
    session.capturing.capture = FakeCapture(speech(2.0))
    original = session.lib.root
    assert session.workspace.select_speaker(other) is False
    assert session.workspace.speaker == session.slug and 'Stop the recording' in session.status_text
    assert session.workspace.set_root(tmp_path / 'other-library') is False
    assert session.lib.root == original and session.capturing.recording
    session.capturing.finish_take()
    assert session.current.master_path.parent.parent == session.lib.folder(session.slug)


def test_the_output_folder_is_remembered_between_runs(session, make_session, tmp_path):
    chosen = tmp_path / 'chosen-folder'
    assert session.workspace.set_root(chosen)
    assert json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))['root'] == str(chosen)
