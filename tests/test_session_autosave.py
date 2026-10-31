"""Autosave in the session: when the work on a take is written, what a restart brings back, and what is never saved."""
import json

from helpers import fill, record
from recorder.session import autosave as autosave_mod


def autosave_files(session):
    folder = session.lib.folder(session.workspace.speaker) / 'masters'
    return sorted(folder.glob('*.autosave.json'))


def cut(session, a=1.0, b=2.0):
    session.editor.set_selection(a, b)
    session.editor.cut_selection()


def settle(session, clock, seconds=autosave_mod.QUIET_SECONDS + 0.5):
    """Let the take stay still for a while, as it does when nobody is touching it."""
    clock.advance(seconds)
    session.tick()


# ---- when the work is written ----------------------------------------------------------------------------------------

def test_a_take_nobody_touched_has_no_autosave_file(session, clock):
    record(session, 12.0)
    settle(session, clock, 30)
    assert autosave_files(session) == []


def test_an_edit_is_written_once_the_take_has_been_still_for_a_moment(session, clock):
    take = record(session, 12.0)
    cut(session)
    session.tick()
    assert autosave_files(session) == []                       # a burst of edits is one write, not many
    cut(session, 3.0, 4.0)
    clock.advance(autosave_mod.QUIET_SECONDS * 0.6)
    session.tick()
    assert autosave_files(session) == []                       # the second edit started the wait again
    settle(session, clock)
    [path] = autosave_files(session)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert path == session.lib.autosave_file(take)
    assert [(e['start_seconds'], e['end_seconds']) for e in data['edits']] == [(1.0, 2.0), (3.0, 4.0)]
    assert session.autosave.waiting == []


def test_steady_typing_is_still_written_every_few_seconds(session, clock):
    take = record(session, 12.0)
    for step in range(8):                                      # a key every 0.9 s: the take is never still for 1 s
        session.workspace.set_meta(transcript='word ' * (step + 1))
        clock.advance(0.9)
        session.tick()
        if autosave_files(session):
            break
    assert step * 0.9 < autosave_mod.LONGEST_WAIT_SECONDS + 1.0, 'it waited far too long'
    assert json.loads(autosave_files(session)[0].read_text(encoding='utf-8'))['transcript'].startswith('word')
    assert take.meta['transcript']


def test_opening_another_take_saves_the_one_left_at_once(session):
    first = record(session, 12.0)
    second = record(session, 12.0)
    session.workspace.open_take(first)
    cut(session)
    assert autosave_files(session) == []
    session.workspace.open_take(second)                        # no time has passed at all
    assert autosave_files(session) == [session.lib.autosave_file(first)]


def test_closing_saves_whatever_is_waiting(session):
    take = record(session, 12.0)
    fill(session)
    cut(session)
    assert autosave_files(session) == []
    assert session.close() == []
    assert json.loads(session.lib.autosave_file(take).read_text(encoding='utf-8'))['transcript'].startswith('The harbour')


def test_a_text_field_changed_by_the_draft_of_a_take_not_in_front_is_saved_for_that_take(session, clock):
    first = record(session, 12.0)
    second = record(session, 12.0)                             # the one in front
    session.drafts.receive(first, 'A draft from the audio.', None)
    settle(session, clock)
    data = json.loads(session.lib.autosave_file(first).read_text(encoding='utf-8'))
    assert data['draft_text'] == data['transcript'] == 'A draft from the audio.' and data['transcript_status'] == 'asr_draft'
    assert not session.lib.autosave_file(second).exists()


# ---- what a restart brings back ---------------------------------------------------------------------------------------

def test_a_restart_brings_back_the_edits_and_the_text_but_not_the_permission_tick(session, make_session):
    take = record(session, 14.0)
    fill(session)                                              # the transcript, origin, allowed use and the tick
    session.workspace.set_meta(label='Greeting', notes='second try')
    cut(session, 0.0, 1.0)
    session.editor.set_chain('highpass', 'High-pass on', enabled=True)
    assert session.close() == []

    fresh = make_session()
    [back] = fresh.workspace.takes
    assert back.id == take.id and back.restored and fresh.workspace.current is back
    assert back.history.edits == take.history.edits and back.history.chain['highpass']['enabled'] is True
    assert back.meta['transcript'] == 'The harbour lights came on at dusk.' and back.meta['label'] == 'Greeting'
    assert back.meta['origin'] == 'recorded in my room' and back.meta['notes'] == 'second try'
    assert back.needs_transcript_review is True                # the words were cut, and nobody has checked them since
    assert back.meta['consent_confirmed'] is False
    assert 'Picked up your saved edits and text on 1 take.' in fresh.status_text
    steps = {s.key: s.done for s in fresh.workspace.readiness().steps}
    assert steps['permission'] is False and steps['checked'] is False

    fresh.workspace.confirm_transcript()                       # and it can be finished from there
    fresh.workspace.set_meta(consent_confirmed=True)
    paths = fresh.keep()
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['label'] == 'Greeting' and data['edits'][0]['end_seconds'] == 1.0 and 'edited' in data['flags']
    assert not list(paths['master'].parent.glob('*.autosave.json'))


