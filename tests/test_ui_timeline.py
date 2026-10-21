"""The waveform, spectrogram, overview and number boxes: what the pointer and keys do, and what is drawn."""
import numpy as np
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import ui_helpers  # noqa: E402
from helpers import record, speech  # noqa: E402
from recorder.ui import timeline  # noqa: E402
from recorder.ui.timeline import ViewRange  # noqa: E402


@pytest.fixture
def take(window, session, qapp):
    t = record(session, 12.0)
    window.flush()
    ui_helpers.pump(qapp)
    return t


@pytest.fixture
def wave(window, take):
    return window.editor_panel.waveform


def mid(wave):
    return wave.height() / 2


# ---- the visible range --------------------------------------------------------------------------------------------------

def test_a_new_range_shows_the_whole_clip():
    view = ViewRange()
    view.reset(12.0)
    assert (view.t0, view.t1, view.zoom) == (0.0, 12.0, 1.0)


def test_the_range_never_leaves_the_clip_or_gets_narrower_than_a_few_samples():
    view = ViewRange()
    view.reset(12.0)
    view.set(-5.0, 3.0)
    assert (view.t0, view.t1) == (0.0, 8.0)
    view.set(10.0, 40.0)
    assert view.t1 == 12.0 and view.t0 >= 0.0
    view.set(5.0, 5.0)
    assert view.span == pytest.approx(ViewRange.MIN_SPAN)


def test_zooming_keeps_the_anchor_where_it_is_on_screen():
    view = ViewRange()
    view.reset(12.0)
    view.zoom_by(2.0, 3.0)
    assert view.span == pytest.approx(6.0)
    assert (3.0 - view.t0) / view.span == pytest.approx(0.25)
    view.zoom_by(0.5, 3.0)
    assert (view.t0, view.t1) == pytest.approx((0.0, 12.0))


def test_zoom_to_a_range_leaves_a_margin_and_ensure_visible_scrolls_without_zooming():
    view = ViewRange()
    view.reset(12.0)
    view.zoom_to(4.0, 6.0)
    assert view.t0 < 4.0 and view.t1 > 6.0 and view.span < 3.0
    span = view.span
    view.ensure_visible(10.0)
    assert view.t0 <= 10.0 <= view.t1 and view.span == pytest.approx(span)
    before = (view.t0, view.t1)
    view.ensure_visible(view.t0 + 0.1)
    assert (view.t0, view.t1) == before


def test_changes_are_announced_only_when_the_range_changes(qapp):
    view = ViewRange()
    view.reset(12.0)
    heard = []
    view.changed.connect(lambda: heard.append(1))
    view.set(0.0, 12.0)
    assert heard == []
    view.pan_by(0.0)
    assert heard == []
    view.zoom_by(2.0, 6.0)
    assert heard == [1]


def test_axis_labels_are_round_numbers():
    assert timeline.nice_step(10.0, 9) == 2
    assert timeline.nice_step(0.8, 9) == pytest.approx(0.1)
    assert timeline.clock_label(65.0, 1) == '1:05'
    assert timeline.clock_label(2.5, 0.5) == '0:02.5'


# ---- the pointer on the waveform -----------------------------------------------------------------------------------------

def test_dragging_across_the_waveform_sets_the_in_and_out_points(wave, session):
    ui_helpers.drag(wave, (wave.x_of(2.0), mid(wave)), (wave.x_of(5.0), mid(wave)))
    assert session.editor.sel == pytest.approx((2.0, 5.0), abs=0.05)


def test_dragging_backwards_selects_the_same_range(wave, session):
    ui_helpers.drag(wave, (wave.x_of(5.0), mid(wave)), (wave.x_of(2.0), mid(wave)))
    assert session.editor.sel == pytest.approx((2.0, 5.0), abs=0.05)


