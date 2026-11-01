"""The colours. Warm neutrals and one violet accent, with green, amber and red for state, in three themes.

`PALETTE` is the theme in use. Everything reads its colours from it when it draws, and `use` swaps what it holds, so nothing
else knows there are themes. tests/test_ui_access.py measures the contrast of the text in each one.
"""
from PySide6.QtGui import QColor

DARK = {
    'bg0': '#12110f', 'bg1': '#171613', 'bg2': '#1e1c19', 'bg3': '#282622', 'bg4': '#35322c', 'press': '#3f3b34',
    'line': '#2a2824', 'line2': '#3a3731',
    'text': '#ece8e1', 'muted': '#a9a399', 'dim': '#9b9589',
    'accent': '#b18cff', 'accent_d': '#8e6bdc', 'accent_hi': '#c3a3ff', 'accent_off': '#3a3350', 'accent_off_text': '#a39bb8',
    'ink': '#17121f', 'accent_rgb': '177,140,255',
    'red': '#ff6a5f', 'red_hi': '#ff8279', 'red_ink': '#230d0b', 'red_rgb': '255,106,95', 'green': '#5fd391', 'amber': '#f3b94d',
}
LIGHT = {
    'bg0': '#f4f1ea', 'bg1': '#faf8f3', 'bg2': '#ffffff', 'bg3': '#ece8df', 'bg4': '#dfdacf', 'press': '#d3cdc0',
    'line': '#e2ddd2', 'line2': '#c9c3b6',
    'text': '#1d1b18', 'muted': '#55504a', 'dim': '#67615a',
    'accent': '#6236c7', 'accent_d': '#7f5ad6', 'accent_hi': '#4f27ad', 'accent_off': '#d9d0ee', 'accent_off_text': '#5d5570',
    'ink': '#ffffff', 'accent_rgb': '98,54,199',
    'red': '#bd2f24', 'red_hi': '#a3261c', 'red_ink': '#ffffff', 'red_rgb': '189,47,36', 'green': '#17703f', 'amber': '#8a5a00',
}
CONTRAST = {
    'bg0': '#000000', 'bg1': '#000000', 'bg2': '#0a0a0a', 'bg3': '#1f1f1f', 'bg4': '#333333', 'press': '#4d4d4d',
    'line': '#808080', 'line2': '#bfbfbf',
    'text': '#ffffff', 'muted': '#f0f0f0', 'dim': '#d9d9d9',
    'accent': '#ffe14d', 'accent_d': '#ffd000', 'accent_hi': '#fff09a', 'accent_off': '#4d4300', 'accent_off_text': '#fff5b8',
    'ink': '#000000', 'accent_rgb': '255,225,77',
    'red': '#ff8a80', 'red_hi': '#ffb3ad', 'red_ink': '#000000', 'red_rgb': '255,138,128', 'green': '#7dffb0', 'amber': '#ffc04d',
}
THEMES = {'dark': DARK, 'light': LIGHT, 'contrast': CONTRAST}
PALETTE = dict(DARK)


def use(name):
    """Make a theme the one in use. A name that is not one gives the dark theme."""
    PALETTE.clear()
    PALETTE.update(THEMES.get(name, DARK))


def hex_of(name):
    """A palette name as '#rrggbb'; anything else is returned as it is."""
    return PALETTE.get(name, name)


def color(name, alpha=None):
    """A QColor from a palette name or a colour string, optionally with alpha 0 to 255."""
    c = QColor(hex_of(name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c
