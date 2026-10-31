"""The main window: what it shows in each state, what the commands do, how it asks, and how it closes.

Everything runs on Qt's offscreen platform with a fake player, a fake capture and canned dialogs.
"""
import pytest

pytest.importorskip('PySide6')
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402

import ui_helpers  # noqa: E402
from helpers import RATE, fill, record, speech  # noqa: E402
from recorder import settings  # noqa: E402
from recorder.ui.editor_panel import EditorPanel  # noqa: E402
from recorder.ui.inspector import Inspector  # noqa: E402


def cards(window):
    return [card for card in window.library._cards if not card.isHidden()]


# ---- what the window shows ----------------------------------------------------------------------------------------------

def test_the_window_opens_on_the_welcome_screen(window):
    assert window.editor_panel.stack.currentIndex() == EditorPanel.EMPTY
    assert window.inspector.modes.currentIndex() == Inspector.IDLE
    assert window.actions.record.isEnabled() and window.actions.import_recording.isEnabled()
    assert not window.actions.undo.isEnabled() and not window.actions.keep.isEnabled()
    assert window.library.empty.isVisibleTo(window.library) and not cards(window)


def test_a_new_take_opens_in_the_editor(window, session):
    record(session, 12.0)
    window.flush()
    assert window.editor_panel.stack.currentIndex() == EditorPanel.EDIT
    assert window.inspector.modes.currentIndex() == Inspector.REVIEW
    assert len(cards(window)) == 1 and window.library.count.text() == '1'
    assert window.actions.keep.isEnabled() and window.actions.play.isEnabled()


def test_edit_commands_follow_the_selection(window, session):
    record(session, 12.0)
    window.flush()
    needs_selection = ('cut', 'trim', 'silence', 'fade_in', 'fade_out', 'gain', 'zoom_selection')
    assert not any(window.actions[name].isEnabled() for name in needs_selection)
    session.editor.set_selection(2.0, 3.0)
    window.flush()
    assert all(window.actions[name].isEnabled() for name in needs_selection)
    session.editor.clear_selection()
    window.flush()
    assert not any(window.actions[name].isEnabled() for name in needs_selection)


def test_undo_and_redo_are_enabled_by_the_history(window, session):
    record(session, 12.0)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    window.flush()
    assert window.actions.undo.isEnabled() and not window.actions.redo.isEnabled()
    assert window.actions.check_joins.isEnabled()
    window.actions.undo.trigger()
    window.flush()
    assert not window.actions.undo.isEnabled() and window.actions.redo.isEnabled()
    assert not window.actions.check_joins.isEnabled()


def test_the_clock_shows_the_edited_length_after_a_cut(window, session):
    record(session, 12.0)
    session.editor.set_selection(2.0, 4.0)
    session.editor.cut_selection()
    window.flush()
    assert window.topbar.clock.text() == '00:00.000'
    assert 'after cuts' in window.topbar.sub.text() and '00:10.000' in window.topbar.sub.text()


def test_the_status_line_shows_the_latest_status(window, session):
    session.bus.status('Something happened.')
    assert window.status_text.full_text == 'Something happened.'


def test_a_notice_is_shown_once_the_session_call_that_raised_it_has_returned(window, session, dialogs):
    session.bus.notice('warning', 'Careful', 'Mind the gap.')
    assert dialogs.notices == []                   # not inside the call that raised it
    window.flush()
    assert [(n.level, n.title, n.text) for n in dialogs.notices] == [('warning', 'Careful', 'Mind the gap.')]


def test_an_empty_capture_is_refused_with_a_message(window, session, dialogs):
    assert session.workspace.add_take(speech(0.0), RATE, 'system', {}) is None
    window.flush()
    assert 'No audio' in dialogs.notices[0].text


# ---- recording ----------------------------------------------------------------------------------------------------------

def test_recording_switches_the_window_to_the_live_view_and_back(window, session, open_fake, clock):
    window.actions.record.trigger()
    window.flush()
    assert window.editor_panel.stack.currentIndex() == EditorPanel.LIVE
    assert window.inspector.modes.currentIndex() == Inspector.RECORDING
    assert window.topbar.record.text().startswith('Stop')
    assert not window.topbar.source.isEnabled() and not window.topbar.device.isEnabled()
    assert not window.actions.arm.isEnabled() and not window.actions.import_recording.isEnabled()
    window._tick()
    assert cards(window)[0].live and window.library.count.text() == '0'
    window.actions.record.trigger()
    clock.advance(1.0)
    window._tick()
    window.flush()
    assert window.editor_panel.stack.currentIndex() == EditorPanel.EDIT
    assert window.topbar.record.text().startswith('Record')
    assert len(session.workspace.takes) == 1 and 'Take saved' in window.status_text.full_text


