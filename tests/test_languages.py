"""The window in other languages: the mechanism, and whether the language files fit the program."""
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip('PySide6')

import ui_helpers  # noqa: E402
from helpers import fill, record  # noqa: E402
from recorder import i18n, settings  # noqa: E402
from recorder.ui import dialogs as dialog_mod  # noqa: E402
from recorder.ui.main_window import MainWindow  # noqa: E402
from recorder.ui.translate import translate_tree  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import strings  # noqa: E402

LANGUAGES = [code for code in i18n.LANGUAGES if code != 'en']
FIELDS = re.compile(r'\{\w+\}')


@pytest.fixture
def speak():
    """Use a language for one test."""
    yield i18n.use
    i18n.use('en')


def table(code):
    return json.loads((i18n.FOLDER / f'{code}.json').read_text(encoding='utf-8'))


# ---- the mechanism ------------------------------------------------------------------------------------------------------------

def test_english_needs_no_file_and_changes_nothing():
    i18n.use('en')
    assert not i18n.active() and i18n.tr('Record') == 'Record'


def test_a_text_with_no_entry_stays_in_english(speak):
    speak('de')
    assert i18n.active() and i18n.tr('Record') == 'Aufnehmen' and i18n.tr('Something new') == 'Something new'


@pytest.mark.parametrize('code', ['xx', '', None, '../../settings'])
def test_a_language_with_no_file_is_english(speak, code):
    speak(code)
    assert not i18n.active()


def test_a_file_that_cannot_be_read_is_english(speak, monkeypatch, tmp_path):
    (tmp_path / 'de.json').write_text('not json', encoding='utf-8')
    monkeypatch.setattr(i18n, 'FOLDER', tmp_path)
    speak('de')
    assert not i18n.active()


def test_the_language_setting():
    assert settings.language({}) is None and settings.language({'language': 'fr'}) == 'fr'
    assert settings.language({'language': 7}) is None


@pytest.mark.parametrize('language_id, code', [(0x0407, 'de'), (0x0c0a, 'es'), (0x040c, 'fr'), (0x0409, 'en'), (0x0411, 'en')])
def test_the_language_of_windows_is_followed_when_there_is_a_file(monkeypatch, language_id, code):
    from types import SimpleNamespace
    fake = SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=lambda: language_id))
    monkeypatch.setattr(i18n.ctypes, 'windll', fake, raising=False)
    assert i18n.windows_language() == code


def test_settings_offers_the_languages(qapp):
    dialog = dialog_mod.SettingsDialog({'language': 'es'})
    assert dialog.values()['language'] == 'es' and dialog.language_choice.count() == len(i18n.LANGUAGES) + 1
    assert dialog_mod.SettingsDialog({}).values()['language'] is None


# ---- the files ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('code', LANGUAGES)
def test_a_translation_has_the_fields_of_its_english(code):
    """A field missing from a translation would stop the window with an error."""
    for english, text in table(code).items():
        assert sorted(FIELDS.findall(text)) == sorted(FIELDS.findall(english)), english


@pytest.mark.parametrize('code', LANGUAGES)
def test_every_entry_is_still_something_the_program_says(code):
    known = set(strings.texts())
    stale = [text for text in table(code) if text not in known]
    assert stale == []


@pytest.mark.parametrize('code', LANGUAGES)
def test_every_text_the_program_says_has_a_translation(code):
    """Apart from what is the same in every language, or only names a file type."""
    entries = table(code)
    missing = [text for text in strings.texts() if text not in entries and text not in ('OK', 'FFmpeg')
               and ';;' not in text]
    assert missing == []


@pytest.mark.parametrize('code', LANGUAGES)
def test_the_statement_of_permission_is_translated(code):
    from recorder.ui.inspector import ReadyCard
    assert table(code)[ReadyCard.STATEMENT] != ReadyCard.STATEMENT


# ---- on the window ------------------------------------------------------------------------------------------------------------

def texts_in(widget):
    from PySide6.QtWidgets import QAbstractButton, QLabel
    return ' | '.join(item.text() for item in widget.findChildren(QLabel) + widget.findChildren(QAbstractButton))


@pytest.fixture
def german(qapp, session, dialogs, speak):
    speak('de')
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    ui_helpers.pump(qapp)
    yield window
    window.close()
    ui_helpers.pump(qapp)


def test_the_window_comes_up_in_the_language(german):
    words = texts_in(german)
    assert 'Aufnehmen' in words and 'SPRECHER' in words and 'Noch nichts zu bearbeiten' in words


def test_the_commands_are_in_the_language(german):
    assert german.actions.cut.text() == 'Schneiden' and 'Originalaufnahme' in german.actions.cut.toolTip()


def test_a_take_shows_in_the_language(german, session):
    record(session, 12.0)
    german.flush()
    assert german.library.rows()[0].title.startswith('Take 1')
    assert 'In Prüfung' in german.library.model.index(0).data()
    assert 'Bereit zum Behalten?' in texts_in(german.inspector)
    assert 'Erlaubnis' in texts_in(german.inspector.ready)


def test_a_clip_can_be_kept_with_the_box_in_the_language(german, session):
    take = record(session, 12.0)
    german.flush()
    fill(session)
    german.flush()
    assert session.keep() is not None and take.kept


def test_what_is_stored_stays_in_english_whatever_the_language(german, session):
    """Sidecars are read by other programs."""
    take = record(session, 12.0)
    fill(session)
    session.keep()
    data = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    assert data['purpose'] == 'reference' and data['transcript_status'] == 'typed'


def test_a_dialog_is_translated_when_it_opens(qapp, speak):
    speak('fr')
    dialog = translate_tree(dialog_mod.SettingsDialog({}))
    assert dialog.windowTitle() == 'Réglages' and 'Annuler' in texts_in(dialog)


def test_a_status_line_of_one_fixed_text_is_translated(german, session):
    session.bus.status('Take saved. Review it, then Keep.')
    assert german.status_text.full_text == 'Take gespeichert. Prüfen, dann behalten.'


def test_a_status_line_built_from_values_stays_in_english_and_does_no_harm(german, session):
    session.bus.status('Kept 3 of 3 takes.')
    assert german.status_text.full_text == 'Kept 3 of 3 takes.'


def test_the_language_setting_is_saved(window, session, dialogs):
    dialogs.settings_answer = {'root': None, 'tail_seconds': 0.5, 'ffmpeg': None, 'asr_python': None, 'asr_model': None,
                               'language': 'de'}
    window.open_settings()
    assert settings.load(session.settings_file)['language'] == 'de'


@pytest.mark.parametrize('code', LANGUAGES)
def test_the_whole_window_draws_in_each_language(qapp, session, dialogs, speak, code):
    speak(code)
    record(session, 12.0, script_line='The ferry left at dawn.')
    session.script.load_text('The ferry left at dawn.\nThe gulls followed it.')
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.show()
    ui_helpers.pump(qapp)
    window.flush()
    assert not window.grab().isNull()
    window.close()
    ui_helpers.pump(qapp)
