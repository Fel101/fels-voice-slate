"""Fixtures shared by the tests. No test touches the user's settings, audio devices or Recycle Bin."""
import os

import pytest

from helpers import Bin, Clock, FakeCapture, FakePlayer, speech
from recorder import capture as cap
from recorder import clean, settings, transcript
from recorder.jobs import Jobs
from recorder.session import Session
from recorder.store import Library


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Settings in a temporary file, no real devices, no recognizer, and FFmpeg looked up as each test says."""
    monkeypatch.setenv(settings.ENV_VAR, str(tmp_path / 'settings.json'))
    monkeypatch.setattr(cap, 'list_microphones', lambda: [(1, 'Test microphone')])
    monkeypatch.setattr(cap, 'default_microphone', lambda: 1)
    monkeypatch.setattr(cap, 'list_loopbacks', lambda: [(9, 'Test speakers [Loopback]')])
    monkeypatch.setattr(cap, 'default_loopback', lambda: 9)
    monkeypatch.setattr(transcript, 'available', lambda python, model: False)
    yield
    clean.set_ffmpeg_path(None)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def recycle():
    return Bin()


@pytest.fixture
def make_session(tmp_path, clock, player, recycle, isolated):
    """Build a session on a temporary library; call again with the same root to simulate a restart."""
    made = []

    def make(root=None, **kwargs):
        root = root or tmp_path / 'clips'
        settings.update({'root': str(root)}, tmp_path / 'settings.json')
        session = Session(settings_file=tmp_path / 'settings.json', jobs=kwargs.pop('jobs', Jobs(inline=True)),
                          player=kwargs.pop('player', player), clock=clock, library=Library(root, trash=recycle),
                          **kwargs)
        made.append(session)
        return session

    yield make
    for session in made:
        session.jobs.close()


@pytest.fixture
def open_fake(monkeypatch):
    """Make the next capture the session opens a FakeCapture that is not recording yet. Returns the list of them."""
    opened = []

    def factory(source, device, pre_roll):
        fake = FakeCapture(speech(2.0), kind=source)
        fake.recording = fake.armed = False
        fake.opened_with = (source, device, pre_roll)
        opened.append(fake)
        return fake

    monkeypatch.setattr(cap, 'open_capture', factory)
    return opened


@pytest.fixture
def session(make_session):
    s = make_session()
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    return s


# ---- the window -------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='session')
def qapp():
    """One QApplication for every window test: offscreen, with the program's stylesheet and fonts installed."""
    pytest.importorskip('PySide6')
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication

    import ui_helpers
    from recorder.ui import theme
    app = QApplication.instance()
    if app is None:
        theme.prepare_display()
        app = QApplication(['voice-slate-tests'])
    ui_helpers.register_fonts()
    theme.install(app)
    yield app
    ui_helpers.tear_down_qt(app)


@pytest.fixture
def dialogs():
    import ui_helpers
    return ui_helpers.StubDialogs()


@pytest.fixture
def window(qapp, session, dialogs):
    """The main window over a session with a fake player, a temporary library and canned dialogs. Time is driven by hand:
    the window's own timer is stopped, and a test calls `window.flush()` after it changes something.
    A context menu never blocks: the stub dialogs record it in `dialogs.menus`."""
    import ui_helpers
    from recorder.ui.main_window import MainWindow
    w = MainWindow(session=session, dialogs=dialogs)
    w.timer.stop()
    w.resize(1440, 900)
    w.show()
    ui_helpers.pump(qapp)
    yield w
    w.timer.stop()
    w.close()
    ui_helpers.pump(qapp)
