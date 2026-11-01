"""What a take is for: each purpose has its own length rules and is kept in its own format."""
import json

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, bursts, fill, record, settle
from recorder import audio_core as ac
from recorder.core import analysis, autofix
from recorder.store import KeepBlocked


def speech_of(seconds, rate=RATE):
    """A clip that fills `seconds` with a voice, with short pauses at the ends."""
    return bursts(seconds, [(0.15, seconds - 0.15)], rate=rate, amp=0.14, f0=137.0)


def test_a_reference_keeps_the_rules_it_has_always_had():
    rules = ac.PURPOSES['reference']
    assert (rules.min_seconds, rules.max_seconds, rules.export_rate, rules.bit_depth) == (6.0, 40.0, 24000, 16)
    assert (rules.best_from, rules.best_to) == (10.0, 25.0)


def test_a_clip_from_before_purposes_is_a_reference():
    assert ac.purpose_of(None).key == 'reference' and ac.purpose_of('nonsense').key == 'reference'
    assert ac.purpose_of(7).key == 'reference'


def test_every_purpose_lets_a_recording_run_past_the_longest_clip():
    for rules in ac.PURPOSES.values():
        assert rules.min_seconds < rules.best_from < rules.best_to < rules.max_seconds < rules.record_limit


@pytest.mark.parametrize('recorded, kept', [(48000, 48000), (44100, 44100), (96000, 48000), (16000, 16000)])
def test_an_audition_is_never_kept_above_the_rate_it_was_recorded_at(recorded, kept):
    assert ac.PURPOSES['audition'].rate_for(recorded) == kept


@pytest.mark.parametrize('recorded', [16000, 44100, 48000])
def test_a_sentence_is_kept_at_one_rate_whatever_it_was_recorded_at(recorded):
    assert ac.PURPOSES['sentence'].rate_for(recorded) == 24000


def test_the_example_sentences_can_be_kept_as_sentences(session):
    """They run 2.7 to 4.2 s, which is what the 6 s minimum of a Reference refused."""
    session.workspace.set_purpose('sentence')
    take = record(session, samples=speech_of(3.0))
    assert take.meta['purpose'] == 'sentence'
    fill(session)
    paths = session.keep()
    assert paths is not None
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['purpose'] == 'sentence' and data['export']['sample_rate'] == 24000


def test_the_same_clip_is_refused_as_a_reference(session):
    record(session, samples=speech_of(3.0))
    fill(session)
    with pytest.raises(KeepBlocked) as caught:
        session.keep()
    assert any('6 s minimum' in problem and 'a reference' in problem for problem in caught.value.problems)


def test_a_sentence_is_refused_when_it_is_too_long(session):
    session.workspace.set_purpose('sentence')
    record(session, samples=speech_of(16.0))
    fill(session)
    with pytest.raises(KeepBlocked) as caught:
        session.keep()
    assert any('15 s maximum' in problem for problem in caught.value.problems)


def test_an_audition_is_kept_at_48_khz_and_24_bits(session):
    """Nothing above 12 kHz survived a 24 kHz clip. A 15 kHz tone is the test of that."""
    session.workspace.set_purpose('audition')
    t = np.arange(int(8 * RATE)) / RATE
    air = (0.02 * np.sin(2 * np.pi * 15000 * t)).astype(np.float32)
    take = record(session, samples=speech_of(8.0) + air)
    fill(session)
    paths = session.keep()
    info = sf.info(str(paths['export']))
    assert info.samplerate == 48000 and info.subtype == 'PCM_24'
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['export']['sample_rate'] == 48000 and data['export']['bit_depth'] == 24 and data['purpose'] == 'audition'
    kept, rate = sf.read(str(paths['export']), dtype='float32')
    spectrum = np.abs(np.fft.rfft(kept))
    freqs = np.fft.rfftfreq(len(kept), 1 / rate)
    assert spectrum[np.argmin(np.abs(freqs - 15000))] > 100 * np.median(spectrum)
    assert take.export_rate == 48000


def test_an_audition_can_run_for_minutes(session):
    session.workspace.set_purpose('audition')
    record(session, samples=speech_of(90.0))
    fill(session)
    assert session.keep() is not None


