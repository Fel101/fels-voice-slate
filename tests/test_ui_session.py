"""Long sessions, driven through the window: purposes, the Script tab, the reading check, the words of a take, take keys,
splitting, and many takes at once."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel  # noqa: E402

from helpers import bursts, fill, record  # noqa: E402
from recorder import audio_core as ac  # noqa: E402
from ui_helpers import click_row  # noqa: E402

SCRIPT = 'The ferry left at dawn.\nThe gulls followed it out.\nBy noon the sky was clear.\n'
CTRL = Qt.KeyboardModifier.ControlModifier


def text_of(widget):
    return ' | '.join(label.text() for label in widget.findChildren(QLabel))


def run_batch(window, session):
    for _ in range(1000):
        if not session.batch.running:
            break
        session.tick()
    window.flush()


# ---- purposes ---------------------------------------------------------------------------------------------------------------

def test_the_panel_offers_every_purpose_for_new_takes(window, session):
    picker = window.library.purpose
    assert [picker.itemData(i) for i in range(picker.count())] == list(ac.PURPOSES)
    assert picker.currentData() == 'reference'


def test_picking_a_purpose_sets_what_new_takes_are_for_and_the_status_bar_says_what_keep_writes(window, session):
    window.library.purposePicked.emit('audition')
    window.flush()
    assert session.workspace.purpose.key == 'audition' and window.library.purpose.currentData() == 'audition'
    assert window.status_export.text() == 'Audition: 48 kHz · 24-bit · mono'


def test_the_status_bar_follows_the_open_take(window, session):
    session.workspace.set_purpose('sentence')
    record(session, 12.0)
    window.flush()
    assert window.status_export.text() == 'Sentence: 24 kHz · 16-bit · mono'
    session.workspace.set_meta(purpose='audition')
    window.flush()
    assert window.status_export.text() == 'Audition: 48 kHz · 24-bit · mono'


def test_the_clip_tab_changes_the_purpose_of_one_take(window, session):
    take = record(session, 12.0)
    window.flush()
    clip = window.inspector.clip
    assert clip.purpose.currentData() == 'reference'
    clip.purpose.setCurrentIndex(clip.purpose.findData('sentence'))
    clip.purpose.activated.emit(clip.purpose.currentIndex())
    assert take.meta['purpose'] == 'sentence' and session.workspace.purpose.key == 'reference'


def test_the_picker_is_off_while_recording(window, session, open_fake):
    session.capturing.start()
    window.flush()
    assert not window.library.purpose.isEnabled()


def test_the_length_bar_and_the_live_view_use_the_rules_of_the_purpose(window, session, open_fake):
    session.workspace.set_purpose('sentence')
    session.capturing.start()
    window.flush()
    window._tick()
    assert window.editor_panel.live.target.rules.key == 'sentence'
    assert window.editor_panel.live.wave.limit == 60.0
    assert '1 to 15 s can be kept' in window.editor_panel.live.length.text()


# ---- the script -------------------------------------------------------------------------------------------------------------

def test_the_script_tab_lists_each_line_and_how_far_it_has_got(window, session):
    session.script.load_text(SCRIPT)
    record(session, 12.0, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    window.flush()
    page = window.inspector.script
    rows = [page.lines.item(i).text() for i in range(page.lines.count())]
    assert rows[0].startswith('✓') and rows[1].startswith('○') and 'gulls' in rows[1]
    assert page.summary.text().startswith('1 of 3 lines have a kept clip')
    assert page.lines.currentRow() == 1


def test_a_line_in_progress_has_its_own_mark(window, session):
    session.script.load_text(SCRIPT)
    record(session, 12.0, script_line=session.script.current)
    window.flush()
    assert window.inspector.script.lines.item(0).text().startswith('●')


def test_clicking_a_line_goes_to_it(window, session):
    session.script.load_text(SCRIPT)
    window.flush()
    page = window.inspector.script
    page.lines.itemClicked.emit(page.lines.item(2))
    assert session.script.index == 2


def test_next_line_to_record_skips_what_is_done(window, session):
    session.script.load_text(SCRIPT)
    record(session, 12.0, script_line=session.script.current)
    fill(session, transcript=session.script.current)
    session.keep()
    session.script.go_to(0)
    window.flush()
    window.actions.next_open.setEnabled(True)
    window.actions.next_open.trigger()
    assert session.script.index == 1


def test_with_no_script_the_tab_says_what_one_is_and_offers_to_load_it(window, session, dialogs):
    window.flush()
    page = window.inspector.script
    assert 'one prompt on each line' in page.summary.text() and page.lines.isHidden()
    asked = []
    page.loadRequested.connect(lambda: asked.append(True))
    page.load.click()
    assert asked == [True]


def test_the_script_line_says_how_many_are_kept(window, session):
    session.script.load_text(SCRIPT)
    window.flush()
    assert window.editor_panel.script.caption.text() == 'SCRIPT  ·  LINE 1 OF 3  ·  0 KEPT'
    assert session.script.index == 0


def test_the_tabs_are_there_while_recording_too(window, session, open_fake):
    window.flush()
    assert [b.text() for b in window.inspector.tabs.buttons] == ['Clip', 'Process', 'Check', 'Script']


# ---- the reading check --------------------------------------------------------------------------------------------------------

def test_a_misread_line_is_shown_with_the_places_that_differ(window, session):
    take = record(session, 12.0, script_line='The ferry left at dawn.')
    session.drafts.receive(take, 'The ferry left at noon.', None)
    window.flush()
    clip = window.inspector.clip
    assert clip.reading.isVisibleTo(clip) and clip.reading_title.text() == 'Reading check: 1 place differs'
    assert 'heard: noon' in clip.reading_text.text() and clip.use_heard.isVisibleTo(clip)
    assert clip.state.text() == 'From script: check it'


def test_a_line_read_as_written_says_so_in_green(window, session):
    take = record(session, 12.0, script_line='The ferry left at dawn.')
    session.drafts.receive(take, 'the ferry left at dawn', None)
    window.flush()
    clip = window.inspector.clip
    assert clip.reading_title.text() == 'Reading check: read as written' and not clip.reading_text.isVisibleTo(clip)
    assert clip.state.text() == 'Read as written'


def test_what_is_read_is_escaped(window, session):
    take = record(session, 12.0, script_line='a <b> c')
    session.drafts.receive(take, 'a d c', None)
    window.flush()
    assert '<b>' not in window.inspector.clip.reading_text.text()


def test_use_what_was_heard_replaces_the_transcript(window, session):
    take = record(session, 12.0, script_line='The ferry left at dawn.')
    session.drafts.receive(take, 'The ferry left at noon.', None)
    window.flush()
    window.inspector.clip.use_heard.click()
    window.flush()
    assert take.meta['transcript'] == 'The ferry left at noon.' and not window.inspector.clip.use_heard.isVisibleTo(window)


def test_a_take_with_no_script_has_no_reading_card(window, session):
    record(session, 12.0)
    window.flush()
    assert not window.inspector.clip.reading.isVisibleTo(window.inspector.clip)


# ---- the words --------------------------------------------------------------------------------------------------------------

WORDS = [('One', 1.0, 1.4), ('two', 2.0, 2.4), ('three', 3.0, 3.4), ('<four>', 4.0, 4.4)]


@pytest.fixture
def worded(window, session):
    take = record(session, 12.0)
    session.drafts.receive(take, 'One two three four', None, words=WORDS)
    window.flush()
    return take


def click_word(window, index):
    window.editor_panel.words.anchorClicked.emit(QUrl(str(index)))


def test_the_words_appear_under_the_waveform(window, session, worded):
    strip = window.editor_panel.words
    assert strip.isVisibleTo(window) and 'three' in strip.toPlainText()


def test_no_words_no_strip(window, session):
    record(session, 12.0)
    window.flush()
    assert not window.editor_panel.words.isVisibleTo(window)


def test_a_word_cannot_break_the_page(window, session, worded):
    assert '<four>' in window.editor_panel.words.toPlainText()


def test_clicking_a_word_selects_it(window, session, worded):
    click_word(window, 1)
    window.flush()
    assert session.editor.sel == pytest.approx((2.0, 2.4), abs=0.09) and window.editor_panel.words._chosen() == {1}


def test_delete_cuts_the_selected_word_and_it_stands_struck_through(window, session, worded):
    click_word(window, 2)
    window.flush()
    window.actions.cut.trigger()
    window.flush()
    assert len(worded.history.edits) == 1
    assert '<s ' in window.editor_panel.words.toHtml() or 'line-through' in window.editor_panel.words.toHtml()


def test_clicking_a_struck_word_brings_it_back(window, session, worded):
    click_word(window, 2)
    window.flush()
    window.actions.cut.trigger()
    window.flush()
    click_word(window, 2)
    window.flush()
    assert worded.history.edits == []


def test_the_words_can_be_reached_by_keyboard_and_named(window, session, worded):
    strip = window.editor_panel.words
    assert strip.accessibleName() == 'The words of this take' and strip.focusPolicy() == Qt.FocusPolicy.TabFocus


# ---- moving between takes -------------------------------------------------------------------------------------------------------

def test_the_take_keys_walk_the_list(window, session):
    first, second, _ = record(session, 10.0), record(session, 11.0), record(session, 12.0)
    window.flush()
    window.actions.next_take.trigger()
    assert session.current is second
    window.actions.next_take.trigger()
    window.actions.next_take.trigger()
    assert session.current is first
    window.actions.prev_take.trigger()
    assert session.current is second


def test_the_take_keys_follow_the_filter(window, session):
    first, _, third = record(session, 10.0), record(session, 11.0), record(session, 12.0)
    for take in (first, third):
        session.workspace.open_take(take)
        session.workspace.set_meta(transcript='The harbour lights.')
    window.flush()
    window.library.filter.setText('harbour')
    window.flush()
    assert session.current is third
    window.actions.next_take.trigger()
    assert session.current is first


def test_the_take_keys_have_keys(window):
    assert window.actions.next_take.shortcut().toString() == 'Alt+Down'
    assert window.actions.prev_take.shortcut().toString() == 'Alt+Up'


# ---- splitting ---------------------------------------------------------------------------------------------------------------

def test_split_says_how_many_takes_it_will_make_and_makes_them(window, session, dialogs):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=bursts(18.0, [(0.3, 5.0), (5.6, 11.0), (11.6, 17.7)], amp=0.14, f0=137.0))
    window.flush()
    window.actions.split_take.trigger()
    window.flush()
    assert 'It will become 3 new takes' in dialogs.confirmations[-1].text
    assert len(session.workspace.takes) == 4 and take in session.workspace.takes


def test_saying_no_makes_nothing(window, session, dialogs):
    session.workspace.set_purpose('sentence')
    record(session, samples=bursts(18.0, [(0.3, 5.0), (5.6, 11.0), (11.6, 17.7)], amp=0.14, f0=137.0))
    window.flush()
    dialogs.confirm_answer = False
    window.actions.split_take.trigger()
    assert len(session.workspace.takes) == 1


def test_a_take_with_nowhere_to_split_says_so(window, session, dialogs):
    record(session, samples=bursts(12.0, [(0.0, 12.0)], amp=0.14, f0=137.0))
    window.flush()
    window.actions.split_take.trigger()
    assert dialogs.notices[-1].title == 'Split at pauses' and 'nowhere to split' in dialogs.notices[-1].text


# ---- many takes -------------------------------------------------------------------------------------------------------------

def many(window, session, count=3, **meta):
    takes = [record(session, 12.0) for _ in range(count)]
    for take in takes:
        take.meta.update(transcript='Words spoken in the take.', **meta)
    window.flush()
    return takes


def select_all(window):
    window.library.list.setFocus()
    window.library.list.selectAll()
    window.flush()


def test_ctrl_a_in_the_list_selects_the_takes_and_elsewhere_the_clip(window, session, qapp, monkeypatch):
    takes = many(window, session)
    monkeypatch.setattr(window.library.list, 'hasFocus', lambda: True)
    window.actions.select_all.trigger()
    assert len(window.library.selected_takes()) == 3 and session.editor.sel is None
    monkeypatch.setattr(window.library.list, 'hasFocus', lambda: False)
    window.actions.select_all.trigger()
    assert session.editor.sel == (0.0, session.current.seconds) and session.current in takes


def test_the_commands_for_several_takes_wait_for_several_takes(window, session):
    many(window, session)
    window.flush()
    assert not window.actions.keep_selected.isEnabled()
    select_all(window)
    assert window.actions.keep_selected.isEnabled() and window.actions.discard_selected.isEnabled()


def test_keeping_several_asks_for_the_statement_and_nothing_happens_if_it_is_not_made(window, session, dialogs):
    takes = many(window, session, origin='here', allowed_use='my projects')
    select_all(window)
    window.actions.keep_selected.trigger()
    kind, asked = dialogs.asked[-1]
    assert kind == 'keep_many' and len(asked.takes) == 3
    assert not session.batch.running and not any(take.kept for take in takes)


def test_keeping_several_runs_once_the_statement_is_made(window, session, dialogs):
    takes = many(window, session, origin='here', allowed_use='my projects')
    select_all(window)
    dialogs.keep_many_answer = ('', '')
    window.actions.keep_selected.trigger()
    assert session.batch.running
    window.flush()
    assert window.status_stop.isVisibleTo(window)
    run_batch(window, session)
    assert all(take.kept for take in takes) and not window.status_stop.isVisibleTo(window)


def test_takes_that_are_kept_already_are_not_asked_about(window, session, dialogs):
    take = record(session, 12.0)
    fill(session)
    session.keep()
    window.flush()
    window.keep_selected([take])
    assert dialogs.notices[-1].title == 'Keep several takes' and dialogs.asked == []


def test_stop_ends_the_job(window, session, dialogs):
    many(window, session, origin='here', allowed_use='my projects')
    select_all(window)
    dialogs.keep_many_answer = ('', '')
    window.actions.keep_selected.trigger()
    window.flush()
    window.status_stop.click()
    window.flush()
    assert not session.batch.running and session.status_text.startswith('Stopped.')


def test_the_commands_wait_while_a_job_runs(window, session, dialogs):
    many(window, session, origin='here', allowed_use='my projects')
    select_all(window)
    dialogs.keep_many_answer = ('', '')
    window.actions.keep_selected.trigger()
    window.flush()
    assert not window.actions.record.isEnabled() and not window.actions.keep.isEnabled()
    run_batch(window, session)
    assert window.actions.record.isEnabled()


def test_discarding_several_asks_once(window, session, dialogs):
    many(window, session)
    select_all(window)
    window.actions.discard_selected.trigger()
    assert dialogs.confirmations[-1].title == 'Discard 3 takes?'
    run_batch(window, session)
    assert session.workspace.takes == []


def test_delete_in_the_list_discards_what_is_selected(window, session, dialogs):
    many(window, session)
    select_all(window)
    window.library.list.deleteRequested.emit()
    assert dialogs.confirmations[-1].title == 'Discard 3 takes?'


def test_moving_several_asks_which_speaker(window, session, dialogs):
    takes = many(window, session)
    first = session.workspace.speaker
    second = session.workspace.add_speaker('Other')
    session.workspace.select_speaker(first)
    window.flush()
    select_all(window)
    dialogs.item_answer = 0
    window.actions.move_selected.trigger()
    run_batch(window, session)
    assert all(take.speaker == second for take in takes)


def test_the_right_click_menu_of_several_takes(window, session, dialogs):
    takes = many(window, session)
    select_all(window)
    menu = window.build_selection_menu(takes)
    assert [a.text() for a in menu.actions() if a.text()] == ['Keep 3 takes...', 'Discard 3 takes...']


def test_opening_another_take_waits_for_a_job(window, session, dialogs):
    takes = many(window, session, origin='here', allowed_use='my projects')
    session.batch.keep(takes)
    window._take_picked(takes[0])
    assert 'Wait for this to finish' in session.status_text


def test_a_click_on_one_row_after_a_selection_opens_it(window, session):
    first = many(window, session)[0]
    select_all(window)
    click_row(window, 2)
    assert session.current is first


# ---- the statement itself ---------------------------------------------------------------------------------------------------

def statement_dialog(qapp, session, takes, defaults=None):
    from recorder.ui.dialogs import KeepManyDialog
    return KeepManyDialog('Someone', takes, defaults or {'origin': '', 'allowed_use': ''})


def test_the_box_starts_empty_and_keep_waits_for_it(window, qapp, session):
    takes = many(window, session, origin='here', allowed_use='my projects')
    dialog = statement_dialog(qapp, session, takes)
    assert not dialog.consent.isChecked() and not dialog.keep.isEnabled()
    dialog.consent.setChecked(True)
    assert dialog.keep.isEnabled()
    assert dialog.values() == ('', '')


def test_the_dialog_shows_what_the_takes_say(window, qapp, session):
    takes = many(window, session, 2, origin='recorded here', allowed_use='my projects')
    other = many(window, session, 1, origin='a <studio>', allowed_use='a game')
    dialog = statement_dialog(qapp, session, takes + other)
    words = text_of(dialog)
    assert 'recorded here' in words and 'a game' in words and '&lt;studio&gt;' in words
    assert 'on each of these 3 takes' in words


def test_a_take_that_says_nothing_needs_words_typed(window, qapp, session):
    takes = many(window, session, 2)
    dialog = statement_dialog(qapp, session, takes)
    dialog.consent.setChecked(True)
    assert not dialog.keep.isEnabled()
    dialog.origin.setText('Recorded here')
    assert not dialog.keep.isEnabled()
    dialog.allowed.setText('My projects')
    assert dialog.keep.isEnabled() and dialog.values() == ('Recorded here', 'My projects')


def test_the_words_remembered_for_the_speaker_are_offered(window, qapp, session):
    takes = many(window, session, 2)
    dialog = statement_dialog(qapp, session, takes, {'origin': 'at my desk', 'allowed_use': 'narration'})
    assert dialog.origin.text() == 'at my desk' and dialog.allowed.text() == 'narration'


def test_the_box_is_a_real_checkbox_with_a_name(window, qapp, session):
    takes = many(window, session, 2, origin='here', allowed_use='my projects')
    dialog = statement_dialog(qapp, session, takes)
    assert isinstance(dialog.consent, QCheckBox) and 'right to use this voice' in dialog.consent.accessibleName()
    assert isinstance(dialog, QDialog)
