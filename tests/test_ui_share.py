"""Sharing and filing, driven through the window: dragging a kept clip out, saving a copy of it, exporting a manifest of the
kept clips, and moving a take to another speaker."""
import csv
import json
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from helpers import fill, record  # noqa: E402
from recorder import manifest, share  # noqa: E402
from recorder.ui.actions import SPEC  # noqa: E402


def cards(window):
    return [card for card in window.library._cards if not card.isHidden()]


def keep_one(window, session, **meta):
    """Record a take, fill in what Keep needs, keep it, and return it."""
    take = record(session, 12.0)
    fill(session)
    if meta:
        session.workspace.set_meta(**meta)
    session.keep()
    window.flush()
    return take


def other_speaker(session, name='Other Speaker'):
    """A second speaker, with the first still selected. Returns (first, second)."""
    first = session.workspace.speaker
    second = session.workspace.add_speaker(name)
    session.workspace.select_speaker(first)
    return first, second


def pull(card, dx=40, dy=0, press=(20, 20)):
    """Press on the card and move the pointer away with the button held, as someone starting a drag does."""
    where, there = QPointF(*press), QPointF(press[0] + dx, press[1] + dy)
    none, left, plain = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QApplication.sendEvent(card, QMouseEvent(QEvent.Type.MouseButtonPress, where, card.mapToGlobal(where), left, left, plain))
    QApplication.sendEvent(card, QMouseEvent(QEvent.Type.MouseMove, there, card.mapToGlobal(there), none, left, plain))


class Drags(list):
    """The drags the library started, in order, and the actions each one was offered."""

    def __init__(self):
        super().__init__()
        self.offered = []

    def start(self, drag, actions):
        self.append(drag)
        self.offered.append(actions)


@pytest.fixture
def drags(window):
    """Every drag the library starts, instead of a real one that waits for the mouse."""
    seen = Drags()
    window.library.run_drag = seen.start
    return seen


# ---- dragging a clip out ------------------------------------------------------------------------------------------------

def test_pulling_a_kept_card_away_starts_a_drag_that_carries_the_clips_file(window, session, drags):
    take = keep_one(window, session)
    [card] = cards(window)
    pull(card)
    [drag] = drags
    mime = drag.mimeData()
    assert mime.hasUrls() and mime.urls() == [QUrl.fromLocalFile(str(take.export_path))]
    assert mime.formats() == ['text/uri-list']                       # a list of files and nothing else
    assert Path(mime.urls()[0].toLocalFile()) == take.export_path and take.export_path.is_file()


def test_the_drag_offers_copying_and_nothing_else(window, session, drags):
    """A drop target that is offered only a copy cannot move the clip out of the library."""
    keep_one(window, session)
    pull(cards(window)[0])
    assert drags.offered == [Qt.DropAction.CopyAction]
    assert not drags.offered[0] & Qt.DropAction.MoveAction and not drags.offered[0] & Qt.DropAction.LinkAction


def test_the_drag_carries_a_picture_of_the_card(window, session, drags):
    keep_one(window, session)
    pull(cards(window)[0])
    assert not drags[0].pixmap().isNull() and drags[0].pixmap().width() <= 240 * max(1, drags[0].pixmap().devicePixelRatio())


def test_a_take_that_is_not_kept_cannot_be_dragged_out(window, session, drags):
    record(session, 12.0)
    window.flush()
    pull(cards(window)[0])
    assert drags == []


def test_a_small_movement_is_a_click_and_not_a_drag(window, session, drags):
    keep_one(window, session)
    pull(cards(window)[0], dx=1)
    assert drags == []


