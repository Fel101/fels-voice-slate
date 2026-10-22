"""Every command the window offers, once: its name, icon, shortcut and tooltip.

The toolbar buttons, the menus and the keyboard shortcuts all use these QActions, so a command is enabled, disabled
and described in one place.
"""
from PySide6.QtGui import QAction, QKeySequence

from . import icons

# name: (text, icon, shortcuts, tooltip, checkable)
SPEC = {
    # recording and listening
    'record': ('Record', 'record', ['F9'], 'Start recording, or stop and keep what was captured', False),
    'arm': ('Arm', 'arm', ['Ctrl+M'], 'Open the input so the meter is live, and keep the last half second before Record',
            True),
    'play': ('Play', 'play', ['Space'], 'Play or pause from the playhead', False),
    'stop': ('Stop', 'stop', [], 'Stop playing', False),
    'loop': ('Loop', 'loop', ['L'], 'Loop the selection, or the whole clip when nothing is selected', True),
    'ab': ('A/B', None, ['A'], 'Switch between the original and the edited clip, at the same moment', False),
    'escape': ('Stop or deselect', None, ['Esc'], 'Stop playing; with nothing playing, clear the selection', False),
    # editing
    'undo': ('Undo', 'undo', ['Ctrl+Z'], 'Undo the last change', False),
    'redo': ('Redo', 'redo', ['Ctrl+Y', 'Ctrl+Shift+Z'], 'Redo', False),
    'cut': ('Cut', 'cut', ['Delete'], 'Cut the selection out of the clip. The original recording is not changed', False),
    'trim': ('Trim to selection', 'trim', ['Ctrl+T'], 'Keep only the selection', False),
    'silence': ('Silence', 'silence', ['Ctrl+L'], 'Replace the selection with silence, keeping its length', False),
    'fade_in': ('Fade in', 'fade_in', [], 'Fade the selection in from silence', False),
    'fade_out': ('Fade out', 'fade_out', [], 'Fade the selection out to silence', False),
    'gain': ('Gain...', 'gain', [], 'Raise or lower the selection by a number of dB', False),
    'auto_analyze': ('Auto analyze', 'analyze', ['Ctrl+Shift+A'],
                     'Measure the clip and say in plain words what could be better. Nothing is changed', False),
    'auto_fix': ('Auto fix', 'wand', ['Ctrl+Shift+F'],
                 'Repair what Auto analyze finds, as one step that Undo takes back', False),
    'auto_trim': ('Auto-trim ends', 'auto_trim', [], 'Cut the pauses at both ends of the clip down to 0.15 s', False),
    'shorten': ('Shorten pauses', 'shorten', [], 'Cut long pauses inside the clip down to 0.5 s each', False),
    'check_joins': ('Check joins', 'check_joins', [], 'Play one second either side of each cut, so every join can be '
                                                       'judged', False),
    'cut_start': ('Cut from start to here', None, [], 'Cut everything before the playhead (or the end of the selection)',
                  False),
    'cut_end': ('Cut from here to end', None, [], 'Cut everything after the playhead (or the start of the selection)',
                False),
    'select_all': ('Select all', None, ['Ctrl+A'], 'Select the whole clip', False),
    'set_in': ('Set in point', None, ['['], 'Put the start of the selection at the playhead', False),
    'set_out': ('Set out point', None, [']'], 'Put the end of the selection at the playhead', False),
    # view
    'zoom_in': ('Zoom in', 'zoom_in', ['+', '=', 'Ctrl++'], 'Zoom in (the mouse wheel zooms too)', False),
    'zoom_out': ('Zoom out', 'zoom_out', ['-', 'Ctrl+-'], 'Zoom out', False),
    'fit': ('Fit', 'fit', ['0', 'Ctrl+0'], 'Show the whole clip', False),
    'zoom_selection': ('Zoom to selection', None, ['Z'], 'Fill the view with the selection', False),
    # takes and files
    'keep': ('Keep clip', None, ['Ctrl+S'], 'Write the 24 kHz WAV and its sidecar', False),
    'discard': ('Discard take...', 'trash', [], 'Move this take to the Recycle Bin', False),
    'rerecord': ('Re-record', None, [], 'Move this take to the Recycle Bin and record again', False),
    'import_recording': ('Import recording...', 'import', ['Ctrl+O'], 'Copy a recording in as a new take', False),
    'new_speaker': ('New speaker...', 'plus', ['Ctrl+N'], 'Add a speaker', False),
    'refresh_devices': ('Refresh devices', None, [], 'Look for microphones and outputs again', False),
    'open_folder': ('Open output folder', 'folder', ['Ctrl+Shift+O'], 'Show the output folder in Explorer', False),
    'load_script': ('Load script...', None, ['Ctrl+Shift+L'], 'Read prompts from a text file, one line per take', False),
    'clear_script': ('Clear script', None, [], 'Stop using the script', False),
    'prev_line': ('Previous script line', 'chevron_left', ['Ctrl+Up'], 'Go back one line of the script', False),
    'next_line': ('Next script line', 'chevron_right', ['Ctrl+Down'], 'Go to the next line of the script', False),
    # the program
    'settings': ('Settings...', 'settings', ['Ctrl+,'], 'Output folder, record key, FFmpeg and draft transcripts', False),
    'shortcuts': ('Keyboard shortcuts', None, ['F1'], 'List every shortcut', False),
    'about': ('About...', None, [], 'Version, licence and credits', False),
    'quit': ('Quit', None, ['Ctrl+Q'], 'Close the recorder', False),
}


def shortcut_text(keys):
    return ' / '.join(QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText) for k in keys)


class Actions:
    """The window's QActions by name: `actions.cut`, `actions['cut']`."""

    def __init__(self, parent):
        self._items = {}
        for name, (text, icon, keys, tip, checkable) in SPEC.items():
            action = QAction(text, parent)
            if icon:
                action.setIcon(icons.icon(icon))
            if keys:
                action.setShortcuts([QKeySequence(k) for k in keys])
            action.setToolTip(f'{tip} ({shortcut_text(keys)})' if keys else tip)
            action.setStatusTip(tip)
            action.setCheckable(checkable)
            action.setObjectName(name)
            parent.addAction(action)                  # shortcuts work even when a button is hidden or collapsed
            self._items[name] = action

    def __getattr__(self, name):
        try:
            return self._items[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name):
        return self._items[name]

    def __iter__(self):
        return iter(self._items.values())

    def names(self):
        return list(self._items)
