import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from helpers import RATE, bursts, speech, tone
from recorder import audio_core as ac
from recorder.core import analysis

EXPORT = ac.EXPORT_RATE


def status(report, key):
    return next(row for row in report if row.key == key).status


# ---- the peak pyramid -------------------------------------------------------------------------------------------------

def brute(x, start, end, count):
    edges = np.round(start + np.arange(count + 1) * (end - start) / count).astype(int)
    edges[-1] = end
    return (np.array([x[edges[i]:max(edges[i + 1], edges[i] + 1)].min() for i in range(count)]),
            np.array([x[edges[i]:max(edges[i + 1], edges[i] + 1)].max() for i in range(count)]))


@pytest.mark.parametrize('start, end, count', [(30000, 31000, 200), (50000, 150000, 700), (0, 192000, 800)])
def test_columns_are_exact_until_there_are_hundreds_of_samples_each(start, end, count):
    x = speech(4.0)
    lo, hi = analysis.PeakPyramid(x).columns(start, end, count)
    exp_lo, exp_hi = brute(x, start, end, count)
    assert np.array_equal(lo, exp_lo) and np.array_equal(hi, exp_hi)


@pytest.mark.parametrize('start, end, count', [(0, 40 * RATE, 800), (5 * RATE, 35 * RATE, 600), (RATE, 3 * RATE, 40)])
def test_zoomed_out_columns_stay_within_a_block_of_the_true_peaks(start, end, count):
    x = speech(40.0)
    lo, hi = analysis.PeakPyramid(x).columns(start, end, count)
    exp_lo, exp_hi = brute(x, start, end, count)
    assert lo.shape == hi.shape == (count,) and np.all(lo <= hi)
    assert float(hi.max()) == pytest.approx(float(x[start:end].max()), abs=1e-3)
    assert float(lo.min()) == pytest.approx(float(x[start:end].min()), abs=1e-3)
    assert float(np.max(np.abs(hi - exp_hi))) < 0.03 and float(np.max(np.abs(lo - exp_lo))) < 0.03


def test_the_last_column_does_not_reach_past_the_window():
    x = np.zeros(RATE * 40, np.float32)
    x[RATE * 30] = 1.0                                          # a spike well after the window
    lo, hi = analysis.PeakPyramid(x).columns(0, 20 * RATE, 100)
    assert float(hi.max()) == 0.0


def test_empty_and_tiny_inputs_are_safe():
    assert analysis.PeakPyramid(np.zeros(0, np.float32)).columns(0, 10, 5)[1].tolist() == [0.0] * 5
    tiny = analysis.PeakPyramid(np.array([0.1, -0.2, 0.3], np.float32))
    lo, hi = tiny.columns(0, 3, 3)
    assert lo.tolist() == pytest.approx([0.1, -0.2, 0.3]) and hi.tolist() == pytest.approx([0.1, -0.2, 0.3])
    assert tiny.columns(2, 2, 4)[0].tolist() == [0.0] * 4


def brute_rms(x, start, end, count):
    edges = np.round(start + np.arange(count + 1) * (end - start) / count).astype(int)
    edges[-1] = end
    return np.array([np.sqrt(np.mean(x[edges[i]:max(edges[i + 1], edges[i] + 1)].astype(np.float64) ** 2))
                     for i in range(count)])


@pytest.mark.parametrize('start, end, count', [(30000, 31000, 200), (50000, 150000, 700), (0, 192000, 800)])
def test_the_rms_body_is_exact_until_there_are_hundreds_of_samples_each(start, end, count):
    x = speech(4.0)
    assert np.allclose(analysis.PeakPyramid(x).body(start, end, count), brute_rms(x, start, end, count), atol=1e-6)


@pytest.mark.parametrize('start, end, count', [(0, 40 * RATE, 800), (5 * RATE, 35 * RATE, 600), (RATE, 3 * RATE, 40)])
def test_the_zoomed_out_rms_body_stays_close_to_the_true_rms(start, end, count):
    x = speech(40.0)
    body = analysis.PeakPyramid(x).body(start, end, count)
    assert body.shape == (count,) and float(np.max(np.abs(body - brute_rms(x, start, end, count)))) < 0.02
    lo, hi = analysis.PeakPyramid(x).columns(start, end, count)
    assert np.all(body <= np.maximum(hi, -lo) + 1e-6)            # the body never pokes out of the peaks


