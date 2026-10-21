import pytest

from recorder.core.edits import History, describe, validate_edits


def test_undo_and_redo_restore_the_exact_list():
    h = History(seconds=10.0)
    h.cut(0, 1)
    h.cut(4, 5)
    before = [dict(e) for e in h.edits]
    assert h.undo() is True
    assert len(h.edits) == 1
    assert h.redo() is True
    assert h.edits == before


def test_there_is_nothing_to_undo_or_redo_at_the_ends():
    h = History(seconds=10.0)
    assert not h.can_undo and not h.can_redo
    assert h.undo() is False and h.redo() is False
    h.cut(0, 1)
    assert h.can_undo and not h.can_redo


def test_cut_labels_its_region_from_where_it_falls():
    h = History(seconds=10.0)
    assert h.cut(0, 1)['region'] == 'start'
    assert h.cut(4, 5)['region'] == 'middle'
    assert h.cut(9, 10)['region'] == 'end'


def test_tiny_and_whole_clip_selections_are_refused():
    h = History(seconds=10.0)
    with pytest.raises(ValueError, match='20 ms'):
        h.cut(1.0, 1.01)
    with pytest.raises(ValueError, match='whole clip'):
        h.cut(0.0, 10.0)
    assert h.edits == []


def test_points_are_clamped_to_the_clip():
    h = History(seconds=10.0)
    e = h.cut(-2, 1)
    assert e['start_seconds'] == 0.0 and e['region'] == 'start'


def test_points_must_be_numbers():
    h = History(seconds=10.0)
    with pytest.raises(ValueError, match='numbers'):
        h.cut(float('nan'), 2.0)


def test_the_union_of_cuts_cannot_remove_everything_and_a_failure_leaves_redo_alone():
    history = History(seconds=12)
    history.cut(0, 6)
    history.cut(8, 9)
    history.undo()
    before = [dict(e) for e in history.edits]
    with pytest.raises(ValueError, match='whole clip'):
        history.cut(5, 12)
    assert history.edits == before
    assert history.redo() and len(history.edits) == 2


def test_silence_fade_and_gain_are_recorded_with_their_settings():
    h = History(seconds=10.0)
    h.silence(2.0, 2.5)
    h.fade('out', 8.0, 9.0)
    h.gain(-6.0, 3.0, 4.0)
    assert [e['op'] for e in h.edits] == ['silence', 'fade', 'gain']
    assert h.edits[1]['direction'] == 'out' and h.edits[2]['gain_db'] == -6.0
    assert h.cuts == []
    assert h.words_changed is True          # silence can remove words


def test_only_fades_and_gain_do_not_touch_the_words():
    h = History(seconds=10.0)
    h.fade('in', 0.0, 0.5)
    h.gain(3.0, 1.0, 2.0)
    assert h.words_changed is False


@pytest.mark.parametrize('call', [
    lambda h: h.fade('sideways', 1.0, 2.0),
    lambda h: h.gain(99.0, 1.0, 2.0),
    lambda h: h.gain(float('nan'), 1.0, 2.0),
    lambda h: h.silence(1.0, 1.005),
])
def test_bad_operations_are_refused_without_changing_the_history(call):
    h = History(seconds=10.0)
    with pytest.raises(ValueError):
        call(h)
    assert h.edits == [] and not h.can_undo


def test_several_operations_go_in_as_one_step_or_not_at_all():
    h = History(seconds=10.0)
    h.add_ops([('cut', 0, 1, {}), ('cut', 9, 10, {})], label='Auto-trim')
    assert len(h.edits) == 2 and h.steps == ['Auto-trim']
    h.undo()
    assert h.edits == []
    with pytest.raises(ValueError):
        h.add_ops([('cut', 0, 1, {}), ('cut', 2, 2.001, {})])
    assert h.edits == []


def test_steps_are_listed_and_any_step_can_be_jumped_to():
    h = History(seconds=10.0)
    h.cut(0, 1)
    h.silence(4, 5)
    h.set_chain('level', 'Level +3.0 dB', gain_db=3.0)
    assert h.steps == ['Cut 0.00 to 1.00 s', 'Silence 4.00 to 5.00 s', 'Level +3.0 dB']
    assert h.position == 3
    assert h.jump(1) is True
    assert len(h.edits) == 1 and h.chain['level']['gain_db'] == 0.0
    assert h.jump(3) is True and h.chain['level']['gain_db'] == 3.0
    assert h.jump(3) is False and h.jump(9) is False


def test_a_new_change_after_undo_drops_the_steps_that_could_have_been_redone():
    h = History(seconds=10.0)
    h.cut(0, 1)
    h.cut(4, 5)
    h.undo()
    h.silence(6, 7)
    assert not h.can_redo
    assert [e['op'] for e in h.edits] == ['cut', 'silence']


def test_chain_changes_are_steps_and_can_be_undone():
    h = History(seconds=10.0)
    assert h.set_chain('denoise', 'Noise cleaning on', enabled=True) is True
    assert h.chain['denoise']['enabled'] is True
    h.undo()
    assert h.chain['denoise']['enabled'] is False


