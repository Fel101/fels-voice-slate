"""Whether everyone can use the window: names for a screen reader, the keyboard, contrast in every theme, and small screens."""
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QCheckBox, QComboBox,  # noqa: E402
                               QKeySequenceEdit, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QScrollBar,
                               QToolButton)

import ui_helpers  # noqa: E402
from helpers import fill, record  # noqa: E402
from recorder import settings  # noqa: E402
from recorder.ui import dialogs as dialog_mod  # noqa: E402
from recorder.ui import main_window, palette, theme  # noqa: E402
from recorder.ui.main_window import MIN_SIZE  # noqa: E402


def luminance(hex_colour):
    c = QColor(hex_colour)

    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# ---- contrast -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', list(palette.THEMES))
@pytest.mark.parametrize('foreground', ['text', 'muted', 'dim', 'accent', 'red', 'green', 'amber'])
@pytest.mark.parametrize('background', ['bg0', 'bg1', 'bg2', 'bg3'])
def test_text_is_easy_to_read_in_every_theme(name, foreground, background):
    """4.5 to 1 is the WCAG AA level for ordinary text."""
    colours = palette.THEMES[name]
    assert contrast(colours[foreground], colours[background]) >= 4.5


@pytest.mark.parametrize('name', list(palette.THEMES))
def test_main_text_is_very_easy_to_read(name):
    colours = palette.THEMES[name]
    assert all(contrast(colours['text'], colours[background]) >= 7 for background in ('bg0', 'bg1', 'bg2', 'bg3'))


@pytest.mark.parametrize('name', list(palette.THEMES))
def test_the_words_on_the_coloured_buttons_can_be_read(name):
    colours = palette.THEMES[name]
    assert contrast(colours['ink'], colours['accent']) >= 4.5 and contrast(colours['red_ink'], colours['red']) >= 4.5


def test_every_theme_has_every_colour():
    keys = {frozenset(colours) for colours in palette.THEMES.values()}
    assert len(keys) == 1


@pytest.mark.parametrize('name', list(palette.THEMES))
def test_no_colour_is_left_out_of_the_stylesheet(qapp, name):
    palette.use(name)
    try:
        assert '@' not in theme.stylesheet()
    finally:
        palette.use('dark')


def test_no_panel_keeps_a_colour_of_its_own():
    """A colour written into a panel is a colour that stays dark in the light theme."""
    import re
    from pathlib import Path

    from recorder import ui
    folder = Path(ui.__file__).parent
    for path in folder.glob('*.py'):
        if path.name in ('palette.py', 'icons.py'):
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            found = [text for text in re.findall(r'#[0-9a-fA-F]{6}\b', line) if text.lower() != '#ffffff']
            assert not found, f'{path.name}:{number}: {line.strip()}'


def test_the_programs_icon_is_the_same_in_every_theme(qapp):
    from recorder.ui import icons
    dark = icons.app_pixmap(32).toImage()
    palette.use('light')
    try:
        assert icons.app_pixmap(32).toImage() == dark
    finally:
        palette.use('dark')


@pytest.mark.parametrize('choice, app_mode, expected', [('dark', None, 'dark'), ('light', None, 'light'),
                                                        ('contrast', None, 'contrast'), ('nonsense', None, 'dark'),
                                                        ('system', 'Light', 'light'), ('system', 'Dark', 'dark'),
                                                        ('system', 'Unknown', 'dark')])
def test_the_theme_for_each_choice(choice, app_mode, expected):
    from types import SimpleNamespace
    app = None if app_mode is None else SimpleNamespace(
        styleHints=lambda: SimpleNamespace(colorScheme=lambda: getattr(Qt.ColorScheme, app_mode)))
    assert theme.theme_name(choice, app) == expected