def test_moving_with_no_button_down_does_not_drag(window, session, drags):
    keep_one(window, session)
    card = cards(window)[0]
    there = QPointF(90, 20)
    QApplication.sendEvent(card, QMouseEvent(QEvent.Type.MouseMove, there, card.mapToGlobal(there), Qt.MouseButton.NoButton,
                                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert drags == []


def test_one_pull_starts_one_drag_and_the_press_still_opens_the_take(window, session, drags):
    first = keep_one(window, session)
    second = record(session, 12.0)
    window.flush()
    assert session.current is second
    card = next(c for c in cards(window) if c.take is first)
    pull(card, dx=60)
    pull(card, dx=90)                                          # the release came first, so this is a new press
    assert session.current is first and len(drags) == 2
    QApplication.sendEvent(card, QMouseEvent(QEvent.Type.MouseMove, QPointF(150, 20), card.mapToGlobal(QPointF(150, 20)),
                                             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert len(drags) == 2                                     # moving further during the same press starts no second drag


def test_nothing_is_dragged_while_recording(window, session, drags, open_fake):
    keep_one(window, session)
    card = cards(window)[0]
    session.capturing.start()
    window.flush()
    pull(card)
    assert drags == []


def test_a_clip_whose_file_has_gone_is_not_dragged(window, session, drags):
    take = keep_one(window, session)
    take.export_path.unlink()
    pull(cards(window)[0])
    assert drags == []
    assert window.library.make_drag(take, cards(window)[0]) is None


def test_a_kept_card_says_it_can_be_dragged(window, session):
    keep_one(window, session)
    record(session, 12.0)
    window.flush()
    kept, plain = sorted(cards(window), key=lambda c: not c.take.kept)
    assert 'Drag the card' in kept.toolTip() and 'Drag the card' not in plain.toolTip()


# ---- save a copy --------------------------------------------------------------------------------------------------------

def test_the_new_commands_are_in_the_gear_menu_in_groups(window):
    names = [a.text() for a in window.topbar.menu.actions()]
    start = names.index('Move take to speaker...')
    assert names[start - 1] == '' and names[start:start + 3] == ['Move take to speaker...', 'Save a copy...', 'Export manifest...']
    assert names[start + 3] == ''


def test_save_a_copy_has_its_own_shortcut_and_no_other_command_has_it(window):
    assert SPEC['save_copy'][2] == ['Ctrl+Shift+S']
    keys = [tuple(k) for name, (_, _, k, _, _) in SPEC.items() if k]
    assert len(keys) == len(set(keys))
    assert ('Ctrl+Shift+S',) in keys and ['Ctrl+S'] == SPEC['keep'][2]


def test_save_a_copy_is_on_only_for_a_kept_clip(window, session):
    assert not window.actions.save_copy.isEnabled()
    record(session, 12.0)
    window.flush()
    assert not window.actions.save_copy.isEnabled()
    fill(session)
    session.keep()
    window.flush()
    assert window.actions.save_copy.isEnabled()


def test_a_copy_is_written_where_the_person_chose_with_the_ending_the_format_needs(window, session, dialogs, tmp_path):
    take = keep_one(window, session, label='Welcome back')
    before = take.export_path.read_bytes()
    dialogs.copy_answer = ('mp3', 44100)
    dialogs.save_answer = str(tmp_path / 'sent' / 'for-the-editor')
    (tmp_path / 'sent').mkdir()
    window.save_copy()
    copy = tmp_path / 'sent' / 'for-the-editor.mp3'
    assert copy.is_file() and sf.info(str(copy)).samplerate == 44100 and sf.info(str(copy)).format == 'MP3'
    assert take.export_path.read_bytes() == before                         # the kept clip is as it was
    [asked_copy, asked_save] = dialogs.asked
    assert asked_copy[1].name == 'Welcome back'
    assert asked_save[1].start == 'Welcome back.mp3' and asked_save[1].filter == 'MP3, 128 kbit/s (*.mp3)'
    assert str(copy) in window.status_text.full_text and '44.1 kHz' in window.status_text.full_text
    assert dialogs.notices == []


def test_the_name_offered_comes_from_the_label_then_the_transcript_then_the_file(window, session, dialogs, tmp_path):
    keep_one(window, session)                                              # no label: the transcript
    dialogs.copy_answer = ('wav', 48000)
    window.save_copy()
    assert dialogs.asked[1][1].start == 'The harbour lights came on at dusk.wav'
    session.workspace.set_meta(transcript='')                              # a kept clip's text can be emptied in the window
    dialogs.asked.clear()
    window.save_copy()
    assert dialogs.asked[1][1].start == f'{Path(session.current.export_path).stem}.wav'


@pytest.mark.parametrize('typed, expected', [('plain', 'plain.flac'), ('with.wav', 'with.flac'), ('x.FLAC', 'x.FLAC'),
                                             ('some.thing', 'some.thing.flac')])
def test_the_ending_is_put_right_without_making_a_mess(window, session, dialogs, tmp_path, typed, expected):
    keep_one(window, session)
    dialogs.copy_answer = ('flac', 24000)
    dialogs.save_answer = str(tmp_path / typed)
    window.save_copy()
    assert (tmp_path / expected).is_file() and sf.info(str(tmp_path / expected)).format == 'FLAC'


def test_backing_out_of_either_dialog_makes_no_copy(window, session, dialogs, tmp_path):
    keep_one(window, session)
    dialogs.copy_answer = None
    window.save_copy()
    assert [name for name, _ in dialogs.asked] == ['copy']
    dialogs.asked.clear()
    dialogs.copy_answer, dialogs.save_answer = ('wav', 48000), None
    window.save_copy()
    assert [name for name, _ in dialogs.asked] == ['copy', 'save'] and dialogs.notices == []


def test_a_copy_cannot_be_put_inside_the_output_folder(window, session, dialogs):
    take = keep_one(window, session)
    dialogs.copy_answer = ('wav', 48000)
    dialogs.save_answer = str(take.export_path.parent / 'copy.wav')
    window.save_copy()
    assert dialogs.notices[-1].title == 'Save a copy' and 'outside the output folder' in dialogs.notices[-1].text
    assert not (take.export_path.parent / 'copy.wav').exists()
    dialogs.save_answer = str(take.export_path.parent / 'masters' / 'deeper.wav')
    window.save_copy()
    assert len(dialogs.notices) == 2


def test_a_copy_cannot_overwrite_the_kept_clip(window, session, dialogs):
    take = keep_one(window, session)
    before = take.export_path.read_bytes()
    dialogs.copy_answer = ('wav', 24000)
    dialogs.save_answer = str(take.export_path)
    window.save_copy()
    assert take.export_path.read_bytes() == before and dialogs.notices


def test_a_copy_that_cannot_be_written_says_why(window, session, dialogs, tmp_path):
    keep_one(window, session)
    dialogs.copy_answer = ('ogg', 48000)
    dialogs.save_answer = str(tmp_path / 'no-such-folder' / 'x.ogg')
    window.save_copy()
    assert dialogs.notices[-1].level == 'error' and 'could not be made' in dialogs.notices[-1].text
    assert 'Saved a copy' not in window.status_text.full_text


def test_a_kept_clip_whose_file_is_gone_is_reported_not_crashed_on(window, session, dialogs):
    take = keep_one(window, session)
    take.export_path.unlink()
    window.save_copy()
    assert dialogs.asked == [] and 'not in its folder' in dialogs.notices[-1].text


def test_save_a_copy_does_nothing_for_a_take_that_is_not_kept(window, session, dialogs):
    record(session, 12.0)
    window.flush()
    window.save_copy()
    assert dialogs.asked == [] and dialogs.notices == []


def test_the_take_menu_offers_a_copy_only_for_a_kept_clip(window, session, dialogs, tmp_path):
    kept = keep_one(window, session)
    plain = record(session, 12.0)
    window.flush()
    assert 'Save a copy...' in [a.text() for a in window.build_take_menu(kept).actions()]
    assert 'Save a copy...' not in [a.text() for a in window.build_take_menu(plain).actions()]
    dialogs.copy_answer, dialogs.save_answer = ('wav', 44100), str(tmp_path / 'from-menu')
    next(a for a in window.build_take_menu(kept).actions() if a.text() == 'Save a copy...').trigger()
    assert session.current is kept and (tmp_path / 'from-menu.wav').is_file()


# ---- the manifest -------------------------------------------------------------------------------------------------------

def test_a_manifest_of_one_speaker_is_written_next_to_their_clips(window, session, dialogs):
    first, second = other_speaker(session)
    a, b = keep_one(window, session), keep_one(window, session)
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(session.lib.folder(first) / f'{first}-manifest.jsonl')
    window.export_manifest()
    path = session.lib.folder(first) / f'{first}-manifest.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert sorted(r['id'] for r in rows) == sorted([a.id, b.id])
    assert {r['audio_filepath'] for r in rows} == {a.export_path.name, b.export_path.name}       # relative to the manifest
    assert 'Listed 2 clips' in window.status_text.full_text and dialogs.notices == []
    [asked_manifest, asked_save] = dialogs.asked
    assert asked_manifest[1].speaker == 'Smoke Speaker'
    assert Path(asked_save[1].start) == session.lib.folder(first) / f'{first}-manifest.jsonl'
    assert asked_save[1].filter == 'JSON Lines (*.jsonl)'


def test_a_manifest_of_everyone_goes_by_default_in_the_output_folder_with_paths_from_there(window, session, dialogs):
    first, second = other_speaker(session)
    keep_one(window, session)
    session.workspace.select_speaker(second)
    keep_one(window, session)
    dialogs.manifest_answer = ('all', 'csv')
    dialogs.save_answer = str(session.lib.root / 'manifest.csv')
    window.export_manifest()
    assert Path(dialogs.asked[1][1].start) == session.lib.root / 'manifest.csv'
    assert dialogs.asked[1][1].filter == 'CSV (*.csv)'
    with open(session.lib.root / 'manifest.csv', encoding='utf-8', newline='') as f:
        table = list(csv.DictReader(f))
    assert {row['speaker_id'] for row in table} == {first, second} and len(table) == 2
    assert all(row['audio_filepath'].startswith(row['speaker_id'] + '/') for row in table)


def test_the_file_ending_follows_the_file_type_chosen(window, session, dialogs, tmp_path):
    keep_one(window, session)
    dialogs.manifest_answer = ('speaker', 'csv')
    dialogs.save_answer = str(tmp_path / 'list.jsonl')
    window.export_manifest()
    assert (tmp_path / 'list.csv').is_file() and not (tmp_path / 'list.jsonl').exists()


def test_a_manifest_can_go_outside_the_output_folder_with_paths_that_lead_back(window, session, dialogs, tmp_path):
    take = keep_one(window, session)
    (tmp_path / 'elsewhere').mkdir()
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(tmp_path / 'elsewhere' / 'm.jsonl')
    window.export_manifest()
    [row] = [json.loads(line) for line in (tmp_path / 'elsewhere' / 'm.jsonl').read_text(encoding='utf-8').splitlines()]
    assert (tmp_path / 'elsewhere' / row['audio_filepath']).resolve() == take.export_path.resolve()


def test_with_no_kept_clip_there_is_nothing_to_write_and_the_person_is_told(window, session, dialogs, tmp_path):
    record(session, 12.0)
    window.flush()
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(tmp_path / 'empty.jsonl')
    window.export_manifest()
    assert not (tmp_path / 'empty.jsonl').exists() and 'no kept clips' in dialogs.notices[-1].text


def test_clips_that_are_left_out_are_named_in_a_warning_and_the_rest_is_still_written(window, session, dialogs, tmp_path):
    good, changed = keep_one(window, session), keep_one(window, session)
    with open(changed.export_path, 'r+b') as f:
        f.seek(100)
        f.write(b'\x7f\x7f\x7f\x7f')
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(tmp_path / 'm.jsonl')
    window.export_manifest()
    rows = [json.loads(line) for line in (tmp_path / 'm.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['id'] for r in rows] == [good.id]
    notice = dialogs.notices[-1]
    assert notice.level == 'warning' and changed.sidecar_path.name in notice.text and 'changed since it was kept' in notice.text
    assert '1 left out' in window.status_text.full_text


def test_a_manifest_that_cannot_be_written_says_why(window, session, dialogs, tmp_path):
    keep_one(window, session)
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(tmp_path / 'no-such-folder' / 'm.jsonl')
    window.export_manifest()
    assert dialogs.notices[-1].level == 'error' and dialogs.notices[-1].title == 'Could not export the manifest'
    assert not dialogs.notices[-1].busy                                   # the message does not open under the wait cursor
    assert QApplication.overrideCursor() is None                          # and the wait cursor does not stay on


def test_backing_out_of_the_manifest_dialogs_writes_nothing(window, session, dialogs, tmp_path):
    keep_one(window, session)
    window.export_manifest()
    assert [name for name, _ in dialogs.asked] == ['manifest']
    dialogs.asked.clear()
    dialogs.manifest_answer = ('all', 'jsonl')
    window.export_manifest()
    assert [name for name, _ in dialogs.asked] == ['manifest', 'save'] and dialogs.notices == []


def test_the_manifest_command_is_on_with_a_speaker_and_off_while_recording(window, session, open_fake):
    assert window.actions.export_manifest.isEnabled()                      # the session starts with a speaker
    session.capturing.start()
    window.flush()
    assert not window.actions.export_manifest.isEnabled()


def test_manifest_rows_match_the_names_the_toolkits_read(window, session, dialogs, tmp_path):
    keep_one(window, session)
    dialogs.manifest_answer = ('speaker', 'jsonl')
    dialogs.save_answer = str(tmp_path / 'm.jsonl')
    window.export_manifest()
    [row] = [json.loads(line) for line in (tmp_path / 'm.jsonl').read_text(encoding='utf-8').splitlines()]
    assert list(row)[:3] == ['audio_filepath', 'text', 'duration'] and set(row) == set(manifest.COLUMNS)
    assert row['text'] == 'The harbour lights came on at dusk.' and row['duration'] == pytest.approx(12.0, abs=0.05)
    assert share.RATES and row['sample_rate'] == 24000


# ---- moving a take to another speaker ----------------------------------------------------------------------------------

def test_moving_the_open_take_from_the_gear_menu_asks_which_speaker(window, session, dialogs):
    first, second = other_speaker(session)
    older, newer = record(session, 12.0), record(session, 12.0)
    window.flush()
    dialogs.item_answer = 0
    window.move_take()
    window.flush()
    assert dialogs.asked[0][1].items == ['Other Speaker'] and dialogs.asked[0][1].title == 'Move take'
    assert newer.speaker == second and session.current is older and len(cards(window)) == 1
    assert 'Moved to Other Speaker.' in window.status_text.full_text and dialogs.confirmations == []


def test_the_list_of_speakers_leaves_out_the_one_the_take_is_under(window, session, dialogs):
    first, second = other_speaker(session)
    third = session.workspace.add_speaker('Third One')
    session.workspace.select_speaker(first)
    record(session, 12.0)
    window.flush()
    dialogs.item_answer = None
    window.move_take()
    assert dialogs.asked[0][1].items == ['Other Speaker', 'Third One']         # sorted by name, without Smoke Speaker
    assert session.current.speaker == first and third


def test_with_only_one_speaker_the_person_is_told_to_add_another(window, session, dialogs):
    record(session, 12.0)
    window.flush()
    assert not window.actions.move_take.isEnabled()
    window.move_take()
    assert dialogs.asked == [] and 'no other speaker' in dialogs.notices[-1].text


def test_the_move_command_follows_the_take_and_the_speakers(window, session, dialogs, open_fake):
    first, second = other_speaker(session)
    assert not window.actions.move_take.isEnabled()                            # no take yet
    record(session, 12.0)
    window.flush()
    assert window.actions.move_take.isEnabled()
    session.capturing.start()
    window.flush()
    assert not window.actions.move_take.isEnabled()


def test_the_take_menu_lists_the_other_speakers_to_move_to(window, session, dialogs):
    first, second = other_speaker(session)
    take = record(session, 12.0)
    window.flush()
    menu = window.build_take_menu(take)
    move = next(a for a in menu.actions() if a.text() == 'Move to speaker')
    assert [a.text() for a in move.menu().actions()] == ['Other Speaker']
    move.menu().actions()[0].trigger()
    window.flush()
    assert take.speaker == second and cards(window) == [] and dialogs.confirmations == []


def test_the_take_menu_has_no_move_entry_with_one_speaker(window, session):
    take = record(session, 12.0)
    window.flush()
    assert 'Move to speaker' not in [a.text() for a in window.build_take_menu(take).actions()]


def test_moving_a_kept_clip_asks_first_and_says_what_goes_with_it(window, session, dialogs):
    first, second = other_speaker(session)
    take = keep_one(window, session)
    dialogs.confirm_answer = False
    window._move(take, second)
    assert take.speaker == first and take.export_path.is_file()
    [asked] = dialogs.confirmations
    assert asked.title == 'Move this kept clip?' and 'Other Speaker' in asked.text and 'Smoke Speaker' in asked.text
    assert 'permission' in asked.text and asked.yes == 'Move clip'
    dialogs.confirm_answer = True
    window._move(take, second)
    window.flush()
    assert take.speaker == second and cards(window) == []
    data = json.loads(Path(take.sidecar_path).read_text(encoding='utf-8'))
    assert data['speaker'] == second and data['consent_confirmed'] is True


def test_a_move_that_fails_leaves_the_take_listed_and_says_why(window, session, dialogs, monkeypatch):
    first, second = other_speaker(session)
    take = record(session, 12.0)
    window.flush()

    def refuse(self_lib, take_, slug):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(type(session.lib), 'move_take', refuse)
    window._move(take, second)
    assert dialogs.notices[-1].title == 'Could not move the take' and 'No space' in dialogs.notices[-1].text
    assert take.speaker == first and cards(window)


def test_a_take_that_cannot_be_moved_is_refused_with_the_reason(window, session, dialogs):
    first, second = other_speaker(session)
    take = record(session, 12.0)
    take.master_path.unlink()
    window.flush()
    window._move(take, second)
    assert 'not saved on disk' in dialogs.notices[-1].text and take.speaker == first


def test_nothing_is_moved_while_recording(window, session, dialogs, open_fake):
    first, second = other_speaker(session)
    take = record(session, 12.0)
    session.capturing.start()
    window._move(take, second)
    assert take.speaker == first and dialogs.notices == [] and 'Stop the recording' in window.status_text.full_text


def test_the_speaker_picker_shows_the_clip_under_the_new_speaker(window, session, dialogs):
    first, second = other_speaker(session)
    take = keep_one(window, session)
    dialogs.confirm_answer = True
    window._move(take, second)
    window.flush()
    window.library.speakerPicked.emit(second)
    window.flush()
    [card] = cards(window)
    assert card.take is take and card.status == 'Kept' and 'Drag the card' in card.toolTip()


# ---- the dialogs --------------------------------------------------------------------------------------------------------

def test_the_copy_dialog_offers_every_format_and_rate_and_starts_on_wav_at_48_khz(qapp):
    from recorder.ui.dialogs import CopyDialog
    dialog = CopyDialog('Welcome back')
    assert [dialog.kind.itemData(i) for i in range(dialog.kind.count())] == list(share.FORMATS)
    assert [dialog.rate.itemData(i) for i in range(dialog.rate.count())] == list(share.RATES)
    assert dialog.values() == ('wav', 48000)
    assert [dialog.rate.itemText(i) for i in range(dialog.rate.count())] == ['16 kHz', '22.05 kHz', '24 kHz (as kept)',
                                                                             '44.1 kHz', '48 kHz']
    dialog.kind.setCurrentIndex(dialog.kind.findData('mp3'))
    dialog.rate.setCurrentIndex(dialog.rate.findData(44100))
    assert dialog.values() == ('mp3', 44100)


def test_the_manifest_dialog_names_the_speaker_and_starts_on_their_clips_as_json_lines(qapp):
    from recorder.ui.dialogs import ManifestDialog
    dialog = ManifestDialog('Ann Lee')
    assert dialog.scope.itemText(0) == 'Kept clips of Ann Lee' and dialog.scope.itemText(1) == 'Kept clips of all speakers'
    assert dialog.values() == ('speaker', 'jsonl')
    dialog.scope.setCurrentIndex(1)
    dialog.kind.setCurrentIndex(dialog.kind.findData('csv'))
    assert dialog.values() == ('all', 'csv')
    assert [dialog.kind.itemText(i) for i in range(dialog.kind.count())] == ['JSON Lines (.jsonl)', 'CSV (.csv)']


def test_the_real_dialogs_hand_back_what_was_chosen_or_none(qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog

    from recorder.ui.dialogs import CopyDialog, Dialogs, ManifestDialog
    dialogs = Dialogs(None)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: ('C:/x/out.mp3', 'MP3 (*.mp3)')))
    assert dialogs.choose_save_file('Save', 'start', 'MP3 (*.mp3)') == 'C:/x/out.mp3'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', staticmethod(lambda *a, **k: ('', '')))
    assert dialogs.choose_save_file('Save', 'start') is None
    monkeypatch.setattr(QInputDialog, 'getItem', staticmethod(lambda *a, **k: ('Third', True)))
    assert dialogs.choose_item('Move', 'To:', ['First', 'Second', 'Third']) == 2
    monkeypatch.setattr(QInputDialog, 'getItem', staticmethod(lambda *a, **k: ('Third', False)))
    assert dialogs.choose_item('Move', 'To:', ['First', 'Second', 'Third']) is None
    monkeypatch.setattr(CopyDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(ManifestDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    assert dialogs.copy_options('x') == ('wav', 48000) and dialogs.manifest_options('Ann') == ('speaker', 'jsonl')
    monkeypatch.setattr(CopyDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(ManifestDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    assert dialogs.copy_options('x') is None and dialogs.manifest_options('Ann') is None


def test_the_shortcuts_dialog_lists_save_a_copy(qapp):
    from recorder.ui.dialogs import SHORTCUT_GROUPS
    group = dict(SHORTCUT_GROUPS)['Clips and files']
    assert 'save_copy' in group and group.index('save_copy') == group.index('rename') + 1
    assert all(name in SPEC for names in dict(SHORTCUT_GROUPS).values() for name in names)


# ---- each guard on its own ----------------------------------------------------------------------------------------------

def test_a_card_of_a_take_that_is_not_kept_never_asks_for_a_drag(window, session):
    record(session, 12.0)
    window.flush()
    card = cards(window)[0]
    asked = []
    card.dragRequested.connect(asked.append)
    pull(card)
    assert asked == []


def test_the_panel_starts_no_drag_for_a_take_that_is_not_kept(window, session, drags):
    take = record(session, 12.0)
    window.flush()
    window.library._drag(take)
    assert drags == []
    kept = keep_one(window, session)
    window.library._drag(kept)
    assert len(drags) == 1


def test_the_panel_starts_no_drag_while_recording(window, session, drags, open_fake):
    kept = keep_one(window, session)
    session.capturing.start()
    window.library._drag(kept)
    assert drags == []
