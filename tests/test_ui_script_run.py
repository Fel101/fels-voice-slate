"""Hands-free reading in the window: the record key, Esc, the line again, the count-in on the screen and the sign on top.

The run itself is tested without a window in test_script_run.py."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtGui import QKeySequence  # noqa: E402

from recorder import settings  # noqa: E402
from recorder.session import run as run_mod  # noqa: E402
from recorder.ui.editor_panel import EditorPanel  # noqa: E402

EVERYTHING = {'next_line': True, 'stop_after_pause': True, 'pause_seconds': 1.5, 'auto_start': True, 'count_in': 3}
LINES = 'The first line to read.\nThe second line to read.\nThe third line to read.'


@pytest.fixture
def scripted(window, session, open_fake):
    session.script.load_text(LINES)
    session.run.set_options(**EVERYTHING)
    window.flush()
    return window


def test_with_the_count_in_on_the_record_button_starts_a_run(scripted, session, clock):
    window = scripted
    assert window.topbar.record.text().startswith('Start run')
    window.actions.record.trigger()
    window.flush()
    assert session.run.active and window.editor_panel.stack.currentIndex() == EditorPanel.LIVE
    assert window.topbar.record.text().startswith('End run')
    banner = window.editor_panel.live.banner
    assert banner.isVisible() and banner.big.text() == 'Next line in 3' and 'Esc or F9 ends the run' in banner.keys.text()
    live = window.editor_panel.live
    assert not live.wave.isVisible() and not live.length_box.isVisible()        # nothing records yet: no REC sign
    clock.advance(1.2)
    session.tick()
    window.flush()
    assert banner.big.text() == 'Next line in 2'
    clock.advance(2.0)
    session.tick()
    window.flush()
    assert session.capturing.recording and banner.big.text() == 'Recording: read the line'
    assert live.wave.isVisible() and live.length_box.isVisible()


def test_editing_and_moving_along_the_script_wait_for_the_run(scripted, session):
    window = scripted
    window.actions.record.trigger()
    window.flush()
    for name in ('next_line', 'prev_line', 'hands_free', 'import_recording', 'settings', 'clear_script', 'arm'):
        assert not window.actions[name].isEnabled(), name
    assert window.actions.again.isEnabled() and not window.topbar.device.isEnabled()


def test_esc_ends_the_run_and_lets_the_microphone_go(scripted, session, open_fake):
    window = scripted
    window.actions.record.trigger()
    window.flush()
    window.actions.escape.trigger()
    window.flush()
    assert not session.run.active and open_fake[0].closed and session.capturing.capture is None
    assert window.topbar.record.text().startswith('Start run') and not window.editor_panel.live.banner.isVisible()


def test_the_record_key_ends_the_run(scripted, session):
    window = scripted
    window.actions.record.trigger()
    window.actions.record.trigger()
    assert not session.run.active


def test_the_sign_on_top_counts_in_while_the_window_is_out_of_sight(scripted, session, monkeypatch):
    window = scripted
    monkeypatch.setattr(window, 'isActiveWindow', lambda: False)
    window.actions.record.trigger()
    window._tick()
    assert window.badge.isVisible() and window.badge.text.text() == 'Script run  ·  next line in 3  ·  F9 ends'
    window.badge.stopRequested.emit()                   # a click on the sign
    assert not session.run.active


def test_the_line_again_has_a_key(scripted, session):
    window = scripted
    assert window.actions.again.shortcut() == QKeySequence('F8')
    session.script.step(1)
    window.actions.again.trigger()
    window.flush()
    assert session.run.active and session.run.phase == 'counting' and session.script.index == 1


def test_with_f8_as_the_record_key_the_line_again_moves_to_f7(qapp, make_session, dialogs, tmp_path):
    """0.6 let F8 be the record key. Two commands on one key would make Qt run neither."""
    from recorder.ui.main_window import MainWindow
    settings.update({'record_key': 'F8'}, tmp_path / 'settings.json')
    window = MainWindow(session=make_session(), dialogs=dialogs)
    window.timer.stop()
    try:
        assert window.actions.record.shortcut() == QKeySequence('F8')
        assert window.actions.again.shortcut() == QKeySequence('F7') and run_mod.again_key('F8') == 'F7'
    finally:
        window.close()


def test_the_hands_free_options_are_chosen_in_their_dialog(window, session, dialogs):
    session.script.load_text(LINES)
    dialogs.hands_free_answer = dict(EVERYTHING, pause_seconds=2.0)
    window.actions.hands_free.trigger()
    window.flush()
    assert dialogs.asked[-1][0] == 'hands_free' and dialogs.asked[-1][1].values == run_mod.OPTIONS
    assert session.run.options['pause_seconds'] == 2.0 and settings.load(session.settings_file)['hands_free']['auto_start']
    strip = window.editor_panel.script
    assert strip.hands.isVisible() and 'a take ends after 2 s of quiet' in strip.hands.text()


def test_backing_out_of_the_dialog_changes_nothing(window, session, dialogs):
    session.script.load_text(LINES)
    window.actions.hands_free.trigger()
    assert session.run.options == run_mod.OPTIONS and 'hands_free' not in settings.load(session.settings_file)


def test_the_dialog_offers_the_count_in_only_with_the_pause(qapp):
    from recorder.ui.dialogs import HandsFreeDialog
    dialog = HandsFreeDialog(dict(run_mod.OPTIONS), 'F9')
    assert not dialog.auto_start.isEnabled() and not dialog.count_in.isEnabled()
    dialog.auto_start.setChecked(True)
    assert dialog.values()['auto_start'] is False
    dialog.stop_after_pause.setChecked(True)
    assert dialog.auto_start.isEnabled() and dialog.values()['auto_start'] is True


def test_during_a_run_another_take_cannot_be_opened_nor_a_recording_imported(scripted, session, tmp_path):
    """The run records the line in front into the speaker in front: a take opened or imported meanwhile would come between."""
    window = scripted
    session.workspace.add_take(__import__('helpers').speech(2.0), 48000, 'mic', {})
    earlier = session.current
    window.actions.record.trigger()
    window.flush()
    window._take_picked(earlier)
    assert 'End the script run' in session.status_text
    window.import_files([tmp_path / 'some.wav'])
    assert session.status_text == 'End the script run before importing.' and len(session.workspace.takes) == 1
    assert not window.library.speakers.isEnabled() and not window.library.add_speaker.isEnabled()
    window.actions.escape.trigger()
    window.flush()
    assert window.library.add_speaker.isEnabled()