def test_a_recording_too_long_for_a_reference_comes_back_whole_after_a_crash(session):
    """The spool holds a purpose, so the limit of the take is the one it was recorded under."""
    lib, slug = session.lib, session.slug
    session.workspace.set_purpose('audition')
    spool = lib.open_spool(slug, RATE, 1, 'mic', {}, purpose='audition')
    spool.write([speech_of(130.0)])
    spool.close()
    take = next(t for t in lib.load_takes(slug) if 'interrupted' in t.flags)
    assert take.seconds == pytest.approx(130.0, abs=0.01) and take.meta['purpose'] == 'audition'


def test_changing_the_purpose_of_one_take_leaves_the_choice_for_new_takes_alone(session):
    take = record(session, samples=speech_of(3.0))
    session.workspace.set_meta(purpose='sentence')
    assert take.meta['purpose'] == 'sentence' and session.workspace.purpose.key == 'reference'
    assert record(session, samples=speech_of(3.0)).meta['purpose'] == 'reference'


def test_the_choice_for_new_takes_is_remembered(session, make_session):
    session.workspace.set_purpose('sentence')
    assert make_session().workspace.purpose.key == 'sentence'


def test_a_new_purpose_is_measured_again(session):
    take = record(session, samples=speech_of(8.0))
    settle(session)
    first = session.measure.derived(take) or session.measure.derived(take)
    settle(session)
    first = session.measure.derived(take)
    assert first is not None
    session.workspace.set_meta(purpose='audition')
    assert session.measure.derived(take) is None
    settle(session)
    again = session.measure.derived(take)
    assert again is not None and again.key != first.key


def test_changing_the_purpose_of_a_kept_clip_is_a_change_keep_has_to_write(session):
    take = record(session, samples=speech_of(8.0))
    fill(session)
    paths = session.keep()
    assert not take.changed
    session.workspace.set_meta(purpose='audition')
    assert take.changed
    session.workspace.set_meta(consent_confirmed=True)
    session.keep()
    assert sf.info(str(paths['export'])).samplerate == 48000 and not take.changed


def test_the_purpose_comes_back_after_a_restart(session, make_session):
    take = record(session, samples=speech_of(3.0))
    session.workspace.set_meta(purpose='sentence')
    session.autosave.flush()
    assert take.meta['purpose'] == 'sentence'
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert again.current.meta['purpose'] == 'sentence'


def test_a_kept_clip_comes_back_with_its_purpose(session, make_session):
    session.workspace.set_purpose('sentence')
    record(session, samples=speech_of(3.0))
    fill(session)
    session.keep()
    again = make_session()
    again.workspace.select_speaker(session.slug)
    assert again.current.meta['purpose'] == 'sentence' and not again.current.changed


def test_the_checklist_gives_the_rules_of_the_take(session):
    session.workspace.set_purpose('sentence')
    record(session, samples=speech_of(3.0))
    settle(session)
    take = session.current
    session.measure.derived(take)
    settle(session)
    step = session.workspace.readiness(session.measure.derived(take)).steps[0]
    assert step.key == 'length' and step.done and step.label == 'Length 1 to 15 s'


def test_the_check_tab_judges_the_length_by_the_purpose():
    x = speech_of(3.0, 24000)
    rows = {row.key: row for row in analysis.quality_report(x, 24000, purpose=ac.PURPOSES['sentence'])}
    assert rows['length'].status == 'ok' and '1 and 15 s' in rows['length'].hint
    rows = {row.key: row for row in analysis.quality_report(x, 24000)}
    assert rows['length'].status == 'bad' and '6 and 40 s' in rows['length'].hint


def test_auto_fix_will_not_trim_a_sentence_below_what_can_be_kept(session):
    session.workspace.set_purpose('sentence')
    x = bursts(2.4, [(0.95, 1.35)], amp=0.14, f0=137.0)
    take = record(session, samples=x)
    diagnosis = autofix.diagnose(take.samples, take.rate, take.history, lambda r: session.lib.before_level(take, r),
                                 denoise_ok=False, purpose=take.purpose)
    assert not any(op[0] == 'cut' for op in diagnosis.ops)
    assert any('1 s' in finding.detail for finding in diagnosis.findings if finding.key == 'ends')


def test_a_long_clip_is_told_about_splitting(session):
    take = record(session, samples=speech_of(50.0))
    diagnosis = autofix.diagnose(take.samples, take.rate, take.history, lambda r: session.lib.before_level(take, r),
                                 denoise_ok=False, purpose=take.purpose)
    finding = next(f for f in diagnosis.findings if f.key == 'length')
    assert finding.status == 'bad' and 'split' in finding.detail
