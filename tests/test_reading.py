"""The reading check, the words of a take and splitting a take at its pauses."""
import json

import numpy as np
import pytest

from helpers import bursts, fill, record, settle
from recorder import transcript
from recorder.core import analysis, reading
from recorder.session.workspace import reading_of


def states(check):
    return [(word.text, word.state) for word in check.words]


# ---- comparing ---------------------------------------------------------------------------------------------------------------

def test_a_line_read_as_written_has_no_differences():
    check = reading.compare('The ferry left at dawn.', 'The ferry left at dawn.')
    assert reading.differences(check) == 0 and (check.same, check.total) == (5, 5)


def test_capitals_and_punctuation_are_not_differences():
    assert reading.differences(reading.compare('Well, then!', 'well then')) == 0
    assert reading.differences(reading.compare("It's “fine”.", 'it’s fine')) == 0


def test_a_word_read_as_another_is_marked_with_what_was_heard():
    check = reading.compare('The ferry left at dawn', 'The ferry left at noon')
    assert check.words[-1].state == 'changed' and check.words[-1].text == 'dawn' and check.words[-1].heard == 'noon'
    assert (check.same, check.total) == (4, 5)


def test_a_missed_word_is_marked():
    assert states(reading.compare('The old ferry left', 'The ferry left')) == [('The', 'same'), ('old', 'missed'),
                                                                               ('ferry', 'same'), ('left', 'same')]


def test_an_added_word_is_marked():
    check = reading.compare('The ferry left', 'The big ferry left')
    assert ('big', 'added') in states(check) and check.total == 3


def test_more_words_replaced_than_written_keeps_the_rest_as_added():
    check = reading.compare('at dawn', 'at the break of day')
    assert ('dawn', 'changed') in states(check) and any(word.state == 'added' for word in check.words)


def test_a_language_without_spaces_is_compared_letter_by_letter():
    check = reading.compare('今日は天気です', '今日は元気です')
    assert reading.differences(check) == 1 and check.total == 7


def test_accented_letters_are_compared_as_they_are():
    assert reading.differences(reading.compare('café crème', 'CAFÉ CRÈME')) == 0
    assert reading.differences(reading.compare('cafe', 'café')) == 1


def test_nothing_read_is_nothing_but_differences():
    check = reading.compare('One two', '')
    assert check.same == 0 and all(word.state == 'missed' for word in check.words)


# ---- on a take -------------------------------------------------------------------------------------------------------------

LINE = 'The ferry left at dawn.'


def read_take(session, heard=LINE, words=()):
    take = record(session, 12.0, script_line=LINE)
    session.drafts.receive(take, heard, None, words=list(words))
    return take


def test_a_take_with_no_script_line_has_no_reading_check(session):
    take = record(session, 12.0)
    session.drafts.receive(take, 'Some words.', None)
    assert reading_of(take) is None


def test_the_check_waits_for_the_draft(session):
    take = record(session, 12.0, script_line=LINE)
    assert reading_of(take) is None


def test_the_transcript_stays_the_script_line_whatever_was_heard(session):
    take = read_take(session, 'The ferry left at noon.')
    assert take.meta['transcript'] == LINE and take.meta['transcript_status'] == 'read_from_script'


def test_a_line_read_as_written_says_so(session):
    read_take(session)
    assert session.status_text == 'Reading check: the line was read as written.'


def test_a_misread_line_says_how_many_places_differ(session):
    read_take(session, 'The ferry left at noon.')
    assert session.status_text.startswith('Reading check: 1 place differs from the script')


def test_ready_to_keep_warns_about_a_misread_line_but_does_not_stop_keep(session):
    read_take(session, 'The ferry left at noon.')
    settle(session)
    derived = session.measure.derived(session.current)
    settle(session)
    derived = session.measure.derived(session.current)
    advice = session.workspace.readiness(derived).advice
    assert any(item.key == 'reading' for item in advice)
    fill(session, transcript=LINE)
    assert session.keep() is not None


def test_what_was_heard_can_become_the_transcript(session):
    take = read_take(session, 'The ferry left at noon.')
    session.workspace.use_what_was_heard()
    assert take.meta['transcript'] == 'The ferry left at noon.'


def test_the_check_goes_away_when_the_clip_is_cut_and_comes_back_with_a_new_draft(session, monkeypatch, clock):
    take = read_take(session)
    assert reading_of(take) is not None
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', lambda *a: {'text': 'The ferry left.'})
    session.editor.set_selection(5.0, 6.0)
    session.editor.cut_selection()
    assert reading_of(take) is None
    clock.advance(2.0)
    session.tick()
    session.tick()
    assert take.meta['draft_text'] == 'The ferry left.' and reading.differences(reading_of(take)) == 2
    assert take.meta['transcript'] == LINE


# ---- the words -------------------------------------------------------------------------------------------------------------

WORDS = [('One', 1.0, 1.4), ('two', 2.0, 2.4), ('three', 3.0, 3.4), ('four', 4.0, 4.4)]


