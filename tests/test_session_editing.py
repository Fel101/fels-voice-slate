"""Editing a take: selection, every operation, the chain, undo, and the guarantee that a refused edit changes nothing."""
import numpy as np
import pytest

from helpers import RATE, bursts, fill, record, with_pop
from recorder import clean


def kinds(session):
    return [e['op'] for e in session.current.history.edits]


# ---- selection and playhead -------------------------------------------------------------------------------------------

def test_a_selection_is_clamped_to_the_clip_and_ordered(session):
    record(session, 10.0)
    session.editor.set_selection(12.0, -3.0)
    assert session.editor.sel == (0.0, 10.0)


def test_a_range_with_no_length_clears_the_selection(session):
    record(session, 10.0)
    session.editor.set_selection(2.0, 4.0)
    session.editor.set_selection(4.0, 4.0)
    assert session.editor.sel is None


def test_in_and_out_keys_make_a_selection_in_either_order(session):
    record(session, 10.0)
    editor = session.editor
    editor.set_playhead(2.0)
    editor.set_in()
    editor.set_playhead(5.0)
    editor.set_out()
    assert editor.sel == pytest.approx((2.0, 5.0))
    editor.set_playhead(1.0)
    editor.set_in()
    assert editor.sel == pytest.approx((1.0, 5.0))
    editor.set_playhead(7.0)
    editor.set_in()
    assert editor.sel == pytest.approx((5.0, 7.0))


def test_a_lone_in_point_is_not_a_selection(session):
    record(session, 10.0)
    session.editor.set_playhead(3.0)
    session.editor.set_in()
    assert session.editor.sel is None and session.editor.in_point == pytest.approx(3.0)


def test_a_dragged_selection_replaces_stale_in_and_out_points(session):
    record(session, 10.0)
    editor = session.editor
    editor.set_playhead(2.0)
    editor.set_in()                                             # a lone In marker at 2 s
    editor.set_selection(6.0, 8.0)                              # then a drag elsewhere
    assert (editor.in_point, editor.out_point) == (6.0, 8.0)
    editor.set_playhead(9.0)
    editor.set_out()                                            # moves only the end of the selection
    assert editor.sel == pytest.approx((6.0, 9.0))
    editor.set_selection(5.0, 5.0)
    assert editor.sel is None and editor.in_point is None and editor.out_point is None


def test_opening_another_take_clears_the_selection_and_playhead(session):
    first = record(session, 10.0)
    session.editor.set_selection(2.0, 4.0)
    session.editor.set_playhead(3.0)
    record(session, 8.0)
    assert session.editor.sel is None and session.editor.playhead == 0.0
    session.workspace.open_take(first)
    assert session.editor.sel is None


# ---- cuts -------------------------------------------------------------------------------------------------------------

def test_cut_selection_undo_and_redo(session):
    take = record(session, 12.0)
    session.editor.set_selection(4.0, 5.0)
    session.editor.cut_selection()
    assert len(take.history.edits) == 1 and take.needs_transcript_review is True
    assert session.editor.sel is None
    session.editor.undo()
    assert take.history.edits == [] and take.needs_transcript_review is False
    session.editor.redo()
    assert len(take.history.edits) == 1 and take.needs_transcript_review is True


def test_cut_from_start_and_to_end_use_the_playhead_when_nothing_is_selected(session):
    record(session, 12.0)
    session.editor.set_playhead(1.5)
    session.editor.cut_from_start()
    session.editor.set_playhead(10.0)
    session.editor.cut_to_end()
    assert [e['region'] for e in session.current.history.edits] == ['start', 'end']


def test_cut_from_start_and_to_end_use_the_selection_when_there_is_one(session):
    record(session, 12.0)
    session.editor.set_selection(2.0, 9.0)
    session.editor.cut_from_start()
    edit = session.current.history.edits[0]
    assert edit['region'] == 'start' and edit['end_seconds'] == pytest.approx(9.0)


def test_keep_only_selection_makes_two_end_cuts(session):
    record(session, 12.0)
    session.editor.set_selection(2.0, 8.0)
    session.editor.keep_only_selection()
    assert [e['region'] for e in session.current.history.edits] == ['start', 'end']
    assert len(session.current.history.steps) == 1               # one undo step, not two
    session.editor.undo()
    assert session.current.history.edits == []


