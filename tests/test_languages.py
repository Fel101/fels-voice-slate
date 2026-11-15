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


@pytest.mark.parametrize('ask', [[], ['--missing', 'de'], ['--stale', 'de']])
def test_the_list_can_be_piped_whatever_the_console(ask):
    """Through a pipe, Windows gives the output its old code page, which has no "✓"; the list is UTF-8 all the same."""
    import os
    import subprocess
    tool = Path(strings.__file__)
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
    done = subprocess.run([sys.executable, str(tool), *ask], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stderr.decode('utf-8', 'replace')
    listed = json.loads(done.stdout.decode('utf-8'))
    if not ask:
        assert listed == strings.texts() and any('✓' in text for text in listed)


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


def test_a_status_line_the_files_have_no_entry_for_stays_as_it_is(german, session):
    session.bus.status('Something new, 3 of 3.')
    assert german.status_text.full_text == 'Something new, 3 of 3.'


def test_a_status_line_built_from_values_is_in_the_language(german, session):
    """It was left in English: "Kept 3 of 3 takes." after Keep on three."""
    from helpers import finish_batch
    takes = [record(session, 12.0) for _ in range(3)]
    for take in takes:
        take.meta.update(transcript='Words spoken in the take.', origin='recorded here', allowed_use='my own projects')
    session.batch.keep(takes)
    finish_batch(session)
    german.flush()
    assert german.status_text.full_text == '3 von 3 Takes behalten.'


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


@pytest.mark.parametrize('code', i18n.LANGUAGES)
def test_each_tab_fits_its_panel_in_each_language(qapp, session, dialogs, speak, code):
    """A page wider than the panel is cut off on the right, every row of it. The two Auto analyze buttons in German and
    Spanish, the two copy buttons in Spanish and French, and in English a Check row whose value is long once made a tab so;
    so did the value of mains hum in French, "60 Hz, 46 dB au-dessus de son voisinage". The take hums, as the one in the
    pictures does, so that the hum row has its longest value."""
    import synthetic
    speak(code)
    session.workspace.set_purpose('audition')
    record(session, samples=synthetic.take(14.0))
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    window.flush()
    session.jobs.drain()
    window.flush()
    for tab in range(3):
        window.inspector.show_tab(tab)
        ui_helpers.pump(qapp)
        scroll = window.inspector.pages.currentWidget()
        assert scroll.widget().width() <= scroll.viewport().width(), window.inspector.TABS[tab]
    window.close()
    ui_helpers.pump(qapp)


# ---- nothing left in English --------------------------------------------------------------------------------------------------

WORD = re.compile(r'[A-Za-z]{3,}')
SAME = {'dBFS', 'LUFS', 'kHz', 'RMS', 'ACX', 'Ctrl', 'Clip',      # units and names, the same in every language here
        'FFmpeg', 'Fel', 'Voice', 'Slate',
        # and words one of the languages has too: German says Takes, Zoom and Format; French Gain, Notes and Silence
        'Clips', 'clips', 'min', 'TAKES', 'Zoom', 'Format', 'Original', 'Script', 'Gain', 'Microphone', 'Notes', 'Silence'}


def shown_texts(*widgets):
    """What the labels and buttons under these widgets show now."""
    from PySide6.QtWidgets import QAbstractButton, QLabel
    return {item.text() for widget in widgets for item in widget.findChildren(QLabel) + widget.findChildren(QAbstractButton)
            if item.isVisibleTo(widget) and item.text()}


def analyzed_audition(qapp, make_session, dialogs, root, code):
    """An Audition with a draft transcript, quiet and with long pauses at its ends, after Auto analyze, in a language: what
    the Check tab, the Keep card and the status line say."""
    from helpers import bursts
    i18n.use(code)
    session = make_session(root)
    session.workspace.add_speaker('Smoke Speaker')
    session.workspace.set_purpose('audition')
    take = record(session, samples=bursts(15.0, [(3.0, 6.0), (6.5, 9.5), (10.0, 12.5)], amp=0.03, noise=0.0005, f0=137.0))
    session.drafts.receive(take, 'The harbour lights came on at dusk.', None)
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    window.actions.auto_analyze.trigger()
    session.jobs.drain()
    window.flush()
    window._request_analysis()
    session.jobs.drain()
    window.flush()
    ui_helpers.pump(qapp)
    said = shown_texts(window.inspector.check, window.inspector.ready) | {window.status_text.full_text}
    window.close()
    ui_helpers.pump(qapp)
    return said


@pytest.mark.parametrize('code', LANGUAGES)
def test_the_check_tab_and_the_keep_card_say_nothing_in_english(qapp, make_session, dialogs, speak, tmp_path, code):
    """The measurements, Auto analyze, the ACX rows and the advice on the Keep card are built from values, and they were all
    left in English."""
    english = analyzed_audition(qapp, make_session, dialogs, tmp_path / 'en', 'en')
    theirs = analyzed_audition(qapp, make_session, dialogs, tmp_path / code, code)
    assert any('ACX' in text for text in english) and any('Auto fix:' in text for text in english)    # it saw them all
    assert any(text.startswith('Speech is at') for text in english) and any('draft' in text for text in english)
    left = sorted(text for text in english & theirs if any(word not in SAME for word in WORD.findall(text)))
    assert left == []


@pytest.mark.parametrize('code', i18n.LANGUAGES)
def test_the_choices_of_the_take_list_fit_their_boxes(qapp, session, dialogs, speak, code):
    """A choice wider than its box is shortened in the middle: "Plus réc…bord" for "Plus récentes d'abord", and so were
    the other names of the order and of which takes are shown in German, Spanish and French."""
    speak(code)
    record(session, 5.0)
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    window.flush()
    ui_helpers.pump(qapp)
    for combo in (window.library.order, window.library.shown):
        assert combo.isVisible()
        for index in range(combo.count()):
            assert combo.fontMetrics().horizontalAdvance(combo.itemText(index)) <= combo.room(), combo.itemText(index)
    window.close()
    ui_helpers.pump(qapp)


def edited_take(qapp, make_session, dialogs, root, code):
    """A take with a cut, a fade, gain on a part and its level set, with a part selected, in a language: what every tab, the
    bar under the waveform, the history, the take list's choices and the status line say."""
    i18n.use(code)
    session = make_session(root)
    session.workspace.add_speaker('Zo')
    record(session, 12.0)
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    window.flush()
    editor = session.editor
    editor.set_selection(1.0, 1.5)
    editor.cut_selection()
    editor.set_selection(0.0, 0.3)
    editor.fade_selection('in')
    editor.set_selection(5.0, 6.0)
    editor.gain_selection(3.0)
    editor.normalize_level()
    editor.set_selection(2.0, 3.0)
    window.flush()
    session.jobs.drain()
    window.flush()
    said = {window.status_text.full_text}
    for tab in range(3):
        window.inspector.show_tab(tab)
        ui_helpers.pump(qapp)
        said |= shown_texts(window)
    history = window.inspector.process.history
    said |= {history.item(row).text() for row in range(history.count())}
    said |= {combo.itemText(index) for combo in (window.library.order, window.library.shown) for index in range(combo.count())}
    window.close()
    ui_helpers.pump(qapp)
    return said


def opened_dialogs(code):
    """What the dialogs say as they open, in a language."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QAbstractButton, QLabel
    i18n.use(code)
    said = {'origin': 'Zo', 'allowed_use': 'Zo'}
    totals = {'speaker': (48, 1234.5), 'all': (112, 3001.0)}
    release = {'file': 'Zo.pdf', 'signed': '2026-09-02', 'expires': '2027-09-02', 'notes': 'Zo'}
    opened = [dialog_mod.SettingsDialog({}), dialog_mod.AboutDialog(), dialog_mod.ShortcutsDialog(), dialog_mod.CopyDialog('Zo'),
              dialog_mod.ManifestDialog('Zo', totals), dialog_mod.DatasetDialog('Zo', totals),
              dialog_mod.SpeakerDialog('Zo', release, True), dialog_mod.KeepManyDialog('Zo', [SimpleNamespace(meta=said)] * 3, said)]
    texts = set()
    for dialog in opened:
        translate_tree(dialog)
        texts |= {dialog.windowTitle()} | {item.text() for item in dialog.findChildren(QLabel) + dialog.findChildren(QAbstractButton)
                                            if item.text()}
    return texts


@pytest.mark.parametrize('code', LANGUAGES)
def test_an_edited_take_and_the_dialogs_say_nothing_in_english(qapp, make_session, dialogs, speak, tmp_path, code):
    """The history of edits, the bar under the waveform, the Record card, the status lines and parts of the dialogs are
    built from values, and they were left in English."""
    english = edited_take(qapp, make_session, dialogs, tmp_path / 'en', 'en') | opened_dialogs('en')
    theirs = edited_take(qapp, make_session, dialogs, tmp_path / code, code) | opened_dialogs(code)
    assert any(text.startswith('Cut 1.00 to 1.50 s') for text in english)                     # it saw the history
    assert any(text.startswith('Released under') for text in english)                         # and the dialogs
    left = sorted(text for text in english & theirs if any(word not in SAME for word in WORD.findall(text)))
    assert left == []


@pytest.mark.parametrize('code', i18n.LANGUAGES)
def test_the_names_on_the_record_card_are_whole(qapp, session, speak, code):
    """The names had 70 pixels in every language, and "Enregistré dans" was cut to "Enregistré d"."""
    from recorder.ui.inspector import RecordCard
    speak(code)
    card = RecordCard(session)
    card.resize(340, 300)
    card.show()
    card.refresh()
    ui_helpers.pump(qapp)
    for title in card.titles:
        assert title.width() >= title.fontMetrics().horizontalAdvance(title.text()), title.text()
    card.close()


@pytest.mark.parametrize('code', i18n.LANGUAGES)
def test_when_and_where_a_take_comes_from_fits_its_card(qapp, session, dialogs, speak, monkeypatch, code):
    """The second line of a card was cut by its edge with no sign: "Aujourd'hui 01:10 · Syst" in French, "Hoy 01:10 ·
    Importació" in Spanish, and "Yesterday 23:59 · Sys" in English. Today, yesterday or any month, late in the day and from
    any source, it now fits whole."""
    import datetime as dt

    from PySide6.QtGui import QFontMetrics

    from recorder.ui import library, theme
    speak(code)
    rooms = []
    fit = library.TakeDelegate.fit
    monkeypatch.setattr(library.TakeDelegate, 'fit', staticmethod(lambda text, font, room: rooms.append(room) or
                                                                  fit(text, font, room)))
    takes = [record(session, 12.0, source=source) for source in library.SOURCES]
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    window.resize(1440, 900)
    window.show()
    window.flush()
    ui_helpers.pump(qapp)
    window.library.list.viewport().grab()
    room, font = min(rooms), theme.ui_font(11)
    metrics = QFontMetrics(font)
    now = dt.datetime.now()
    moments = [now.replace(hour=23, minute=59), (now - dt.timedelta(days=1)).replace(hour=23, minute=59)]
    moments += [dt.datetime(now.year - 1, month, 28, 23, 59) for month in range(1, 13)]
    for take in takes:
        for moment in moments:
            take.created = moment.astimezone().isoformat(timespec='seconds')
            line = window.library.model.row_at(window.library.model.items.index(take)).sub
            shown = fit(line, font, room)
            assert '…' not in shown and metrics.horizontalAdvance(shown) <= int(room) + 4, line
    window.close()
    ui_helpers.pump(qapp)
