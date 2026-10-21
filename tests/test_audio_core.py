import numpy as np
import pytest

from helpers import RATE, tone
from recorder import audio_core as ac


def ramp(seconds, rate=RATE):
    """Each sample is its index / N: every sample value is unique, so content checks are exact."""
    n = int(round(seconds * rate))
    return (np.arange(n, dtype=np.float32) / n - 0.5) * 0.8


# ---- measurements ---------------------------------------------------------------------------------------------------

def test_measure_reports_length_and_levels_of_a_known_sine():
    x = tone(2.0, amp=0.5)
    m = ac.measure(x, RATE)
    assert m['seconds'] == pytest.approx(2.0, abs=1e-3)
    assert m['peak_dbfs'] == pytest.approx(20 * np.log10(0.5), abs=0.05)
    assert m['rms_dbfs'] == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.05)
    assert m['clipped'] is False
    assert m['silence_fraction'] == 0.0


def test_silence_is_all_silent_frames():
    m = ac.measure(np.zeros(RATE, np.float32), RATE)
    assert m['silence_fraction'] == 1.0
    assert m['rms_dbfs'] < -200


def test_full_scale_samples_flag_clipping_and_count_towards_the_tenth_of_a_percent_rule():
    x = np.concatenate([tone(1.0), np.ones(480, np.float32)])
    m = ac.measure(x, RATE)
    assert m['clipped'] is True
    assert m['clipped_fraction'] > 0


def test_stereo_is_measured_on_the_mono_mix():
    left, right = tone(1.0, amp=0.4), np.zeros(RATE, np.float32)
    stereo = np.stack([left, right], axis=1)
    assert ac.measure(stereo, RATE)['peak_dbfs'] == pytest.approx(20 * np.log10(0.2), abs=0.05)


# ---- resampling -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('rate_in', [44100, 48000, 16000])
def test_resample_to_24k_gives_the_expected_length(rate_in):
    x = np.zeros(rate_in * 3, np.float32)
    y = ac.resample(x, rate_in, ac.EXPORT_RATE)
    assert abs(len(y) - 3 * ac.EXPORT_RATE) <= 1


def test_resample_keeps_a_tone_at_its_frequency():
    rate_in = 48000
    y = ac.resample(tone(1.0, freq=1000.0, rate=rate_in), rate_in, ac.EXPORT_RATE)
    spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), 1 / ac.EXPORT_RATE)
    assert freqs[np.argmax(spectrum)] == pytest.approx(1000.0, abs=2.0)


# ---- cuts -----------------------------------------------------------------------------------------------------------

def test_cut_from_the_start_removes_the_leading_seconds_and_keeps_the_rest_exactly():
    x = ramp(4.0)
    cut = ac.render(x, RATE, [{'start_seconds': 0.0, 'end_seconds': 1.0}], fade_ms=0)
    assert len(cut) == len(x) - RATE
    assert np.array_equal(cut, x[RATE:])


def test_cut_from_the_end_removes_the_trailing_seconds():
    x = ramp(4.0)
    cut = ac.render(x, RATE, [{'start_seconds': 3.0, 'end_seconds': 4.0}], fade_ms=0)
    assert len(cut) == 3 * RATE
    assert np.array_equal(cut, x[: 3 * RATE])


def test_middle_cut_joins_the_two_sides_with_a_fade_and_loses_one_fade_length():
    x = ramp(4.0)
    fade_ms = 10
    cut = ac.render(x, RATE, [{'start_seconds': 1.5, 'end_seconds': 2.5}], fade_ms=fade_ms)
    k = int(round(fade_ms / 1000 * RATE))
    assert abs(len(cut) - (len(x) - RATE)) <= k + 1
    # the audio before the cut is unchanged up to the seam; after the seam it is unchanged from the far side
    seam_start = int(round(1.5 * RATE)) - k
    assert np.array_equal(cut[:seam_start], x[:seam_start])
    assert np.array_equal(cut[-(len(x) - int(round(2.5 * RATE)) - k):], x[int(round(2.5 * RATE)) + k:])


def test_zero_fade_join_is_a_plain_splice():
    x = ramp(4.0)
    cut = ac.render(x, RATE, [{'start_seconds': 1.0, 'end_seconds': 2.0}], fade_ms=0)
    assert np.array_equal(cut, np.concatenate([x[:RATE], x[2 * RATE:]]))


def test_several_cuts_merge_and_apply_in_master_time():
    x = ramp(6.0)
    edits = [{'start_seconds': 4.0, 'end_seconds': 5.0}, {'start_seconds': 0.0, 'end_seconds': 0.5},
             {'start_seconds': 0.4, 'end_seconds': 1.0}]
    cut = ac.render(x, RATE, edits, fade_ms=0)
    # 0 to 1.0 s and 4 to 5 s are removed (the first two overlap and merge); 4 s remain
    assert len(cut) == 4 * RATE
    assert np.array_equal(cut[: 3 * RATE], x[RATE: 4 * RATE])
    assert np.array_equal(cut[3 * RATE:], x[5 * RATE:])