def test_the_welcome_screen_record_button_starts_a_recording(window, session, open_fake):
    window.editor_panel.empty.recordRequested.emit()
    assert session.capturing.recording


def test_a_take_cannot_be_opened_while_recording(window, session, open_fake):
    first = record(session, 12.0)
    second = record(session, 10.0)
    session.capturing.start()
    window.flush()
    window._take_picked(first)
    assert session.current is second and 'Stop the recording' in window.status_text.full_text


def test_the_meter_follows_an_armed_device(window, session, open_fake):
    window.actions.arm.trigger()
    window.flush()
    window._tick()
    assert window.topbar.meter.peak == pytest.approx(-20.0) and window.topbar.meter.rms == pytest.approx(-30.0)


def test_picking_a_device_from_the_list_keeps_the_choice(window, session, monkeypatch):
    from recorder import capture as cap
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'First'), (5, 'Second')])
    session.capturing.refresh_devices()
    window.flush()
    assert window.topbar.device.count() == 2
    window.topbar.device.setCurrentIndex(1)
    window.topbar.device.activated.emit(1)
    assert session.capturing.device == 5
    session.bus.notify('devices')
    window.flush()
    assert window.topbar.device.currentData() == 5


def test_the_source_switch_lists_the_loopback_devices(window, session):
    window.topbar.source.buttons[1].click()
    window.flush()
    assert session.capturing.source == 'system'
    assert window.topbar.device.itemText(0) == 'Test speakers [Loopback]'


# ---- keeping, discarding, importing -------------------------------------------------------------------------------------

def test_keep_writes_the_clip(window, session):
    take = record(session, 14.0)
    fill(session)
    window.flush()
    window.keep()
    assert take.kept and take.export_path.exists() and take.sidecar_path.exists()
    window.flush()
    assert window.inspector.ready.keep.text().startswith('Update kept clip')


def test_keep_after_a_cut_asks_whether_the_transcript_still_matches(window, session, dialogs):
    take = record(session, 14.0)
    fill(session)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    window.flush()
    dialogs.confirm_answer = False
    window.keep()
    assert dialogs.confirmations[0].title == 'Check the transcript' and not take.kept
    assert window.inspector.tabs.current == 0
    dialogs.confirm_answer = True
    window.keep()
    assert take.kept


def test_keep_with_missing_permission_shows_the_reasons_and_writes_nothing(window, session, dialogs):
    take = record(session, 14.0)
    fill(session, consent=False)
    window.keep()
    assert not take.kept and take.export_path is None
    assert dialogs.notices[-1].level == 'warning' and 'permission' in dialogs.notices[-1].text.lower()


def test_keep_reports_a_failed_write(window, session, dialogs, monkeypatch):
    take = record(session, 14.0)
    fill(session)
    monkeypatch.setattr(session.lib, 'keep', lambda slug, t: (_ for _ in ()).throw(OSError('disk full')))
    window.keep()
    assert not take.kept and dialogs.notices[-1].level == 'error' and 'disk full' in dialogs.notices[-1].text


def test_discard_asks_then_sends_the_files_to_the_recycle_bin(window, session, dialogs, recycle):
    take = record(session, 12.0)
    master = take.master_path
    window.flush()
    dialogs.confirm_answer = False
    window.discard()
    assert master.exists() and session.current is take and recycle.sent == []
    dialogs.confirm_answer = True
    window.discard()
    assert not master.exists() and session.current is None and recycle.sent
    assert 'Recycle Bin' in dialogs.confirmations[-1].text and dialogs.confirmations[-1].yes == 'Move to Recycle Bin'


def test_discard_offers_permanent_deletion_only_when_there_is_no_recycle_bin(window, session, dialogs, recycle):
    take = record(session, 12.0)
    master = take.master_path
    recycle.fail = 'The Recycle Bin is full.'
    dialogs.confirm_answer = [True, False]
    window.discard()
    assert master.exists() and dialogs.confirmations[-1].danger and 'cannot be undone' in dialogs.confirmations[-1].text
    dialogs.confirm_answer = [True, True]
    window.discard()
    assert not master.exists() and session.current is None


def test_discarding_one_of_two_takes_removes_only_that_take(window, session, dialogs):
    first = record(session, 12.0)
    second = record(session, 10.0)
    window.flush()
    window._take_picked(first)
    window.discard()
    assert session.workspace.takes == [second]