def test_a_setting_that_changes_nothing_adds_no_step():
    h = History(seconds=10.0)
    assert h.set_chain('level', 'Level', gain_db=0.0) is False
    assert not h.can_undo


def test_an_out_of_range_setting_is_refused_and_leaves_the_history_alone():
    h = History(seconds=10.0)
    with pytest.raises(ValueError, match='between'):
        h.set_chain('level', 'Level', gain_db=99.0)
    assert h.chain['level']['gain_db'] == 0.0 and not h.can_undo


def test_moving_one_control_repeatedly_is_a_single_step():
    h = History(seconds=10.0)
    h.cut(0, 1)
    for gain in (1.0, 2.0, 3.0, 4.0):
        h.set_chain('level', f'Level {gain:+.1f} dB', merge='level', gain_db=gain)
    assert len(h.steps) == 2 and h.chain['level']['gain_db'] == 4.0
    h.undo()
    assert h.chain['level']['gain_db'] == 0.0 and len(h.edits) == 1
    h.set_chain('level', 'Level +9.0 dB', merge='level', gain_db=9.0)       # after an undo it starts a new step
    assert len(h.steps) == 2 and h.chain['level']['gain_db'] == 9.0


def test_the_join_fade_is_clamped_and_is_a_step():
    h = History(seconds=10.0)
    assert h.set_fade_ms(200) is True and h.fade_ms == 50.0
    assert h.set_fade_ms(50) is False
    h.undo()
    assert h.fade_ms == 10.0
    with pytest.raises(ValueError):
        h.set_fade_ms(float('nan'))


def test_removing_an_edit_is_a_step():
    h = History(seconds=10.0)
    h.cut(0, 1)
    h.silence(4, 5)
    h.remove(0)
    assert [e['op'] for e in h.edits] == ['silence'] and h.steps[-1].startswith('Remove')
    h.undo()
    assert len(h.edits) == 2


def test_the_json_form_rebuilds_the_same_recipe():
    h = History(seconds=10.0)
    h.cut(0, 1)
    h.gain(-3.0, 4, 5)
    h.set_chain('highpass', 'High-pass', enabled=True, hz=100.0)
    h.set_fade_ms(20)
    data = h.to_json()
    again = History(data['edits'], seconds=10.0, chain=data['chain'], fade_ms=data['join_fade_ms'])
    assert again.edits == h.edits and again.chain == h.chain and again.fade_ms == 20.0


def test_a_copy_is_independent():
    h = History(seconds=10.0)
    h.cut(0, 1)
    before = h.copy()
    h.cut(4, 5)
    assert len(before.edits) == 1 and len(h.edits) == 2


def test_describe_reads_naturally():
    assert describe({'op': 'cut', 'start_seconds': 1, 'end_seconds': 2.5}) == 'Cut 1.00 to 2.50 s'
    assert describe({'op': 'gain', 'gain_db': 3, 'start_seconds': 1, 'end_seconds': 2}) == 'Gain +3.0 dB 1.00 to 2.00 s'
    assert describe({'op': 'fade', 'direction': 'out', 'start_seconds': 1, 'end_seconds': 2}) == 'Fade out 1.00 to 2.00 s'


def test_validate_edits_accepts_the_known_operations():
    assert validate_edits([{'op': 'cut', 'start_seconds': 0, 'end_seconds': 1},
                           {'op': 'silence', 'start_seconds': 1, 'end_seconds': 2},
                           {'op': 'fade', 'direction': 'in', 'start_seconds': 2, 'end_seconds': 3},
                           {'op': 'gain', 'gain_db': -3, 'start_seconds': 3, 'end_seconds': 4}], 5)


def test_validate_edits_says_what_is_wrong():
    with pytest.raises(ValueError, match='must be a list'):
        validate_edits({'op': 'cut'}, 5)
    with pytest.raises(ValueError, match='not a known edit'):
        validate_edits(['cut'], 5)
    with pytest.raises(ValueError, match='not a known edit'):
        validate_edits([{'op': 'pitch', 'start_seconds': 0, 'end_seconds': 1}], 5)
    with pytest.raises(ValueError, match='no usable start and end'):
        validate_edits([{'op': 'cut', 'start_seconds': 'soon', 'end_seconds': 1}], 5)
    with pytest.raises(ValueError, match='outside the clip'):
        validate_edits([{'op': 'cut', 'start_seconds': 0, 'end_seconds': 9}], 5)
    with pytest.raises(ValueError, match='"in" or "out"'):
        validate_edits([{'op': 'fade', 'start_seconds': 0, 'end_seconds': 1}], 5)
    with pytest.raises(ValueError, match='gain_db'):
        validate_edits([{'op': 'gain', 'start_seconds': 0, 'end_seconds': 1}], 5)
    with pytest.raises(ValueError, match='between'):
        validate_edits([{'op': 'gain', 'gain_db': 90, 'start_seconds': 0, 'end_seconds': 1}], 5)
    assert validate_edits([{'op': 'cut', 'start_seconds': 0, 'end_seconds': 5.0004}], 5.0)
