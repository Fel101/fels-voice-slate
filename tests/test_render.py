import numpy as np
import pytest

from helpers import RATE, speech, tone
from recorder import audio_core as ac
from recorder.core import render
from recorder.core.edits import History


# ---- region operations ------------------------------------------------------------------------------------------------

def test_no_region_operations_means_no_envelope_and_the_samples_come_back_as_they_are():
    x = speech(2.0)
    assert render.region_envelope(len(x), RATE, [{'op': 'cut', 'start_seconds': 0, 'end_seconds': 1}]) is None
    assert render.apply_regions(x, RATE, []) is x


def test_silence_is_zero_inside_and_blends_over_three_milliseconds_outside():
    env = render.region_envelope(RATE * 4, RATE, [{'op': 'silence', 'start_seconds': 1.0, 'end_seconds': 1.5}])
    a, b = RATE, int(1.5 * RATE)
    assert np.all(env[a:b] == 0)
    guard = int(0.004 * RATE)
    assert np.all(env[:a - guard] == 1) and np.all(env[b + guard:] == 1)
    before = env[a - 144:a]                                    # 3 ms at 48 kHz
    assert 0 < before[0] < 1 and np.all(np.diff(before) < 0)
    after = env[b:b + 144]
    assert 0 < after[-1] < 1 and np.all(np.diff(after) > 0)


def test_a_fade_out_falls_from_full_to_nothing_across_the_selection_only():
    env = render.region_envelope(RATE * 4, RATE,
                                 [{'op': 'fade', 'direction': 'out', 'start_seconds': 1.0, 'end_seconds': 2.0}])
    assert env[RATE] == pytest.approx(1.0) and env[2 * RATE - 1] == pytest.approx(0.0, abs=1e-9)
    assert np.all(np.diff(env[RATE:2 * RATE]) <= 1e-12)
    assert np.all(env[:RATE] == 1) and np.all(env[2 * RATE:] == 1)


def test_a_fade_in_rises_across_the_selection():
    env = render.region_envelope(RATE * 4, RATE,
                                 [{'op': 'fade', 'direction': 'in', 'start_seconds': 0.0, 'end_seconds': 0.5}])
    assert env[0] == 0.0 and env[int(0.5 * RATE) - 1] == pytest.approx(1.0)
    assert np.all(np.diff(env[:int(0.5 * RATE)]) >= -1e-12)


def test_region_gain_changes_the_level_inside_by_the_stated_amount():
    env = render.region_envelope(RATE * 4, RATE,
                                 [{'op': 'gain', 'gain_db': 6.0, 'start_seconds': 1.0, 'end_seconds': 2.0}])
    assert env[int(1.5 * RATE)] == pytest.approx(10 ** (6 / 20)) and env[0] == 1.0 and env[-1] == 1.0


def test_overlapping_operations_multiply():
    env = render.region_envelope(RATE * 4, RATE, [
        {'op': 'gain', 'gain_db': -6.0, 'start_seconds': 1.0, 'end_seconds': 3.0},
        {'op': 'gain', 'gain_db': -6.0, 'start_seconds': 2.0, 'end_seconds': 3.0}])
    assert env[int(1.5 * RATE)] == pytest.approx(10 ** (-6 / 20))
    assert env[int(2.5 * RATE)] == pytest.approx(10 ** (-12 / 20))


def test_regions_keep_a_stereo_layout():
    x = np.stack([speech(2.0), speech(2.0)], axis=1)
    y = render.apply_regions(x, RATE, [{'op': 'silence', 'start_seconds': 0.5, 'end_seconds': 1.0}])
    assert y.shape == x.shape and not y[int(0.75 * RATE)].any()


# ---- the pipeline -----------------------------------------------------------------------------------------------------

def cut_only(x, *cuts):
    h = History(seconds=len(x) / RATE)
    for a, b in cuts:
        h.cut(a, b)
    return h


def test_an_operation_inside_a_cut_leaves_no_trace():
    x = speech(6.0)
    plain = cut_only(x, (0.5, 2.5))
    shaped = cut_only(x, (0.5, 2.5))
    shaped.silence(1.0, 2.0)
    shaped.gain(12.0, 1.2, 1.8)
    assert np.array_equal(render.process(x, RATE, plain), render.process(x, RATE, shaped))


def test_cuts_snap_on_the_master_so_other_operations_cannot_move_them():
    x = tone(4.0, freq=100.0)
    start = 0.5 + 37 / RATE
    plain, shaped = History(seconds=4.0), History(seconds=4.0)
    plain.cut(start, 1.0)
    shaped.silence(0.49, 0.52)
    shaped.cut(start, 1.0)
    assert len(render.cut_stage(x, RATE, plain)) == len(render.cut_stage(x, RATE, shaped))


def test_the_level_stage_comes_last_and_before_level_leaves_it_out():
    x = speech(6.0, amp=0.05)
    h = History(seconds=6.0)
    h.set_chain('level', 'Level', gain_db=6.0)
    before = render.before_level(x, RATE, h)
    assert np.array_equal(before, ac.to_mono(x))
    assert np.allclose(render.process(x, RATE, h), ac.apply_gain(before, 6.0))


def test_filters_run_before_the_cleaner_and_the_cleaner_before_the_gain():
    order = []

    def cleaner(mono, rate, nf):
        order.append('clean')
        return mono

    x = speech(3.0)
    h = History(seconds=3.0, chain={'highpass': {'enabled': True}, 'denoise': {'enabled': True},
                                    'level': {'gain_db': 3.0}})
    seen = render.before_level(x, RATE, h, cleaner)
    assert order == ['clean'] and not np.array_equal(seen, ac.to_mono(x))        # the high-pass had run already


