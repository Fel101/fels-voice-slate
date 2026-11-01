"""Moving a take to another speaker, through the session: the lists, the open take, the saved work and the status line."""
import json
from pathlib import Path

import pytest

from helpers import fill, record
from recorder.store import Take


@pytest.fixture
def two(session):
    """The session's speaker and a second one; the first stays selected."""
    first = session.slug
    other = session.workspace.add_speaker('Other Speaker')
    session.workspace.select_speaker(first)
    return first, other


def listed(session):
    return list(session.workspace.takes)


def test_the_open_take_moves_and_the_next_one_opens(session, two):
    first, other = two
    older, newer = record(session), record(session)
    assert listed(session) == [newer, older] and session.current is newer
    left = session.workspace.move_take(other)
    assert left == [] and listed(session) == [older] and session.current is older
    assert newer.speaker == other and 'Moved to Other Speaker.' in session.status_text
    session.workspace.select_speaker(other)
    assert listed(session) == [newer]
    assert newer.master_path.parent == session.lib.folder(other) / 'masters' and newer.master_path.is_file()
    assert not (session.lib.folder(first) / 'masters' / newer.master_path.name).exists()


def test_the_take_keeps_its_undo_history_and_its_text_after_the_move(session, two):
    first, other = two
    take = record(session)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    session.workspace.set_meta(transcript='Typed before the move.', label='Moved one')
    session.workspace.move_take(other)
    session.workspace.select_speaker(other)
    [same] = listed(session)
    assert same is take and session.current is take
    assert take.history.can_undo and take.meta['transcript'] == 'Typed before the move.'
    session.editor.undo()
    assert not take.history.edits and take.history.can_redo


def test_moving_a_take_that_is_not_open_leaves_the_open_one_alone(session, two):
    first, other = two
    older, newer = record(session), record(session)
    session.workspace.move_take(other, older)
    assert listed(session) == [newer] and session.current is newer


def test_moving_the_only_take_leaves_nothing_open(session, two):
    first, other = two
    take = record(session)
    session.workspace.move_take(other)
    assert listed(session) == [] and session.current is None and take.speaker == other


def test_the_next_take_opened_is_the_one_now_at_the_same_place(session, two):
    first, other = two
    takes = [record(session) for _ in range(4)]                  # listed newest first: takes[3], [2], [1], [0]
    session.workspace.open_take(takes[2])
    session.workspace.move_take(other)
    assert session.current is takes[1]                           # the one that took its place
    session.workspace.open_take(takes[0])
    session.workspace.move_take(other)
    assert session.current is takes[1] and listed(session) == [takes[3], takes[1]]
    session.workspace.open_take(takes[1])
    session.workspace.move_take(other)
    assert session.current is takes[3]                           # the list's last, when the moved one was last


def test_a_kept_clip_moves_and_is_still_kept(session, two):
    first, other = two
    take = record(session)
    fill(session)
    session.keep()
    old = take.export_path
    session.workspace.move_take(other)
    assert take.kept and take.export_path != old and not old.exists() and take.speaker == other
    session.workspace.select_speaker(other)
    [back] = listed(session)
    assert back.kept and session.workspace.state_of(back) == 'kept'
    data = json.loads(Path(back.sidecar_path).read_text(encoding='utf-8'))
    assert data['speaker'] == other and data['moves'][0]['from'] == first


def test_work_not_yet_written_goes_with_the_take(session, two, clock):
    """The edit was made a moment ago and has not reached the autosave file; the move saves what the take holds now."""
    first, other = two
    take = record(session)
    session.editor.set_selection(1.0, 2.0)
    session.editor.cut_selection()
    assert session.autosave.waiting
    session.workspace.move_take(other)
    data = json.loads(session.lib.autosave_file(take).read_text(encoding='utf-8'))
    assert len(data['edits']) == 1 and session.lib.autosave_file(take).parent.parent.name == other
    clock.advance(10)
    session.tick()
    assert [p.name for p in (session.lib.folder(first) / 'masters').glob('*')] == []        # nothing came back to the old place


def test_the_moved_take_is_where_a_restart_finds_it(make_session, session, two):
    first, other = two
    take = record(session)
    session.editor.set_selection(1.0, 2.0)
    session.editor.cut_selection()
    session.workspace.set_meta(transcript='Words typed.')
    session.workspace.move_take(other)
    assert session.close() == []
    again = make_session(session.lib.root)
    again.workspace.select_speaker(first)
    assert again.workspace.takes == []
    again.workspace.select_speaker(other)
    [back] = again.workspace.takes
    assert back.id == take.id and back.restored and back.meta['transcript'] == 'Words typed.' and back.history.edits


def test_a_move_is_refused_while_recording(session, two, open_fake):
    first, other = two
    take = record(session)
    session.capturing.start()
    assert session.workspace.move_take(other, take) is None
    assert take.speaker == first and 'Stop the recording' in session.status_text and listed(session)[-1] is take


def test_a_take_with_no_file_on_disk_is_refused_and_stays_listed(session, two):
    first, other = two
    take = Take(source='mic', samples=record(session).samples, rate=48000, speaker=first, unsaved=True)
    session.workspace.takes.insert(0, take)
    with pytest.raises(ValueError, match='not saved on disk'):
        session.workspace.move_take(other, take)
    assert take in listed(session) and take.speaker == first


def test_nothing_happens_without_a_take(session, two):
    first, other = two
    assert session.workspace.move_take(other) is None


def test_a_speaker_that_is_gone_is_refused(session, two):
    record(session)
    with pytest.raises(ValueError, match='not in this folder'):
        session.workspace.move_take('nobody')
    assert len(listed(session)) == 1


def test_files_left_in_the_old_place_are_named_in_the_status_line(session, two, monkeypatch):
    first, other = two
    record(session)
    fill(session)
    session.keep()
    sidecar = session.current.sidecar_path
    real_unlink = Path.unlink

    def locked(self, missing_ok=False):
        if self == sidecar:
            raise PermissionError(13, 'in use')
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', locked)
    left = session.workspace.move_take(other)
    monkeypatch.undo()
    assert sidecar in left and f'{sidecar.name}' in session.status_text and 'could not be removed' in session.status_text


def test_the_takes_and_current_topics_are_announced(session, two):
    first, other = two
    record(session)
    heard = []
    session.bus.listen(lambda topic, payload: heard.append(topic))
    session.workspace.move_take(other)
    assert 'takes' in heard and 'current' in heard
    heard.clear()
    record(session)
    record(session)
    heard.clear()
    session.workspace.move_take(other, listed(session)[1])           # not the open one
    assert 'takes' in heard and 'current' not in heard