def test_only_cuts_are_cuts():
    x = ramp(4.0)
    edits = [{'op': 'silence', 'start_seconds': 1.0, 'end_seconds': 2.0},
             {'op': 'gain', 'gain_db': 3.0, 'start_seconds': 0.0, 'end_seconds': 1.0},
             {'op': 'cut', 'start_seconds': 3.0, 'end_seconds': 4.0}]
    assert ac.cut_ranges(edits, RATE, len(x)) == [(3 * RATE, 4 * RATE)]
    assert len(ac.render(x, RATE, edits, fade_ms=0)) == 3 * RATE


def test_cut_points_snap_to_a_zero_crossing_within_5_ms():
    x = tone(2.0, freq=100.0)
    mono = ac.to_mono(x)
    start = int(0.5 * RATE) + 37  # not on a crossing
    snapped = ac._snap(mono, start, RATE)
    assert abs(snapped - start) <= int(0.005 * RATE) + 1
    assert np.signbit(mono[snapped - 1]) != np.signbit(mono[snapped])


def test_cuts_snap_on_the_signal_given_not_on_the_samples_being_cut():
    x = tone(2.0, freq=100.0)
    flat = np.full_like(x, 0.1)                       # no zero crossings of its own
    edits = [{'start_seconds': 0.5 + 37 / RATE, 'end_seconds': 1.0}]
    snapped = ac.render(flat, RATE, edits, fade_ms=0, snap_to=x)
    unsnapped = ac.render(flat, RATE, edits, fade_ms=0)
    assert len(snapped) != len(unsnapped)


def test_a_cut_that_removes_everything_is_refused():
    with pytest.raises(ValueError, match='whole clip'):
        ac.render(tone(2.0), RATE, [{'start_seconds': 0.0, 'end_seconds': 2.0}])


def test_render_keeps_stereo_layout():
    x = np.stack([ramp(3.0), ramp(3.0)], axis=1)
    cut = ac.render(x, RATE, [{'start_seconds': 1.0, 'end_seconds': 2.0}], fade_ms=0)
    assert cut.shape == (2 * RATE, 2)


# ---- warnings and keep rules ----------------------------------------------------------------------------------------

def stats(seconds=12.0, rms=-20.0, clipped=False, silence=0.0):
    return {'seconds': seconds, 'rms_dbfs': rms, 'clipped': clipped, 'silence_fraction': silence}


def blocking(warnings):
    return [w.code for w in warnings if w.blocks]


def test_short_and_long_clips_block_or_warn_at_the_documented_limits():
    assert blocking(ac.take_warnings(stats(seconds=5.9))) == ['short']
    assert blocking(ac.take_warnings(stats(seconds=6.0))) == []
    assert blocking(ac.take_warnings(stats(seconds=40.0))) == []
    assert blocking(ac.take_warnings(stats(seconds=40.1))) == ['long']
    assert [w.code for w in ac.take_warnings(stats(seconds=30.0))] == ['target']


def test_silence_blocks_and_quiet_only_warns():
    assert blocking(ac.take_warnings(stats(rms=-51))) == ['silent']
    codes = [w.code for w in ac.take_warnings(stats(rms=-40))]
    assert codes == ['quiet']


def test_clipping_and_mostly_silence_warn_without_blocking():
    codes = [w.code for w in ac.take_warnings(stats(clipped=True, silence=0.5))]
    assert codes == ['clipped', 'mostly_silent']
    assert blocking(ac.take_warnings(stats(clipped=True, silence=0.5))) == []


def test_interrupted_flag_warns():
    assert [w.code for w in ac.take_warnings(stats(), ['interrupted'])] == ['interrupted']


def good_meta(**over):
    meta = {'transcript': 'This is the exact text.', 'origin': 'my room', 'allowed_use': 'my own voice',
            'consent_confirmed': True}
    meta.update(over)
    return meta


def test_keep_problems_empty_for_a_complete_take():
    assert ac.keep_problems(good_meta(), ac.take_warnings(stats())) == []


def test_keep_problems_name_each_missing_field():
    problems = ac.keep_problems(good_meta(transcript='ab', origin=' ', allowed_use='', consent_confirmed=False),
                                ac.take_warnings(stats(seconds=2)))
    text = ' '.join(problems)
    assert '3 to 600' in text and 'origin' in text and 'allowed use' in text and 'permission' in text
    assert 'Too short' in text


def test_transcript_limits():
    assert ac.keep_problems(good_meta(transcript='x' * 600), ac.take_warnings(stats())) == []
    assert ac.keep_problems(good_meta(transcript='x' * 601), ac.take_warnings(stats()))


def test_consent_must_be_exactly_true():
    assert ac.keep_problems(good_meta(consent_confirmed='yes'), ac.take_warnings(stats()))


def test_script_lines_skip_blank_lines_and_trim_spaces():
    assert ac.script_lines('  First line \n\n second\r\n   \nthird') == ['First line', 'second', 'third']
