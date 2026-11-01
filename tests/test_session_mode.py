"""Long sessions: the script remembered with its progress, moving from take to take, and many takes at once."""
import json

import pytest

from helpers import Clock, fill, record, settle
from recorder.session.batch import SLICE_SECONDS
from recorder.session.script import Script
from recorder.store import TrashUnavailable

SCRIPT = 'The ferry left at dawn.\nThe gulls followed it out.\nBy noon the sky was clear.\n'


def kept_line(session, seconds=12.0):
    """Record the line in front and keep it."""
    take = record(session, seconds, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    return take


# ---- the script is remembered -------------------------------------------------------------------------------------------

def test_the_script_and_the_line_in_front_come_back_after_a_restart(session, make_session):
    session.script.load_text(SCRIPT)
    session.script.step(2)
    again = make_session()
    assert again.script.lines == session.script.lines and again.script.index == 2


def test_clearing_the_script_forgets_it(session, make_session):
    session.script.load_text(SCRIPT)
    session.script.clear()
    assert make_session().script.lines == []


def test_the_script_is_kept_beside_the_settings(session):
    session.script.load_text(SCRIPT)
    assert session.script.path.parent == session.settings_file.parent
    assert json.loads(session.script.path.read_text(encoding='utf-8'))['lines'][0] == 'The ferry left at dawn.'


@pytest.mark.parametrize('text', ['not json', '[1, 2]', '{"schema": "other", "lines": ["x"]}',
                                  '{"schema": "voice-slate-script-v1", "lines": [7]}',
                                  '{"schema": "voice-slate-script-v1", "lines": []}'])
def test_a_script_file_that_cannot_be_used_is_ignored(tmp_path, session, text):
    path = tmp_path / 'script.json'
    path.write_text(text, encoding='utf-8')
    assert Script(session.bus, path).lines == []


def test_a_line_number_out_of_range_goes_back_to_the_first_line(tmp_path, session):
    path = tmp_path / 'script.json'
    path.write_text('{"schema": "voice-slate-script-v1", "index": 9, "lines": ["one", "two"]}', encoding='utf-8')
    script = Script(session.bus, path)
    assert script.index == 0 and script.current == 'one'


def test_a_place_that_cannot_be_saved_is_said_once_and_harms_nothing(tmp_path, session):
    blocked = tmp_path / 'script.json'
    blocked.mkdir()
    script = Script(session.bus, blocked)
    script.load_text(SCRIPT)
    assert 'could not be saved' in session.status_text and script.current == 'The ferry left at dawn.'
    session.bus.status('Something else.')
    script.step(1)
    assert session.status_text == 'Something else.' and script.index == 1


# ---- progress --------------------------------------------------------------------------------------------------------------

def test_progress_counts_the_takes_and_the_kept_clips_of_each_line(session):
    session.script.load_text(SCRIPT)
    record(session, 12.0, script_line=session.script.current)
    kept_line(session)
    progress = session.script.progress(session.workspace.takes)
    assert [(line.number, line.takes, line.kept) for line in progress] == [(1, 2, 1), (2, 0, 0), (3, 0, 0)]


def test_progress_survives_a_restart(session, make_session):
    session.script.load_text(SCRIPT)
    kept_line(session)
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert [line.kept for line in again.script.progress(again.workspace.takes)] == [1, 0, 0]


def test_a_clip_kept_before_the_line_was_recorded_counts_by_its_transcript(session, make_session):
    session.script.load_text(SCRIPT)
    take = kept_line(session)
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    del data['script_line']
    take.sidecar_path.write_text(json.dumps(data), encoding='utf-8')
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert [line.kept for line in again.script.progress(again.workspace.takes)] == [1, 0, 0]


def test_keep_moves_past_the_lines_that_have_a_kept_clip(session):
    session.script.load_text(SCRIPT)
    session.script.go_to(1)
    kept_line(session)
    session.script.go_to(0)
    kept_line(session)
    assert session.script.index == 2


def test_the_last_line_says_so_when_every_line_has_a_clip(session):
    session.script.load_text(SCRIPT)
    for _ in range(3):
        kept_line(session)
    assert 'Every line of the script' in session.status_text


def test_next_line_to_record_goes_round_to_the_lines_left_at_the_start(session):
    session.script.load_text(SCRIPT)
    session.script.go_to(1)
    kept_line(session)
    assert session.script.index == 2
    kept_line(session)
    assert session.script.index == 0


def test_there_is_no_next_line_when_every_line_has_a_clip(session):
    session.script.load_text(SCRIPT)
    for _ in range(3):
        kept_line(session)
    assert session.script.next_to_record(session.workspace.takes) is None
    assert session.script.go_to_next(session.workspace.takes) is False


# ---- many takes at once ----------------------------------------------------------------------------------------------------

def run(session, clock=None):
    """Tick until the batch has finished."""
    for _ in range(1000):
        if not session.batch.running:
            return
        session.tick()
    pytest.fail('the batch never finished')


def many(session, count=3, **meta):
    takes = [record(session, 12.0) for _ in range(count)]
    for take in takes:
        take.meta.update(transcript='Words spoken in the take.', **meta)
    return takes


def test_keeping_many_takes_writes_every_one(session):
    takes = many(session, origin='recorded here', allowed_use='my own projects')
    session.batch.keep(takes)
    assert session.batch.running
    run(session)
    assert all(take.kept for take in takes) and session.status_text == 'Kept 3 of 3 takes.'


def test_the_statement_goes_on_each_clip_with_how_many_it_covered(session):
    takes = many(session, origin='recorded here', allowed_use='my own projects')
    session.batch.keep(takes)
    run(session)
    data = [json.loads(take.sidecar_path.read_text(encoding='utf-8')) for take in takes]
    assert all(d['consent_confirmed'] is True and d['consent_batch'] == 3 for d in data)
    assert len({d['consent_confirmed_at'] for d in data}) == 1


def test_a_single_keep_has_no_batch_note(session):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    assert 'consent_batch' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


def test_ticking_the_box_for_one_take_replaces_the_batch_note(session):
    take = many(session, 1, origin='recorded here', allowed_use='my own projects')[0]
    session.batch.keep([take])
    run(session)
    session.workspace.open_take(take)
    session.workspace.set_meta(allowed_use='something else')
    assert take.meta['consent_confirmed'] is False and 'consent_batch' not in take.meta
    session.workspace.set_meta(consent_confirmed=True)
    session.keep()
    assert 'consent_batch' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


def test_words_typed_for_the_batch_fill_only_the_takes_that_have_none(session):
    mine, blank = many(session, 2)
    mine.meta.update(origin='my own words', allowed_use='my own use')
    session.batch.keep([mine, blank], 'typed origin', 'typed use')
    run(session)
    assert mine.meta['origin'] == 'my own words' and blank.meta['origin'] == 'typed origin'
    assert blank.meta['allowed_use'] == 'typed use' and mine.kept and blank.kept


def test_a_take_with_no_origin_and_no_words_typed_is_left_out_and_named(session):
    fine, blank = many(session, 2)
    fine.meta.update(origin='recorded here', allowed_use='my own projects', label='The good one')
    blank.meta.update(label='The blank one')
    session.batch.keep([fine, blank])
    run(session)
    assert fine.kept and not blank.kept
    notice = session.bus.notices[-1]
    assert notice.level == 'warning' and 'The blank one' in notice.text and 'origin' in notice.text


def test_a_take_left_out_keeps_no_statement(session):
    """Permission is never remembered: a Keep that did not happen leaves the box as it was."""
    blank = many(session, 1)[0]
    session.batch.keep([blank])
    run(session)
    assert blank.meta['consent_confirmed'] is False and blank.meta['consent_confirmed_at'] == ''
    assert 'consent_batch' not in blank.meta


def test_a_take_too_short_for_its_purpose_is_left_out(session):
    short = record(session, 2.0)
    short.meta.update(transcript='Too short.', origin='recorded here', allowed_use='my own projects')
    session.batch.keep([short])
    run(session)
    assert not short.kept and '6 s minimum' in session.bus.notices[-1].text


def test_a_failed_write_leaves_that_take_out_and_goes_on(session, monkeypatch):
    first, second = many(session, 2, origin='recorded here', allowed_use='my own projects')
    real = session.lib.keep

    def keep(slug, take):
        if take is first:
            raise OSError('the disk is full')
        return real(slug, take)

    monkeypatch.setattr(session.lib, 'keep', keep)
    session.batch.keep([first, second])
    run(session)
    assert not first.kept and second.kept and 'the disk is full' in session.bus.notices[-1].text


def test_a_long_job_stops_when_asked_and_what_was_done_stays(make_session):
    clock = Clock()
    session = make_session()
    session.workspace.add_speaker('Someone')
    session.batch.clock = clock
    takes = many(session, 3, origin='recorded here', allowed_use='my own projects')
    real = session.lib.keep

    def slow(slug, take):
        clock.advance(SLICE_SECONDS * 2)
        return real(slug, take)

    session.lib.keep = slow
    session.batch.keep(takes)
    session.tick()
    assert sum(take.kept for take in takes) == 1 and session.batch.running
    session.batch.cancel()
    assert not session.batch.running and sum(take.kept for take in takes) == 1
    assert session.status_text == 'Stopped. Kept 1 of 3 takes.'
    session.tick()
    assert sum(take.kept for take in takes) == 1


def test_the_window_is_told_how_far_it_has_got(make_session):
    clock = Clock()
    session = make_session()
    session.workspace.add_speaker('Someone')
    session.batch.clock = clock
    takes = many(session, 3, origin='recorded here', allowed_use='my own projects')
    real = session.lib.keep

    def slow(slug, take):
        clock.advance(SLICE_SECONDS * 2)
        return real(slug, take)

    session.lib.keep = slow
    session.batch.keep(takes)
    session.tick()
    assert session.status_text == 'Keeping takes: 1 of 3...'
    run(session)


def test_closing_the_program_stops_a_batch(session):
    takes = many(session, 2, origin='recorded here', allowed_use='my own projects')
    session.batch.keep(takes)
    assert session.close() == [] and not session.batch.running


def test_discarding_many_takes_sends_them_to_the_bin(session, recycle):
    takes = many(session, 3)
    masters = [take.master_path for take in takes]
    session.batch.discard(takes)
    run(session)
    assert session.workspace.takes == [] and session.current is None
    assert not any(path.exists() for path in masters) and sum(len(sent) for sent in recycle.sent) >= 3


def test_discard_stops_when_the_bin_cannot_take_them_and_deletes_nothing_for_good(session, monkeypatch):
    takes = many(session, 3)

    def refuse(paths):
        raise TrashUnavailable('there is no Recycle Bin on this drive')

    monkeypatch.setattr(session.lib, 'trash', refuse)
    session.batch.discard(takes)
    run(session)
    assert len(session.workspace.takes) == 3 and all(take.master_path.exists() for take in takes)
    assert 'no Recycle Bin' in session.bus.notices[-1].text


def test_moving_many_takes_files_them_under_the_other_speaker(session):
    takes = many(session, 3)
    first = session.workspace.speaker
    second = session.workspace.add_speaker('Other')
    session.workspace.select_speaker(first)
    session.batch.move(takes, second)
    run(session)
    assert session.workspace.takes == [] and all(take.speaker == second for take in takes)
    assert session.status_text == 'Moved 3 of 3 takes to Other.'


# ---- a recording made while the list is busy ----------------------------------------------------------------------------------

def test_settle_delivers_what_a_batch_asked_for(session):
    takes = many(session, 1, origin='recorded here', allowed_use='my own projects')
    session.batch.keep(takes)
    run(session)
    settle(session)
    assert session.batch.left_out == []
