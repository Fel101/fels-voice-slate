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
    import logging
    import os

    from PySide6.QtWidgets import QApplication

    from .. import __version__, i18n, launch, settings
    from ..hotkey import GlobalKey
    from . import theme
    from .main_window import MainWindow

    _taskbar_identity()
    theme.prepare_display()
    data = settings.load()
    i18n.use(settings.language(data) or i18n.windows_language())
    scale = settings.text_scale(data) or theme.windows_text_scale()
    if scale != 1.0:
        os.environ.setdefault('QT_SCALE_FACTOR', f'{scale:g}')     # read as the application starts, and it scales everything
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationVersion(__version__)
    theme.install(app, settings.theme(data))
    hotkey = GlobalKey(settings.record_key())
    window = MainWindow(hotkey=hotkey)
    launch.report_errors_to(lambda error: window.unexpected.emit(str(error) or type(error).__name__))
    window.closed.connect(lambda: launch.report_errors_to(None))
    app.aboutToQuit.connect(lambda: hotkey.set_enabled(False))
    log = logging.getLogger('recorder')            # what the log says about the two optional things people ask about
    log.info('Draft transcripts: %s', 'ready' if window.session.drafts.available() else 'not set up')
    log.info('%s in other programs: %s', hotkey.name, 'on' if hotkey.active else hotkey.problem or 'off')
    window.show()
    return app.exec()