@pytest.mark.parametrize('name', ['light', 'contrast'])
def test_the_whole_window_draws_in_the_other_themes(qapp, session, dialogs, name):
    from recorder.ui.main_window import MainWindow
    palette.use(name)
    try:
        record(session, 12.0)
        window = MainWindow(session=session, dialogs=dialogs)
        window.timer.stop()
        window.resize(1440, 900)
        window.show()
        ui_helpers.pump(qapp)
        assert not window.grab().isNull()
        window.close()
        ui_helpers.pump(qapp)
    finally:
        palette.use('dark')


# ---- the settings -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('saved, expected', [({}, 'dark'), ({'theme': 'light'}, 'light'), ({'theme': 'system'}, 'system'),
                                             ({'theme': 'pink'}, 'dark'), ({'theme': 7}, 'dark')])
def test_the_theme_setting(saved, expected):
    assert settings.theme(saved) == expected


@pytest.mark.parametrize('saved, expected', [({}, None), ({'text_scale': 1.5}, 1.5), ({'text_scale': 9}, None),
                                             ({'text_scale': 'big'}, None)])
def test_the_size_setting(saved, expected):
    assert settings.text_scale(saved) == expected


def test_settings_offers_the_look(qapp):
    dialog = dialog_mod.SettingsDialog({'theme': 'light', 'text_scale': 1.25})
    assert dialog.values()['theme'] == 'light' and dialog.values()['text_scale'] == 1.25
    assert dialog.theme.count() == 4 and dialog.text_scale.count() == 6


def test_the_choices_are_saved_and_the_default_theme_is_not(window, session, dialogs):
    answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None, 'theme': 'light',
              'text_scale': 1.5}
    dialogs.settings_answer = answer
    window.open_settings()
    saved = settings.load(session.settings_file)
    assert saved['theme'] == 'light' and saved['text_scale'] == 1.5
    dialogs.settings_answer = dict(answer, theme='dark', text_scale=None)
    window.open_settings()
    assert 'theme' not in settings.load(session.settings_file) and 'text_scale' not in settings.load(session.settings_file)


def test_windows_own_text_size_is_read_when_it_can_be(monkeypatch):
    import winreg

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(winreg, 'OpenKey', lambda *a: Key())
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda key, name: (150, 4))
    assert theme.windows_text_scale() == 1.5
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda key, name: (400, 4))
    assert theme.windows_text_scale() == 2.0


def test_no_text_size_setting_is_ordinary_size(monkeypatch):
    import winreg

    def missing(*args):
        raise OSError('there is no such key')

    monkeypatch.setattr(winreg, 'OpenKey', missing)
    assert theme.windows_text_scale() == 1.0


# ---- a screen reader --------------------------------------------------------------------------------------------------------------

def unnamed(root):
    """The controls under `root` that a screen reader would have nothing to say about."""
    missing = []
    for widget in root.findChildren(QAbstractButton) + root.findChildren(QComboBox) + root.findChildren(QLineEdit) \
            + root.findChildren(QPlainTextEdit) + root.findChildren(QAbstractSlider) + root.findChildren(QAbstractSpinBox):
        if isinstance(widget, QScrollBar) or isinstance(widget.parent(), (QComboBox, QAbstractSpinBox, QKeySequenceEdit,
                                                                          QLineEdit)):
            continue                                    # part of a control that has its own name
        words = widget.accessibleName()
        if isinstance(widget, QAbstractButton):
            words = words or widget.text()
        if isinstance(widget, QToolButton) and widget.defaultAction() is not None:
            words = words or widget.defaultAction().text()
        if not words.strip():
            missing.append(f'{type(widget).__name__} {widget.objectName() or widget.toolTip()[:40]}')
    return missing


def test_every_control_in_the_window_has_a_name(window, session):
    record(session, 12.0)
    window.flush()
    assert unnamed(window) == []


def test_every_control_in_settings_has_a_name(qapp):
    assert unnamed(dialog_mod.SettingsDialog({})) == []


def test_every_control_in_speaker_details_has_a_name(qapp):
    assert unnamed(dialog_mod.SpeakerDialog('Someone', None, False)) == []


