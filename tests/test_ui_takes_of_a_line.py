"""Choosing between the takes of one line: the order of the list, which takes it shows, the takes of the line in front, how
many takes each line of the script has, the reading check in each row, and one command for the takes not chosen."""
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt  # noqa: E402

from helpers import fill, record  # noqa: E402
from recorder.ui.library import arrange  # noqa: E402

LINES = ['The ferry left at dawn.', 'The harbour lights came on at dusk.', 'Rain fell on the quay.']


def fake(line=None, kept=False, changed=False):
    return SimpleNamespace(meta={'script_line': line}, kept=kept, changed=changed)


# ---- arranging the takes, without a window ------------------------------------------------------------------------------

def test_the_list_is_newest_first_unless_asked_otherwise():
    takes = [fake(), fake(), fake()]
    assert arrange(takes) == takes
    assert arrange(takes, 'oldest') == takes[::-1]


def test_in_script_order_each_line_has_its_takes_in_the_order_they_were_read():
    newer_first, older_first = fake(LINES[0]), fake(LINES[0])
    third, loose_new, loose_old, second = fake(LINES[2]), fake(), fake(), fake(LINES[1])
    takes = [newer_first, third, loose_new, older_first, second, loose_old]     # newest first, as the workspace has them
    assert arrange(takes, 'script', lines=LINES) == [older_first, newer_first, second, third, loose_new, loose_old]


def test_the_list_can_be_narrowed_to_one_kind_of_take():
    plain, kept, changed = fake(LINES[0]), fake(LINES[1], kept=True), fake(LINES[0], kept=True, changed=True)
    takes = [plain, kept, changed]
    assert arrange(takes, shown='not_kept') == [plain]
    assert arrange(takes, shown='kept') == [kept, changed]
    assert arrange(takes, shown='changed') == [changed]
    assert arrange(takes, shown='line', lines=LINES, line=LINES[0]) == [plain, changed]
    assert arrange(takes, shown='line', lines=LINES, line=None) == []


# ---- in the window ------------------------------------------------------------------------------------------------------

def listed(window):
    model = window.library.model
    return [model.row_at(row).take for row in range(model.rowCount())]


def choose(combo, key):
    combo.setCurrentIndex(combo.findData(key))
    combo.activated.emit(combo.currentIndex())


@pytest.fixture
def scripted(window, session):
    session.script.load_text('\n'.join(LINES))
    window.flush()
    return window


def read(session, line, seconds=10.0):
    return record(session, seconds, script_line=line)


def test_the_order_chosen_above_the_list_is_the_order_of_the_rows(scripted, session):
    window = scripted
    second = read(session, LINES[1])
    first = read(session, LINES[0])
    loose = record(session, 9.0)
    window.flush()
    assert listed(window) == [loose, first, second]
    choose(window.library.order, 'oldest')
    assert listed(window) == [second, first, loose]
    choose(window.library.order, 'script')
    assert listed(window) == [first, second, loose]


def test_show_its_takes_lists_only_the_takes_of_the_line_in_front(scripted, session):
    window = scripted
    one = read(session, LINES[0])
    other = read(session, LINES[1])
    two = read(session, LINES[0])
    session.script.go_to(0)
    window.flush()
    page = window.inspector.script
    assert not page.show_takes.isHidden()                       # on the Script tab, which is not in front
    page.show_takes.click()
    window.flush()
    assert listed(window) == [two, one] and window.library.shown.currentData() == 'line'
    assert window.library.count.text() == '2 of 3'
    session.script.go_to(1)                                     # the list follows the line in front
    window.flush()
    assert listed(window) == [other]
    session.script.go_to(2)
    window.flush()
    assert listed(window) == [] and window.library.empty.text() == 'No takes of this line yet.'


def test_without_a_script_there_is_no_line_to_show_and_no_script_order(window, session):
    record(session, 10.0)
    window.flush()
    library = window.library
    assert library.shown.findData('line') == -1
    assert not library.order.model().item(library.order.findData('script')).isEnabled()
    session.script.load_text('\n'.join(LINES))
    window.flush()
    assert library.shown.findData('line') != -1
    assert library.order.model().item(library.order.findData('script')).isEnabled()
    choose(library.shown, 'line')
    choose(library.order, 'script')
    session.script.clear()
    window.flush()
    assert library.shown.currentData() == 'all' and library.order.currentData() == 'newest'
    assert len(listed(window)) == 1


