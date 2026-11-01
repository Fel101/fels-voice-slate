"""Naming takes, finding them with the filter box, and renaming a speaker, driven through the window."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from helpers import record  # noqa: E402
from recorder import store  # noqa: E402
from recorder.ui import theme  # noqa: E402
from recorder.ui.actions import SPEC  # noqa: E402


def cards(window):
    return [card for card in window.library._cards if not card.isHidden()]


def three_takes(window, session):
    """Three takes with different words; the list shows them newest first: third, second, first."""
    first = record(session, 12.0)
    session.workspace.set_meta(transcript='Welcome back to the show.')
    second = record(session, 10.0)
    session.workspace.set_meta(transcript='The harbour lights came on at dusk.', label='Evening')
    third = record(session, 9.0)
    session.workspace.set_meta(transcript='Welcome home, said the captain.', notes='retake')
    window.flush()
    return first, second, third


# ---- labels --------------------------------------------------------------------------------------------------------------

def test_a_label_replaces_the_take_number_on_its_card(window, session):
    record(session, 12.0)
    window.flush()
    assert cards(window)[0].title == 'Take 1  ·  12.0 s' and not cards(window)[0].named
    session.workspace.set_meta(label='  Welcome   back ')
    window.flush()
    card = cards(window)[0]
    assert card.title == 'Welcome back' and card.named
    assert '12.0 s' in card.sub and 'Mic' not in card.sub
    assert 'Welcome back' in card.toolTip() and 'Take 1' in card.toolTip() and 'Mic' in card.toolTip()


def test_typing_in_the_label_field_names_the_take_on_its_card(window, session):
    take = record(session, 12.0)
    window.flush()
    field = window.inspector.clip.name_edit
    QTest.keyClicks(field, 'Welcome back')
    window.flush()
    assert take.meta['label'] == 'Welcome back' and cards(window)[0].title == 'Welcome back'
    assert field.maxLength() == store.LABEL_MAX


def test_the_label_field_shows_the_label_of_the_take_that_is_open(window, session):
    first = record(session, 12.0)
    session.workspace.set_meta(label='First one')
    record(session, 10.0)
    window.flush()
    assert window.inspector.clip.name_edit.text() == ''
    session.workspace.open_take(first)
    window.flush()
    assert window.inspector.clip.name_edit.text() == 'First one'


def test_a_long_label_is_cut_with_an_ellipsis_to_fit_the_card(window, session):
    record(session, 12.0)
    session.workspace.set_meta(label='x' * 80)
    window.flush()
    card = cards(window)[0]
    room = card.width() - 134
    font = theme.ui_font(13, QFont.Weight.DemiBold)
    shown = card._fit(card.title, font, room)
    assert shown.endswith('…') and QFontMetrics(font).horizontalAdvance(shown) <= room + 4
    assert not card.grab().isNull()                                   # and the card still paints


# ---- the filter box ------------------------------------------------------------------------------------------------------

def test_the_filter_shows_only_the_takes_that_have_all_the_words(window, session):
    first, second, third = three_takes(window, session)
    box = window.library.filter
    box.setText('welcome')
    assert [c.take for c in cards(window)] == [third, first] and window.library.count.text() == '2 of 3'
    box.setText('WELCOME  captain')                            # capitals and spacing do not matter; every word must be there
    assert [c.take for c in cards(window)] == [third]
    box.setText('evening')                                      # the label counts
    assert [c.take for c in cards(window)] == [second]
    box.setText('retake')                                       # and so do the notes
    assert [c.take for c in cards(window)] == [third]
    box.setText('')
    assert [c.take for c in cards(window)] == [third, second, first] and window.library.count.text() == '3'


def test_the_filter_keeps_the_take_numbers_and_says_when_nothing_matches(window, session):
    three_takes(window, session)
    box = window.library.filter
    box.setText('captain')
    [card] = cards(window)
    assert card.title.startswith('Take 3')                      # counted in the whole list, not the shown part
    box.setText('zebra')
    assert cards(window) == [] and window.library.empty.isVisibleTo(window.library)
    assert 'zebra' in window.library.empty.text()
    box.setText('')
    assert len(cards(window)) == 3 and not window.library.empty.isVisibleTo(window.library)


def test_the_filter_follows_what_is_typed_into_the_open_take(window, session):
    first, second, third = three_takes(window, session)
    window.library.filter.setText('lighthouse')
    assert cards(window) == []
    session.workspace.open_take(second)
    session.workspace.set_meta(transcript='The lighthouse came on at dusk.')
    window.flush()
    assert [c.take for c in cards(window)] == [second]


def test_the_filter_box_appears_with_the_first_take(window, session):
    assert not window.library.filter.isVisibleTo(window.library)
    record(session, 12.0)
    window.flush()
    assert window.library.filter.isVisibleTo(window.library)


# ---- renaming ------------------------------------------------------------------------------------------------------------

def test_rename_in_the_take_menu_opens_the_take_and_puts_the_cursor_in_the_label(window, session):
    first = record(session, 12.0)
    second = record(session, 10.0)
    window.flush()
    assert session.current is second
    next(a for a in window.build_take_menu(first).actions() if a.text() == 'Rename').trigger()
    window.flush()
    assert session.current is first
    assert window.inspector.pages.currentIndex() == window.inspector.CLIP_TAB
    assert window.focusWidget() is window.inspector.clip.name_edit


def test_f2_renames_the_take_in_front_and_is_off_without_one(window, session):
    assert SPEC['rename'][2] == ['F2'] and not window.actions.rename.isEnabled()
    record(session, 12.0)
    window.flush()
    window.inspector.show_tab(window.inspector.CHECK_TAB)
    assert window.actions.rename.isEnabled()
    window.actions.rename.trigger()
    assert window.inspector.pages.currentIndex() == window.inspector.CLIP_TAB
    assert window.focusWidget() is window.inspector.clip.name_edit


def test_rename_speaker_asks_with_the_current_name_and_changes_only_the_name(window, session, dialogs, monkeypatch):
    record(session, 12.0)
    window.flush()
    asked = []
    monkeypatch.setattr(dialogs, 'ask_text', lambda title, prompt, text='': asked.append((title, text)) or 'Sam Rivera')
    window.rename_speaker()
    window.flush()
    assert asked == [('Rename speaker', 'Smoke Speaker')]
    assert session.workspace.name_of('smoke-speaker') == 'Sam Rivera' and session.workspace.speaker == 'smoke-speaker'
    assert window.library.speakers.currentText() == 'Sam Rivera'
    assert session.lib.folder('smoke-speaker').is_dir() and len(cards(window)) == 1       # their takes are still there


def test_cancelling_the_rename_or_keeping_the_name_changes_nothing(window, session, dialogs):
    dialogs.text_answer = None
    window.rename_speaker()
    dialogs.text_answer = 'Smoke Speaker'
    window.rename_speaker()
    assert session.workspace.name_of('smoke-speaker') == 'Smoke Speaker' and dialogs.notices == []


def test_a_speaker_name_that_is_taken_is_refused_with_a_message(window, session, dialogs):
    session.workspace.add_speaker('Sam Rivera')
    session.workspace.select_speaker('smoke-speaker')
    window.flush()
    dialogs.text_answer = 'sam rivera'
    window.rename_speaker()
    assert dialogs.notices[-1].title == 'Rename speaker' and 'already a speaker' in dialogs.notices[-1].text
    assert session.workspace.name_of('smoke-speaker') == 'Smoke Speaker'


def test_right_clicking_the_speaker_list_offers_rename_and_new(window, session, dialogs):
    window.library.speakers.customContextMenuRequested.emit(QPoint(5, 5))
    [menu] = dialogs.menus
    assert [a.text() for a in menu.actions()] == ['Rename speaker...', 'New speaker...']
    dialogs.text_answer = 'Sam Rivera'
    menu.actions()[0].trigger()
    window.flush()
    assert window.library.speakers.currentText() == 'Sam Rivera'


def test_the_gear_menu_lists_rename_speaker_next_to_new_speaker(window):
    names = [a.text() for a in window.topbar.menu.actions() if a.text()]
    assert names.index('Rename speaker...') == names.index('New speaker...') + 1


def test_the_rename_commands_are_off_while_recording(window, session, open_fake):
    record(session, 12.0)
    window.flush()
    assert window.actions.rename.isEnabled() and window.actions.rename_speaker.isEnabled()
    session.capturing.start()
    window.flush()
    assert not window.actions.rename.isEnabled() and not window.actions.rename_speaker.isEnabled()
