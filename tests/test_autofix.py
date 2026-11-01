"""Auto analyze and Auto fix: what the advisor finds in a clip, and that its plan, applied, repairs it."""
import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from helpers import RATE, bursts, tone
from recorder import audio_core as ac
from recorder import clean
from recorder.core import analysis, autofix, render
from recorder.core.edits import History

SPANS = [(1.0, 3.5), (4.0, 7.0), (7.4, 10.0), (10.5, 12.5)]     # 13 s: a second of pause before the voice, half a second after
TIGHT = [(0.3, 3.0), (3.5, 6.5), (7.0, 10.0), (10.5, 12.7)]     # 13 s with short pauses at the ends: nothing to trim
F0 = 137.0                                                       # on none of the 50 and 60 Hz mains harmonics


def voice(spans=TIGHT, seconds=13.0, amp=0.14, **kwargs):
    return bursts(seconds, spans, amp=amp, f0=F0, **kwargs)


def sine(hz, amp, seconds=13.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def hum(hz, seconds=13.0):
    return sum(sine(hz * k, 0.003 / k, seconds) for k in (1, 2, 3))


def gate(mono, rate, nf):
    """A stand-in for FFmpeg: it turns the quiet frames down, so the noise falls and the speech does not."""
    frame = int(rate * 0.02)
    n = len(mono) // frame * frame
    frames = np.array(mono[:n], dtype=np.float32).reshape(-1, frame)
    level = np.sqrt((frames ** 2).mean(axis=1))
    frames *= np.where(level < level.max() * 0.15, 0.03, 1.0).astype(np.float32)[:, None]
    return np.concatenate([frames.ravel(), mono[n:]]).astype(np.float32)


def identity(mono, rate, nf):
    return mono


def muffle(mono, rate, nf):
    """A stand-in for cleaning set too hard: the noise falls as `gate` makes it, and the top of the voice goes with it."""
    return sosfiltfilt(butter(8, 800.0, 'low', fs=rate, output='sos'), gate(mono, rate, nf)).astype(np.float32)


def advise(x, history=None, denoise=gate, denoise_ok=True):
    history = history if history is not None else History(seconds=len(x) / RATE)
    diagnosis = autofix.diagnose(x, RATE, history, lambda recipe: render.before_level(x, RATE, recipe, denoise=denoise),
                                 denoise_ok=denoise_ok)
    return diagnosis, history


def fixed(x, diagnosis, history, denoise=gate):
    """The recipe with the plan applied as one step, and the export it renders."""
    recipe = history.copy()
    assert recipe.add_step(diagnosis.ops, diagnosis.chain, diagnosis.label)
    return recipe, render.export_samples(x, RATE, recipe, denoise)


def find(diagnosis, key):
    return next(f for f in diagnosis.findings if f.key == key)


def keys(diagnosis):
    return [f.key for f in diagnosis.findings]


# ---- a clip that needs nothing ----------------------------------------------------------------------------------------

def test_a_clean_clip_has_nothing_to_fix():
    d, _ = advise(voice())
    assert keys(d) == ['ok'] and d.findings[0].status == 'ok'
    assert not d.has_fixes and d.steps == [] and d.ops == [] and d.chain == {}
    assert d.changes == [] and d.after == d.before


def test_ordinary_speech_is_not_given_a_filter():
    d, _ = advise(voice(SPANS))
    assert 'highpass' not in d.chain and 'hum' not in d.chain and 'denoise' not in d.chain


# ---- the ends and the pauses ------------------------------------------------------------------------------------------

def test_long_silence_at_the_ends_is_cut_down_to_a_short_margin():
    x = voice([(3.0, 6.0), (6.5, 9.5), (10.0, 15.0)], seconds=18.0)
    d, h = advise(x)
    assert find(d, 'ends').fix == 'Trim the silence at the ends'
    (_, a1, b1, _), (_, a2, b2, _) = d.ops
    assert (a1, b1) == pytest.approx((0.0, 2.85), abs=0.03) and (a2, b2) == pytest.approx((15.15, 18.0), abs=0.03)
    _, out = fixed(x, d, h)
    lead, tail = analysis.edge_silence(out, ac.EXPORT_RATE)
    assert 0.1 < lead < 0.25 and 0.1 < tail < 0.25


def test_silence_that_only_shows_once_the_other_end_is_cut_goes_too(monkeypatch):
    """A blip of noise left by the cleaner can hide a silent end until the other end has gone, as it did on real speech."""
    x = voice([(3.0, 6.0), (6.5, 9.5), (10.0, 15.0)], seconds=18.0)
    real, looks = autofix._look, []

    def hiding(audio, rate):
        look = real(audio, rate)
        looks.append(look)
        if len(looks) == 2:                                 # the look the ends are first planned from
            look.tail = 0.0
        return look

    monkeypatch.setattr(autofix, '_look', hiding)
    d, h = advise(x)
    monkeypatch.setattr(autofix, '_look', real)
    assert d.steps == ['Trim the silence at the ends'] and len(d.ops) == 2
    recipe, out = fixed(x, d, h)
    lead, tail = analysis.edge_silence(out, ac.EXPORT_RATE)
    assert 0.1 < lead < 0.25 and 0.1 < tail < 0.25
    assert advise(x, recipe)[0].steps == []


def test_long_pauses_inside_the_clip_are_shortened_to_about_half_a_second():
    x = voice([(0.4, 3.0), (6.0, 9.0), (12.5, 15.5), (19.0, 19.7)], seconds=20.0)
    d, h = advise(x)
    assert find(d, 'pauses').detail.startswith('3 pauses longer than 1.2 s (the longest is 3.5 s)')
    assert keys(d) == ['pauses'] and d.steps == ['Shorten the long pauses']
    _, out = fixed(x, d, h)
    inner = [run for run in analysis.silent_runs(out, ac.EXPORT_RATE, min_seconds=0.3) if 0 < run[0]]
    assert inner and all(b - a < 1.0 for a, b in inner)
    assert len(out) / ac.EXPORT_RATE == pytest.approx(20.0 - (3.0 + 3.5 + 3.5) + 3 * autofix.PAUSE_KEEP, abs=0.3)
    assert [c.label for c in d.changes] == ['Length']


def test_a_cut_that_would_leave_less_than_six_seconds_is_not_made():
    x = voice([(2.5, 5.5)], seconds=8.0)
    d, _ = advise(x)
    ends = find(d, 'ends')
    assert ends.fix == '' and 'Not fixed: the clip would be left shorter than 6 s' in ends.detail
    assert not d.has_fixes


def test_a_clip_already_too_short_is_reported_and_not_made_shorter():
    d, _ = advise(voice([(0.5, 3.5)], seconds=4.0))
    assert find(d, 'length').status == 'bad' and find(d, 'length').title == 'Too short'
    assert find(d, 'length').fix == ''


def test_a_clip_over_the_maximum_says_how_to_cut_it():
    d, _ = advise(tone(42.0, 180.0, 0.14))
    assert find(d, 'length').title == 'Too long' and 'Trim to selection' in find(d, 'length').detail


def test_the_plan_builds_on_the_cuts_already_made():
    x = voice([(3.0, 6.0), (6.5, 9.5), (10.0, 14.0)], seconds=16.0)
    h = History(seconds=16.0)
    h.cut(0.0, 1.0)                                        # the person has already taken the first second off
    d, _ = advise(x, h)
    ops = [op for op in d.ops if op[0] == 'cut']
    assert ops[0][1:3] == pytest.approx((1.0, 2.85), abs=0.03)         # on the master timeline, not the edited one
    assert ops[1][1:3] == pytest.approx((14.15, 16.0), abs=0.03)
    _, out = fixed(x, d, h)
    lead, _ = analysis.edge_silence(out, ac.EXPORT_RATE)
    assert 0.1 < lead < 0.25


# ---- level and peaks --------------------------------------------------------------------------------------------------

def test_a_quiet_clip_is_brought_to_the_target_with_the_limiter_on():
    x = voice(amp=0.02)
    d, h = advise(x)
    assert find(d, 'level').fix.startswith('Level +') and find(d, 'level').fix.endswith('with the peak limiter')
    assert d.chain['limiter'] == {'enabled': True} and d.chain['level']['gain_db'] > 10
    recipe, out = fixed(x, d, h)
    assert ac.voiced_rms_db(out, ac.EXPORT_RATE) == pytest.approx(ac.TARGET_VOICED_DB, abs=1.0)
    assert ac.measure(out, ac.EXPORT_RATE)['peak_dbfs'] <= -1.0 + 1e-6
    assert recipe.chain['limiter']['enabled']
    assert {'Speech level', 'Peak'} <= {c.label for c in d.changes} and all(c.improved for c in d.changes)


def test_a_loud_clip_is_turned_down():
    x = voice(amp=0.35)
    d, h = advise(x)
    assert d.chain['level']['gain_db'] < -5
    _, out = fixed(x, d, h)
    assert ac.voiced_rms_db(out, ac.EXPORT_RATE) == pytest.approx(ac.TARGET_VOICED_DB, abs=1.0)


def test_the_limiter_is_not_mentioned_when_it_is_already_on():
    h = History(seconds=13.0)
    h.set_chain('limiter', 'Peak limiter', enabled=True)
    d, _ = advise(voice(amp=0.02), h)
    assert 'limiter' not in find(d, 'level').fix and 'limiter' not in d.chain


def test_a_gain_the_person_set_is_replaced_and_not_added_to():
    x = voice(amp=0.02)
    h = History(seconds=13.0)
    h.set_chain('level', 'Gain', gain_db=3.0)
    d, _ = advise(x, h)
    _, out = fixed(x, d, h)
    assert ac.voiced_rms_db(out, ac.EXPORT_RATE) == pytest.approx(ac.TARGET_VOICED_DB, abs=1.0)


def test_peaks_over_the_ceiling_get_the_limiter_alone_when_the_speech_level_is_right():
    x = voice(amp=0.14)
    x[int(5.2 * RATE):int(5.2 * RATE) + 40] = 0.97                  # one short, loud pop
    d, h = advise(x)
    assert find(d, 'peak').fix == 'Peak limiter' and 'level' not in keys(d)
    _, out = fixed(x, d, h)
    assert ac.measure(out, ac.EXPORT_RATE)['peak_dbfs'] <= -1.0 + 1e-6


def test_a_limited_peak_stays_under_the_ceiling_after_the_export_is_resampled():
    x = voice(amp=0.02)
    d, h = advise(x)
    recipe, _ = fixed(x, d, h)
    recipe.set_chain('level', 'Gain', gain_db=30.0)
    out = render.export_samples(x, RATE, recipe, gate)
    assert float(np.abs(out).max()) <= 10 ** (-1.0 / 20.0) + 1e-6


# ---- filters ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('mains', [50.0, 60.0])
def test_mains_hum_gets_a_notch_at_its_own_frequency(mains):
    x = voice() + hum(mains)
    d, h = advise(x)
    assert find(d, 'hum').fix == f'{mains:.0f} Hz hum filter'
    assert d.chain['hum'] == {'enabled': True, 'hz': mains}
    _, out = fixed(x, d, h)
    was = analysis.detect_hum(ac.resample(x, RATE, ac.EXPORT_RATE), ac.EXPORT_RATE)
    now = analysis.detect_hum(out, ac.EXPORT_RATE)      # a narrow notch rings a little after a loud syllable: it may still show
    assert now is None or now[1] < was[1] - 25.0
    assert 'Mains hum' in {c.label for c in d.changes}


def test_low_rumble_gets_the_high_pass_and_the_clip_is_measured_with_it_on():
    x = voice(SPANS) + sine(30.0, 0.05)
    d, h = advise(x)
    assert find(d, 'rumble').fix == 'High-pass filter at 80 Hz'
    assert d.chain['highpass'] == {'enabled': True, 'hz': 80.0}
    assert 'measured with the filters on' in find(d, 'ends').detail      # the rumble hid the silence until it was filtered
    recipe, out = fixed(x, d, h)
    assert autofix.rumble_db(out, ac.EXPORT_RATE) < autofix.RUMBLE_LIMIT
    labels = {c.label: c for c in d.changes}
    assert labels['Speech above the noise'].improved


def test_a_filter_the_person_already_turned_on_is_left_alone_and_said_so():
    h = History(seconds=13.0)
    h.set_chain('highpass', 'High-pass', enabled=True, hz=20.0)             # too low to take out a 30 Hz rumble
    x = voice() + sine(30.0, 0.05)
    d, _ = advise(x, h)
    assert 'highpass' not in d.chain
    assert 'The high-pass filter is already on' in find(d, 'rumble').detail and find(d, 'rumble').fix == ''


def test_the_rumble_measure_tells_voice_from_rumble():
    assert autofix.rumble_db(voice(SPANS), RATE) < -50.0
    assert autofix.rumble_db(voice(SPANS) + sine(30.0, 0.05), RATE) > autofix.RUMBLE_LIMIT


# ---- noise ------------------------------------------------------------------------------------------------------------

def test_a_clearly_noisy_clip_is_cleaned_when_the_cleaner_helps():
    x = voice(noise=0.012)
    d, h = advise(x)
    assert find(d, 'noise').status == 'bad' and find(d, 'noise').fix.startswith('Noise reduction')
    assert d.chain['denoise'] == {'enabled': True, 'nf': autofix.noise_setting(analysis.noise_floor_db(x, RATE))}
    before, after = d.snr
    assert after - before >= autofix.NOISE_MIN_GAIN
    assert 'Speech above the noise' in {c.label for c in d.changes}
    recipe, _ = fixed(x, d, h)
    assert recipe.chain['denoise']['enabled']


def test_noise_cleaning_is_not_kept_when_it_does_not_help():
    d, _ = advise(voice(noise=0.012), denoise=identity)
    noise = find(d, 'noise')
    assert 'denoise' not in d.chain and noise.fix == ''
    assert 'Not fixed: noise cleaning did not lower it enough' in noise.detail


def test_noise_cleaning_is_set_from_the_noise_the_clip_has():
    assert autofix.noise_setting(-50.2) == -45.0 and autofix.noise_setting(-38.6) == -34.0
    assert autofix.noise_setting(-200.0) == -80.0 and autofix.noise_setting(-10.0) == -20.0     # the limits of the filter
    quiet, loud = (advise(voice(noise=noise))[0].chain['denoise']['nf'] for noise in (0.01, 0.016))
    assert loud - quiet == pytest.approx(4.0, abs=1.0)                   # the noise is 4 dB louder, and so is the setting


def test_noise_cleaning_that_takes_the_voice_with_it_is_not_kept():
    """The noise measure alone calls this a success: the speech stands further above the noise than it did."""
    x = voice(noise=0.012)
    cleaned = muffle(x, RATE, -40.0)
    assert autofix._snr(cleaned, RATE) - autofix._snr(x, RATE) >= autofix.NOISE_MIN_GAIN
    d, _ = advise(x, denoise=muffle)
    noise = find(d, 'noise')
    assert 'denoise' not in d.chain and noise.fix == '' and 'too much of the voice' in noise.detail


def test_noise_cleaning_is_not_tried_without_ffmpeg():
    d, _ = advise(voice(noise=0.012), denoise_ok=False)
    assert 'denoise' not in d.chain and 'FFmpeg was not found' in find(d, 'noise').detail


def test_a_cleaner_that_fails_is_reported_and_the_rest_of_the_plan_still_stands():
    def broken(mono, rate, nf):
        raise clean.CleanError('FFmpeg stopped')

    d, _ = advise(voice(SPANS, noise=0.012), denoise=broken)
    assert 'Not fixed: FFmpeg stopped' in find(d, 'noise').detail
    assert d.steps == ['Trim the silence at the ends']


def test_slightly_noisy_speech_is_reported_and_left_to_the_person():
    x = voice(noise=0.0032)                                 # about 30 dB between the speech and the noise
    d, _ = advise(x)
    noise = find(d, 'noise')
    assert noise.status == 'warn' and noise.fix == '' and 'leaves that to you' in noise.detail
    assert 'denoise' not in d.chain


def test_noise_that_is_already_being_cleaned_is_not_cleaned_twice():
    h = History(seconds=13.0)
    h.set_chain('denoise', 'Noise cleaning', enabled=True)
    d, _ = advise(voice(noise=0.012), h, denoise=identity)
    assert 'denoise' not in d.chain and 'Noise reduction is already on' in find(d, 'noise').detail


def test_a_clip_that_is_already_being_cleaned_with_no_ffmpeg_still_gets_the_rest_of_the_plan():
    h = History(seconds=13.0)
    h.set_chain('denoise', 'Noise cleaning', enabled=True)
    d, _ = advise(voice(SPANS), h, denoise=identity, denoise_ok=False)
    assert d.steps == ['Trim the silence at the ends']


# ---- what cannot be repaired ------------------------------------------------------------------------------------------

def test_clipping_is_reported_with_no_fix():
    x = np.clip(voice(amp=0.6), -0.5, 0.5) * 2.0
    d, _ = advise(x.astype(np.float32))
    clipping = find(d, 'clipping')
    assert clipping.status == 'bad' and clipping.fix == '' and 'record again' in clipping.detail


def test_the_limiter_does_not_make_clipping_go_away():
    x = (np.clip(voice(amp=0.6), -0.5, 0.5) * 2.0).astype(np.float32)
    d, h = advise(x)
    assert find(d, 'clipping').status == 'bad' and d.chain['limiter'] == {'enabled': True}
    recipe, out = fixed(x, d, h)
    assert ac.measure(out, ac.EXPORT_RATE)['clipped_fraction'] == 0.0        # the finished clip measures clean
    again, _ = advise(x, recipe)
    clipping = find(again, 'clipping')
    assert clipping.status == 'bad' and 'only hide it' in clipping.detail and 'ok' not in keys(again)
    assert next(row for row in again.before if row.key == 'clipping').status == 'bad'


def test_a_silent_clip_says_there_is_no_speech_and_plans_nothing():
    d, _ = advise(voice([], seconds=8.0))
    assert keys(d) == ['speech'] and d.findings[0].status == 'bad' and not d.has_fixes


def test_a_telephone_band_clip_is_reported_as_narrow():
    spectrum = np.fft.rfft(voice(SPANS))
    spectrum[int(13.0 * 2500.0):] = 0                               # nothing above 2.5 kHz
    d, _ = advise(np.fft.irfft(spectrum, int(13.0 * RATE)).astype(np.float32))
    assert find(d, 'bandwidth').fix == '' and 'cannot be fixed' in find(d, 'bandwidth').detail


# ---- the plan as a whole ----------------------------------------------------------------------------------------------

def test_the_whole_plan_is_one_undo_step():
    x = voice(SPANS, amp=0.03)
    d, h = advise(x)
    assert len(d.steps) >= 2
    recipe = h.copy()
    assert recipe.add_step(d.ops, d.chain, d.label)
    assert recipe.steps == [d.label] and d.label == f'Auto fix ({len(d.steps)} steps)'
    assert recipe.undo()
    assert recipe.edits == h.edits and recipe.chain == h.chain


def test_findings_come_worst_first():
    x = np.clip(voice(SPANS, amp=0.6), -0.5, 0.5) * 2.0 + hum(60.0)
    d, _ = advise(x.astype(np.float32))
    rank = {'bad': 0, 'warn': 1, 'ok': 2}
    assert [f.status for f in d.findings] == sorted((f.status for f in d.findings), key=rank.get)
    assert d.findings[0].status == 'bad' and len(d.findings) >= 3


def test_every_finding_has_plain_words_and_every_step_is_some_findings_fix():
    x = voice(SPANS, amp=0.03) + hum(60.0) + sine(30.0, 0.03)
    d, _ = advise(x)
    assert len(d.steps) == 3
    assert all(f.title and f.detail for f in d.findings)
    assert {f.fix for f in d.findings if f.fix} == set(d.steps)


@pytest.mark.parametrize('make', [
    lambda: voice(SPANS),
    lambda: voice(SPANS, amp=0.02),
    lambda: voice(amp=0.4),
    lambda: voice([(3.0, 6.0), (6.5, 9.5), (10.0, 15.0)], seconds=18.0),
    lambda: voice([(0.4, 3.0), (6.0, 9.0), (12.5, 15.5), (19.0, 19.7)], seconds=20.0),
    lambda: voice(SPANS) + hum(60.0),
    lambda: voice(SPANS) + hum(50.0),
    lambda: voice(SPANS) + sine(30.0, 0.05),
    lambda: voice(SPANS, noise=0.012),
], ids=['clean', 'quiet', 'loud', 'long-ends', 'long-pauses', 'hum60', 'hum50', 'rumble', 'noisy'])
def test_after_the_plan_a_second_look_finds_nothing_more_to_do(make):
    x = make().astype(np.float32)
    d, h = advise(x)
    recipe, _ = fixed(x, d, h) if d.has_fixes else (h, None)
    again, _ = advise(x, recipe)
    assert again.steps == [] and not again.has_fixes
