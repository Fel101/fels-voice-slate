"""The panels around the editor: the take list, the inspector's three pages and the Keep checklist, the top bar, the
edit toolbar and the dialogs. The window is driven through the same widgets a person would use."""
import datetime as dt

import numpy as np
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402

import ui_helpers  # noqa: E402
from helpers import fill, record, speech  # noqa: E402
from recorder import audio_core as ac  # noqa: E402
from recorder import clean, transcript  # noqa: E402
from recorder.ui import dialogs as dialog_mod  # noqa: E402
from recorder.ui import library  # noqa: E402
from recorder.ui.inspector import Inspector  # noqa: E402


def label_text(widget):
    """Everything the labels under a widget say, as one string."""
    return ' | '.join(label.text() for label in widget.findChildren(QLabel))


def cards(window):
    return [card for card in window.library._cards if not card.isHidden()]


def settle(window, session):
    """Deliver the measurements the way the timer would, then redraw."""
    session.jobs.drain()
    window.flush()


# ---- the take list ----------------------------------------------------------------------------------------------------

def test_takes_are_listed_newest_first_with_their_state(window, session):
    first = record(session, 12.0)
    fill(session)
    window.keep()
    second = record(session, 10.0, source='system')
    third = record(session, 9.0)
    window.flush()
    shown = cards(window)
    assert [c.take for c in shown] == [third, second, first]
    assert [c.status for c in shown] == ['In review', 'Not kept', 'Kept']
    assert [c.title for c in shown] == ['Take 3  ·  9.0 s', 'Take 2  ·  10.0 s', 'Take 1  ·  12.0 s']
    assert 'System' in shown[1].sub and 'Mic' in shown[0].sub
    assert [c.selected for c in shown] == [True, False, False]


