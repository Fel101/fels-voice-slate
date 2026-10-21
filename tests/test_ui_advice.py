"""Auto analyze and Auto fix in the window: the two toolbar buttons, the card at the top of the Check tab, and the way
it follows the clip."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QLabel  # noqa: E402

from helpers import bursts, record  # noqa: E402
from recorder.ui.inspector import Inspector  # noqa: E402

LONG_ENDS = [(3.0, 6.0), (6.5, 9.5), (10.0, 12.5)]


def words(widget):
    """Everything the labels under a widget say, as one string."""
    return ' | '.join(label.text() for label in widget.findChildren(QLabel))


def settle(window, session):
    """Deliver the background work the way the timer would, then redraw."""
    session.jobs.drain()
    window.flush()


@pytest.fixture
def quiet(window, session):
    """A quiet clip with three seconds of silence in front of the voice and next to no noise, open in the window."""
    take = record(session, samples=bursts(15.0, LONG_ENDS, amp=0.03, noise=0.00005, f0=137.0))
    window.flush()
    return take


@pytest.fixture
def tidy(window, session):
    take = record(session, samples=bursts(13.0, [(0.3, 3.0), (3.5, 6.5), (7.0, 10.0), (10.5, 12.7)], amp=0.14,
                                          noise=0.00005, f0=137.0))
    window.flush()
    return take


def card(window):
    return window.inspector.check.auto


# ---- the buttons ------------------------------------------------------------------------------------------------------

def test_the_two_buttons_wait_for_a_take_and_sit_in_the_toolbar(window, session):
    names = ('auto_analyze', 'auto_fix')
    assert not any(window.actions[name].isEnabled() for name in names)
    record(session, 12.0)
    window.flush()
    assert all(window.actions[name].isEnabled() for name in names)
    entries = [e.action for e in window.editor_panel.toolbar._entries]
    assert all(window.actions[name] in entries for name in names)


def test_the_shortcuts_are_bound(window):
    assert 'Ctrl+Shift+A' in [s.toString() for s in window.actions.auto_analyze.shortcuts()]
    assert 'Ctrl+Shift+F' in [s.toString() for s in window.actions.auto_fix.shortcuts()]


# ---- Auto analyze -----------------------------------------------------------------------------------------------------

def test_a_clip_nobody_has_asked_about_shows_the_introduction(window, session, quiet):
    window.flush()
    assert 'in plain words what could be better' in words(card(window))
    assert card(window).verdict.isHidden() and card(window).fix.isEnabled()


def test_auto_analyze_shows_the_check_tab_and_then_what_it_found(window, session, quiet):
    window.flush()
    window.inspector.show_tab(Inspector.CLIP_TAB)
    window.actions.auto_analyze.trigger()
    window.flush()
    assert window.inspector.pages.currentIndex() == Inspector.CHECK_TAB
    assert card(window).verdict.text() == 'Analyzing...'
    settle(window, session)
    text = words(card(window))
    assert 'Speech level' in text and 'Long silence at the ends' in text
    assert 'Auto fix: Trim the silence at the ends' in text and 'Auto fix: Level +' in text
    assert 'AUTO FIX WILL CHANGE' in text and '→' in text                       # captions are set in capitals
    assert card(window).verdict.text().endswith('to look at') and card(window).fix.isEnabled()


def test_the_card_follows_the_clip_as_it_is_edited(window, session, quiet):
    window.actions.auto_analyze.trigger()
    settle(window, session)
    session.editor.set_selection(0.0, 1.0)
    session.editor.cut_selection()
    window.flush()
    assert card(window).verdict.text() == 'Analyzing...'
    window._request_analysis()                                  # what the analysis timer does a moment after an edit
    settle(window, session)
    assert card(window).verdict.text().endswith('to look at')


def test_a_clip_that_passes_says_so_and_leaves_nothing_for_auto_fix(window, session, tidy):
    window.actions.auto_analyze.trigger()
    settle(window, session)
    assert card(window).verdict.text() == 'Nothing to fix' and not card(window).fix.isEnabled()
    assert 'Every check passes' in words(card(window))


def test_a_clip_that_could_not_be_looked_at_says_why(window, session, quiet, monkeypatch):
    from recorder.core import autofix

    def broken(*args, **kwargs):
        raise ValueError('FFmpeg stopped')

    monkeypatch.setattr(autofix, 'diagnose', broken)
    window.actions.auto_analyze.trigger()
    settle(window, session)
    assert 'Could not analyze the clip: FFmpeg stopped' in words(card(window))


# ---- Auto fix ---------------------------------------------------------------------------------------------------------

def test_auto_fix_repairs_the_clip_and_the_card_says_what_it_did(window, session, quiet):
    window.flush()
    card(window).fix.click()                                    # the button on the card, not the toolbar
    settle(window, session)
    assert quiet.history.steps == ['Auto fix (2 steps)']
    text = words(card(window))
    assert 'AUTO FIX DID' in text and '✓  Trim the silence at the ends' in text and '✓  Level +' in text
    assert 'BEFORE AND AFTER' in text and 'Speech level' in text
    settle(window, session)                                     # the follow-up look at the repaired clip
    assert card(window).verdict.text() == 'Nothing to fix' and not card(window).fix.isEnabled()
    assert window.actions.undo.isEnabled()


def test_undo_takes_the_whole_auto_fix_back(window, session, quiet):
    window.actions.auto_fix.trigger()
    settle(window, session)
    window.actions.undo.trigger()
    settle(window, session)
    assert quiet.history.edits == [] and quiet.history.chain['level']['gain_db'] == 0.0
    window._request_analysis()
    settle(window, session)
    assert 'AUTO FIX DID' not in words(card(window)) and card(window).fix.isEnabled()


def test_the_toolbar_button_does_the_same_and_shows_the_check_tab(window, session, quiet):
    window.inspector.show_tab(Inspector.CLIP_TAB)
    window.actions.auto_fix.trigger()
    settle(window, session)
    assert window.inspector.pages.currentIndex() == Inspector.CHECK_TAB
    assert quiet.history.steps == ['Auto fix (2 steps)'] and 'Auto fix (2 steps) done' in session.status_text
