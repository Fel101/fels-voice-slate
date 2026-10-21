import numpy as np
import pytest

from helpers import RATE, speech, tone, with_pop
from recorder import audio_core as ac
from recorder.core import chain


def level_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x, np.float64) ** 2)) + 1e-12)


# ---- settings ---------------------------------------------------------------------------------------------------------

def test_defaults_are_complete_and_everything_is_off():
    c = chain.normalize()
    assert set(c) == {'highpass', 'hum', 'denoise', 'level', 'limiter'}
    assert not (c['highpass']['enabled'] or c['hum']['enabled'] or c['denoise']['enabled'] or c['limiter']['enabled'])
    assert c['level']['gain_db'] == 0.0


def test_normalize_fills_in_missing_values_and_ignores_unknown_keys():
    c = chain.normalize({'highpass': {'enabled': True}, 'future': {'x': 1}, 'level': {'gain_db': 3, 'extra': 1}})
    assert c['highpass'] == {'enabled': True, 'hz': 80.0}
    assert c['level'] == {'gain_db': 3.0} and 'future' not in c


@pytest.mark.parametrize('bad', [
    {'level': {'gain_db': float('nan')}}, {'level': {'gain_db': 99}}, {'level': {'gain_db': True}},
    {'level': {'gain_db': 'loud'}}, {'highpass': {'hz': 5}}, {'hum': {'harmonics': 9}},
    {'denoise': {'nf': -10}}, {'limiter': {'ceiling_db': 3}}, {'highpass': {'enabled': 'yes'}},
    {'level': 5}, 'chain',
])
def test_bad_settings_are_refused(bad):
    with pytest.raises(ValueError):
        chain.normalize(bad)


def test_with_values_returns_a_checked_copy():
    base = chain.normalize()
    changed = chain.with_values(base, 'level', gain_db=6.0)
    assert changed['level']['gain_db'] == 6.0 and base['level']['gain_db'] == 0.0
    with pytest.raises(ValueError):
        chain.with_values(base, 'level', gain_db=100.0)


# ---- filters ----------------------------------------------------------------------------------------------------------

def test_the_high_pass_removes_rumble_and_leaves_the_voice_band():
    mid = slice(RATE, 2 * RATE)                       # away from the filter's edges
    rumble, voice = tone(3.0, freq=40.0, amp=0.3), tone(3.0, freq=1000.0, amp=0.1)
    assert level_db(chain.highpass(rumble, RATE, 100.0)[mid]) < level_db(rumble[mid]) - 20
    assert level_db(chain.highpass(voice, RATE, 100.0)[mid]) == pytest.approx(level_db(voice[mid]), abs=0.3)


