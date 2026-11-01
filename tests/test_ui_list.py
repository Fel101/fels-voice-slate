"""The list of takes: reached and driven by the keyboard, several rows at once, and thousands of takes."""
import time

import numpy as np
import pytest

pytest.importorskip('PySide6')
import soundfile as sf  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from helpers import RATE, fill, record, speech  # noqa: E402
from ui_helpers import click_row, pump  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def three(window, session):
    first, second, third = record(session, 10.0), record(session, 11.0), record(session, 12.0)
    window.flush()
    return first, second, third


def press(window, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(window.library.list, key, modifiers)


def test_the_list_can_be_reached_with_the_tab_key(window, session):
    three(window, session)
    assert window.library.list.focusPolicy() & Qt.FocusPolicy.TabFocus


def test_the_arrow_keys_open_the_takes(window, session):
    first, second, third = three(window, session)
    window.library.list.setFocus()
    press(window, Qt.Key.Key_Down)
    assert session.current is second
    press(window, Qt.Key.Key_Down)
    assert session.current is first
    press(window, Qt.Key.Key_Up)
    assert session.current is second


def test_a_screen_reader_can_read_each_row(window, session):
    record(session, 12.0)
    session.workspace.set_meta(label='Welcome back')
    window.flush()
    model = window.library.model
    text = model.index(0).data(Qt.ItemDataRole.AccessibleTextRole)
    assert 'Welcome back' in text and 'In review' in text and '12.0 s' in text
    assert window.library.list.accessibleName() == 'Takes'


def test_ctrl_click_selects_several_without_changing_the_open_take(window, session):
    first, second, third = three(window, session)
    click_row(window, 1, modifiers=CTRL)
    assert window.library.selected_takes() == [third, second]
    assert session.current is third


def test_shift_click_selects_a_run_of_takes(window, session):
    first, second, third = three(window, session)
    click_row(window, 2, modifiers=SHIFT)
    assert window.library.selected_takes() == [third, second, first]


def test_a_plain_click_goes_back_to_one_take(window, session):
    first, second, third = three(window, session)
    click_row(window, 1, modifiers=CTRL)
    click_row(window, 2)
    assert window.library.selected_takes() == [first] and session.current is first


def test_several_selected_rows_survive_typing_in_the_open_take(window, session):
    first, second, third = three(window, session)
    click_row(window, 1, modifiers=CTRL)
    session.workspace.set_meta(transcript='Words typed while two takes are selected.')
    window.flush()
    assert window.library.selected_takes() == [third, second]


def test_the_list_follows_the_take_that_is_opened(window, session):
    first, second, third = three(window, session)
    session.workspace.open_take(first)
    window.flush()
    assert window.library.selected_takes() == [first]


def test_a_new_take_is_selected_and_opened(window, session):
    three(window, session)
    fourth = record(session, 9.0)
    window.flush()
    assert window.library.selected_takes() == [fourth] and window.library.rows()[0].take is fourth


def test_the_recording_in_progress_is_not_a_selected_take(window, session, open_fake):
    first, second, third = three(window, session)
    session.capturing.start()
    window.flush()
    window._tick()
    window.library.list.selectAll()
    assert window.library.selected_takes() == [third, second, first]


def test_a_right_click_on_a_selected_row_offers_the_selection(window, session):
    first, second, third = three(window, session)
    click_row(window, 1, modifiers=CTRL)
    asked = []
    window.library.selectionContext.connect(lambda takes, where: asked.append(takes))
    from ui_helpers import row_point
    window.library._context_menu(row_point(window, 0))
    assert asked == [[third, second]]


def test_a_right_click_elsewhere_offers_that_take(window, session):
    first, second, third = three(window, session)
    click_row(window, 1, modifiers=CTRL)
    asked = []
    window.library.takeContext.connect(lambda take, where: asked.append(take))
    from ui_helpers import row_point
    window.library._context_menu(row_point(window, 2))
    assert asked == [first]


def test_several_kept_clips_can_be_dragged_out_together(window, session):
    keeps = []
    for _ in range(2):
        keeps.append(record(session, 12.0))
        fill(session)
        session.keep()
    plain = record(session, 12.0)
    window.flush()
    drag = window.library.make_drag([keeps[1], plain, keeps[0]])
    assert [url.toLocalFile() for url in drag.mimeData().urls()] == [str(keeps[1].export_path).replace('\\', '/'),
                                                                    str(keeps[0].export_path).replace('\\', '/')]


def test_an_outline_is_worked_out_only_for_the_rows_on_screen(window, session, qapp):
    takes = [record(session, 2.0) for _ in range(40)]
    window.resize(1440, 900)
    window.flush()
    session.tick()
    pump(qapp)
    window.library.list.viewport().repaint()
    drawn = [take for take in takes if take.cache.get('thumb_requested')]
    assert 0 < len(drawn) < 25
    assert takes[-1] in drawn and takes[0] not in drawn          # the newest are at the top


def test_an_outline_that_arrives_is_drawn(window, session, qapp):
    take = record(session, 12.0)
    window.flush()
    assert session.measure.thumbnail(take) is None
    session.tick()
    pump(qapp)
    assert session.measure.thumbnail(take) is not None


def test_a_take_whose_file_has_gone_shows_with_no_outline(window, session, qapp):
    first, second, third = three(window, session)
    first.let_go()
    first.master_path.unlink()
    first.cache.clear()
    window.library.list.viewport().repaint()
    session.tick()
    pump(qapp)
    assert first.cache.get('thumb') is None


def test_a_big_library_opens_quickly_and_scrolls(window, session, qapp):
    folder = session.lib.folder(session.slug) / 'masters'
    folder.mkdir(parents=True, exist_ok=True)
    x = speech(0.2)
    for number in range(1500):
        sf.write(str(folder / f'20261020-1612{number % 60:02d}_mic_{number:08x}.raw.wav'), x, RATE, subtype='PCM_24')
    session.lib.load_takes(session.slug)                         # the virus scanner has its look at the new files
    started = time.perf_counter()
    session.workspace.select_speaker(session.slug)
    window.flush()
    pump(qapp)
    took = time.perf_counter() - started
    assert window.library.model.rowCount() == 1500 and took < 3.0
    held = [take for take in session.workspace.takes if take.__dict__.get('_samples') is not None]
    assert len(held) < 30
    started = time.perf_counter()
    window.library.list.scrollToBottom()
    window.library.list.viewport().repaint()
    assert time.perf_counter() - started < 1.0


def test_typing_in_a_long_list_stays_quick(window, session, qapp):
    takes = [record(session, 0.2) for _ in range(300)]
    window.flush()
    started = time.perf_counter()
    for number in range(20):
        session.workspace.set_meta(transcript=f'Typing {number}')
        window.flush()
    assert (time.perf_counter() - started) / 20 < 0.1
    assert takes[0] in session.workspace.takes


def test_a_filter_keeps_working_on_the_rows(window, session):
    first, second, third = three(window, session)
    session.workspace.open_take(second)
    session.workspace.set_meta(transcript='The harbour lights.')
    window.flush()
    window.library.filter.setText('harbour')
    window.flush()
    assert [row.take for row in window.library.rows()] == [second]
    assert np.isclose(second.seconds, 11.0)