def test_the_rms_body_of_a_constant_signal_is_that_constant_at_every_zoom():
    x = np.full(RATE * 20, 0.25, np.float32)
    pyramid = analysis.PeakPyramid(x)
    for start, end, count in ((0, 20 * RATE, 500), (RATE, 2 * RATE, 300), (0, 40000, 9)):
        assert np.allclose(pyramid.body(start, end, count), 0.25, atol=1e-6)


def test_the_rms_body_of_nothing_is_nothing():
    assert analysis.PeakPyramid(np.zeros(0, np.float32)).body(0, 10, 5).tolist() == [0.0] * 5
    assert analysis.PeakPyramid(np.ones(100, np.float32)).body(50, 50, 3).tolist() == [0.0] * 3


# ---- the spectrogram --------------------------------------------------------------------------------------------------

def test_a_tone_shows_up_in_its_frequency_bin_at_its_level():
    sp = analysis.spectrogram(tone(2.0, freq=1500.0, amp=0.5), RATE)
    assert sp.db.shape[0] == int(12000 / sp.bin_hz) + 1
    middle = sp.db.shape[1] // 2
    loudest = int(np.argmax(sp.db[:, middle]))
    assert loudest * sp.bin_hz == pytest.approx(1500.0, abs=sp.bin_hz)
    assert float(sp.db[loudest, middle]) == pytest.approx(20 * np.log10(0.5), abs=1.5)
    assert sp.hop_seconds == pytest.approx(0.005, abs=0.001)


def test_a_long_take_is_given_fewer_frames_to_stay_small():
    sp = analysis.spectrogram(np.zeros(RATE * 600, np.float32), RATE, max_frames=2000)
    assert sp.db.shape[1] <= 2100


# ---- levels -----------------------------------------------------------------------------------------------------------

def test_loudness_of_a_full_scale_997_hz_sine_is_minus_3_lufs():
    x = tone(10.0, freq=997.0, amp=1.0)
    assert analysis.loudness_lufs(x, RATE) == pytest.approx(-3.0, abs=0.15)
    assert analysis.loudness_lufs(x * 0.5, RATE) == pytest.approx(-9.0, abs=0.15)
    assert analysis.loudness_lufs(ac.resample(x, RATE, EXPORT), EXPORT) == pytest.approx(-3.0, abs=0.2)


def test_loudness_ignores_the_silence_between_the_words():
    both = bursts(10.0, [(1.0, 3.0), (6.0, 8.0)], amp=0.3)
    one = bursts(10.0, [(1.0, 3.0)], amp=0.3)
    assert analysis.loudness_lufs(both, RATE) == pytest.approx(analysis.loudness_lufs(one, RATE), abs=0.5)
    assert analysis.loudness_lufs(np.zeros(RATE * 2, np.float32), RATE) == -120.0


def test_the_noise_floor_is_found_in_the_pauses_and_the_snr_follows():
    x = bursts(10.0, [(1.0, 3.0), (5.0, 8.0)], amp=0.2, noise=0.0005)
    assert analysis.noise_floor_db(x, RATE) == pytest.approx(20 * np.log10(0.0005), abs=2.5)
    assert analysis.snr_db(x, RATE) > 35
    assert analysis.noise_floor_db(np.zeros(RATE, np.float32), RATE) == -120.0


def test_the_noise_floor_does_not_rise_when_the_pauses_are_cut_short():
    """With under a tenth of the frames in pauses, the quietest tenth reaches into the speech. The quietest stretch does not."""
    floor = 20 * np.log10(0.0005)
    spans = [(0.1 + 3.22 * i, 0.1 + 3.22 * i + 3.0) for i in range(5)]       # 0.22 s gaps: about 7% of the clip is pause
    tight = bursts(16.1, spans, amp=0.14, noise=0.0005)
    roomy = bursts(20.0, [(2.0, 4.5), (7.0, 9.5), (12.0, 14.5), (17.0, 19.5)], amp=0.14, noise=0.0005)
    assert analysis.noise_floor_db(tight, RATE) == pytest.approx(floor, abs=3.0)
    assert analysis.noise_floor_db(roomy, RATE) == pytest.approx(floor, abs=3.0)
    assert analysis.snr_db(tight, RATE) == pytest.approx(analysis.snr_db(roomy, RATE), abs=3.0)