def test_the_status_line_can_be_read_out(window, session):
    session.bus.status('Something to say.')
    assert window.status_text.accessibleName() == 'Something to say.'


def heard(monkeypatch):
    said = []
    reader = SimpleNamespace(updateAccessibility=lambda event: said.append(event.message()))
    monkeypatch.setattr(main_window, 'QAccessible', reader)
    return said


def test_a_new_status_is_said_out_loud(window, session, monkeypatch):
    """A screen reader says nothing when a label changes by itself, and after Keep this line is all the answer there is."""
    said = heard(monkeypatch)
    session.bus.status('Something to say.')
    window._announcer.timeout.emit()
    assert said == ['Something to say.']


def test_a_burst_of_status_messages_is_said_once_as_the_last_of_them(window, session, monkeypatch):
    """A batch reports each take as it goes."""
    said = heard(monkeypatch)
    window._announcer.setInterval(0)
    for done in range(5):
        session.bus.status(f'Keeping {done} of 5...')
    assert said == []
    QTest.qWait(10)
    assert said == ['Keeping 4 of 5...']


def test_a_closed_window_reads_out_nothing_more(window, session, monkeypatch):
    """Its last status would otherwise be said once the delay ran out, over whatever came after: in the tests, another
    test's window."""
    said = heard(monkeypatch)
    session.bus.status('Something to say.')
    assert window.close()
    QTest.qWait(main_window.ANNOUNCE_AFTER_MS + 200)
    assert said == []


def test_a_mark_is_read_out_as_a_word(window, session):
    """Windows' accessibility tree showed them read out as "white circle", which says nothing about the check beside it."""
    record(session)
    window.flush()
    marks = [widget for widget in window.findChildren(QLabel) if widget.text() and not any(c.isalnum() for c in widget.text())]
    assert marks
    assert [mark.text() for mark in marks if not any(c.isalpha() for c in mark.accessibleName())] == []


def test_the_status_dot_says_when_it_is_recording(window, session, open_fake):
    assert window.status_dot.accessibleName() == 'Ready'
    session.capturing.start()
    window.flush()
    assert window.status_dot.accessibleName() == 'Recording'


def test_a_line_of_the_script_is_read_out_with_how_far_it_has_got(window, session):
    session.script.load_text('One line to read out.\nAnother line to read.')
    record(session, 12.0, script_line=session.script.current)
    window.flush()
    lines = window.inspector.script.lines
    spoken = [lines.item(i).data(Qt.ItemDataRole.AccessibleTextRole) for i in range(lines.count())]
    assert spoken == ['Recorded. 1. One line to read out.', 'Not recorded yet. 2. Another line to read.']


def test_the_record_button_says_what_it_does_now(window, session, open_fake):
    assert window.topbar.record.accessibleName() == 'Record'
    session.capturing.start()
    window.flush()
    assert window.topbar.record.accessibleName() == 'Stop'


# ---- the keyboard ------------------------------------------------------------------------------------------------------------------

def test_the_waveform_can_be_reached_with_the_tab_key(window):
    assert window.editor_panel.waveform.focusPolicy() & Qt.FocusPolicy.TabFocus


def test_every_control_in_the_window_can_be_reached_by_keyboard(window, session):
    record(session, 12.0)
    window.flush()
    for widget in window.findChildren(QAbstractButton) + window.findChildren(QComboBox):
        if widget.isEnabled() and widget.isVisibleTo(window) and not isinstance(widget.parent(), QComboBox):
            assert widget.focusPolicy() & Qt.FocusPolicy.TabFocus, f'{type(widget).__name__} {widget.text()}'


# ---- small screens ------------------------------------------------------------------------------------------------------------------

def test_the_window_fits_a_1080p_screen_at_150_percent():
    """1280 by 720 in the window's own pixels, less about 32 for the title bar and 48 for the taskbar."""
    assert MIN_SIZE.width() <= 1280 and MIN_SIZE.height() <= 720 - 32 - 48


