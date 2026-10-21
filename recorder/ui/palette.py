"""The colours. Warm near-black neutrals and one violet accent, with green, amber and red for state."""
from PySide6.QtGui import QColor

PALETTE = {
    'bg0': '#12110f', 'bg1': '#171613', 'bg2': '#1e1c19', 'bg3': '#282622', 'bg4': '#35322c',
    'line': '#2a2824', 'line2': '#3a3731',
    'text': '#ece8e1', 'muted': '#a29c92', 'dim': '#8a8478',
    'accent': '#b18cff', 'accent_d': '#8e6bdc', 'ink': '#17121f',
    'red': '#ff6a5f', 'green': '#5fd391', 'amber': '#f3b94d',
}


def hex_of(name):
    """A palette name as '#rrggbb'; anything else is returned as it is."""
    return PALETTE.get(name, name)


def color(name, alpha=None):
    """A QColor from a palette name or a colour string, optionally with alpha 0 to 255."""
    c = QColor(hex_of(name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c