def test_dragging_outside_the_clip_is_held_to_its_ends(wave, session):
    ui_helpers.drag(wave, (wave.x_of(10.0), mid(wave)), (wave.width() - 1, mid(wave)))
    assert session.editor.sel[1] == pytest.approx(12.0, abs=0.01)
    ui_helpers.drag(wave, (wave.x_of(1.0), mid(wave)), (1, mid(wave)))
    assert session.editor.sel[0] == pytest.approx(0.0, abs=0.01)


def test_a_click_clears_the_selection_and_moves_the_playhead(wave, session):
    session.editor.set_selection(2.0, 5.0)
    ui_helpers.click(wave, wave.x_of(8.0), mid(wave))
    assert session.editor.sel is None and session.editor.playhead == pytest.approx(8.0, abs=0.05)


def test_dragging_an_edge_moves_only_that_edge(wave, session):
    session.editor.set_selection(2.0, 5.0)
    ui_helpers.drag(wave, (wave.x_of(5.0), mid(wave)), (wave.x_of(7.0), mid(wave)))
    assert session.editor.sel == pytest.approx((2.0, 7.0), abs=0.05)
    ui_helpers.drag(wave, (wave.x_of(2.0), mid(wave)), (wave.x_of(1.0), mid(wave)))
    assert session.editor.sel == pytest.approx((1.0, 7.0), abs=0.05)


def test_dragging_an_edge_past_the_other_edge_swaps_them(wave, session):
    session.editor.set_selection(4.0, 5.0)
    ui_helpers.drag(wave, (wave.x_of(5.0), mid(wave)), (wave.x_of(2.0), mid(wave)))
    assert session.editor.sel == pytest.approx((2.0, 4.0), abs=0.05)


def test_the_ruler_scrubs_the_playhead(wave, session):
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, ui_helpers.point(wave.x_of(4.0), 10))
    assert session.editor.playhead == pytest.approx(4.0, abs=0.05)
    QTest.mouseMove(wave, ui_helpers.point(wave.x_of(8.0), 10))
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       ui_helpers.point(wave.x_of(8.0), 10))
    assert session.editor.playhead == pytest.approx(8.0, abs=0.05) and session.editor.sel is None


def test_dragging_past_the_edge_scrolls_and_extends_the_selection(wave, session):
    view = wave.view
    view.set(2.0, 6.0)
    QTest.mousePress(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     ui_helpers.point(wave.x_of(3.0), mid(wave)))
    QTest.mouseMove(wave, ui_helpers.point(wave.width() - 2, mid(wave)))
    QTest.mouseMove(wave, ui_helpers.point(wave.width() + 40, mid(wave)))
    assert wave._scroller.isActive()
    before = view.t0
    for _ in range(10):
        wave._autoscroll()
    QTest.mouseRelease(wave, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       ui_helpers.point(wave.width() + 40, mid(wave)))
    assert view.t0 > before and session.editor.sel[1] == pytest.approx(view.t1, abs=0.01)
    assert not wave._scroller.isActive()


def test_nothing_is_selectable_without_a_take(window):
    wave = window.editor_panel.waveform
    ui_helpers.drag(wave, (200, 100), (400, 100))
    assert window.session.editor.sel is None


def test_the_cursor_shows_what_a_drag_would_do(wave, session):
    session.editor.set_selection(2.0, 5.0)
    ui_helpers.hover(wave, wave.x_of(5.0), mid(wave))
    assert wave.cursor().shape() == Qt.CursorShape.SizeHorCursor
    ui_helpers.hover(wave, wave.x_of(8.0), mid(wave))
    assert wave.cursor().shape() == Qt.CursorShape.CrossCursor
    ui_helpers.hover(wave, wave.x_of(8.0), 10)
    assert wave.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_a_right_click_opens_the_menu_and_moves_the_playhead_unless_it_is_inside_the_selection(window, dialogs, wave, session):
    asked = []
    wave.contextRequested.connect(lambda pos, at: asked.append(at))
    session.editor.set_selection(2.0, 3.0)
    session.editor.set_playhead(1.0)
    ui_helpers.click(wave, wave.x_of(2.5), mid(wave), Qt.MouseButton.RightButton)
    assert session.editor.playhead == pytest.approx(1.0) and len(dialogs.menus) == 1
    ui_helpers.click(wave, wave.x_of(7.0), mid(wave), Qt.MouseButton.RightButton)
    assert session.editor.playhead == pytest.approx(7.0, abs=0.05) and len(dialogs.menus) == 2
    assert asked == [pytest.approx(2.5, abs=0.05), pytest.approx(7.0, abs=0.05)]