def test_the_window_really_goes_down_to_that_size(window, session, qapp):
    record(session, 12.0)
    window.flush()
    window.resize(MIN_SIZE)
    ui_helpers.pump(qapp)
    assert window.height() == MIN_SIZE.height() and window.width() == MIN_SIZE.width()


def test_the_permission_box_is_in_view_at_the_smallest_size(window, session, qapp):
    """It used to be scrolled out of sight there."""
    record(session, 12.0)
    window.flush()
    window.resize(MIN_SIZE)
    ui_helpers.pump(qapp, 6)
    box = window.inspector.ready.consent
    top_left = box.mapTo(window, box.rect().topLeft())
    assert box.isVisible() and window.rect().contains(top_left) and window.rect().contains(top_left + box.rect().bottomRight())
    assert not any(isinstance(parent, QScrollArea) for parent in _parents(box))


def _parents(widget):
    while widget is not None:
        widget = widget.parentWidget()
        if widget is not None:
            yield widget


def test_the_top_bar_fits_the_smallest_width_with_the_input_picker_showing(window, session, qapp, monkeypatch):
    """Seen on a real two-input microphone: the row ran out of room and clipped Microphone and System audio."""
    from recorder import capture
    monkeypatch.setattr(capture, 'input_channels', lambda device: 2)
    session.capturing.refresh_devices()
    window.flush()
    window.resize(MIN_SIZE)
    ui_helpers.pump(qapp, 6)
    bar = window.topbar
    assert not bar.channel.isHidden() and bar.layout().minimumSize().width() <= MIN_SIZE.width()
    assert all(b.width() >= b.sizeHint().width() - 1 for b in bar.source.buttons)


def test_the_keep_button_is_in_view_at_the_smallest_size(window, session, qapp):
    record(session, 12.0)
    window.flush()
    window.resize(MIN_SIZE)
    ui_helpers.pump(qapp, 6)
    keep = window.inspector.ready.keep
    bottom = keep.mapTo(window, keep.rect().bottomRight())
    assert window.rect().contains(bottom)


def test_settings_keeps_its_rows_readable_and_ok_in_reach_on_a_small_screen(qapp, monkeypatch):
    """It outgrew a laptop's screen: Qt squeezed the rows until the record key could not be read."""
    from types import SimpleNamespace

    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1280, 672))
    monkeypatch.setattr(QGuiApplication, 'primaryScreen', staticmethod(lambda: screen))
    dialog = dialog_mod.SettingsDialog({})
    dialog.show()
    ui_helpers.pump(qapp, 4)
    assert dialog.height() <= 672
    assert dialog.record_key.height() >= dialog.record_key.sizeHint().height() - 2
    assert dialog.rect().contains(dialog.ok.mapTo(dialog, dialog.ok.rect().bottomRight()))
    assert not any(isinstance(parent, QScrollArea) for parent in _parents(dialog.ok))
    dialog.close()


def test_ticking_the_box_beside_keep_is_the_permission(window, session):
    take = record(session, 12.0)
    window.flush()
    assert isinstance(window.inspector.ready.consent, QCheckBox)
    window.inspector.ready.consent.setChecked(True)
    assert take.meta['consent_confirmed'] is True


def test_the_box_follows_the_take_in_front_and_is_never_carried_over(window, session):
    first = record(session, 12.0)
    fill(session)
    window.flush()
    assert window.inspector.ready.consent.isChecked()
    record(session, 12.0)
    window.flush()
    assert not window.inspector.ready.consent.isChecked()
    session.workspace.open_take(first)
    window.flush()
    assert window.inspector.ready.consent.isChecked()


def test_changing_the_words_clears_the_box(window, session):
    record(session, 12.0)
    fill(session)
    window.flush()
    session.workspace.set_meta(allowed_use='something else')
    window.flush()
    assert not window.inspector.ready.consent.isChecked()