def test_one_loud_clip_with_no_pause_long_enough_still_has_a_high_floor():
    x = bursts(10.0, [(0.0, 4.9), (5.0, 10.0)], amp=0.14, noise=0.0005)        # a 0.1 s gap is not a stretch to measure
    assert analysis.noise_floor_db(x, RATE) > -50.0


# ---- pauses -----------------------------------------------------------------------------------------------------------

SPANS = [(2.0, 4.0), (5.0, 8.0)]


def test_silence_at_the_ends_is_measured():
    lead, tail = analysis.edge_silence(bursts(10.0, SPANS), RATE)
    assert lead == pytest.approx(2.0, abs=0.1) and tail == pytest.approx(2.0, abs=0.1)
    assert analysis.edge_silence(bursts(10.0, [(0.0, 10.0)]), RATE) == (0.0, 0.0)


def test_auto_trim_leaves_a_short_margin_of_pause():
    (a0, b0), (a1, b1) = analysis.auto_trim_ranges(bursts(10.0, SPANS), RATE, margin=0.15)
    assert a0 == 0.0 and b0 == pytest.approx(2.0 - 0.15, abs=0.15)
    assert a1 == pytest.approx(8.0 + 0.15, abs=0.15) and b1 == pytest.approx(10.0)


def test_auto_trim_does_nothing_when_the_ends_are_already_tight():
    assert analysis.auto_trim_ranges(bursts(10.0, [(0.1, 9.9)]), RATE) == []


def test_shorten_pauses_cuts_the_middle_of_long_pauses_only():
    x = bursts(12.0, [(0.5, 2.0), (4.0, 6.0), (6.2, 8.0), (11.0, 11.8)])
    (a, b), (c, d) = analysis.shorten_pause_ranges(x, RATE, keep_seconds=0.5)   # 2-4 s and 8-11 s; 6.0-6.2 s is short
    assert a == pytest.approx(2.0 + 0.25, abs=0.15) and b == pytest.approx(4.0 - 0.25, abs=0.15)
    assert c == pytest.approx(8.0 + 0.25, abs=0.15) and d == pytest.approx(11.0 - 0.25, abs=0.15)


def test_shorten_pauses_leaves_the_ends_to_auto_trim():
    ranges = analysis.shorten_pause_ranges(bursts(10.0, SPANS), RATE, keep_seconds=0.5)
    assert len(ranges) == 1 and 4.0 < ranges[0][0] < ranges[0][1] < 5.0 + 0.01      # only the 4-5 s pause is long
    assert analysis.shorten_pause_ranges(bursts(10.0, [(1.0, 9.0)]), RATE) == []


def test_silent_runs_report_the_pauses_in_seconds():
    runs = analysis.silent_runs(bursts(10.0, SPANS), RATE, min_seconds=0.5)
    assert len(runs) == 3 and runs[0][0] == 0.0 and runs[2][1] == pytest.approx(10.0, abs=0.05)
    assert runs[1] == pytest.approx((4.0, 5.0), abs=0.1)


# ---- bandwidth and hum ------------------------------------------------------------------------------------------------

def test_bandwidth_shows_a_band_limited_source():
    noise = np.random.default_rng(3).normal(0, 0.1, EXPORT * 4)
    full = analysis.bandwidth_hz(noise.astype(np.float32), EXPORT)
    sos = butter(8, 4000, btype='lowpass', fs=EXPORT, output='sos')
    limited = analysis.bandwidth_hz(sosfilt(sos, noise).astype(np.float32), EXPORT)
    assert full > 11000 and 3400 <= limited <= 5500


def test_hum_is_found_at_the_mains_frequency_and_absent_without_it():
    base = bursts(10.0, SPANS, noise=0.0005)
    assert analysis.detect_hum(base, RATE) is None
    for mains in (50.0, 60.0):
        hummed = base + tone(10.0, freq=mains, amp=0.002) + tone(10.0, freq=2 * mains, amp=0.001)
        found = analysis.detect_hum(hummed, RATE)
        assert found is not None and found[0] == mains and found[1] >= 12.0