def test_a_too_short_selection_shows_a_message_and_makes_no_edit(session):
    record(session, 12.0)
    session.editor.set_selection(3.0, 3.01)
    session.editor.cut_selection()
    assert session.current.history.edits == [] and '20 ms' in session.status_text


def test_an_operation_with_no_selection_asks_for_one(session):
    record(session, 12.0)
    session.editor.cut_selection()
    assert 'Select a range' in session.status_text and session.current.history.edits == []


def test_combined_cuts_are_refused_without_breaking_history(session):
    take = record(session, 12.0)
    session.editor.set_selection(0, 6)
    session.editor.cut_selection()
    session.editor.set_selection(6, 12)
    session.editor.cut_selection()
    assert len(take.history.edits) == 1 and 'whole clip' in session.status_text
    assert len(session.lib.export_for(take)) > 0
    session.editor.undo()
    assert take.history.edits == []
    session.editor.redo()
    assert len(take.history.edits) == 1


def test_a_multi_cut_action_rolls_back_every_edit_when_it_fails(session):
    take = record(session, 12.0)
    session.editor.set_selection(3, 9)
    session.editor.cut_selection()
    before = [dict(e) for e in take.history.edits]
    session.editor.set_selection(4, 8)          # keeping 4 to 8 would cut everything: neither new cut may commit
    session.editor.keep_only_selection()
    assert take.history.edits == before and 'whole clip' in session.status_text
    session.editor.undo()
    assert take.history.edits == []


def test_a_refused_edit_leaves_the_redo_steps_alone(session):
    take = record(session, 12.0)
    session.editor.set_selection(0, 6)
    session.editor.cut_selection()
    session.editor.undo()
    session.editor.set_selection(3.0, 3.001)
    session.editor.cut_selection()                              # refused
    assert take.history.can_redo


