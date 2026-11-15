"""A recordings folder that cannot be reached, told apart from an empty one.

The faults are real ones: a drive letter with nothing on it, and a file standing where the folder must be. No error is
faked, because a faked one hid a real bug here once."""
import os
import string
from pathlib import Path

import pytest

from helpers import RATE, record, speech
from recorder import settings
from recorder.store import Library, out_of_reach


def free_drive():
    """A drive letter with nothing on it, from the end of the alphabet, where Windows gives out letters last."""
    for letter in reversed(string.ascii_uppercase[3:]):
        if not os.path.exists(f'{letter}:\\'):
            return Path(f'{letter}:\\')
    pytest.skip('every drive letter is in use on this computer')


@pytest.fixture(params=['drive', 'file', 'file above'])
def kind(request):
    return request.param


@pytest.fixture
def unreachable(kind, tmp_path):
    """An output folder that cannot be reached, of each kind."""
    if kind == 'drive':
        return free_drive() / 'voice-clips'
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file, not a folder', encoding='utf-8')
    return blocker if kind == 'file' else blocker / 'voice-clips'


def untouched(kind, folder):
    """Nothing was made where the folder should be, and a file in the way is as it was."""
    if kind == 'drive':
        return not Path(folder.anchor).exists()
    blocker = folder if kind == 'file' else folder.parent
    return blocker.is_file() and blocker.read_text(encoding='utf-8') == 'a file, not a folder'


def wav(tmp_path):
    import soundfile as sf
    path = tmp_path / 'outside' / 'reading.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, speech(3.0), RATE)
    return path


# ---- telling them apart ------------------------------------------------------------------------------------------------

def test_each_kind_is_found(unreachable, kind):
    problem = out_of_reach(unreachable)
    assert problem is not None and problem.kind == ('drive' if kind == 'drive' else 'file')
    if kind == 'drive':
        assert problem.place == Path(unreachable.anchor)
    else:
        assert problem.place == (unreachable if kind == 'file' else unreachable.parent)


def test_a_folder_that_is_there_or_can_be_made_is_in_reach(tmp_path):
    (tmp_path / 'there').mkdir()
    assert out_of_reach(tmp_path / 'there') is None
    assert out_of_reach(tmp_path / 'not' / 'made' / 'yet') is None       # as on the first start, before anything is recorded
    assert not (tmp_path / 'not').exists()


def test_the_window_opens_saying_which_folder_and_that_nothing_is_lost(make_session, unreachable):
    session = make_session(root=unreachable)
    assert session.workspace.root_problem is not None
    assert str(unreachable) in session.status_text and 'cannot be reached' in session.status_text
    assert session.workspace.speakers == [] and session.workspace.takes == []


def test_an_empty_folder_is_just_empty(make_session, tmp_path):
    session = make_session(root=tmp_path / 'new library')
    assert session.workspace.root_problem is None and 'cannot be reached' not in session.status_text


# ---- what cannot be done there, explained --------------------------------------------------------------------------------

def test_record_explains_and_opens_no_microphone(make_session, unreachable, kind, open_fake):
    session = make_session(root=unreachable)
    assert session.capturing.start() is False
    notice = session.bus.notices[-1]
    assert notice.title == 'Could not start recording' and str(unreachable) in notice.text and 'not lost' in notice.text
    assert open_fake == [] and not session.capturing.recording
    assert untouched(kind, unreachable)


def test_import_explains(make_session, unreachable, kind, tmp_path):
    session = make_session(root=unreachable)
    assert session.workspace.import_file(wav(tmp_path)) is None
    notice = session.bus.notices[-1]
    assert notice.title == 'Could not import' and str(unreachable) in notice.text
    assert untouched(kind, unreachable)


def test_a_new_speaker_explains(make_session, unreachable, kind):
    session = make_session(root=unreachable)
    with pytest.raises(ValueError, match='cannot be reached'):
        session.workspace.add_speaker('Ann')
    assert untouched(kind, unreachable)


def test_the_setting_is_never_changed_and_no_library_is_started_elsewhere(make_session, unreachable, tmp_path, open_fake):
    session = make_session(root=unreachable)
    session.capturing.start()
    session.workspace.import_file(wav(tmp_path))
    session.workspace.retry_root()
    assert settings.load(session.settings_file)['root'] == str(unreachable)
    assert session.lib.root == unreachable and session.workspace.speakers == []


# ---- coming back ---------------------------------------------------------------------------------------------------------