# ---- zooming and scrolling -----------------------------------------------------------------------------------------------

def test_the_wheel_zooms_around_the_pointer_and_shift_wheel_scrolls(wave):
    view, x = wave.view, wave.x_of(6.0)
    ui_helpers.wheel(wave, x, mid(wave), 120)
    assert view.span < 12.0 and wave.t_of(x) == pytest.approx(6.0, abs=0.02)
    span, start = view.span, view.t0
    ui_helpers.wheel(wave, x, mid(wave), -120, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert view.span == pytest.approx(span) and view.t0 > start
    ui_helpers.wheel(wave, x, mid(wave), -120)
    assert view.span > span


def test_the_spectrogram_and_overview_share_the_view(window, wave):
    window.editor_panel.view.set(2.0, 6.0)
    assert (wave.view.t0, wave.view.t1) == (2.0, 6.0)
    ui_helpers.wheel(window.editor_panel.spectrogram, window.editor_panel.spectrogram.x_of(4.0), 50, 120)
    assert wave.view.span < 4.0


def test_the_zoom_buttons_and_fit_change_the_shared_view(window, session, take, wave):
    window.actions.zoom_in.trigger()
    assert wave.view.zoom == pytest.approx(1.5)
    window.actions.zoom_out.trigger()
    assert wave.view.zoom == pytest.approx(1.0)
    session.editor.set_selection(4.0, 5.0)
    window.flush()
    window.actions.zoom_selection.trigger()
    assert wave.view.span < 2.0
    window.actions.fit.trigger()
    assert (wave.view.t0, wave.view.t1) == (0.0, 12.0)


def test_arrow_keys_move_the_playhead_by_ten_milliseconds_a_tenth_and_a_second(wave, session):
    wave.setFocus()
    session.editor.set_playhead(5.0)
    ui_helpers.key(wave, Qt.Key.Key_Right)
    assert session.editor.playhead == pytest.approx(5.01)
    ui_helpers.key(wave, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert session.editor.playhead == pytest.approx(5.11)
    ui_helpers.key(wave, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    assert session.editor.playhead == pytest.approx(4.11)
    ui_helpers.key(wave, Qt.Key.Key_End)
    assert session.editor.playhead == pytest.approx(12.0)
    ui_helpers.key(wave, Qt.Key.Key_Home)
    assert session.editor.playhead == 0.0


def test_the_view_follows_the_playhead_while_playing(window, session, player, wave):
    window.editor_panel.view.set(0.0, 3.0)
    session.transport.play()
    player.pos = 7.0
    window._tick()
    assert window.editor_panel.view.t0 <= 7.0 <= window.editor_panel.view.t1


# ---- drawing --------------------------------------------------------------------------------------------------------------

def test_the_waveform_is_drawn_once_and_dragging_redraws_only_the_overlay(window, session, wave):
    wave.grab()
    builds = wave.layer_builds
    assert builds >= 1
    for t in (2.0, 3.0, 4.0, 5.0):
        session.editor.set_selection(2.0, t)
        session.editor.set_playhead(t)
        window.flush()
        wave.grab()
    assert wave.layer_builds == builds
    window.editor_panel.view.zoom_by(2.0, 6.0)
    wave.grab()
    assert wave.layer_builds == builds + 1


def test_the_waveform_is_drawn_again_for_a_new_size_or_take(window, session, wave, qapp):
    wave.grab()
    builds = wave.layer_builds
    window.resize(1300, 860)
    ui_helpers.pump(qapp)
    wave.grab()
    assert wave.layer_builds == builds + 1
    record(session, 8.0)
    window.flush()
    wave.grab()
    assert wave.layer_builds == builds + 2


def pixels(widget):
    image = widget.grab().toImage()
    return image, image.width(), image.height()


def test_clipped_samples_are_marked_in_red(window, session, qapp):
    clipped = np.clip(speech(12.0, amp=2.0), -1.0, 1.0)
    record(session, samples=clipped)
    window.flush()
    wave = window.editor_panel.waveform
    image, width, _ = pixels(wave)
    reds = sum(1 for x in range(timeline.GUTTER, width - timeline.RIGHT) for y in range(timeline.RULER + 2, timeline.RULER + 11)
               if (c := image.pixelColor(x, y)).red() > 220 and c.green() < 130 and c.blue() < 120)
    assert reds > 20
    quiet_take = record(session, 12.0)
    window.flush()
    image, width, _ = pixels(window.editor_panel.waveform)
    assert quiet_take is session.current
    assert not any((c := image.pixelColor(x, timeline.RULER + 6)).red() > 220 and c.green() < 130 and c.blue() < 120
                   for x in range(timeline.GUTTER, width - timeline.RIGHT))


def test_every_kind_of_edit_can_be_drawn_on_all_three_views(window, session, take, qapp):
    editor = session.editor
    editor.set_selection(1.0, 2.0)
    editor.cut_selection()
    editor.set_selection(3.0, 4.0)
    editor.silence_selection()
    editor.set_selection(5.0, 6.0)
    editor.fade_selection('in')
    editor.set_selection(7.0, 8.0)
    editor.fade_selection('out')
    editor.set_selection(9.0, 10.5)
    editor.gain_selection(3.0)
    window.flush()
    assert len(take.history.edits) == 5
    panel = window.editor_panel
    for view in (panel.waveform, panel.spectrogram, panel.minimap):
        image, width, height = pixels(view)
        assert width > 0 and height > 0
    editor.set_selection(2.0, 4.0)
    window.flush()
    for view in (panel.waveform, panel.spectrogram, panel.minimap):
        view.grab()


def test_the_spectrogram_says_it_is_busy_until_the_analysis_arrives(window, session, take, qapp):
    spectrogram = window.editor_panel.spectrogram
    take.cache.pop('spectrogram_image', None)
    take.cache.pop('spectrogram', None)
    assert spectrogram.grab().toImage().width() > 0
    session.jobs.drain()
    window.flush()
    spec, image = spectrogram._image(take)
    assert spec is not None and image is not None and image.width() > 10


# ---- the overview ---------------------------------------------------------------------------------------------------------

def test_clicking_the_overview_outside_the_box_centres_the_view_there(window, take):
    overview, view = window.editor_panel.minimap, window.editor_panel.view
    view.set(1.0, 3.0)
    ui_helpers.click(overview, overview._x_of(9.0), overview.height() / 2)
    assert (view.t0 + view.t1) / 2 == pytest.approx(9.0, abs=0.1) and view.span == pytest.approx(2.0)


def test_dragging_the_box_scrolls_the_view_and_a_double_click_fits(window, take):
    overview, view = window.editor_panel.minimap, window.editor_panel.view
    view.set(2.0, 4.0)
    ui_helpers.drag(overview, (overview._x_of(3.0), 29), (overview._x_of(6.0), 29))
    assert view.t0 == pytest.approx(5.0, abs=0.1) and view.span == pytest.approx(2.0)
    QTest.mouseDClick(overview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      ui_helpers.point(overview._x_of(6.0), 29))
    assert (view.t0, view.t1) == (0.0, 12.0)


# ---- the number boxes -----------------------------------------------------------------------------------------------------

def test_the_number_boxes_show_the_selection_and_the_playhead(window, session, take):
    session.editor.set_selection(2.0, 5.0)
    session.editor.set_playhead(3.5)
    window.flush()
    info = window.editor_panel.info
    assert (info.in_spin.value(), info.out_spin.value(), info.length_spin.value(), info.head_spin.value()) == \
        pytest.approx((2.0, 5.0, 3.0, 3.5))


def test_typing_a_number_moves_that_end_of_the_selection(window, session, take):
    session.editor.set_selection(2.0, 5.0)
    window.flush()
    info = window.editor_panel.info
    info.out_spin.setValue(6.0)
    info.out_spin.editingFinished.emit()
    assert session.editor.sel == pytest.approx((2.0, 6.0))
    info.in_spin.setValue(1.0)
    info.in_spin.editingFinished.emit()
    assert session.editor.sel == pytest.approx((1.0, 6.0))
    info.length_spin.setValue(2.0)
    info.length_spin.editingFinished.emit()
    assert session.editor.sel == pytest.approx((1.0, 3.0))
    info.head_spin.setValue(9.0)
    info.head_spin.editingFinished.emit()
    assert session.editor.playhead == pytest.approx(9.0)


def test_the_playhead_box_shows_the_sound_being_played(window, session, player, take):
    session.transport.play()
    player.pos = 4.25
    window._tick()
    assert window.editor_panel.info.head_spin.value() == pytest.approx(4.25, abs=0.05)


def test_the_number_boxes_give_things_up_in_a_narrow_window(window, take):
    info = window.editor_panel.info
    widths = []
    for level in range(len(info.LEVELS)):                  # the room each arrangement needs, whatever the font
        info._apply(level)
        info.layout().activate()
        widths.append(info.layout().minimumSize().width())
    assert widths == sorted(widths, reverse=True) and len(set(widths)) == len(widths)
    assert [info.fit(w) for w in widths] == [0, 1, 2, 3]
    assert info.fit(10000) == 0 and info.zoom.isVisibleTo(info) and info.length_label.text() == 'Length'
    assert info.fit(widths[1]) == 1 and not info.zoom.isVisibleTo(info) and info.length_label.text() == 'Length'
    assert info.fit(widths[2]) == 2 and info.length_label.text() == 'Len' and info.length_spin.isVisibleTo(info)
    assert info.fit(widths[3] - 40) == 3
    assert not info.length_spin.isVisibleTo(info) and info.in_spin.isVisibleTo(info) and info.head_spin.isVisibleTo(info)


def test_the_number_boxes_are_disabled_without_a_take(window):
    info = window.editor_panel.info
    info.refresh()
    assert not info.in_spin.isEnabled() and not info.head_spin.isEnabled()


# ---- the live view --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('elapsed, end', [(0.0, 10.0), (3.0, 10.0), (9.5, 15.0), (14.5, 20.0), (38.5, 40.0), (60.0, 40.0)])
def test_the_live_view_widens_in_steps_up_to_the_limit(qapp, elapsed, end):
    live = timeline.LiveWaveView()
    live.set_state(None, elapsed)
    assert live.window_end() == end


def test_the_live_view_draws_what_has_been_recorded(qapp):
    live = timeline.LiveWaveView()
    live.resize(600, 300)
    rows = np.array([(0.02 * (i + 1), -0.4, 0.5) for i in range(200)], dtype=np.float32)
    live.set_state(rows, 4.0)
    image = live.grab().toImage()
    top = image.pixelColor(int(timeline.GUTTER + 4.0 / 10.0 * (600 - timeline.GUTTER - timeline.RIGHT) - 30), 150)
    assert top.blue() > 150 and top.red() > 120               # the violet waveform, not the empty background
    live.set_state(None, 0.0)
    live.grab()