def worded(session):
    take = record(session, 12.0)
    session.drafts.receive(take, 'One two three four', None, words=WORDS)
    return take


def test_rows_that_cannot_be_used_are_left_out():
    assert reading.clean_words([['one', 1, 2], ['', 1, 2], [3, 1, 2], ['bad', 'x', 2], ['back', 3, 2], 'junk', ['two', 2, 3]]) \
        == [('one', 1.0, 2.0), ('two', 2.0, 3.0)]
    assert reading.clean_words('nonsense') == [] and reading.clean_words(None) == []


def test_clicking_a_word_selects_it_on_the_waveform(session):
    worded(session)
    session.editor.select_words(1, 1)
    assert session.editor.sel == pytest.approx((2.0, 2.4), abs=0.09)
    assert session.editor.playhead == session.editor.sel[0]


def test_the_edges_of_a_word_go_to_the_quiet_beside_it():
    """A recognizer's times are rough. The quiet between two words is 40 ms away from where it says."""
    x = bursts(4.0, [(0.5, 1.96), (2.0, 3.5)], amp=0.14, noise=0.0002, f0=137.0)
    assert analysis.quietest_near(x, RATE, 1.92) == pytest.approx(1.98, abs=0.02)
    assert analysis.quietest_near(x, RATE, 2.04) == pytest.approx(1.98, abs=0.02)


def test_a_word_at_the_very_start_or_end_stays_inside_the_clip():
    x = bursts(2.0, [(0.0, 2.0)], amp=0.14, f0=137.0)
    assert 0.0 <= analysis.quietest_near(x, RATE, 0.0) and analysis.quietest_near(x, RATE, 2.0) <= 2.0


def test_nothing_to_measure_leaves_the_time_alone():
    assert analysis.quietest_near(np.zeros(10, np.float32), RATE, 5.0) == 5.0


def test_a_run_of_words_selects_from_the_first_to_the_last(session):
    worded(session)
    session.editor.select_words(1, 3)
    assert session.editor.sel == pytest.approx((2.0, 4.4), abs=0.09)


def test_cutting_a_selected_word_is_an_ordinary_cut(session):
    take = worded(session)
    session.editor.select_words(1, 1)
    session.editor.cut_selection()
    assert reading.word_is_cut(2.0, 2.4, take.history.edits) and not reading.word_is_cut(3.0, 3.4, take.history.edits)
    assert session.editor.undo() and not take.history.edits


def test_a_word_that_was_cut_can_be_brought_back(session):
    take = worded(session)
    session.editor.select_words(1, 1)
    session.editor.cut_selection()
    assert session.editor.restore_word(1) and take.history.edits == []


def test_bringing_back_a_word_that_was_never_cut_does_nothing(session):
    take = worded(session)
    assert session.editor.restore_word(0) is False and take.history.edits == []


def test_a_word_out_of_range_does_nothing(session):
    worded(session)
    session.editor.select_words(7, 9)
    assert session.editor.sel is None and session.editor.restore_word(9) is False


def test_the_times_of_words_come_back_on_the_masters_timeline(session, monkeypatch):
    """The recognizer hears the edited clip, so a word after a cut has to be put back where it is in the recording."""
    take = record(session, 12.0)
    session.editor.set_selection(2.0, 4.0)
    session.editor.cut_selection()
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', lambda *a: {'text': 'late', 'words': [('late', 5.0, 5.5)]})
    session.drafts.request(take)
    session.tick()
    assert take.meta['draft_words'] == [('late', pytest.approx(7.0, abs=0.01), pytest.approx(7.5, abs=0.01))]


def test_the_words_are_saved_with_the_take(session, make_session):
    worded(session)
    session.autosave.flush()
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert again.current.meta['draft_words'] == WORDS


def test_the_words_go_in_the_sidecar_and_come_back(session, make_session):
    take = worded(session)
    fill(session, transcript='One two three four')
    session.keep()
    assert json.loads(take.sidecar_path.read_text(encoding='utf-8'))['words'][0] == ['One', 1.0, 1.4]
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert again.current.meta['draft_words'] == WORDS


def test_a_clip_with_no_words_has_no_field_for_them(session):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    assert 'words' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


# ---- splitting --------------------------------------------------------------------------------------------------------------

SPANS = [(0.3, 3.0), (3.6, 6.0), (6.6, 9.0), (9.6, 12.0), (12.6, 15.0), (15.6, 17.7)]
RATE = 48000


def long_take():
    return bursts(18.0, SPANS, amp=0.14, f0=137.0)


def test_a_take_is_split_in_the_middle_of_its_pauses():
    ranges = analysis.split_ranges(long_take(), RATE, 2.0, 1.0, 15.0)
    assert len(ranges) == 6
    cuts = [first / RATE for first, _ in ranges[1:]]
    assert cuts == pytest.approx([3.3, 6.3, 9.3, 12.3, 15.3], abs=0.05)


