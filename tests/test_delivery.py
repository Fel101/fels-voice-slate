"""An Audition against the limits ACX gives for an audiobook file: what is measured, and how the Check tab words it."""
import math

import numpy as np
import pytest

from helpers import RATE, bursts, record, settle, with_pop
from recorder import audio_core as ac
from recorder import i18n
from recorder.core import analysis

SPANS = [(0.5, 2.5), (4.5, 6.5), (8.0, 10.0)]          # speech, with two pauses of 2 and 1.5 seconds


def take_of(noise=0.0005, amp=0.14):
    return bursts(12.0, SPANS, amp=amp, noise=noise)


def test_the_level_is_the_rms_of_the_whole_file_with_its_pauses():
    x = take_of()
    measured = analysis.delivery(x, RATE)
    whole = 20 * math.log10(math.sqrt(float(np.mean(x.astype(np.float64) ** 2))))
    assert measured.rms_db == pytest.approx(whole, abs=0.051)
    assert measured.rms_db < ac.voiced_rms_db(x, RATE) - 1.5        # the pauses bring it under the Speech level row


def test_each_limit_is_met_up_to_and_including_acx_figure():
    assert analysis.Delivery(-18.0, -3.0, -60.0).level_ok
    assert analysis.Delivery(-23.0, -3.0, -60.0).level_ok
    edge = analysis.Delivery(-18.0, -3.0, -60.0)
    assert edge.peak_ok and edge.noise_ok
    over = analysis.Delivery(-17.9, -2.9, -59.9)
    assert not over.level_ok and not over.peak_ok and not over.noise_ok
    assert not analysis.Delivery(-23.1, -3.0, -60.0).level_ok


def test_values_are_rounded_as_shown_so_what_is_shown_and_said_agree():
    x = take_of()
    stats = dict(ac.measure(x, RATE), rms_dbfs=-18.04, peak_dbfs=-2.96)
    measured = analysis.delivery(x, RATE, stats)
    assert (measured.rms_db, measured.peak_db) == (-18.0, -3.0)
    assert measured.level_ok and measured.peak_ok


def test_a_pop_goes_over_the_peak_limit():
    x = take_of()
    assert analysis.delivery(x, RATE).peak_ok
    assert not analysis.delivery(with_pop(x), RATE).peak_ok


@pytest.mark.parametrize('noise, ok', [(0.0005, True), (0.002, False)])
def test_the_noise_floor_is_the_quietest_half_second(noise, ok):
    measured = analysis.delivery(take_of(noise=noise), RATE)
    assert measured.noise_db == pytest.approx(20 * math.log10(noise), abs=0.5)
    assert measured.noise_ok is ok


def test_digital_silence_is_not_taken_for_a_quiet_room():
    x = take_of(noise=0.002)                    # a room at about -54 dB, over ACX's -60
    x[int(2.7 * RATE):int(4.3 * RATE)] = 0.0    # what the Silence edit leaves
    measured = analysis.delivery(x, RATE)
    assert measured.noise_db == pytest.approx(-54.0, abs=0.5) and not measured.noise_ok


def test_a_clip_with_nothing_to_read_the_noise_from_has_none():
    assert analysis.quietest_db(np.zeros(RATE * 3, np.float32), RATE) is None
    assert analysis.quietest_db(take_of()[:int(0.4 * RATE)], RATE) is None
    assert not analysis.Delivery(-20.0, -6.0, None).noise_ok


def test_a_long_audition_is_measured_without_a_copy_of_it_in_64_bit_numbers():
    x = np.tile(take_of(), 50)                  # ten minutes at 48 kHz
    measured = analysis.delivery(x, RATE)
    assert measured.noise_db == pytest.approx(-66.0, abs=0.5)


# ---- in the session ---------------------------------------------------------------------------------------------------

def test_an_audition_is_measured_against_acx_and_other_takes_are_not(session):
    take = record(session, samples=take_of())
    session.measure.derived(take)
    settle(session)
    assert session.measure.derived(take).delivery is None
    session.workspace.set_purpose('audition')
    audition = record(session, samples=take_of())
    session.measure.derived(audition)
    settle(session)
    derived = session.measure.derived(audition)
    assert derived.delivery is not None and derived.delivery.level_ok is not None
    assert derived.delivery.rms_db == round(derived.stats['rms_dbfs'], 1)


def test_missing_acx_limits_never_stop_keep(session):
    from helpers import fill
    session.workspace.set_purpose('audition')
    loud = with_pop(take_of(noise=0.004))       # a peak over -3 dB and a noisy room
    take = record(session, samples=loud)
    session.measure.derived(take)
    settle(session)
    delivery = session.measure.derived(take).delivery
    assert not delivery.peak_ok and not delivery.noise_ok
    fill(session)
    session.keep()
    assert take.kept


# ---- the words --------------------------------------------------------------------------------------------------------

def rows(delivery):
    pytest.importorskip('PySide6')
    from recorder.ui.inspector import delivery_rows
    return {row.key: row for row in delivery_rows(delivery)}


def test_each_row_says_what_acx_asks_and_what_to_do():
    shown = rows(analysis.Delivery(-25.2, -1.1, -52.0))
    assert shown['acx_level'].status == 'warn' and shown['acx_level'].value == '-25.2 dB RMS'
    assert 'ACX asks for -23 to -18 dB RMS' in shown['acx_level'].hint
    assert 'Raise the Level in Process by about 4.7 dB' in shown['acx_level'].hint
    assert shown['acx_peak'].status == 'warn' and 'ceiling at -3.5 dB' in shown['acx_peak'].hint
    assert shown['acx_noise'].status == 'warn' and 'Noise reduction in Process' in shown['acx_noise'].hint
    loud = rows(analysis.Delivery(-16.0, -4.0, -65.0))
    assert 'Lower the Level in Process by about 4.5 dB' in loud['acx_level'].hint


def test_rows_that_pass_only_say_what_acx_asks_and_are_never_a_problem():
    shown = rows(analysis.Delivery(-20.0, -4.0, -65.0))
    assert [row.status for row in shown.values()] == ['ok', 'ok', 'ok']
    assert 'Raise' not in shown['acx_level'].hint and 'limiter' not in shown['acx_peak'].hint
    worst = rows(analysis.Delivery(-40.0, 0.0, -20.0))
    assert 'bad' not in [row.status for row in worst.values()]


def test_a_noise_floor_that_cannot_be_read_says_so():
    shown = rows(analysis.Delivery(-20.0, -4.0, None))
    assert shown['acx_noise'].status == 'info' and shown['acx_noise'].value == 'not measured'
    assert 'no half second without digital silence' in shown['acx_noise'].hint


def test_the_rows_are_in_the_language_in_use():
    i18n.use('de')
    try:
        shown = rows(analysis.Delivery(-25.2, -1.1, None))
    finally:
        i18n.use('en')
    assert shown['acx_level'].label != 'Level (RMS)' and 'ACX' in shown['acx_level'].hint
    assert 'ACX asks' not in shown['acx_level'].hint and '4.7' in shown['acx_level'].hint
    assert shown['acx_noise'].value != 'not measured'