def test_a_take_that_could_not_be_written_is_marked(window, session, monkeypatch):
    monkeypatch.setattr(session.lib, 'save_master', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    record(session, 12.0)
    window.flush()
    assert cards(window)[0].status == 'Not saved'


def test_clicking_a_card_opens_that_take(window, session):
    first = record(session, 12.0)
    record(session, 10.0)
    window.flush()
    QTest.mouseClick(cards(window)[1], Qt.MouseButton.LeftButton)
    assert session.current is first
    window.flush()
    assert [c.selected for c in cards(window)] == [False, True]


def test_right_clicking_a_card_offers_the_take_menu(window, session, dialogs):
    record(session, 12.0)
    window.flush()
    QTest.mouseClick(cards(window)[0], Qt.MouseButton.RightButton)
    assert len(dialogs.menus) == 1 and [a.text() for a in dialogs.menus[0].actions()][:2] == ['Open', 'Copy file path']


def test_the_take_list_says_what_to_do_when_it_is_empty(window, session):
    assert window.library.empty.isVisibleTo(window.library)
    record(session, 12.0)
    window.flush()
    assert not window.library.empty.isVisibleTo(window.library)


def test_choosing_another_speaker_shows_that_speakers_takes(window, session):
    record(session, 12.0)
    session.workspace.add_speaker('Second Voice')
    window.flush()
    assert window.library.speakers.currentText() == 'Second Voice' and cards(window) == []
    window.library.speakers.setCurrentIndex(window.library.speakers.findData('smoke-speaker'))
    window.library.speakers.activated.emit(window.library.speakers.currentIndex())
    window.flush()
    assert len(cards(window)) == 1


def test_the_speaker_cannot_be_changed_while_recording(window, session, open_fake):
    session.workspace.add_speaker('Second Voice')
    session.capturing.start()
    window.flush()
    assert not window.library.speakers.isEnabled() and not window.library.add_speaker.isEnabled()
    assert session.workspace.select_speaker('smoke-speaker') is False
    assert 'Stop the recording' in window.status_text.full_text


def test_the_live_card_shows_what_has_been_recorded_so_far(window, session, open_fake, clock):
    session.capturing.start()
    window.flush()
    window._tick()
    live = cards(window)[0]
    assert live.live and live.take is None and live.status == 'Recording' and live.thumb is not None
    session.capturing.stop()
    clock.advance(1.0)                                  # the stop tail passes, and the take is saved
    window._tick()
    window.flush()
    assert all(not c.live for c in cards(window)) and len(cards(window)) == 1


def test_when_describes_a_take_by_day():
    now = dt.datetime(2026, 10, 20, 12, 0).astimezone()
    today = now.replace(hour=9, minute=5).isoformat()
    yesterday = (now - dt.timedelta(days=1)).replace(hour=21, minute=7).isoformat()
    older = (now - dt.timedelta(days=9)).replace(hour=9, minute=30).isoformat()
    assert library.when(today, now) == 'Today 09:05'
    assert library.when(yesterday, now) == 'Yesterday 21:07'
    assert library.when(older, now) == 'Oct 11 09:30'
    assert library.when('not a date', now) == '' and library.when(None, now) == ''


def test_a_live_outline_has_a_fixed_number_of_columns():
    assert library.live_thumbnail(np.zeros((0, 3), np.float32), 1.0) is None
    rows = np.array([(0.02 * (i + 1), -0.5, 0.25 + 0.001 * i) for i in range(100)], dtype=np.float32)
    high, low = library.live_thumbnail(rows, 2.0)
    assert len(high) == len(low) == library.THUMB_COLUMNS and high.max() > 0.3 and low.min() == pytest.approx(-0.5)


# ---- the Clip page ----------------------------------------------------------------------------------------------------

def test_typing_in_the_text_fields_writes_to_the_take(window, session):
    take = record(session, 12.0)
    window.flush()
    clip = window.inspector.clip
    clip.transcript.setPlainText('The harbour lights came on at dusk.')
    QTest.keyClicks(clip.origin, 'my room')
    QTest.keyClicks(clip.allowed, 'my own use')
    QTest.keyClicks(clip.source_url, 'https://example.org')
    clip.consent.setChecked(True)
    assert take.meta['transcript'] == 'The harbour lights came on at dusk.'
    assert (take.meta['origin'], take.meta['allowed_use'], take.meta['source_url']) == \
        ('my room', 'my own use', 'https://example.org')
    assert take.meta['consent_confirmed'] is True
    assert clip.counter.text() == f'35 / {ac.TRANSCRIPT_MAX}'


def test_switching_takes_swaps_the_fields_without_writing_anything_back(window, session):
    first = record(session, 12.0)
    fill(session, transcript='First take words.')
    second = record(session, 10.0)
    fill(session, transcript='Second take words.')
    window.flush()
    clip = window.inspector.clip
    assert clip.transcript.toPlainText() == 'Second take words.'
    window._take_picked(first)
    window.flush()
    assert clip.transcript.toPlainText() == 'First take words.' and clip.origin.text() == 'recorded in my room'
    assert (first.meta['transcript'], second.meta['transcript']) == ('First take words.', 'Second take words.')


def test_the_transcript_chip_says_where_the_text_came_from(window, session):
    take = record(session, 12.0, script_line='A line from the script.')
    window.flush()
    chip = window.inspector.clip.state
    assert chip.text() == 'From script'
    session.workspace.set_meta(transcript='Typed words of my own.')
    window.flush()
    assert not chip.isVisibleTo(window.inspector.clip)
    take.meta['draft_text'] = 'Typed words of my own.'
    session.workspace.set_meta(transcript='Typed words of my own.', origin='x')
    window.flush()
    assert chip.text() == 'Draft: check it'
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    window.flush()
    assert chip.text() == 'Check after cuts'


def test_the_character_counter_warns_outside_the_allowed_range(window, session):
    record(session, 12.0)
    window.flush()
    clip = window.inspector.clip
    clip.transcript.setPlainText('Hi')
    assert 'f3b94d' in clip.counter.styleSheet().lower()
    clip.transcript.setPlainText('Hello there, this is long enough.')
    assert clip.counter.styleSheet() == ''


def test_suggest_from_audio_explains_when_the_recognizer_is_not_set_up(window, session):
    record(session, 12.0)
    window.flush()
    window.inspector.clip.suggest.click()
    assert window.status_text.full_text == transcript.NOT_SET_UP


def test_suggest_asks_before_replacing_typed_text(window, session, dialogs, monkeypatch):
    record(session, 12.0)
    fill(session, transcript='Words I typed myself.')
    window.flush()
    requested = []
    monkeypatch.setattr(session.drafts, 'request', lambda take, replace=False, automatic=False: requested.append(replace))
    dialogs.confirm_answer = False
    window.suggest_transcript()
    assert requested == [] and dialogs.confirmations[-1].title == 'Replace the transcript?'
    dialogs.confirm_answer = True
    window.suggest_transcript()
    assert requested == [True]


def test_suggest_does_not_ask_before_replacing_the_recognizers_own_text_or_nothing(window, session, dialogs, monkeypatch):
    take = record(session, 12.0)
    window.flush()
    requested = []
    monkeypatch.setattr(session.drafts, 'request', lambda take, replace=False, automatic=False: requested.append(replace))
    window.suggest_transcript()                                 # nothing typed yet
    take.meta.update(transcript='Words the recognizer wrote.', draft_text='Words the recognizer wrote.')
    window.suggest_transcript()                                 # still exactly the draft
    assert requested == [True, True] and dialogs.confirmations == []


def test_the_clip_page_shows_a_draft_on_its_way_and_waits_for_it(window, session, monkeypatch):
    held = []
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(session.jobs, 'submit', lambda fn, done, error=None: held.append((fn, done, error)))
    monkeypatch.setattr(transcript, 'suggest', lambda *a: {'text': 'Words from the audio.'})
    take = record(session, 12.0)
    window.flush()
    clip = window.inspector.clip
    assert clip.state.text() == 'Drafting...' and clip.state.isVisibleTo(clip) and not clip.suggest.isEnabled()
    fn, done, _ = held.pop(0)                                   # the draft was asked for first; the measurements follow
    done(fn())
    window.flush()
    assert take.meta['transcript'] == 'Words from the audio.' and clip.transcript.toPlainText() == 'Words from the audio.'
    assert clip.state.text() == 'Draft: check it' and clip.suggest.isEnabled()


def test_the_copy_buttons_follow_what_there_is_to_copy(window, session):
    record(session, 14.0)
    window.flush()
    clip = window.inspector.clip
    assert not clip.copy_text.isEnabled() and not clip.copy_path.isEnabled()
    fill(session)
    window.flush()
    assert clip.copy_text.isEnabled() and not clip.copy_path.isEnabled()
    window.keep()
    window.flush()
    assert clip.copy_path.isEnabled()


def test_the_inspector_has_nothing_to_edit_without_a_take(window):
    assert window.inspector.modes.currentIndex() == Inspector.IDLE
    assert not window.inspector.clip.isEnabled() and not window.inspector.process.isEnabled()


# ---- the Process page ---------------------------------------------------------------------------------------------------

def chain(session):
    return session.current.history.chain


def test_a_switch_turns_a_step_on_as_one_undo_step(window, session):
    take = record(session, 12.0)
    window.flush()
    page = window.inspector.process
    assert not page.hp.switch.isChecked() and not page.hp_slider.isEnabled()
    page.hp.switch.click()
    assert chain(session)['highpass']['enabled'] is True and take.history.steps[-1] == 'High-pass on'
    window.flush()
    assert page.hp_slider.isEnabled()
    window.actions.undo.trigger()
    window.flush()
    assert chain(session)['highpass']['enabled'] is False and not page.hp.switch.isChecked()


def test_moving_a_slider_is_one_undo_step_however_far_it_goes(window, session):
    take = record(session, 12.0)
    window.flush()
    page = window.inspector.process
    page.hp.switch.click()
    window.flush()
    steps = len(take.history.steps)
    for hz in (90, 100, 110, 120):
        page.hp_slider.setValue(hz)
    assert chain(session)['highpass']['hz'] == 120.0
    assert len(take.history.steps) == steps + 1 and take.history.steps[-1].startswith('High-pass 120')
    window.flush()
    assert page.hp.value.text() == '120 Hz'


def test_the_hum_filter_follows_the_mains_choice(window, session):
    record(session, 12.0)
    window.flush()
    page = window.inspector.process
    page.hum.switch.click()
    window.flush()
    page.hum_region.buttons[0].click()
    assert chain(session)['hum']['enabled'] is True and chain(session)['hum']['hz'] == 50.0
    window.flush()
    assert page.hum_region.current == 0 and page.hum.value.text() == '50 Hz'


def test_noise_reduction_needs_ffmpeg_and_says_so(window, session, monkeypatch):
    record(session, 12.0)
    monkeypatch.setattr(clean, 'available', lambda: False)
    window.flush()
    page = window.inspector.process
    assert not page.denoise.switch.isEnabled() and 'FFmpeg was not found' in page.denoise_note.text()
    monkeypatch.setattr(clean, 'available', lambda: True)
    session.bus.notify('edits')
    window.flush()
    assert page.denoise.switch.isEnabled() and 'Voice identity' in page.denoise_note.text()
    page.denoise.switch.click()
    assert chain(session)['denoise']['enabled'] is True
    assert 'FFmpeg' in window.status_text.full_text


def test_normalize_brings_quiet_speech_up_and_reset_puts_it_back(window, session):
    record(session, 12.0, samples=speech(12.0, amp=0.03))
    window.flush()
    page = window.inspector.process
    page.normalize.click()
    window.flush()
    gain = chain(session)['level']['gain_db']
    assert gain > 5.0 and page.level.value.text() == f'{gain:+.1f} dB'
    page.reset.click()
    window.flush()
    assert chain(session)['level']['gain_db'] == 0.0 and page.gain_slider.value() == 0


def test_the_limiter_and_the_join_fade_have_their_own_controls(window, session):
    take = record(session, 12.0)
    window.flush()
    page = window.inspector.process
    page.limiter.switch.click()
    window.flush()
    page.ceiling_slider.setValue(-30)
    assert chain(session)['limiter'] == {'enabled': True, 'ceiling_db': -3.0}
    page.fade_slider.setValue(30)
    assert take.history.fade_ms == 30
    window.flush()
    assert page.fade.value.text() == '30 ms' and page.limiter.value.text() == '-3.0 dB'


def test_the_history_lists_every_step_and_clicking_one_goes_back_to_it(window, session):
    take = record(session, 12.0)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    session.editor.set_selection(5.0, 6.0)
    session.editor.silence_selection()
    window.flush()
    page = window.inspector.process
    assert page.history.count() == 3 and page.history.item(0).text() == 'Start' and page.history.currentRow() == 2
    page.history.itemClicked.emit(page.history.item(1))
    window.flush()
    assert len(take.history.edits) == 1 and page.history.currentRow() == 1
    assert page.history.item(2).toolTip().startswith('Undone')


def test_the_process_page_shows_the_order_things_happen_in(window, session):
    record(session, 12.0)
    window.flush()
    text = label_text(window.inspector.process)
    assert 'Order:' in text and 'filters' in text and 'limiter' in text
    assert 'Click a step to go back to it' in text


# ---- the Check page and the checklist ------------------------------------------------------------------------------------

def test_the_check_page_lists_each_measurement_with_what_it_means(window, session):
    record(session, 12.0)
    window.flush()
    settle(window, session)
    page = window.inspector.check
    derived = window._derived()
    assert derived is not None and derived.report
    assert page.rows.count() == len(derived.report)
    text = label_text(page)
    for row in derived.report:
        assert row.label in text and row.value in text and row.hint in text
    assert 'Measured on the edited clip' in text


def test_the_check_page_says_it_is_measuring_before_the_result_arrives(window, session):
    record(session, 12.0)
    window.flush()
    page = window.inspector.check
    assert page.rows.count() == 1 and page.rows.itemAt(0).widget().text() == 'Measuring...'


def lines(ready):
    """The checklist as text, one entry per row."""
    out = []
    for i in range(ready.rows.count()):
        row = ready.rows.itemAt(i).layout()
        out.append(' '.join(row.itemAt(j).widget().text() for j in range(row.count()) if row.itemAt(j).widget() is not None
                            and hasattr(row.itemAt(j).widget(), 'text')))
    return out


def test_the_checklist_starts_with_what_is_left_to_do(window, session):
    record(session, 12.0)
    window.flush()
    ready = window.inspector.ready
    assert ready.verdict.text().endswith('to fix') and not ready.keep.isEnabled()
    text = ' | '.join(lines(ready))
    assert 'Transcript must be' in text and 'Enter the origin' in text and 'Tick the permission box' in text
    assert text.count('Measuring the clip...') == 1


def test_a_finished_clip_is_ready_and_the_passed_checks_can_be_shown(window, session):
    record(session, 14.0)
    fill(session)
    window.flush()
    settle(window, session)
    ready = window.inspector.ready
    assert ready.verdict.text() == 'Ready' and ready.keep.isEnabled()
    assert any('checks passed' in line for line in lines(ready))
    folded = len(lines(ready))
    ready._toggle()
    assert len(lines(ready)) > folded and any('Origin entered' in line for line in lines(ready))
    ready._toggle()
    assert len(lines(ready)) == folded


def test_it_matches_confirms_the_transcript_after_a_cut(window, session):
    take = record(session, 14.0)
    fill(session)
    session.editor.set_selection(2.0, 3.0)
    session.editor.cut_selection()
    window.flush()
    settle(window, session)
    ready = window.inspector.ready
    assert take.needs_transcript_review and not ready.keep.isEnabled()
    link = next(b for b in ready.findChildren(QPushButton) if b.text() == 'It matches')
    link.click()
    window.flush()
    assert not take.needs_transcript_review and ready.keep.isEnabled()


def test_advice_that_does_not_block_is_listed_after_the_checks(window, session):
    record(session, 14.0, samples=speech(14.0, amp=0.03))
    fill(session)
    window.flush()
    settle(window, session)
    ready = window.inspector.ready
    assert ready.verdict.text() == 'Ready'
    assert any('target is' in line for line in lines(ready))


def test_the_keep_discard_and_re_record_buttons_do_what_they_say(window, session, dialogs, open_fake, recycle):
    take = record(session, 14.0)
    fill(session)
    window.flush()
    settle(window, session)
    ready = window.inspector.ready
    assert ready.keep.text().startswith('Keep clip')
    ready.keep.click()
    assert take.kept
    window.flush()
    assert ready.keep.text().startswith('Update kept clip')
    ready.discard.click()
    assert dialogs.confirmations[-1].title == 'Discard this take?' and recycle.sent
    record(session, 12.0)
    window.flush()
    ready.rerecord.click()
    assert session.capturing.recording


def test_the_open_folder_button_shows_the_speakers_folder(window, session, dialogs):
    record(session, 12.0)
    window.flush()
    window.inspector.ready.open_folder.click()
    assert dialogs.opened and dialogs.opened[-1].name == 'smoke-speaker'


def test_while_recording_the_inspector_says_where_the_take_comes_from(window, session, open_fake):
    card = window.inspector.record_card
    session.capturing.start()
    window.flush()
    assert card.values['device'].text() == 'Test microphone'
    assert 'kHz' in card.values['capture'].text() and 'half' not in card.values['pre_roll'].text()
    assert card.values['pre_roll'].text() == '0.5 s kept before Record'
    assert card.values['tail'].text() == '0.5 s after Stop'
    assert card.values['saved'].text().endswith('\\masters\\ at Stop')


# ---- the top bar ------------------------------------------------------------------------------------------------------------

def test_the_top_bar_lists_the_devices_and_explains_when_there_are_none(window, session, monkeypatch):
    from recorder import capture as cap
    assert window.topbar.device.count() == 1 and window.topbar.device.itemText(0) == 'Test microphone'
    monkeypatch.setattr(cap, 'list_microphones', lambda: [])
    monkeypatch.setattr(cap, 'default_microphone', lambda: None)
    session.capturing.refresh_devices()
    window.flush()
    assert window.topbar.device.count() == 0 and window.topbar.device.placeholderText() == 'No input device found'


def test_the_clock_turns_red_while_recording_and_counts_what_has_been_captured(window, session, open_fake):
    session.capturing.start()
    window.flush()
    window._tick()
    assert 'ff6a5f' in window.topbar.clock.styleSheet().lower()
    assert window.topbar.clock.text() == '00:01.000' and 'max' in window.topbar.sub.text()


def test_the_transport_buttons_follow_playback(window, session):
    record(session, 12.0)
    window.flush()
    topbar = window.topbar
    assert not topbar.stop.isEnabled() and window.actions.play.text() == 'Play'
    topbar.play.click()
    window.flush()
    assert topbar.stop.isEnabled() and window.actions.play.text() == 'Pause'
    topbar.loop.click()
    window.flush()
    assert session.transport.looping and topbar.loop.isChecked()
    topbar.stop.click()
    window.flush()
    assert not topbar.stop.isEnabled() and window.actions.play.text() == 'Play'


def test_listening_to_the_original_or_the_edited_clip(window, session, player):
    record(session, 12.0)
    window.flush()
    panel = window.editor_panel
    assert panel.listen.current == 1 and session.transport.listen == 'edited'
    panel.listen.buttons[0].click()
    assert session.transport.listen == 'original'
    window.actions.ab.trigger()
    window.flush()
    assert session.transport.listen == 'edited' and panel.listen.current == 1


# ---- the edit toolbar -------------------------------------------------------------------------------------------------------

def test_the_toolbar_never_loses_a_command_at_any_width(window, session):
    record(session, 12.0)
    window.flush()
    bar = window.editor_panel.toolbar
    previous = None
    for width in (1400, 900, 700, 560, 480, 420, 380):
        bar._fit(width)
        for entry in bar._entries:
            if entry.kind in ('action', 'widget') and entry.action is not None:
                assert entry.widget.isVisibleTo(bar) or entry.action in bar.hidden_actions, (width, entry.action.text())
        hidden = len(bar.hidden_actions)
        assert previous is None or hidden >= previous
        previous = hidden
    assert bar.more.isVisibleTo(bar) and bar.menu.actions()
    bar._fit(1400)
    assert bar.hidden_actions == [] and not bar.more.isVisibleTo(bar)


def test_the_least_important_commands_move_to_the_menu_first(window):
    bar = window.editor_panel.toolbar
    bar._fit(1400)
    wide = bar.mode
    bar._fit(bar._total('icon', []) - 1)
    assert 'icon' in (wide, bar.mode)
    names = [a.text() for a in bar.hidden_actions]
    assert names and 'Cut' not in names and 'Undo' not in names


def test_toolbar_buttons_run_the_actions_and_follow_their_enabled_state(window, session):
    record(session, 12.0)
    window.flush()
    bar = window.editor_panel.toolbar
    cut = next(e for e in bar._entries if e.action is window.actions.cut).widget
    assert not cut.isEnabled()
    session.editor.set_selection(2.0, 3.0)
    window.flush()
    assert cut.isEnabled()
    cut.click()
    assert len(session.current.history.edits) == 1


# ---- the dialogs -------------------------------------------------------------------------------------------------------------

def test_the_settings_dialog_returns_what_was_typed_and_none_for_what_was_cleared(qapp, monkeypatch):
    monkeypatch.setattr(clean, 'locate', lambda configured=None: None)
    dialog = dialog_mod.SettingsDialog({'root': 'C:/clips', 'tail_seconds': 0.3, 'ffmpeg': 'C:/tools/ffmpeg.exe'})
    assert dialog.values() == {'root': 'C:/clips', 'tail_seconds': 0.3, 'ffmpeg': 'C:/tools/ffmpeg.exe', 'asr_python': None,
                               'asr_model': None, 'asr_language': None, 'global_hotkey': True}
    dialog.ffmpeg.edit.setText('')
    dialog.root.edit.setText('D:/voice')
    dialog.tail.setValue(1.5)
    values = dialog.values()
    assert values['ffmpeg'] is None and values['root'] == 'D:/voice' and values['tail_seconds'] == 1.0


def test_the_settings_dialog_offers_the_record_key_in_other_programs_and_starts_from_the_saved_choice(qapp, monkeypatch):
    monkeypatch.setattr(clean, 'locate', lambda configured=None: None)
    dialog = dialog_mod.SettingsDialog({})
    assert dialog.hotkey.isChecked() and dialog.values()['global_hotkey'] is True            # on unless the settings say not
    dialog = dialog_mod.SettingsDialog({'global_hotkey': False})
    assert not dialog.hotkey.isChecked() and dialog.values()['global_hotkey'] is False
    dialog.hotkey.setChecked(True)
    assert dialog.values()['global_hotkey'] is True


def test_the_settings_dialog_takes_a_language_code_and_nothing_longer(qapp, monkeypatch):
    monkeypatch.setattr(clean, 'locate', lambda configured=None: None)
    assert dialog_mod.SettingsDialog({'asr_language': 'fr'}).language.text() == 'fr'
    dialog = dialog_mod.SettingsDialog({})
    assert dialog.language.placeholderText() == 'Detect'
    QTest.keyClicks(dialog.language, 'E1S2xyz')            # digits and a fourth letter are not accepted
    assert dialog.language.text() == 'ESx' and dialog.values()['asr_language'] == 'esx'
    dialog.language.clear()
    assert dialog.values()['asr_language'] is None


def test_the_settings_dialog_says_whether_each_path_is_any_good(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(clean, 'locate', lambda configured=None: None)
    dialog = dialog_mod.SettingsDialog({'root': str(tmp_path)})
    assert 'Not found' in dialog.ffmpeg.status.text()
    dialog.ffmpeg.edit.setText('C:/missing/ffmpeg.exe')
    assert 'does not exist' in dialog.ffmpeg.status.text()
    monkeypatch.setattr(clean, 'locate', lambda configured=None: 'C:/real/ffmpeg.exe')
    dialog.ffmpeg.edit.setText('C:/real/ffmpeg.exe')
    assert dialog.ffmpeg.status.text().startswith('Found')
    assert 'Not set up' in dialog.python.status.text()
    dialog.python.edit.setText('C:/Python/python.exe')
    assert 'Both' in dialog.python.status.text()
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    dialog.model.edit.setText('C:/models/small')
    assert dialog.python.status.text().startswith('Ready')
    assert 'OneDrive' in dialog.root.status.text()


def test_the_about_dialog_names_the_licence_and_the_responsible_use_note(qapp):
    text = label_text(dialog_mod.AboutDialog())
    assert 'MIT' in text and 'LGPL' in text and 'permission' in text and 'nothing leaves this computer' in text


def test_the_shortcuts_dialog_lists_every_bound_key(qapp):
    from recorder.ui.actions import SPEC
    text = label_text(dialog_mod.ShortcutsDialog())
    for name, (title, _, keys, _, _) in SPEC.items():
        if keys and name not in ('redo', 'zoom_in', 'zoom_out', 'fit'):
            assert title.rstrip('.') in text, name


def wait_for_modal(app, act, timeout_ms=3000):
    """Run `act(modal)` as soon as a modal dialog is up, so a call that blocks in `exec()` can return."""
    waited = {'ms': 0}
    timer = QTimer()
    timer.setInterval(20)

    def poll():
        modal = app.activeModalWidget()
        waited['ms'] += 20
        if modal is not None:
            timer.stop()
            act(modal)
        elif waited['ms'] > timeout_ms:
            timer.stop()
            raise AssertionError('no dialog appeared')

    timer.timeout.connect(poll)
    timer.start()
    return timer


def press(text):
    def act(box):
        next(b for b in box.buttons() if b.text() == text).click()
    return act


def test_confirm_returns_true_only_for_the_accepting_button(qapp):
    answers = dialog_mod.Dialogs(None)
    timer = wait_for_modal(qapp, press('Do it'))
    assert answers.confirm('Sure?', 'Really?', yes='Do it', no='Back out') is True
    timer = wait_for_modal(qapp, press('Back out'))
    assert answers.confirm('Sure?', 'Really?', yes='Do it', no='Back out') is False
    assert not timer.isActive()


def test_a_dangerous_confirmation_defaults_to_the_safe_button(qapp):
    seen = {}

    def look(box):
        seen['default'] = box.defaultButton().text()
        box.buttons()[-1].click()

    wait_for_modal(qapp, look)
    dialog_mod.Dialogs(None).confirm('Delete?', 'For good.', yes='Delete', no='Keep it', danger=True)
    assert seen['default'] == 'Keep it'


def test_a_notice_waits_for_ok(qapp):
    wait_for_modal(qapp, lambda box: box.buttons()[0].click())
    dialog_mod.Dialogs(None).notice('warning', 'Careful', 'Mind the gap.')


def test_opening_a_path_hands_it_to_the_system(qapp, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    dialog_mod.Dialogs(None).open_path(tmp_path)
    assert opened == [str(tmp_path).replace('\\', '/')] or opened == [str(tmp_path)]


def test_the_stub_and_the_real_dialogs_answer_the_same_questions(qapp):
    real = {name for name in dir(dialog_mod.Dialogs) if not name.startswith('_')}
    stub = {name for name in dir(ui_helpers.StubDialogs) if not name.startswith('_')}
    assert real <= stub, f'the test double is missing {sorted(real - stub)}'