def test_try_again_finds_the_folder_once_it_is_back(make_session, tmp_path):
    """The file in the way is moved, as the person would, and the library that was there all along shows."""
    folder = tmp_path / 'library'
    lib = Library(folder)
    slug = lib.add_speaker('Ann')
    take = record(make_session(root=folder), 2.0)
    assert take is not None
    aside = tmp_path / 'library-aside'
    folder.rename(aside)
    folder.write_text('in the way', encoding='utf-8')
    session = make_session(root=folder)
    assert session.workspace.root_problem is not None
    session.workspace.retry_root()
    assert session.workspace.root_problem is not None and 'still cannot be reached' in session.status_text
    folder.unlink()
    aside.rename(folder)
    session.workspace.retry_root()
    assert session.workspace.root_problem is None and 'can be reached again' in session.status_text
    assert [s for s, _ in session.workspace.speakers] == [slug] and len(session.workspace.takes) == 1


def test_choosing_another_folder_in_settings_is_checked_too(make_session, unreachable, tmp_path):
    session = make_session(root=tmp_path / 'good')
    session.workspace.set_root(unreachable)
    assert session.workspace.root_problem is not None and 'cannot be reached' in session.status_text
    session.workspace.set_root(tmp_path / 'good')
    assert session.workspace.root_problem is None


def test_record_looks_again_when_it_is_pressed(make_session, tmp_path, open_fake):
    """The folder went while the window was open: Record finds out then, not only at the start."""
    folder = tmp_path / 'library'
    session = make_session(root=folder)
    assert session.workspace.root_problem is None
    folder.write_text('in the way', encoding='utf-8')
    assert session.capturing.start() is False and session.workspace.root_problem is not None
    assert open_fake == []


# ---- the window ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def window_on(qapp, make_session, dialogs):
    """A window over a session whose output folder is the one given."""
    import ui_helpers
    from recorder.ui.main_window import MainWindow
    made = []

    def build(root):
        window = MainWindow(session=make_session(root=root), dialogs=dialogs)
        window.timer.stop()
        window.resize(1440, 900)
        window.show()
        ui_helpers.pump(qapp)
        window.flush()
        made.append(window)
        return window

    yield build
    for window in made:
        window.timer.stop()
        window.close()
    ui_helpers.pump(qapp)


def test_the_window_says_which_folder_where_the_eye_is(window_on, unreachable):
    window = window_on(unreachable)
    panel = window.editor_panel
    assert panel.stack.currentIndex() == panel.FOLDER and panel.out_of_reach.isVisible()
    text = panel.out_of_reach.text.text()
    assert str(unreachable) in text and 'not lost' in text and 'Settings' in text
    assert 'cannot be reached' in window.library.empty.text() and 'No takes yet' not in window.library.empty.text()
    assert str(unreachable) in window.status_text.accessibleName()


def test_an_empty_folder_shows_the_welcome_as_before(window_on, tmp_path):
    window = window_on(tmp_path / 'new library')
    panel = window.editor_panel
    assert panel.stack.currentIndex() == panel.EMPTY and 'No takes yet' in window.library.empty.text()


def test_try_again_and_settings_from_the_window(window_on, tmp_path, dialogs):
    folder = tmp_path / 'library'
    folder.write_text('in the way', encoding='utf-8')
    window = window_on(folder)
    panel = window.editor_panel
    panel.out_of_reach.retry.click()
    window.flush()
    assert panel.stack.currentIndex() == panel.FOLDER
    folder.unlink()
    panel.out_of_reach.retry.click()
    window.flush()
    assert panel.stack.currentIndex() == panel.EMPTY and window.session.workspace.root_problem is None
    folder.mkdir(exist_ok=True)
    panel.out_of_reach.settings.click()
    assert dialogs.settings_seen is not None and dialogs.settings_seen['root'] == str(folder)


def test_record_in_the_window_explains(window_on, unreachable, dialogs, open_fake):
    window = window_on(unreachable)
    window.actions.record.trigger()
    window.flush()
    assert dialogs.notices[-1].title == 'Could not start recording' and str(unreachable) in dialogs.notices[-1].text
    assert open_fake == []


def test_importing_several_says_so_once(window_on, unreachable, dialogs, tmp_path):
    window = window_on(unreachable)
    paths = [wav(tmp_path)] * 3
    window.import_files(paths)
    window.flush()
    assert [n.title for n in dialogs.notices] == ['Could not import']


def test_the_folder_button_explains_and_makes_nothing(window_on, unreachable, kind, dialogs):
    window = window_on(unreachable)
    window.library.folder_button.click()
    assert dialogs.notices[-1].title == 'Could not open the folder' and str(unreachable) in dialogs.notices[-1].text
    assert dialogs.opened == [] and untouched(kind, unreachable)
