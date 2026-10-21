"""The window, built with PySide6. `run()` starts the program."""
import sys


def _taskbar_identity():
    """Windows groups taskbar buttons by program; without this the recorder shows Python's icon, not its own. The
    shortcut that recorder/shortcut.py writes carries the same identity, so a pinned copy and the window are one button."""
    try:
        import ctypes

        from .. import settings
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(settings.APP_ID)
    except (AttributeError, OSError, ImportError):
        pass


def run(argv=None):
    """Open the window and run until it is closed. Returns the exit code."""
    from PySide6.QtWidgets import QApplication

    from .. import __version__, settings
    from . import theme
    from .main_window import MainWindow

    _taskbar_identity()
    theme.prepare_display()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationVersion(__version__)
    theme.install(app)
    window = MainWindow()
    window.show()
    return app.exec()