def test_re_record_replaces_the_take_and_starts_recording(window, session, dialogs, open_fake, recycle):
    record(session, 12.0)
    window.flush()
    window.rerecord()
    assert recycle.sent and session.capturing.recording
    dialogs.confirm_answer = False
    session.capturing.capture.recording = False
    record(session, 12.0)
    before = len(session.workspace.takes)
    window.rerecord()
    assert len(session.workspace.takes) == before


def test_import_copies_a_chosen_file_in_as_a_take(window, session, dialogs, tmp_path):
    path = tmp_path / 'voice.wav'
    sf.write(str(path), speech(8.0), RATE)
    dialogs.file_answer = None
    window.import_recording()
    assert session.workspace.takes == []
    dialogs.file_answer = str(path)
    window.import_recording()
    window.flush()
    assert len(session.workspace.takes) == 1 and session.current.source == 'import'


def drop(window, *paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    window.dropEvent(QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier))


def test_dropping_audio_files_imports_them_and_ignores_other_files(window, session, tmp_path):
    good, other = tmp_path / 'a.wav', tmp_path / 'notes.txt'
    sf.write(str(good), speech(8.0), RATE)
    other.write_text('hello', encoding='utf-8')
    drop(window, good, other)
    assert [t.source for t in session.workspace.takes] == ['import']


def test_a_drag_that_holds_no_audio_is_not_accepted(window, tmp_path):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / 'notes.txt'))])
    assert window._dropped_audio(mime) == []


def test_new_speaker_asks_for_a_name(window, session, dialogs):
    dialogs.text_answer = None
    window.new_speaker()
    assert session.workspace.speakers == [('smoke-speaker', 'Smoke Speaker')]
    dialogs.text_answer = 'Sam Rivera'
    window.new_speaker()
    window.flush()
    assert session.workspace.name_of(session.workspace.speaker) == 'Sam Rivera'
    assert window.library.speakers.currentText() == 'Sam Rivera'


def test_adding_an_existing_name_selects_that_speaker_instead_of_duplicating(window, session, dialogs):
    dialogs.text_answer = 'Sam Rivera'
    window.new_speaker()
    dialogs.text_answer = 'Smoke Speaker'
    window.new_speaker()
    assert len(session.workspace.speakers) == 2
    assert session.workspace.name_of(session.workspace.speaker) == 'Smoke Speaker'


def test_a_refused_speaker_name_is_explained(window, session, dialogs, monkeypatch):
    monkeypatch.setattr(session.workspace, 'add_speaker', lambda name: (_ for _ in ()).throw(ValueError('Enter a name')))
    dialogs.text_answer = 'Anyone'
    window.new_speaker()
    assert dialogs.notices[-1].title == 'New speaker' and 'Enter a name' in dialogs.notices[-1].text


def test_open_folder_opens_the_speakers_folder(window, session, dialogs):
    window.open_folder()
    assert dialogs.opened and dialogs.opened[-1].exists()