def test_the_choices_stay_hidden_until_there_is_a_take(window, session):
    assert not window.library.choices.isVisible()
    record(session, 10.0)
    window.flush()
    assert window.library.choices.isVisible()


def test_a_kind_of_take_with_none_says_so(window, session):
    record(session, 10.0)
    window.flush()
    choose(window.library.shown, 'kept')
    assert listed(window) == [] and window.library.empty.text() == 'No takes of this kind.'


def test_each_line_of_the_script_says_how_many_takes_it_has(scripted, session):
    window = scripted
    read(session, LINES[0])
    read(session, LINES[0])
    read(session, LINES[0])
    read(session, LINES[1])
    window.flush()
    lines = window.inspector.script.lines
    assert lines.item(0).text() == f'●  1.  3×  {LINES[0]}'
    assert lines.item(1).text() == f'●  2.  {LINES[1]}'             # one take: nothing to count
    assert lines.item(2).text() == f'○  3.  {LINES[2]}'
    assert lines.item(0).data(Qt.ItemDataRole.AccessibleTextRole) == f'Recorded. 3 takes. 1. {LINES[0]}'
    assert '3× has three takes' in window.inspector.script.summary.text()


def test_each_row_shows_the_reading_check_of_its_take(scripted, session):
    window = scripted
    misread = read(session, LINES[0])
    session.drafts.receive(misread, 'The ferry left at noon.', None)
    right = read(session, LINES[1])
    session.drafts.receive(right, LINES[1], None)
    unread = read(session, LINES[2])
    window.flush()
    model = window.library.model
    rows = {model.row_at(row).take: model.row_at(row) for row in range(model.rowCount())}
    assert rows[misread].reading == 1 and rows[right].reading == 0 and rows[unread].reading is None
    said = {take: model.index(listed(window).index(take)).data(Qt.ItemDataRole.AccessibleTextRole)
            for take in (misread, right, unread)}
    assert said[misread].endswith('Reading check: 1 place differs')
    assert said[right].endswith('Reading check: read as written')
    assert 'Reading check' not in said[unread]
    assert rows[misread].tip.endswith('Reading check: 1 place differs')


def test_the_other_takes_of_a_line_go_to_the_recycle_bin_after_one_question(scripted, session, dialogs, recycle):
    window = scripted
    kept = read(session, LINES[0])
    fill(session, transcript=LINES[0])
    session.keep()
    spare = read(session, LINES[0])
    elsewhere = read(session, LINES[1])
    another = read(session, LINES[0])
    chosen = read(session, LINES[0])
    window.flush()
    assert session.current is chosen and window.actions.discard_others.isEnabled()
    dialogs.confirm_answer = False
    window.actions.discard_others.trigger()
    assert len(dialogs.confirmations) == 1 and set(session.workspace.takes) == {kept, spare, elsewhere, another, chosen}
    asked = dialogs.confirmations[0]
    assert asked.title == 'Discard the 2 other takes of this line?' and LINES[0] in asked.text
    dialogs.confirm_answer = True
    window.actions.discard_others.trigger()
    while session.batch.running:
        session.batch.tick()
    window.flush()
    assert set(session.workspace.takes) == {kept, elsewhere, chosen}          # the kept clip and the other line stay
    assert session.current is chosen and kept.export_path.exists()
    assert not window.actions.discard_others.isEnabled()                     # nothing left to discard


def test_there_is_nothing_to_discard_for_a_take_of_no_line(scripted, session):
    window = scripted
    read(session, LINES[0])
    record(session, 10.0)
    window.flush()
    assert not window.actions.discard_others.isEnabled()


def test_one_other_take_is_asked_about_as_one(scripted, session, dialogs):
    window = scripted
    read(session, LINES[0])
    read(session, LINES[0])
    window.flush()
    dialogs.confirm_answer = False
    window.actions.discard_others.trigger()
    asked = dialogs.confirmations[0]
    assert asked.title == 'Discard the other take of this line?' and 'It moves to the Recycle Bin' in asked.text


def test_the_right_click_menu_of_a_take_offers_its_line_s_other_takes(scripted, session):
    window = scripted
    first = read(session, LINES[0])
    read(session, LINES[0])
    loose = record(session, 10.0)
    window.flush()
    offered = [action.text() for action in window.build_take_menu(first).actions()]
    assert 'Discard the other takes of this line...' in offered
    assert 'Discard the other takes of this line...' not in [a.text() for a in window.build_take_menu(loose).actions()]
