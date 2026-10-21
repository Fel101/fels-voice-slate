"""Small widgets, the stylesheet and the icons, and the whole window at its smallest size."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget  # noqa: E402

import ui_helpers  # noqa: E402
from helpers import fill, record  # noqa: E402
from recorder.ui import icons, palette, theme, widgets  # noqa: E402
from recorder.ui.actions import SPEC  # noqa: E402
from recorder.ui.editor_panel import EditorPanel  # noqa: E402


def painted(widget):
    """Whether anything at all was drawn: some pixel of the widget is not fully transparent."""
    image = widget.grab().toImage()
    return any(image.pixelColor(x, y).alpha() > 0 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2))


# ---- chips and labels ---------------------------------------------------------------------------------------------------

def test_a_chip_is_hidden_when_it_has_no_text_and_takes_the_colour_it_is_given(qapp):
    chip = widgets.chip('3 to fix', 'red')
    widgets.set_chip(chip, 'Ready', 'green')
    assert chip.text() == 'Ready' and chip.kind == 'green' and not chip.isHidden()
    widgets.set_chip(chip, '')
    assert chip.isHidden() and chip.kind == 'green'


def test_every_kind_of_chip_paints_and_a_long_one_can_wrap(qapp):
    for kind in widgets.Chip.COLOURS:
        chip = widgets.chip('A state', kind)
        chip.resize(chip.sizeHint())
        assert painted(chip), kind
    note = widgets.chip('A long message that has to wrap inside the card it is in rather than widen it.', 'amber')
    note.setWordWrap(True)
    note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    note.resize(160, note.heightForWidth(160))
    assert note.height() > note.sizeHint().height() // 2 and painted(note)


def test_an_elided_label_shortens_its_text_and_can_leave_it_out(qapp):
    label = widgets.ElidedLabel('A rather long sentence that cannot possibly fit in a small space')
    label.resize(80, 20)
    assert painted(label) and label.toolTip().startswith('A rather long')
    hint = widgets.ElidedLabel('Wheel zooms', drop=True)
    hint.resize(20, 20)
    assert not painted(hint)
    hint.resize(300, 20)
    assert painted(hint)


def test_an_elided_combo_shows_the_middle_of_a_long_name_as_dots(qapp):
    combo = widgets.ElidedCombo()
    combo.addItem('Microphone (A Very Long Device Name From A Vendor)  [Windows WASAPI]', 1)
    combo.resize(160, 30)
    assert painted(combo)
    empty = widgets.ElidedCombo()
    empty.resize(160, 30)
    assert painted(empty)


# ---- controls ---------------------------------------------------------------------------------------------------------------

def test_a_segmented_control_announces_only_what_the_person_picks(qapp):
    seg = widgets.Segmented(['One', 'Two', 'Three'], 0)
    heard = []
    seg.currentChanged.connect(heard.append)
    seg.buttons[2].click()
    seg.buttons[2].click()
    assert heard == [2] and seg.current == 2
    seg.set_current(0)
    assert heard == [2] and [b.property('seg') for b in seg.buttons] == ['on', 'off', 'off']
    seg.set_tooltips('a', 'b', 'c')
    assert seg.buttons[1].toolTip() == 'b'


def test_a_fixed_segmented_control_is_never_squeezed(qapp):
    seg = widgets.Segmented(['Microphone', 'System audio'], 0, fixed=True)
    assert seg.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    assert widgets.Segmented(['a', 'b']).sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Fixed


def test_a_switch_toggles_with_a_click_or_the_space_bar_and_paints_in_every_state(qapp):
    switch = widgets.Switch()
    heard = []
    switch.toggled.connect(heard.append)
    switch.show()
    switch.click()
    assert switch.isChecked() and heard == [True] and painted(switch)
    switch.setFocus()
    QTest.keyClick(switch, Qt.Key.Key_Space)
    assert not switch.isChecked() and heard == [True, False]
    switch.setEnabled(False)
    assert painted(switch)
    switch.hide()


def test_the_meter_holds_the_peak_then_lets_it_fall(qapp):
    meter = widgets.Meter(300)
    meter.set_levels(-12.0, -20.0, now=0.0)
    assert (meter.peak, meter.rms, meter.hold) == (-12.0, -20.0, -12.0)
    meter.set_levels(-30.0, -35.0, now=0.5)
    assert meter.peak == -30.0 and meter.hold == -12.0
    meter.set_levels(-30.0, -35.0, now=2.0)
    assert meter.hold == -30.0
    meter.set_levels(-200.0, -200.0, now=3.0)
    assert meter.peak == meter.rms == widgets.Meter.FLOOR
    meter.reset()
    assert meter.peak == meter.hold == meter.rms == widgets.Meter.FLOOR


def test_the_meter_fills_in_proportion_to_the_level_and_thins_its_labels_when_narrow(qapp):
    meter = widgets.Meter(400)
    meter.resize(300, 40)
    meter.set_levels(-30.0, -30.0, now=0.0)
    image = meter.grab().toImage()
    inside, beyond = image.pixelColor(60, 10), image.pixelColor(240, 10)
    assert inside.green() > inside.red() and abs(beyond.red() - beyond.green()) < 20
    meter.resize(120, 40)
    assert painted(meter)


def test_the_take_length_bar_paints_inside_and_outside_the_range(qapp):
    bar = widgets.TargetBar()
    bar.resize(400, 36)
    for seconds in (0.0, 3.0, 12.5, 39.0, 55.0):
        bar.set_seconds(seconds)
        assert painted(bar)


def test_the_clock_is_minutes_seconds_and_milliseconds():
    assert widgets.format_clock(0) == '00:00.000'
    assert widgets.format_clock(65.5) == '01:05.500'
    assert widgets.format_clock(-3) == '00:00.000'


def test_clearing_a_layout_removes_its_widgets_at_once(qapp):
    parent = QWidget()
    layout = QVBoxLayout(parent)
    inner = QHBoxLayout()
    first, second = QLabel('a'), QLabel('b')
    inner.addWidget(second)
    layout.addWidget(first)
    layout.addLayout(inner)
    parent.show()
    widgets.clear_layout(layout)
    assert layout.count() == 0 and first.parent() is None and second.parent() is None
    assert not first.isVisible() and not second.isVisible()
    parent.hide()


# ---- the stylesheet, palette and icons ---------------------------------------------------------------------------------------

def test_the_stylesheet_has_no_unresolved_placeholders_and_qt_accepts_it(qapp):
    sheet = theme.stylesheet()
    assert '@' not in sheet
    warnings = []
    previous = qInstallMessageHandler(lambda mode, context, message: warnings.append(message))
    try:
        qapp.setStyleSheet(sheet)
        QApplication.processEvents()
    finally:
        qInstallMessageHandler(previous)
    assert not [w for w in warnings if 'stylesheet' in w.lower() or 'parse' in w.lower()], warnings


def test_the_fonts_are_pixel_sized_so_the_window_scales_as_a_whole(qapp):
    assert theme.ui_font(14).pixelSize() == 14 and theme.mono_font(12).pixelSize() == 12
    assert theme.ui_font(11, weight=theme.QFont.Weight.DemiBold, spacing=0.9).weight() == theme.QFont.Weight.DemiBold


def test_palette_names_become_colours(qapp):
    assert palette.color('accent', 100).alpha() == 100 and palette.color('#336699').name() == '#336699'
    assert palette.hex_of('not a name') == 'not a name'
    for name, value in palette.PALETTE.items():
        assert palette.color(name).isValid(), name


def test_every_icon_draws_in_both_states(qapp):
    for name in icons.SHAPES:
        icon = icons.icon(name)
        assert isinstance(icon, QIcon) and not icon.isNull(), name
        normal = icon.pixmap(18, 18, QIcon.Mode.Normal).toImage()
        disabled = icon.pixmap(18, 18, QIcon.Mode.Disabled).toImage()
        assert any(normal.pixelColor(x, y).alpha() > 0 for x in range(18) for y in range(18)), name
        assert normal != disabled, name


def test_every_command_that_names_an_icon_has_one(qapp):
    for name, (text, icon, keys, tip, checkable) in SPEC.items():
        assert icon is None or icon in icons.SHAPES, f'{name}: {icon}'
        assert text and tip, name


def test_the_program_icon_has_every_size_the_taskbar_asks_for(qapp):
    sizes = {s.width() for s in icons.app_icon().availableSizes()}
    assert {16, 32, 48, 256} <= sizes


# ---- the whole window at its smallest ----------------------------------------------------------------------------------------

def inside(parent, child):
    box = child.geometry()
    return parent.rect().contains(box)


@pytest.mark.parametrize('state', ['empty', 'edit', 'live'])
def test_nothing_is_clipped_at_the_smallest_window(window, session, open_fake, qapp, state):
    if state == 'edit':
        record(session, 12.0)
        fill(session)
    elif state == 'live':
        session.capturing.start()
        window._tick()
    window.flush()
    window.resize(1100, 700)
    ui_helpers.pump(qapp)
    bar = window.topbar
    for part in (bar.record, bar.play, bar.stop, bar.loop, bar.source, bar.device, bar.arm, bar.meter, bar.gear):
        assert part.isVisibleTo(bar) and inside(bar, part), part
    assert bar.source.width() >= bar.source.sizeHint().width()
    assert bar.clock.width() >= bar.clock.sizeHint().width() - 4
    assert bar.device.width() >= bar.device.minimumWidth()
    assert window.library.width() == 292 and window.inspector.width() == 372
    centre = window.editor_panel
    assert centre.width() >= 400
    if state == 'edit':
        info = centre.info
        assert info.layout().minimumSize().width() <= info.width() + 1
        bar_ = centre.toolbar
        for entry in bar_._entries:
            if entry.action is not None:
                assert entry.widget.isVisibleTo(bar_) or entry.action in bar_.hidden_actions
        assert centre.waveform.height() >= 150 and centre.spectrogram.height() >= 80
        ready = window.inspector.ready
        assert ready.isVisibleTo(window.inspector) and ready.keep.isVisibleTo(ready)
    assert window.statusBar().isVisible()


def test_the_window_has_the_documented_minimum_size(window):
    assert (window.minimumWidth(), window.minimumHeight()) == (1100, 700)
    assert window.editor_panel.stack.count() == 3 and window.editor_panel.EMPTY == 0 and EditorPanel.LIVE == 2
