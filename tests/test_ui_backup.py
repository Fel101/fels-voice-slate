"""Back up recordings and Bring in a backup, from the window: where they are, when they can be used, and a round trip."""
import itertools

import pytest

pytest.importorskip('PySide6')

from helpers import fill, finish_batch, record  # noqa: E402


def keep(session, transcript):
    record(session, 8.0)
    fill(session, transcript=transcript)
    session.keep()


def test_both_are_in_the_gear_menu_beside_the_output_folder(window):
    texts = [action.text() for action in window.topbar.menu.actions()]
    at = texts.index('Open output folder')
    assert texts[at + 1:at + 3] == ['Back up recordings...', 'Bring in a backup...']


def test_back_up_needs_recordings_and_bring_in_needs_none(window, session, tmp_path):
    window.flush()
    assert window.actions.back_up.isEnabled() and not window.actions.bring_in.isEnabled()
    session.workspace.set_root(tmp_path / 'empty')
    window.flush()
    assert not window.actions.back_up.isEnabled() and window.actions.bring_in.isEnabled()


def test_neither_can_start_while_recording(window, session, open_fake):
    session.capturing.start()
    window.flush()
    assert not window.actions.back_up.isEnabled() and not window.actions.bring_in.isEnabled()


def test_a_round_trip_through_the_window(window, session, dialogs, tmp_path):
    keep(session, 'First line.')
    keep(session, 'Second line.')
    window.flush()
    dialogs.folder_answer = str(tmp_path / 'usb' / 'backup')
    window.actions.back_up.trigger()
    finish_batch(session)
    assert session.status_text.startswith('Backed up ') and (tmp_path / 'usb' / 'backup' / 'speakers.json').is_file()

    session.workspace.set_root(tmp_path / 'other-pc')
    window.flush()
    assert session.workspace.speakers == [] and window.actions.bring_in.isEnabled()
    dialogs.confirm_answer = True
    window.actions.bring_in.trigger()
    asked = dialogs.confirmations[-1]
    assert asked.title == 'Bring in this backup?' and str(tmp_path / 'other-pc') in asked.text
    finish_batch(session)
    window.flush()
    assert [name for _, name in session.workspace.speakers] == ['Smoke Speaker']
    assert len(session.workspace.takes) == 2 and all(take.kept for take in session.workspace.takes)
    assert window.library.model.rowCount() == 2


def test_saying_no_brings_nothing_in(window, session, dialogs, tmp_path):
    keep(session, 'A line.')
    dialogs.folder_answer = str(tmp_path / 'backup')
    window.actions.back_up.trigger()
    finish_batch(session)
    session.workspace.set_root(tmp_path / 'other-pc')
    window.flush()
    dialogs.confirm_answer = False
    window.actions.bring_in.trigger()
    assert not session.batch.running and not list((tmp_path / 'other-pc').rglob('*.wav'))
    assert session.workspace.speakers == []


def test_stop_says_that_it_removes_what_was_copied(window, session, dialogs, tmp_path):
    keep(session, 'A line.')
    session.batch.clock = itertools.count(0, 0.025).__next__        # one step a tick, as in the window
    dialogs.folder_answer = str(tmp_path / 'backup')
    window.actions.back_up.trigger()
    window.flush()
    assert not window.status_stop.isHidden()
    assert window.status_stop.toolTip() == 'Stop, and remove what it has copied. Nothing else changes'
    window.status_stop.click()
    window.flush()
    assert window.status_stop.isHidden() and not list((tmp_path / 'backup').rglob('*'))


def test_a_folder_that_is_refused_is_said_why(window, session, dialogs, tmp_path):
    keep(session, 'A line.')
    (tmp_path / 'full').mkdir()
    (tmp_path / 'full' / 'mine.txt').write_text('keep me', encoding='utf-8')
    dialogs.folder_answer = str(tmp_path / 'full')
    window.actions.back_up.trigger()
    assert not session.batch.running
    assert dialogs.notices[-1].title == 'Back up recordings' and 'empty folder' in dialogs.notices[-1].text