def test_zero_crossing_snap_cannot_remove_the_last_remaining_samples(session):
    samples = np.ones(RATE, dtype=np.float32) * 0.2
    samples[RATE // 2:] *= -1
    take = record(session, samples=samples)
    session.editor.set_selection(0, 0.499)
    session.editor.cut_selection()
    before = [dict(e) for e in take.history.edits]
    session.editor.set_selection(0.501, 1.0)     # both ends snap to the same crossing, so no samples would remain
    session.editor.cut_selection()
    assert take.history.edits == before and 'whole clip' in session.status_text
    assert len(session.lib.export_for(take)) > 0


def test_editing_with_no_take_does_nothing(make_session):
    s = make_session()
    s.editor.cut_selection()
    s.editor.undo()
    assert s.current is None


# ---- the other operations ---------------------------------------------------------------------------------------------

def test_silencing_a_selection_is_a_word_change_but_leaves_the_length(session):
    take = record(session, 12.0)
    session.editor.set_selection(4.0, 5.0)
    session.editor.silence_selection()
    assert kinds(session) == ['silence'] and take.needs_transcript_review is True
    export = session.lib.export_for(take)
    assert len(export) == pytest.approx(12.0 * 24000, abs=2)
    part = export[int(4.2 * 24000):int(4.8 * 24000)]
    assert np.max(np.abs(part)) < 1e-6


def test_a_fade_or_a_gain_change_does_not_ask_for_the_transcript_to_be_checked(session):
    take = record(session, 12.0)
    session.editor.set_selection(0.0, 1.0)
    session.editor.fade_selection('in')
    session.editor.set_selection(10.0, 12.0)
    session.editor.fade_selection('out')
    session.editor.set_selection(5.0, 7.0)
    session.editor.gain_selection(-6.0)
    assert kinds(session) == ['fade', 'fade', 'gain']
    assert take.needs_transcript_review is False
    assert [e.get('direction') for e in take.history.edits[:2]] == ['in', 'out']
    assert take.history.edits[2]['gain_db'] == -6.0


def test_a_gain_edit_changes_only_its_region(session):
    take = record(session, 12.0)
    plain = session.lib.export_for(take).copy()
    session.editor.set_selection(5.0, 7.0)
    session.editor.gain_selection(-12.0)
    changed = session.lib.export_for(take)
    inside = slice(int(5.5 * 24000), int(6.5 * 24000))
    outside = slice(int(1.0 * 24000), int(4.0 * 24000))
    assert np.allclose(changed[outside], plain[outside], atol=1e-6)
    ratio = np.sqrt(np.mean(changed[inside] ** 2)) / np.sqrt(np.mean(plain[inside] ** 2))
    assert ratio == pytest.approx(10 ** (-12 / 20), rel=0.05)


def test_a_gain_out_of_range_is_refused_with_a_message(session):
    take = record(session, 12.0)
    session.editor.set_selection(5.0, 7.0)
    session.editor.gain_selection(80.0)
    assert take.history.edits == [] and 'Gain must be between' in session.status_text


def test_removing_one_edit_from_the_list_is_one_undo_step(session):
    take = record(session, 12.0)
    for a, b in ((1, 2), (4, 5), (8, 9)):
        session.editor.set_selection(a, b)
        session.editor.cut_selection()
    assert session.editor.remove_edit(1)
    assert [round(e['start_seconds']) for e in take.history.edits] == [1, 8]
    session.editor.undo()
    assert len(take.history.edits) == 3


def test_jumping_to_an_earlier_step_and_back(session):
    take = record(session, 12.0)
    for a, b in ((1, 2), (4, 5), (8, 9)):
        session.editor.set_selection(a, b)
        session.editor.cut_selection()
    assert session.editor.jump_to(1)
    assert len(take.history.edits) == 1
    assert session.editor.jump_to(3) and len(take.history.edits) == 3
    assert session.editor.jump_to(3) is False


# ---- finding the edits -------------------------------------------------------------------------------------------------

@pytest.fixture
def gappy(session):
    """9 s: speech 1.5 to 3.0 and 6.0 to 7.0 over a quiet noise floor."""
    return record(session, samples=bursts(9.0, [(1.5, 3.0), (6.0, 7.0)], amp=0.14))


def test_auto_trim_cuts_the_pauses_at_both_ends(session, gappy):
    session.editor.auto_trim(margin=0.2)
    assert [e['region'] for e in gappy.history.edits] == ['start', 'end']
    start, end = gappy.history.edits
    assert start['end_seconds'] == pytest.approx(1.3, abs=0.12)
    assert end['start_seconds'] == pytest.approx(7.2, abs=0.12)
    assert 'Auto-trim: 2 cuts' in session.status_text
    session.editor.undo()
    assert gappy.history.edits == []                             # both cuts were one step


def test_auto_trim_says_when_there_is_nothing_to_trim(session):
    record(session, samples=bursts(5.0, [(0.1, 4.9)], amp=0.14))
    session.editor.auto_trim()
    assert 'Nothing to trim' in session.status_text and session.current.history.edits == []


def test_shorten_pauses_cuts_the_middle_of_a_long_pause(session, gappy):
    session.editor.shorten_pauses(keep_seconds=0.5)
    cuts = gappy.history.edits
    assert len(cuts) == 1 and cuts[0]['region'] == 'middle'
    assert 3.0 < cuts[0]['start_seconds'] < cuts[0]['end_seconds'] < 6.0
    assert cuts[0]['end_seconds'] - cuts[0]['start_seconds'] == pytest.approx(3.0 - 0.5, abs=0.25)


def test_shorten_pauses_after_a_cut_still_lands_on_master_time(session, gappy):
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    session.editor.shorten_pauses(keep_seconds=0.5)
    cut = gappy.history.edits[-1]
    assert 3.0 < cut['start_seconds'] < cut['end_seconds'] < 6.0     # in master seconds, not edited-clip seconds


def test_shorten_pauses_says_when_no_pause_is_long_enough(session):
    record(session, samples=bursts(5.0, [(0.0, 2.0), (2.3, 5.0)], amp=0.14))
    session.editor.shorten_pauses(keep_seconds=0.5)
    assert 'No pause is longer' in session.status_text


# ---- level, limiter and cleaning --------------------------------------------------------------------------------------

def test_normalize_sets_the_gain_that_puts_the_speech_at_the_target(session):
    take = record(session, samples=bursts(8.0, [(0.5, 7.5)], amp=0.03))
    result = session.editor.normalize_level()
    assert result.gain_db > 10 and take.history.chain['level']['gain_db'] == result.gain_db
    assert 'The original recording is unchanged' in session.status_text


def test_a_loud_pop_holds_normalize_down_until_the_limiter_is_on(session):
    take = record(session, samples=with_pop(bursts(10.0, [(0.5, 9.5)], amp=0.05), at=7.42))
    capped = session.editor.normalize_level(limit=False)
    assert capped.capped and capped.gain_db < capped.wanted_db - 1.0
    assert 'turn on the limiter' in session.status_text
    free = session.editor.normalize_level(limit=True)
    assert not free.capped and free.gain_db == pytest.approx(free.wanted_db)
    assert take.history.chain['limiter']['enabled'] is True
    export = session.lib.export_for(take)
    assert np.max(np.abs(export)) <= 10 ** (take.history.chain['limiter']['ceiling_db'] / 20) + 1e-3


def test_reset_level_puts_the_gain_back(session):
    take = record(session, samples=bursts(8.0, [(0.5, 7.5)], amp=0.03))
    session.editor.normalize_level()
    session.editor.reset_level()
    assert take.history.chain['level']['gain_db'] == 0.0


def test_a_slider_dragged_through_many_values_is_one_undo_step(session):
    take = record(session, 10.0)
    for value in (100.0, 120.0, 140.0, 160.0):
        session.editor.set_chain('highpass', 'High-pass', merge='highpass', enabled=True, hz=value)
    assert len(take.history.steps) == 1
    assert take.history.chain['highpass']['hz'] == 160.0
    session.editor.undo()
    assert take.history.chain['highpass']['enabled'] is False


def test_a_chain_value_out_of_range_is_refused_with_a_message(session):
    take = record(session, 10.0)
    assert session.editor.set_chain('level', 'Gain', gain_db=500.0) is False
    assert take.history.chain['level']['gain_db'] == 0.0 and session.status_text


def test_the_join_fade_is_part_of_the_recipe_and_clamped(session):
    take = record(session, 10.0)
    assert take.join_fade_ms == 10.0
    assert session.editor.set_fade_ms(5000)
    assert take.join_fade_ms == 50.0
    session.editor.undo()
    assert take.join_fade_ms == 10.0


@pytest.mark.parametrize('fade', [0, 50])
def test_a_saved_join_fade_is_restored_and_each_take_keeps_its_own(session, make_session, fade):
    first = record(session, 12.0)
    fill(session)
    session.editor.set_selection(3, 4)
    session.editor.cut_selection()
    session.editor.set_fade_ms(fade)
    session.workspace.confirm_transcript()
    session.keep()
    loaded = session.lib.load_kept(session.slug)[0]
    assert loaded.join_fade_ms == fade
    from recorder.session.measure import frozen
    with_saved = frozen(loaded.history)
    with_default = frozen(loaded.history)
    with_default.fade_ms = 10.0
    assert np.array_equal(session.lib.export_for(loaded), session.lib.export_for(loaded, with_saved))
    if fade != 10:
        assert not np.array_equal(session.lib.export_for(loaded), session.lib.export_for(loaded, with_default))
    other = record(session, 8.0)
    assert other.join_fade_ms == 10.0 and first.join_fade_ms == fade


def test_turning_cleaning_on_without_ffmpeg_reports_it_instead_of_crashing(session, monkeypatch):
    take = record(session, 10.0)
    fill(session)
    session.editor.set_selection(1.0, 2.0)
    session.editor.cut_selection()
    session.workspace.confirm_transcript()
    session.editor.set_denoise(True)

    def no_ffmpeg():
        raise clean.CleanError('FFmpeg was not found')

    monkeypatch.setattr(clean, 'find_ffmpeg', no_ffmpeg)
    monkeypatch.setattr('recorder.transcript.available', lambda python, model: True)
    session.drafts.request(take)
    assert 'FFmpeg was not found' in session.status_text
    session.transport.play(edited=True)
    assert 'FFmpeg was not found' in session.status_text and not session.transport.active
    session.transport.check_cuts()
    assert 'FFmpeg was not found' in session.status_text
    session.editor.auto_trim()
    assert 'FFmpeg was not found' in session.status_text
    with pytest.raises(clean.CleanError):
        session.keep()
    assert not take.kept
    session.editor.set_denoise(False)
    assert session.keep()['export'].exists()