def test_hum_needs_a_quiet_stretch_to_look_at():
    assert analysis.detect_hum(speech(0.3), RATE) is None
    assert analysis.detect_hum(bursts(10.0, [(0.0, 10.0)]), RATE) is None


# ---- the Check tab ----------------------------------------------------------------------------------------------------

def good_clip():
    x = bursts(14.0, [(0.3, 4.0), (5.0, 9.0), (10.0, 13.7)], amp=0.14, noise=0.0003)
    return ac.resample(x, RATE, EXPORT)


def test_a_clean_clip_passes_every_check():
    report = analysis.quality_report(good_clip())
    assert [row.key for row in report] == ['length', 'speech', 'peak', 'clipping', 'loudness', 'noise', 'bandwidth',
                                            'hum', 'ends', 'silence']
    assert [row.key for row in report if row.status not in ('ok', 'info')] == []


def test_each_problem_shows_up_on_its_own_row():
    clip = good_clip()
    clipped = clip.copy()
    clipped[:600] = 1.0
    assert status(analysis.quality_report(clipped), 'clipping') == 'bad'
    assert status(analysis.quality_report(clip[: 8 * EXPORT]), 'length') == 'warn'
    assert status(analysis.quality_report(clip[: 2 * EXPORT]), 'length') == 'bad'
    hummed = clip + tone(14.0, freq=60.0, amp=0.003, rate=EXPORT) + tone(14.0, freq=120.0, amp=0.002, rate=EXPORT)
    assert status(analysis.quality_report(hummed), 'hum') == 'warn'
    noisy = clip + np.random.default_rng(1).normal(0, 0.01, len(clip)).astype(np.float32)
    assert status(analysis.quality_report(noisy), 'noise') == 'bad'
    padded = np.concatenate([np.zeros(3 * EXPORT, np.float32), clip])
    assert status(analysis.quality_report(padded), 'ends') == 'warn'
    assert status(analysis.quality_report(clip * 0.1), 'speech') == 'bad'


# ---- what cleaning took from the voice ---------------------------------------------------------------------------------

TALKING = [(0.5, 2.5), (3.0, 5.0), (5.5, 7.5), (8.0, 10.0)]


def noisy():
    return bursts(10.5, TALKING, noise=0.003, f0=137.0)


def test_cleaning_that_takes_only_the_noise_costs_the_voice_nothing():
    x = noisy()
    frame = int(RATE * ac.FRAME_SECONDS)
    y = x.copy()
    db = analysis.frame_db(x, RATE)
    for index in np.nonzero(db <= analysis.speech_threshold_db(x, RATE))[0]:
        y[index * frame:(index + 1) * frame] *= 0.05
    assert analysis.voice_loss_db(x, y, RATE) < 1.0


def test_cleaning_that_takes_the_top_of_the_voice_is_seen():
    x = noisy()
    sos = butter(8, 1000.0, 'low', fs=RATE, output='sos')
    assert analysis.voice_loss_db(x, sosfilt(sos, x).astype(np.float32), RATE) > 10.0


def test_a_band_where_the_voice_is_buried_in_the_noise_says_nothing():
    x = bursts(10.5, TALKING, noise=0.05, f0=137.0, amp=0.02)
    assert analysis.voice_loss_db(x, np.zeros_like(x), RATE) == 0.0


@pytest.mark.parametrize('x', [tone(6.0), np.zeros(6 * RATE, np.float32), np.zeros(0, np.float32)],
                         ids=['no pauses', 'silence', 'nothing'])
def test_with_no_pauses_or_no_speech_there_is_nothing_to_compare(x):
    assert analysis.voice_loss_db(x, x, RATE) == 0.0


def test_a_shorter_clip_after_cleaning_is_compared_as_far_as_it_goes():
    x = noisy()
    assert analysis.voice_loss_db(x, x[:len(x) - 100], RATE) < 1.0


def test_the_check_tab_reports_clipping_in_the_recording_behind_a_clean_clip():
    rows = {row.key: row for row in analysis.quality_report(tone(12.0, amp=0.1, rate=ac.EXPORT_RATE), recorded_clipped=0.3)}
    assert rows['clipping'].status == 'bad' and rows['clipping'].value == '30.000% of samples'
    assert 'limiter' in rows['clipping'].hint
