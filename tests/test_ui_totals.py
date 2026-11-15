"""The totals in the window: beside Takes, on the Script tab, and in the manifest and dataset dialogs."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt  # noqa: E402

from helpers import fill, record  # noqa: E402
from recorder.ui.dialogs import DatasetDialog, ManifestDialog  # noqa: E402
from recorder.ui.widgets import clips_text, format_total  # noqa: E402
from test_totals import kept  # noqa: E402
from ui_helpers import click_row  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.mark.parametrize('seconds, text', [(0, '0 s'), (41.4, '41 s'), (16.5, '17 s'), (21.5, '22 s'),
                                           (59.6, '1 min 00 s'), (245, '4 min 05 s'), (3599.6, '1 h 00 min'),
                                           (3725, '1 h 02 min'), (36000, '10 h 00 min')])
def test_a_total_is_said_in_words(seconds, text):
    assert format_total(seconds) == text


def test_clips_are_counted_in_words():
    assert clips_text(0, 0) == 'No kept clips yet'
    assert clips_text(1, 12.0) == '1 clip, 12 s'
    assert clips_text(3, 31.2) == '3 clips, 31 s'


def test_beside_takes_is_what_the_speaker_has_kept_or_what_is_selected(window, session):
    totals = window.library.totals
    first = record(session, 12.0)
    second = record(session, 10.0)
    window.flush()
    assert totals.isHidden()                                     # nothing kept: nothing to add up
    fill(session)
    session.keep()
    window.flush()
    assert not totals.isHidden() and totals.text() == '1 kept · 10 s'
    click_row(window, 1, modifiers=CTRL)
    assert window.library.selected_takes() == [second, first] and totals.text() == '2 selected · 22 s'
    click_row(window, 0)
    assert totals.text() == '1 kept · 10 s'


def test_the_script_tab_says_how_far_the_script_has_got(window, session):
    session.script.load_text('One line.\nAnother line.\nA third line.\nThe last line.')
    record(session, 12.0, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    record(session, 8.0)                                         # kept, but of no line of the script
    fill(session)
    session.keep()
    window.flush()
    summary = window.inspector.script.summary.text()
    assert summary.startswith('1 of 4 lines (25%) have a kept clip, 12 s in all, for this speaker.')


def test_the_export_dialogs_are_told_how_much_goes_in(window, session, dialogs):
    record(session, 12.0)
    fill(session)
    session.keep()
    other = session.lib.add_speaker('Ben')
    kept(session.lib, other, 9.0)
    window.flush()
    dialogs.manifest_answer = None
    window.export_manifest()
    dialogs.dataset_answer = None
    window.export_dataset()
    asked = {kind: seen.totals for kind, seen in dialogs.asked if kind in ('manifest', 'dataset')}
    for totals in asked.values():
        assert totals['speaker'] == (1, pytest.approx(12.0, abs=0.01))
        assert totals['all'] == (2, pytest.approx(21.0, abs=0.02))
    assert set(asked) == {'manifest', 'dataset'}


@pytest.mark.parametrize('dialog', [ManifestDialog, DatasetDialog])
def test_the_export_dialogs_show_how_much_goes_in_for_the_choice_made(qapp, dialog):
    shown = dialog('Ann Lee', {'speaker': (1, 12.0), 'all': (3, 245.0)})
    assert shown.scope.itemText(0) == 'Kept clips of Ann Lee'
    assert shown.scope.amount.text() == '1 clip, 12 s'
    shown.scope.setCurrentIndex(shown.scope.findData('all'))
    assert shown.scope.amount.text() == '3 clips, 4 min 05 s'
    shown.close()


def test_without_totals_the_export_dialogs_show_none(qapp):
    shown = ManifestDialog('Ann Lee')
    assert not hasattr(shown.scope, 'amount')
    shown.close()