def test_a_crash_loses_only_the_last_moment(session, make_session, clock):
    """The program is killed: no close, no flush. What had been written is there on the next start."""
    take = record(session, 14.0)
    fill(session)
    cut(session, 0.0, 1.0)
    settle(session, clock)                                     # written
    cut(session, 5.0, 6.0)                                     # a moment later, never written: the crash comes first
    fresh = make_session()                                     # the first session is simply abandoned
    [back] = fresh.workspace.takes
    assert back.id == take.id and back.restored
    assert [(e['start_seconds'], e['end_seconds']) for e in back.history.edits] == [(0.0, 1.0)]
    assert back.meta['transcript'] == 'The harbour lights came on at dusk.' and back.meta['origin'] == 'recorded in my room'


def test_the_pick_up_message_is_not_repeated_when_the_list_is_loaded_again(session, make_session):
    record(session, 12.0)
    cut(session)
    session.close()
    fresh = make_session()
    assert 'Picked up' in fresh.status_text
    fresh.bus.status('Ready.')
    fresh.workspace.load_speaker_takes()                       # the take is in memory now, not read back
    assert fresh.status_text == 'Ready.'


def test_an_autosave_file_that_cannot_be_used_is_named_and_left_alone(session, make_session):
    take = record(session, 12.0)
    cut(session)
    assert session.close() == []
    path = session.lib.autosave_file(take)
    path.write_text('{"schema": ', encoding='utf-8')
    fresh = make_session()
    [back] = fresh.workspace.takes
    assert back.history.edits == [] and not back.restored
    assert 'could not be used' in fresh.status_text and path.name in fresh.status_text
    assert path.read_text(encoding='utf-8') == '{"schema": '


# ---- what is not saved, and what goes wrong ---------------------------------------------------------------------------

def test_a_discarded_take_is_not_written_again(session, clock):
    record(session, 12.0)
    cut(session)
    session.workspace.discard()                                # while its edit was still waiting
    settle(session, clock)
    assert autosave_files(session) == [] and session.autosave.waiting == []


def test_keeping_removes_the_autosave_file_and_a_kept_take_is_not_autosaved_again(session, clock):
    take = record(session, 14.0)
    fill(session)
    cut(session, 0.0, 1.0)
    session.workspace.confirm_transcript()
    settle(session, clock)
    assert autosave_files(session) == [session.lib.autosave_file(take)]
    session.keep()
    assert autosave_files(session) == []
    cut(session, 2.0, 3.0)                                     # a kept take edited again waits for Keep, as before
    settle(session, clock)
    assert autosave_files(session) == []


def test_a_new_label_on_a_kept_take_goes_into_its_sidecar(session, clock):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    session.workspace.set_meta(label='Greeting')
    assert 'label' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    settle(session, clock)
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['label'] == 'Greeting'
    assert autosave_files(session) == []
    session.workspace.set_meta(label='')
    settle(session, clock)
    assert 'label' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


def test_a_failed_write_is_reported_once_and_tried_again_later(session, clock, monkeypatch):
    record(session, 12.0)
    calls = []
    real = session.lib.autosave

    def broken(take):
        calls.append(take)
        raise OSError('disk full')

    monkeypatch.setattr(session.lib, 'autosave', broken)
    cut(session)
    settle(session, clock)
    assert len(calls) == 1 and 'Could not save your changes' in session.status_text and 'disk full' in session.status_text
    session.bus.status('Ready.')
    settle(session, clock, 2.0)
    assert len(calls) == 1                                     # too soon to try again
    settle(session, clock, autosave_mod.RETRY_SECONDS)
    assert len(calls) == 2 and session.status_text == 'Ready.'     # tried again, and did not say it a second time
    monkeypatch.setattr(session.lib, 'autosave', real)
    settle(session, clock, autosave_mod.RETRY_SECONDS)
    assert len(autosave_files(session)) == 1 and session.autosave.waiting == []


def test_closing_after_a_failed_write_warns_once_and_then_lets_go(session, monkeypatch):
    record(session, 12.0)
    cut(session)
    monkeypatch.setattr(session.lib, 'autosave', lambda take: (_ for _ in ()).throw(OSError('disk full')))
    failures = session.close()
    assert failures and 'disk full' in failures[0] and 'could not be saved' in failures[0] and not session.closed
    assert 'Close again' in session.status_text
    assert session.close() == [] and session.closed            # a folder that stays broken must not trap the window


def test_a_take_whose_original_is_not_on_disk_waits_and_is_written_after_the_close_saves_it(session, monkeypatch):
    real = session.lib.save_master
    monkeypatch.setattr(session.lib, 'save_master', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    take = record(session, 12.0)
    assert take.unsaved
    cut(session)
    session.tick()
    assert session.autosave.waiting == [take] and autosave_files(session) == []
    monkeypatch.setattr(session.lib, 'save_master', real)
    assert session.close() == []
    assert not take.unsaved and [p.name for p in autosave_files(session)] == [session.lib.autosave_file(take).name]


def test_something_that_cannot_be_written_as_json_is_handled_like_a_failed_write(session, clock):
    take = record(session, 12.0)
    take.device = {'name': object()}                           # not something a file can hold
    cut(session)
    settle(session, clock)                                     # must not raise, and must not complain on every tick
    assert 'Could not save your changes' in session.status_text and session.autosave.waiting == [take]
    session.bus.status('Ready.')
    for _ in range(5):
        settle(session, clock, 1.0)
    assert session.status_text == 'Ready.' and autosave_files(session) == []


def test_the_session_ticks_autosave_even_when_another_part_fails(session, clock, monkeypatch):
    record(session, 12.0)
    cut(session)
    monkeypatch.setattr(session.transport, 'tick', lambda: (_ for _ in ()).throw(RuntimeError('player lost')))
    settle(session, clock)
    assert len(autosave_files(session)) == 1
