"""Shortcuts that start the recorder with no console window, and the icon they show."""
import struct
import sys
from pathlib import Path

import pytest

from recorder import settings, shortcut

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows shortcuts')
PNG = b'\x89PNG\r\n\x1a\n'


def test_a_shortcut_starts_this_program_with_its_own_icon_and_identity(tmp_path):
    link = shortcut.create(tmp_path / 'Recorder.lnk')
    info = shortcut.read(link)
    assert Path(info['program']).name.lower() in ('pythonw.exe', 'python.exe')
    assert info['arguments'] == '-m recorder' and Path(info['folder']) == settings.program_dir()
    assert Path(info['icon']).is_file() and Path(info['icon']).suffix == '.ico'
    assert info['app_id'] == settings.APP_ID and info['description']


def test_the_program_is_started_without_a_console_when_pythonw_is_there(tmp_path, monkeypatch):
    (tmp_path / 'python.exe').write_bytes(b'')
    monkeypatch.setattr(shortcut.sys, 'executable', str(tmp_path / 'python.exe'))
    assert shortcut.launch_target()[0] == tmp_path / 'python.exe'                # a build with no pythonw: the next best
    (tmp_path / 'pythonw.exe').write_bytes(b'')
    assert shortcut.launch_target()[0] == tmp_path / 'pythonw.exe'


def test_making_a_shortcut_again_replaces_it(tmp_path):
    link = tmp_path / 'Recorder.lnk'
    shortcut.create(link, arguments='-m recorder --first')
    shortcut.create(link, arguments='-m recorder --second')
    assert shortcut.read(link)['arguments'] == '-m recorder --second'


def test_a_shortcut_can_start_any_program(tmp_path):
    link = shortcut.create(tmp_path / 'Notes.lnk', program=r'C:\Windows\notepad.exe', arguments='', folder=tmp_path,
                           icon=tmp_path / 'none.ico', description='Notes', app_id=None)
    info = shortcut.read(link)
    assert info['program'].lower().endswith('notepad.exe') and info['arguments'] == ''
    assert Path(info['folder']) == tmp_path and info['icon'] == '' and info['app_id'] == '' and info['description'] == 'Notes'


def test_the_folder_for_a_shortcut_is_made_if_needed(tmp_path):
    link = shortcut.create(tmp_path / 'a' / 'b' / 'Recorder.lnk')
    assert link.is_file()


def test_a_shortcut_that_is_not_there_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        shortcut.read(tmp_path / 'missing.lnk')


def test_the_desktop_and_the_start_menu_are_found():
    assert shortcut.known_folder('desktop').parent.is_dir()
    assert shortcut.known_folder('start-menu').is_dir()
    assert shortcut.default_path('desktop').name == "Fel's Voice Slate.lnk"


def test_the_program_folder_is_a_place_for_a_shortcut(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'program_dir', lambda: tmp_path)
    assert shortcut.default_path('folder') == tmp_path / "Fel's Voice Slate.lnk"


# ---- the icon file ----------------------------------------------------------------------------------------------------

def test_the_icon_file_holds_every_size_as_a_png():
    data = (settings.program_dir() / shortcut.ICON).read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data)
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = []
    for i in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        assert width == height and planes == 1 and bits == 32
        assert data[offset:offset + 8] == PNG and offset + length <= len(data)
        sizes.append(width or 256)                                            # the directory stores 256 as 0
    assert sizes == [16, 24, 32, 48, 64, 128, 256]


ICON_STEPS = 8          # how far a pixel of the icon files may be from what the window draws, of 255


def largest_difference(first, second):
    """The largest difference of any channel of any pixel of two pictures of one size, from 0 to 255."""
    import numpy as np
    from PySide6.QtGui import QImage

    assert first.size() == second.size()
    images = [image.convertToFormat(QImage.Format.Format_ARGB32) for image in (first, second)]  # kept alive while read
    a, b = (np.frombuffer(image.constBits(), np.uint8).astype(np.int16) for image in images)
    return int(np.abs(a - b).max())


def test_the_icon_files_match_what_the_window_draws(qapp):
    """Qt can draw an antialiased edge slightly differently on another computer: on GitHub's runners the 48-pixel picture
    came out different under Python 3.12 only, while here every Python draws it as the files hold it. So the files are
    compared with what the window draws within a few steps. A changed picture, or files not made again after the SVG
    changed, differ by far more (the next test)."""
    from PySide6.QtGui import QImage

    sys.path.insert(0, str(settings.program_dir() / 'tools'))
    try:
        import make_icon
    finally:
        sys.path.pop(0)
    from recorder.ui import icons

    icon = settings.program_dir() / shortcut.ICON
    data = icon.read_bytes()
    for index, size in enumerate(icons.APP_ICON_SIZES):
        length, offset = struct.unpack_from('<II', data, 6 + 16 * index + 8)
        drawn = QImage.fromData(make_icon.png_bytes(icons.app_pixmap(size)), 'PNG')
        difference = largest_difference(QImage.fromData(data[offset:offset + length], 'PNG'), drawn)
        assert difference <= ICON_STEPS, f'the {size}-pixel picture in the .ico differs by up to {difference} of 255'
    difference = largest_difference(QImage(str(icon.with_suffix('.png'))), drawn)
    assert difference <= ICON_STEPS, f'the PNG differs by up to {difference} of 255'


def test_a_changed_icon_differs_by_more_than_the_steps_allowed(qapp):
    from PySide6.QtGui import QColor, QPainter

    from recorder.ui import icons

    drawn = icons.app_pixmap(48).toImage()
    changed = drawn.copy()
    painter = QPainter(changed)
    painter.fillRect(changed.rect().adjusted(20, 20, -20, -20), QColor('#ffffff'))
    painter.end()
    assert largest_difference(drawn, drawn.copy()) == 0
    assert largest_difference(drawn, changed) > ICON_STEPS