def test_a_script_file_fills_the_script_strip(window, session, dialogs, tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('First line of the script.\nSecond line of the script.\n', encoding='utf-8')
    dialogs.file_answer = str(path)
    window.load_script()
    window.flush()
    strip = window.editor_panel.script
    assert strip.isVisibleTo(window.editor_panel) and strip.line.text() == 'First line of the script.'
    window.actions.next_line.trigger()
    window.flush()
    assert strip.line.text() == 'Second line of the script.' and not strip.next.isEnabled() and strip.previous.isEnabled()
    window.actions.clear_script.trigger()
    window.flush()
    assert not strip.isVisibleTo(window.editor_panel)


# ---- settings, copying, geometry -----------------------------------------------------------------------------------------

def test_settings_are_saved_only_when_the_dialog_is_accepted(window, session, dialogs, tmp_path):
    window.open_settings()
    assert dialogs.settings_seen['tail_seconds'] == pytest.approx(0.5)
    assert 'tail_seconds' not in settings.load(session.settings_file)
    new_root = tmp_path / 'elsewhere'
    dialogs.settings_answer = {'root': str(new_root), 'tail_seconds': 0.25, 'ffmpeg': None, 'asr_python': None,
                               'asr_model': None}
    window.open_settings()
    assert settings.load(session.settings_file)['tail_seconds'] == 0.25
    assert session.capturing.tail_seconds == pytest.approx(0.25)
    assert session.lib.root == new_root.resolve() or session.lib.root == new_root


def test_copying_the_transcript_and_the_path_uses_the_clipboard(window, session, qapp):
    take = record(session, 14.0)
    fill(session, transcript='The harbour lights came on at dusk.')
    window.keep()
    window.copy_text('transcript')
    assert qapp.clipboard().text() == 'The harbour lights came on at dusk.'
    window.copy_text('path')
    assert qapp.clipboard().text() == str(take.export_path)


def test_the_windows_geometry_is_saved_and_restored(session, dialogs, qapp, monkeypatch):
    from recorder.ui.main_window import MainWindow
    first = MainWindow(session=session, dialogs=dialogs)
    first.timer.stop()
    first.show()
    ui_helpers.pump(qapp)
    first._save_geometry()
    saved = bytes(first.saveGeometry())
    first.hide()
    assert isinstance(settings.load(session.settings_file).get('window'), str)
    restored = []
    monkeypatch.setattr(MainWindow, 'restoreGeometry', lambda self, data: restored.append(bytes(data)) or True)
    second = MainWindow(session=session, dialogs=dialogs)
    second.timer.stop()
    assert restored == [saved]


def test_a_damaged_saved_geometry_does_not_stop_the_window_opening(session, dialogs, qapp):
    from recorder.ui.main_window import MainWindow
    settings.update({'window': 'this is !!! not base64'}, session.settings_file)
    w = MainWindow(session=session, dialogs=dialogs)
    w.timer.stop()
    w.show()
    ui_helpers.pump(qapp)
    assert w.isVisible()
    w.hide()


# ---- closing ------------------------------------------------------------------------------------------------------------

def test_closing_saves_a_running_take_first(window, session, open_fake):
    session.capturing.start()
    assert window.close()
    assert len(session.workspace.takes) == 1 and session.workspace.takes[0].master_path.is_file() and session.closed


def test_a_failed_save_keeps_the_window_open_and_a_later_close_retries(window, session, dialogs, open_fake, monkeypatch):
    save_master = session.lib.save_master
    monkeypatch.setattr(session.lib, 'save_master', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    session.capturing.start()
    assert window.close() is False
    take = session.workspace.takes[0]
    assert window.isVisible() and take.unsaved and not session.closed
    assert dialogs.notices[-1].level == 'error' and 'disk full' in dialogs.notices[-1].text
    monkeypatch.setattr(session.lib, 'save_master', save_master)
    assert window.close()
    assert not take.unsaved and take.master_path.is_file() and session.closed


# ---- the keyboard -------------------------------------------------------------------------------------------------------

def test_no_two_commands_share_a_shortcut(window):
    seen = {}
    for name in window.actions.names():
        for sequence in window.actions[name].shortcuts():
            text = sequence.toString()
            assert text not in seen, f'{name} and {seen[text]} both use {text}'
            seen[text] = name


def test_the_documented_shortcuts_are_bound(window):
    expected = {'record': 'F9', 'play': 'Space', 'cut': 'Del', 'undo': 'Ctrl+Z', 'keep': 'Ctrl+S', 'loop': 'L', 'ab': 'A',
                'import_recording': 'Ctrl+O', 'shortcuts': 'F1', 'escape': 'Esc'}
    for name, text in expected.items():
        assert text in [s.toString() for s in window.actions[name].shortcuts()], name


def test_pressing_keys_runs_the_commands(window, session, qapp):
    record(session, 12.0)
    window.flush()
    window.activateWindow()
    ui_helpers.pump(qapp)
    session.editor.set_selection(2.0, 3.0)
    window.flush()
    ui_helpers.key(window, Qt.Key.Key_Delete)
    assert len(session.current.history.edits) == 1
    window.flush()                                  # the window enables Undo once it has heard about the cut
    ui_helpers.key(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert session.current.history.edits == []


def test_space_plays_and_pauses_and_escape_stops_then_deselects(window, session, player, qapp):
    record(session, 12.0)
    window.flush()
    window.activateWindow()
    session.editor.set_selection(2.0, 3.0)
    window.flush()
    ui_helpers.key(window, Qt.Key.Key_Space)
    assert session.transport.active and session.transport.playing
    ui_helpers.key(window, Qt.Key.Key_Space)
    assert session.transport.active and not session.transport.playing
    ui_helpers.key(window, Qt.Key.Key_Escape)
    assert not session.transport.active and session.editor.sel is not None
    ui_helpers.key(window, Qt.Key.Key_Escape)
    assert session.editor.sel is None


def test_the_in_and_out_keys_make_a_selection_in_either_order(window, session, qapp):
    record(session, 12.0)
    window.flush()
    window.activateWindow()
    session.editor.set_playhead(6.0)
    window.actions.set_in.trigger()
    session.editor.set_playhead(2.0)
    window.actions.set_out.trigger()
    window.flush()
    assert session.editor.sel == pytest.approx((2.0, 6.0))


def test_a_lone_in_point_is_not_a_selection(window, session):
    record(session, 12.0)
    window.flush()
    session.editor.set_playhead(3.0)
    window.actions.set_in.trigger()
    window.flush()
    assert session.editor.sel is None and session.editor.in_point == pytest.approx(3.0)
    assert not window.actions.cut.isEnabled()


# ---- context menus -------------------------------------------------------------------------------------------------------

def test_the_waveform_menu_offers_the_edit_commands_and_can_remove_an_edit(window, session):
    record(session, 12.0)
    session.editor.set_selection(4.0, 5.0)
    session.editor.cut_selection()
    window.flush()
    menu = window.build_timeline_menu(4.5)
    labels = [a.text() for a in menu.actions() if a.text()]
    assert 'Cut' in labels and 'Select all' in labels and any(text.startswith('Remove: ') for text in labels)
    next(a for a in menu.actions() if a.text().startswith('Remove: ')).trigger()
    assert session.current.history.edits == []
    assert not any(a.text().startswith('Remove: ') for a in window.build_timeline_menu(4.5).actions())


def test_the_take_menu_opens_copies_and_discards(window, session, dialogs, qapp):
    first = record(session, 12.0)
    second = record(session, 10.0)
    window.flush()
    menu = window.build_take_menu(first)
    assert [a.text() for a in menu.actions() if a.text()] == ['Open', 'Rename', 'Copy file path', 'Discard...']
    menu.actions()[0].trigger()
    assert session.current is first
    next(a for a in menu.actions() if a.text() == 'Copy file path').trigger()
    assert qapp.clipboard().text() == str(first.master_path)
    next(a for a in menu.actions() if a.text() == 'Discard...').trigger()
    assert session.workspace.takes == [second]


# ---- starting up and reaching everything ---------------------------------------------------------------------------------

def test_a_damaged_speakers_file_does_not_stop_the_window_opening(make_session, dialogs, qapp, tmp_path):
    from recorder.ui.main_window import MainWindow
    clips = tmp_path / 'damaged-clips'
    (clips / 'bot' / 'masters').mkdir(parents=True)
    (clips / 'speakers.json').write_text('{"bot": ', encoding='utf-8')
    session = make_session(clips)
    w = MainWindow(session=session, dialogs=dialogs)
    w.timer.stop()
    w.show()
    ui_helpers.pump(qapp)
    assert w.isVisible() and session.workspace.speaker == 'bot'
    assert 'moved to' in session.lib.recovery_note and 'moved to' in w.status_text.full_text
    w.hide()


def test_every_field_of_the_clip_page_can_be_scrolled_into_view_in_a_small_window(window, session, qapp):
    record(session, 12.0)
    window.flush()
    window.resize(1100, 700)
    ui_helpers.pump(qapp)
    clip = window.inspector.clip
    scroll = window.inspector.pages.widget(0)
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0                                    # it does not all fit, so it must scroll
    reachable = set()
    for value in [*range(0, bar.maximum(), 5), bar.maximum()]:
        bar.setValue(value)
        for name in ('transcript', 'origin', 'allowed', 'source_url', 'consent', 'copy_path'):
            field = getattr(clip, name)
            top = field.mapTo(scroll.viewport(), field.rect().topLeft()).y()
            if 0 <= top and top + field.height() <= scroll.viewport().height():
                reachable.add(name)
    assert reachable == {'transcript', 'origin', 'allowed', 'source_url', 'consent', 'copy_path'}


def test_a_first_window_fits_the_screen_but_never_goes_below_the_minimum(qapp, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QSize
    from PySide6.QtGui import QGuiApplication

    from recorder.ui.main_window import MainWindow

    def on_screen(width, height):
        screen = SimpleNamespace(availableGeometry=lambda: SimpleNamespace(size=lambda: QSize(width, height)))
        monkeypatch.setattr(QGuiApplication, 'primaryScreen', staticmethod(lambda: screen))
        return MainWindow._first_size()

    assert on_screen(3840, 2000) == QSize(1440, 900)
    assert on_screen(1366, 728) == QSize(1306, 700)
    assert on_screen(800, 480) == QSize(1100, 700)