def test_the_parts_cover_the_whole_take():
    ranges = analysis.split_ranges(long_take(), RATE, 2.0, 1.0, 15.0)
    assert ranges[0][0] == 0 and ranges[-1][1] == 18 * RATE
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))


def test_a_part_runs_on_to_the_first_pause_after_it_is_long_enough():
    ranges = analysis.split_ranges(long_take(), RATE, 5.0, 1.0, 15.0)
    assert [round(first / RATE, 1) for first, _ in ranges] == pytest.approx([0.0, 6.3, 12.3], abs=0.1)


def test_a_last_part_too_short_to_keep_joins_the_one_before():
    x = bursts(12.0, [(0.3, 5.0), (5.6, 11.0), (11.5, 11.9)], amp=0.14, f0=137.0)
    ranges = analysis.split_ranges(x, RATE, 5.0, 1.0, 15.0)
    assert ranges[-1][1] == 12 * RATE and ranges[-1][1] - ranges[-1][0] > 2 * RATE


def test_a_take_with_no_pauses_stays_whole():
    x = bursts(12.0, [(0.0, 12.0)], amp=0.14, f0=137.0)
    assert analysis.split_ranges(x, RATE, 2.0, 1.0, 15.0) == [(0, 12 * RATE)]


def test_splitting_makes_a_take_of_each_part_and_leaves_the_long_one(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=long_take())
    parts = session.workspace.split_take()
    assert len(parts) == 6 and take in session.workspace.takes and take.master_path.exists()
    assert session.current is parts[0] and all(part.master_path.exists() for part in parts)
    assert all(part.meta['purpose'] == 'sentence' and 'split' in part.flags for part in parts)
    assert sum(part.seconds for part in parts) == pytest.approx(18.0, abs=0.01)


def test_the_parts_sound_as_the_recording_did(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=long_take())
    parts = session.workspace.split_take()
    assert np.array_equal(np.concatenate([part.samples for part in parts]), take.samples)


def test_the_list_shows_the_parts_in_the_order_they_were_said(session):
    session.workspace.set_purpose('sentence')
    record(session, samples=long_take())
    parts = session.workspace.split_take()
    shown = [take for take in session.workspace.takes if take in parts]
    assert shown == sorted(parts, key=lambda part: part.created, reverse=True)
    assert parts[0].created < parts[-1].created


def test_a_part_says_where_it_came_from(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=long_take())
    part = session.workspace.split_take()[1]
    fill(session)
    session.workspace.open_take(part)
    fill(session)
    session.keep()
    data = json.loads(part.sidecar_path.read_text(encoding='utf-8'))
    assert data['split_from']['take'] == take.id and data['split_from']['start_seconds'] == pytest.approx(3.3, abs=0.05)
    assert 'split' in data['flags']


def test_a_part_takes_its_share_of_the_words(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=long_take())
    words = [('first', 1.0, 1.5), ('second', 4.0, 4.5), ('third', 7.0, 7.5)]
    session.drafts.receive(take, 'first second third', None, words=words)
    parts = session.workspace.split_take()
    assert parts[1].meta['transcript'] == 'second' and parts[1].meta['transcript_status'] == 'asr_draft'
    assert parts[1].meta['draft_words'] == [('second', pytest.approx(0.7, abs=0.06), pytest.approx(1.2, abs=0.06))]


def test_a_part_with_no_words_asks_for_a_draft(session, monkeypatch):
    session.workspace.set_purpose('sentence')
    record(session, samples=long_take())
    asked = []
    session.workspace.on_take_added = asked.append
    assert len(session.workspace.split_take()) == 6 and len(asked) == 6


def test_a_take_that_cannot_be_split_makes_nothing(session):
    record(session, samples=bursts(12.0, [(0.0, 12.0)], amp=0.14, f0=137.0))
    assert session.workspace.split_take() == [] and len(session.workspace.takes) == 1


def test_the_parts_come_back_after_a_restart(session, make_session):
    session.workspace.set_purpose('sentence')
    record(session, samples=long_take())
    parts = session.workspace.split_take()
    again = make_session()
    again.workspace.select_speaker(session.slug)
    back = [take for take in again.workspace.takes if take.id == parts[0].id][0]
    assert 'split' in back.flags and back.meta['split_from'] == parts[0].meta['split_from']


def test_a_take_too_long_to_keep_can_be_split_into_clips_that_can_be_kept(session):
    """What a recording that ran past the longest clip is for."""
    session.workspace.set_purpose('sentence')
    x = bursts(40.0, [(start + 0.3, start + 5.6) for start in np.arange(0.0, 36.0, 6.0)], amp=0.14, f0=137.0)
    record(session, samples=x)
    parts = session.workspace.split_take()
    assert len(parts) >= 5 and all(part.seconds <= 15.0 for part in parts)
    session.workspace.open_take(parts[0])
    fill(session)
    assert session.keep() is not None