def test_the_high_pass_keeps_the_length_and_does_not_shift_the_audio():
    x = speech(2.0)
    y = chain.highpass(x, RATE, 80.0)
    assert len(y) == len(x)
    window = slice(RATE // 2, RATE // 2 + 4800)
    lag = np.argmax(np.correlate(y[window], x[window], 'full')) - (4800 - 1)
    assert abs(lag) <= 1


def test_the_hum_notch_removes_mains_and_its_harmonics():
    hum = tone(3.0, freq=60.0, amp=0.05) + tone(3.0, freq=120.0, amp=0.03) + tone(3.0, freq=180.0, amp=0.02)
    x = hum + tone(3.0, freq=1000.0, amp=0.1)
    y = chain.hum_notch(x, RATE, 60.0, 3)
    mid = slice(RATE, 2 * RATE)
    assert level_db(y[mid]) == pytest.approx(level_db(tone(3.0, freq=1000.0, amp=0.1)[mid]), abs=0.3)
    assert level_db(chain.hum_notch(hum, RATE, 60.0, 3)[mid]) < level_db(hum[mid]) - 30


def test_short_clips_pass_through_the_filters_untouched():
    x = np.ones(100, np.float32)
    assert chain.highpass(x, RATE, 80.0) is x
    assert chain.hum_notch(x, RATE, 60.0, 3) is x


def test_apply_filters_does_nothing_when_both_are_off():
    x = speech(1.0)
    assert chain.apply_filters(x, RATE, chain.normalize()) is x
    on = chain.normalize({'highpass': {'enabled': True}})
    assert not np.array_equal(chain.apply_filters(x, RATE, on), x)


def test_the_filter_key_changes_only_with_the_filter_settings():
    a = chain.normalize()
    b = chain.with_values(a, 'level', gain_db=5.0)
    c = chain.with_values(a, 'highpass', enabled=True)
    assert chain.filter_key(a) == chain.filter_key(b) != chain.filter_key(c)
    assert not chain.uses_filters(a) and chain.uses_filters(c)


# ---- limiter ----------------------------------------------------------------------------------------------------------

def test_the_limiter_keeps_every_sample_under_the_ceiling():
    x = speech(5.0, amp=0.9)
    x[RATE:RATE + 50] = 1.0
    y = chain.limit(x, RATE, ceiling_db=-3.0)
    assert float(np.abs(y).max()) <= 10 ** (-3 / 20) + 1e-6
    assert len(y) == len(x)


def test_the_limiter_leaves_a_signal_under_the_ceiling_alone():
    x = speech(2.0, amp=0.2)
    assert chain.limit(x, RATE, -1.0) is x


def test_the_limiter_only_touches_the_neighbourhood_of_a_peak():
    x = speech(6.0, amp=0.2)
    x[3 * RATE] = 1.5
    y = chain.limit(x, RATE, -1.0)
    far = np.r_[0:RATE, 5 * RATE:6 * RATE]
    assert np.allclose(y[far], x[far], atol=1e-6)               # a second or more away: untouched
    assert y[3 * RATE] < x[3 * RATE]


def test_the_gain_recovers_gradually_after_a_peak():
    x = np.full(RATE, 0.1, np.float32)
    x[RATE // 4] = 2.0
    y = chain.limit(x, RATE, -1.0)
    ratio = y / x
    after = ratio[RATE // 4 + 100: RATE // 4 + 20000]
    assert after.min() < 0.99 and np.all(np.diff(after) >= -1e-6)    # climbing back up, never dipping again


def test_the_limiter_handles_clips_longer_than_one_block():
    x = speech(12.0, amp=0.1)
    x[::50000] = 1.0
    y = chain.limit(x, RATE, -6.0)
    assert float(np.abs(y).max()) <= 10 ** (-6 / 20) + 1e-6


def test_one_loud_pop_no_longer_decides_how_loud_the_speech_can_be():
    quiet = speech(14.0, amp=0.045)
    popped = with_pop(quiet, amp=0.9)
    capped = ac.normalize_gain_db(popped, RATE)
    free = ac.normalize_gain_db(popped, RATE, cap_peak=False)
    assert capped < 2.0 < 8.0 < free                             # the pop holds the capped gain almost at zero
    c = chain.normalize({'level': {'gain_db': free}, 'limiter': {'enabled': True, 'ceiling_db': -1.0}})
    out = chain.apply_level(popped, RATE, c)
    assert ac.voiced_rms_db(out, RATE) == pytest.approx(ac.TARGET_VOICED_DB, abs=1.0)
    assert float(np.abs(out).max()) <= 10 ** (-1 / 20) + 1e-6


def test_gain_without_the_limiter_still_clips_at_full_scale():
    c = chain.normalize({'level': {'gain_db': 24.0}})
    out = chain.apply_level(speech(1.0, amp=0.5), RATE, c)
    assert float(np.abs(out).max()) == 1.0


def test_no_gain_and_no_limiter_leaves_the_samples_equal():
    x = speech(1.0)
    assert np.array_equal(chain.apply_level(x, RATE, chain.normalize()), x)
