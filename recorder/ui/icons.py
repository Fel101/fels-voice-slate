"""The icons: small line drawings on a 24 by 24 grid, written for this program as SVG and drawn by Qt.

They are plain geometry (no icon font and no third-party artwork), so they look the same on every Windows version and
stay sharp at any display scale. `icon(name, color)` returns a QIcon with a normal and a dimmed (disabled) picture.
"""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import palette

STROKE = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
SOLID = 'fill="{c}" stroke="{c}" stroke-width="1.4" stroke-linejoin="round"'

SHAPES = {
    # transport
    'record': ('<circle cx="12" cy="12" r="6.5" {solid}/>', SOLID),
    'stop': ('<rect x="6.5" y="6.5" width="11" height="11" rx="2" {solid}/>', SOLID),
    'play': ('<path d="M8.5 5.8v12.4L18.8 12z" {solid}/>', SOLID),
    'pause': ('<rect x="6.8" y="5.5" width="3.8" height="13" rx="1" {solid}/>'
              '<rect x="13.4" y="5.5" width="3.8" height="13" rx="1" {solid}/>', SOLID),
    'loop': ('<path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H19M16 3l3 3-3 3M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H5M8 15l-3 3 3 3" '
             '{stroke}/>', STROKE),
    # editing
    'undo': ('<path d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" {stroke}/>', STROKE),
    'redo': ('<path d="m15 14 5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" {stroke}/>', STROKE),
    'cut': ('<circle cx="6" cy="6.5" r="2.8" {stroke}/><circle cx="6" cy="17.5" r="2.8" {stroke}/>'
            '<path d="M8.4 8 20 18M8.4 16 20 6" {stroke}/>', STROKE),
    'trim': ('<path d="M7 3v12a2 2 0 0 0 2 2h12M17 21V9a2 2 0 0 0-2-2H3" {stroke}/>', STROKE),
    'silence': ('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" {stroke}/><path d="m16 9.5 5 5m0-5-5 5" {stroke}/>', STROKE),
    'fade_in': ('<path d="M3.5 19 20.5 5.5V19z" {stroke}/>', STROKE),
    'fade_out': ('<path d="M20.5 19 3.5 5.5V19z" {stroke}/>', STROKE),
    'gain': ('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" {stroke}/><path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" '
             '{stroke}/>', STROKE),
    'analyze': ('<path d="M3 12.5h4l2.5-6.5 4 12 2.5-5.5H21" {stroke}/>', STROKE),
    'wand': ('<path d="m4.5 19.5 9-9" {stroke}/><path d="M16.5 3.5 17.7 6.8 21 8l-3.3 1.2-1.2 3.3-1.2-3.3L12 8l3.3-1.2z" '
             '{stroke}/><path d="M19 15v4M17 17h4" {stroke}/>', STROKE),
    'auto_trim': ('<path d="M3.5 5v14M20.5 5v14M7.5 12h4M9.5 9.5 12 12l-2.5 2.5M16.5 12h-4M14.5 9.5 12 12l2.5 2.5" '
                  '{stroke}/>', STROKE),
    'shorten': ('<path d="M12 3.5v5M12 20.5v-5M9 6l3 3 3-3M9 18l3-3 3 3M4 12h4M16 12h4" {stroke}/>', STROKE),
    # view
    'zoom_in': ('<circle cx="10.5" cy="10.5" r="6.5" {stroke}/><path d="m15.5 15.5 5 5M10.5 7.8v5.4M7.8 10.5h5.4" '
                '{stroke}/>', STROKE),
    'zoom_out': ('<circle cx="10.5" cy="10.5" r="6.5" {stroke}/><path d="m15.5 15.5 5 5M7.8 10.5h5.4" {stroke}/>', STROKE),
    'fit': ('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" {stroke}/>', STROKE),
    'check_joins': ('<path d="M3.5 12h6M14.5 12h6M12 5v14M9 8.5 12 12l-3 3.5" {stroke}/>', STROKE),
    # app
    'settings': ('<path d="M4 7h8M16 7h4M4 17h4M12 17h8" {stroke}/><circle cx="14" cy="7" r="2.2" {stroke}/>'
                 '<circle cx="10" cy="17" r="2.2" {stroke}/>', STROKE),
    'plus': ('<path d="M12 5v14M5 12h14" {stroke}/>', STROKE),
    'folder': ('<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" {stroke}/>',
               STROKE),
    'import': ('<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" {stroke}/>', STROKE),
    'mic': ('<rect x="9" y="3.5" width="6" height="11" rx="3" {stroke}/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" '
            '{stroke}/>', STROKE),
    'arm': ('<path d="M4 12h2.5M17.5 12H20M9 8v8M12 5v14M15 9v6" {stroke}/>', STROKE),
    'trash': ('<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6" {stroke}/>', STROKE),
    'copy': ('<rect x="9" y="9" width="11" height="11" rx="2" {stroke}/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" '
             '{stroke}/>', STROKE),
    'chevron_down': ('<path d="m7 10 5 5 5-5" {stroke}/>', STROKE),
    'chevron_left': ('<path d="m14 6-6 6 6 6" {stroke}/>', STROKE),
    'chevron_right': ('<path d="m10 6 6 6-6 6" {stroke}/>', STROKE),
    'more': ('<circle cx="5.5" cy="12" r="1.5" {solid}/><circle cx="12" cy="12" r="1.5" {solid}/>'
            '<circle cx="18.5" cy="12" r="1.5" {solid}/>', SOLID),
    'check': ('<path d="m5 12.5 4.5 4.5L19 7.5" {stroke}/>', STROKE),
    'close': ('<path d="m6 6 12 12M18 6 6 18" {stroke}/>', STROKE),
}


def _svg(name, color):
    body, style = SHAPES[name]
    attrs = style.format(c=color)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            + body.replace('{stroke}', attrs).replace('{solid}', attrs) + '</svg>')


def render(name, color, size, scale=3):
    """The icon as a transparent pixmap `size` device-independent pixels wide, drawn at `scale` times that."""
    renderer = QSvgRenderer(QByteArray(_svg(name, QColor(color).name()).encode('utf-8')))
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(scale)
    return pixmap


@lru_cache(maxsize=None)
def icon(name, color='text', size=18):
    """A QIcon for a name in SHAPES. `color` is a palette name or a colour; the disabled picture is the dim colour."""
    out = QIcon()
    out.addPixmap(render(name, palette.hex_of(color), size), QIcon.Mode.Normal)
    out.addPixmap(render(name, palette.hex_of('dim'), size), QIcon.Mode.Disabled)
    return out


APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def app_pixmap(size):
    """The program's own icon at one size: a violet rounded square with a few waveform bars."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(palette.PALETTE['accent']))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    painter.setBrush(QColor(palette.PALETTE['ink']))
    heights = (0.28, 0.5, 0.74, 0.44, 0.6, 0.32)
    slot = size * 0.64 / len(heights)
    for i, h in enumerate(heights):
        x = size * 0.18 + i * slot + slot * 0.18
        bar = size * h * 0.8
        painter.drawRoundedRect(QRectF(x, (size - bar) / 2, slot * 0.64, bar), slot * 0.32, slot * 0.32)
    painter.end()
    return pixmap


def app_icon():
    """The program's own icon for the window and the taskbar. tools/make_icon.py writes the same picture as the .ico
    file that a desktop shortcut shows."""
    out = QIcon()
    for size in APP_ICON_SIZES:
        out.addPixmap(app_pixmap(size))
    return out
