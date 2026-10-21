"""Background measurements: the keep rules' numbers, the Check rows, the spectrogram and the peaks for drawing."""
import pytest

from helpers import HeldJobs, bursts, record
from recorder.session.measure import recipe_key


def test_the_measurement_arrives_after_the_job_is_delivered(session):
    take = record(session, 10.0)
    assert session.measure.derived(take) is None
    session.tick()
    derived = session.measure.derived(take)
    assert derived.stats['seconds'] == pytest.approx(10.0, abs=0.01) and derived.error == ''
    assert derived.report                                       # the Check tab's rows


def test_the_measurement_is_made_once_per_recipe(make_session):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    take = record(s, 10.0)
    s.measure.derived(take)
    s.measure.derived(take)
    s.measure.derived(take)
    assert held.pending == 1


def test_a_changed_recipe_is_measured_again(session):
    take = record(session, 10.0)
    session.measure.derived(take)
    session.tick()
    before = session.measure.derived(take)
    session.editor.set_selection(0.0, 4.0)
    session.editor.cut_selection()
    assert session.measure.derived(take) is None                # stale: a new measurement was requested
    session.tick()
    after = session.measure.derived(take)
    assert after.key != before.key and after.stats['seconds'] == pytest.approx(6.0, abs=0.05)


def test_undo_measures_the_earlier_recipe_again(session):
    take = record(session, 10.0)
    session.editor.set_selection(0.0, 4.0)
    session.editor.cut_selection()
    session.measure.derived(take)
    session.tick()
    assert session.measure.derived(take).stats['seconds'] == pytest.approx(6.0, abs=0.05)
    session.editor.undo()
    assert session.measure.derived(take) is None
    session.tick()
    assert session.measure.derived(take).stats['seconds'] == pytest.approx(10.0, abs=0.05)


def test_a_late_result_for_an_old_recipe_does_not_replace_the_current_one(make_session):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    take = record(s, 10.0)
    s.measure.derived(take)                                     # job 0: the whole 10 s
    s.editor.set_selection(0.0, 4.0)
    s.editor.cut_selection()
    s.measure.derived(take)                                     # job 1: 6 s after the cut
    held.run(1)
    held.run(0)                                                 # the older measurement finishes last
    assert s.measure.derived(take).stats['seconds'] == pytest.approx(6.0, abs=0.05)


def test_the_measurement_judges_the_edited_audio_not_the_master(session):
    take = record(session, samples=bursts(12.0, [(5.0, 11.0)], amp=0.14))
    session.measure.derived(take)
    session.tick()
    assert session.measure.derived(take).stats['silence_fraction'] > 0.3
    session.editor.set_selection(0.0, 5.0)
    session.editor.cut_selection()
    session.measure.derived(take)
    session.tick()
    assert session.measure.derived(take).stats['silence_fraction'] < 0.15


def test_a_measurement_that_fails_is_recorded_as_an_error(session, monkeypatch):
    take = record(session, 10.0)
    monkeypatch.setattr(session.lib, 'export_for', lambda *a: (_ for _ in ()).throw(ValueError('could not measure')))
    session.measure.derived(take)
    session.tick()
    derived = session.measure.derived(take)
    assert derived.stats is None and 'could not measure' in derived.error
    steps = {step.key: step for step in session.workspace.readiness(derived).steps}
    assert steps['length'].done is False and 'could not measure' in steps['length'].detail


def test_the_spectrogram_is_computed_once_and_the_window_is_told(session):
    take = record(session, 10.0)
    heard = []
    session.bus.listen(lambda topic, payload: heard.append(topic))
    assert session.measure.spectrogram(take) is None
    assert session.measure.spectrogram(take) is None            # asking again does not start a second job
    session.tick()
    spec = session.measure.spectrogram(take)
    assert spec is not None and spec.db.shape[1] > 100 and 'analysis' in heard
    assert session.measure.spectrogram(take) is spec


def test_the_waveform_peaks_are_computed_once_per_take(session):
    take = record(session, 10.0)
    assert session.measure.pyramid(take) is session.measure.pyramid(take)
    assert session.measure.pyramid(take) is not session.measure.pyramid(record(session, 5.0))


def test_a_changed_chain_setting_changes_the_recipe_key(session):
    take = record(session, 10.0)
    key = recipe_key(take.history)
    session.editor.set_chain('highpass', 'High-pass', enabled=True, hz=100.0)
    assert recipe_key(take.history) != key