def test_a_stage_is_computed_once_for_the_same_settings():
    calls = []

    def cleaner(mono, rate, nf):
        calls.append(nf)
        return mono * 0.5

    x = speech(6.0)
    cache = render.RenderCache()
    h = History(seconds=6.0, chain={'denoise': {'enabled': True, 'nf': -25.0}})
    first = render.process(x, RATE, h, cleaner, cache, 'take')
    assert np.array_equal(first, render.process(x, RATE, h, cleaner, cache, 'take'))
    assert calls == [-25.0]
    h.set_chain('level', 'Level', gain_db=6.0)                    # gain comes after the cleaner: no new run
    render.process(x, RATE, h, cleaner, cache, 'take')
    assert calls == [-25.0]
    h.set_chain('denoise', 'nf', nf=-30.0)
    render.process(x, RATE, h, cleaner, cache, 'take')
    assert calls == [-25.0, -30.0]
    h.cut(1.0, 2.0)
    render.process(x, RATE, h, cleaner, cache, 'take')
    assert calls == [-25.0, -30.0, -30.0]


def test_undoing_back_to_an_earlier_recipe_reuses_its_result():
    calls = []

    def cleaner(mono, rate, nf):
        calls.append(nf)
        return mono

    x = speech(6.0)
    cache = render.RenderCache()
    h = History(seconds=6.0, chain={'denoise': {'enabled': True}})
    render.process(x, RATE, h, cleaner, cache, 'take')
    h.cut(1.0, 2.0)
    render.process(x, RATE, h, cleaner, cache, 'take')
    h.undo()
    render.process(x, RATE, h, cleaner, cache, 'take')
    assert len(calls) == 2


def test_without_a_cache_or_an_id_nothing_is_remembered():
    calls = []
    x = speech(3.0)
    h = History(seconds=3.0, chain={'denoise': {'enabled': True}})
    for _ in range(2):
        render.process(x, RATE, h, lambda m, r, nf: calls.append(1) or m)
    assert len(calls) == 2


def test_another_take_does_not_get_this_takes_cached_result():
    calls = []
    cache = render.RenderCache()
    h = History(seconds=3.0, chain={'denoise': {'enabled': True}})
    for ident in ('a', 'b'):
        render.process(speech(3.0), RATE, h, lambda m, r, nf: calls.append(ident) or m, cache, ident)
    assert calls == ['a', 'b']


def test_cleaning_that_is_on_without_a_cleaner_is_an_error():
    h = History(seconds=3.0, chain={'denoise': {'enabled': True}})
    with pytest.raises(ValueError, match='no cleaner'):
        render.process(speech(3.0), RATE, h)


def test_the_cache_forgets_the_oldest_results_and_can_drop_a_take():
    cache = render.RenderCache(limit=2)
    cache.put(('a', 1), 1)
    cache.put(('b', 1), 2)
    cache.get(('a', 1))
    cache.put(('c', 1), 3)
    assert cache.get(('b', 1)) is None and cache.get(('a', 1)) == 1 and len(cache) == 2
    cache.drop('a')
    assert cache.get(('a', 1)) is None


def test_the_export_is_mono_24k_and_clipped_to_full_scale():
    x = np.stack([speech(4.0, amp=0.9), speech(4.0, amp=0.9)], axis=1)
    h = History(seconds=4.0)
    h.set_chain('level', 'Level', gain_db=20.0)
    out = render.export_samples(x, RATE, h)
    assert out.ndim == 1 and abs(len(out) - 4 * ac.EXPORT_RATE) <= 2
    assert float(np.abs(out).max()) <= 1.0


# ---- master time and edited time --------------------------------------------------------------------------------------

def test_edited_time_maps_back_to_the_master_timeline():
    segments = [(0, RATE * 2), (RATE * 5, RATE * 9)]       # master 0-2 s and 5-9 s are kept
    assert render.edited_to_master(0.5, segments, RATE) == pytest.approx(0.5)
    assert render.edited_to_master(2.5, segments, RATE) == pytest.approx(5.5)
    assert render.edited_to_master(99, segments, RATE) == pytest.approx(9.0)
    assert render.edited_to_master(1.0, [], RATE) == 0.0


def test_master_time_maps_onto_the_edited_timeline():
    segments = [(0, 2 * RATE), (5 * RATE, 9 * RATE)]       # cuts at 2 to 5 s and 9 to 10 s
    to_edited = render.master_to_edited
    assert to_edited(1.0, segments, RATE) == pytest.approx(1.0)
    assert to_edited(3.0, segments, RATE) == pytest.approx(2.0)   # inside a cut: the next kept part starts here
    assert to_edited(6.0, segments, RATE) == pytest.approx(3.0)
    assert to_edited(9.5, segments, RATE) == pytest.approx(6.0)


def test_segments_and_joins_follow_the_cuts():
    x = speech(10.0)
    h = cut_only(x, (2.0, 5.0), (9.0, 10.0))
    segments = render.segments_of(x, RATE, h.edits)
    assert len(segments) == 2 and segments[0][0] == 0
    assert render.join_positions(segments) == [segments[0][1] - segments[0][0]]
    assert render.join_positions([(0, 10)]) == []
