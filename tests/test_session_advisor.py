"""Auto analyze and Auto fix in the session: the advice is worked out in the background, follows the edits of a watched
take, and Auto fix applies the plan as one undo step."""
from types import SimpleNamespace

import pytest

from helpers import HeldJobs, bursts, record, settle
from recorder.core import autofix

LONG_ENDS = [(3.0, 6.0), (6.5, 9.5), (10.0, 12.5)]          # 15 s: three seconds of pause before the voice


@pytest.fixture
def quiet(session):
    """A quiet clip with three seconds of silence in front of the voice: Auto fix has a trim and a level to do."""
    return record(session, samples=bursts(15.0, LONG_ENDS, amp=0.03, f0=137.0))


@pytest.fixture
def tidy(session):
    """A clip that is already as it should be."""
    return record(session, samples=bursts(13.0, [(0.3, 3.0), (3.5, 6.5), (7.0, 10.0), (10.5, 12.7)], amp=0.14, f0=137.0))


# ---- Auto analyze -----------------------------------------------------------------------------------------------------

def test_auto_analyze_is_worked_out_in_the_background_and_announced_when_it_arrives(session, quiet):
    advisor = session.advisor
    assert advisor.analyze()
    assert advisor.watching(quiet) and advisor.working(quiet) and advisor.advice(quiet) is None
    assert session.status_text == 'Analyzing the clip...'
    settle(session)
    advice = advisor.advice(quiet)
    assert advice is not None and advice.diagnosis.has_fixes and not advisor.working(quiet)
    assert session.status_text.startswith('Auto analyze: ') and 'Auto fix has 2 steps ready' in session.status_text


def test_asking_again_when_the_advice_is_known_answers_at_once_and_starts_nothing(session, quiet):
    session.advisor.analyze()
    settle(session)
    session.bus.status('something else')
    session.advisor.analyze()
    assert session.jobs.pending == 0 and session.status_text.startswith('Auto analyze: ')


def test_a_clean_clip_is_told_so(session, tidy):
    session.advisor.analyze()
    settle(session)
    assert session.status_text.startswith('Auto analyze: nothing to fix')
    assert not session.advisor.advice(tidy).diagnosis.has_fixes


def test_the_advice_goes_out_of_date_with_an_edit_and_a_watched_take_gets_fresh_advice(session, quiet):
    advisor = session.advisor
    advisor.analyze()
    settle(session)
    old = advisor.advice(quiet)
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    assert advisor.advice(quiet) is None and advisor.working(quiet)
    advisor.refresh()
    settle(session)
    fresh = advisor.advice(quiet)
    assert fresh is not None and fresh.key != old.key


def test_a_take_nobody_asked_about_is_not_analyzed(session, quiet):
    session.advisor.refresh()
    assert session.jobs.pending == 0 and not session.advisor.watching(quiet)
    assert not session.advisor.working(quiet)


def test_each_take_keeps_its_own_advice(session, quiet):
    session.advisor.analyze()
    settle(session)
    other = record(session, samples=bursts(13.0, [(1.0, 12.0)], amp=0.14, f0=137.0))
    assert session.current is other and not session.advisor.watching(other) and session.advisor.advice(other) is None
    session.workspace.open_take(quiet)
    assert session.advisor.advice(quiet) is not None


def test_without_a_take_there_is_nothing_to_analyze_or_fix(session):
    assert session.advisor.analyze() is False and session.advisor.fix() is False


def test_a_clip_that_cannot_be_looked_at_says_why(session, quiet, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError('FFmpeg stopped')

    monkeypatch.setattr(autofix, 'diagnose', broken)
    session.advisor.analyze()
    settle(session)
    assert session.advisor.advice(quiet).error == 'FFmpeg stopped'
    assert session.status_text == 'Could not analyze the clip: FFmpeg stopped'
    assert session.advisor.fix() is False and 'Auto fix could not look at the clip: FFmpeg stopped' in session.status_text


# ---- Auto fix ---------------------------------------------------------------------------------------------------------

def test_auto_fix_applies_the_whole_plan_as_one_undo_step(session, quiet):
    assert session.advisor.fix() and quiet.history.steps == []                  # waiting for the advice
    assert session.status_text == 'Auto fix: looking at the clip first...'
    settle(session)
    assert quiet.history.steps == ['Auto fix (2 steps)']
    assert [e['op'] for e in quiet.history.edits] == ['cut', 'cut'] and quiet.history.chain['limiter']['enabled']
    assert quiet.history.chain['level']['gain_db'] > 10
    assert 'Auto fix (2 steps) done' in session.status_text and 'original recording is unchanged' in session.status_text
    assert quiet.needs_transcript_review                                         # the same as any other cut
    session.editor.undo()
    assert quiet.history.edits == [] and quiet.history.chain['level']['gain_db'] == 0.0
    session.editor.redo()
    assert quiet.history.steps == ['Auto fix (2 steps)']


def test_auto_fix_with_the_advice_known_applies_at_once(session, quiet):
    session.advisor.analyze()
    settle(session)
    assert session.advisor.fix() is True
    assert quiet.history.steps == ['Auto fix (2 steps)'] and session.jobs.pending <= 1


def test_auto_fix_leaves_a_clip_that_passes_alone(session, tidy):
    session.advisor.fix()
    settle(session)
    assert tidy.history.steps == [] and 'nothing here it can repair' in session.status_text


def test_after_auto_fix_the_next_advice_has_nothing_more_to_repair(session, quiet):
    session.advisor.fix()
    settle(session)
    settle(session)                                                             # the follow-up analysis of the new recipe
    again = session.advisor.advice(quiet)
    assert again is not None and not again.diagnosis.has_fixes


def test_auto_fix_remembers_what_it_did_until_something_else_changes(session, quiet):
    session.advisor.fix()
    settle(session)
    done = session.advisor.done(quiet)
    assert done.steps[0] == 'Trim the silence at the ends' and done.steps[1].startswith('Level +')
    assert any(c.label == 'Speech level' for c in done.changes)
    session.editor.set_fade_ms(20)
    assert session.advisor.done(quiet) is None


def test_a_change_while_auto_fix_is_looking_stops_it(session, make_session):
    held = HeldJobs()
    s = make_session(root=session.lib.root.parent / 'held', jobs=held)
    s.workspace.add_speaker('Held Speaker')
    take = record(s, samples=bursts(15.0, LONG_ENDS, amp=0.03, f0=137.0))
    s.advisor.fix()
    s.editor.set_selection(0.0, 0.5)
    s.editor.cut_selection()                                                    # the person edits while the plan is made
    held.run(0)
    assert 'The clip changed while Auto fix was looking at it' in s.status_text
    assert take.history.steps == ['Cut 0.00 to 0.50 s']


def test_a_plan_the_editor_refuses_changes_nothing(session, quiet):
    plan = SimpleNamespace(ops=[('cut', 0.0, 99.0, {})], chain={}, label='Auto fix (1 step)', steps=['x'], has_fixes=True)
    assert session.editor.apply_auto_fix(plan) is False
    assert quiet.history.steps == [] and 'cannot remove the whole clip' in session.status_text


def test_a_plan_with_nothing_to_change_is_refused(session, quiet):
    plan = SimpleNamespace(ops=[], chain={}, label='Auto fix (1 step)', steps=['x'], has_fixes=True)
    assert session.editor.apply_auto_fix(plan) is False
    assert 'nothing to change' in session.status_text and quiet.history.steps == []
