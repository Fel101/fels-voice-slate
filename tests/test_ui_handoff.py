"""Send and save Voice Lab bundles through the same actions that appear in the window."""
import json
import zipfile
from types import SimpleNamespace

from helpers import fill, record


def keep(session, label):
    take = record(session, 12.0)
    fill(session)
    session.workspace.set_meta(label=label)
    session.keep()
    return take


def settle(window, qapp):
    for _ in range(24):
        window.session.tick()
        qapp.processEvents()
        window.flush()
    assert not window.session.batch.running


def action(menu, text):
    return next(item for item in menu.actions() if item.text() == text)


def test_selection_menu_sends_three_kept_clips(window, session, qapp, dialogs, tmp_path):
    takes = [keep(session, f'Line {number}') for number in range(1, 4)]
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)
    action(window.build_selection_menu(takes), 'Send to Voice Lab...').trigger()
    settle(window, qapp)
    files = list(inbox.glob('*.voiceslate'))
    assert len(files) == 3
    assert any('Sent 3 clips to Voice Lab. Open the Cast page' in notice.text for notice in dialogs.notices)


def test_save_action_sits_next_to_copy_when_there_is_no_inbox(window, session, qapp, dialogs, tmp_path):
    take = keep(session, 'Save this line')
    menu = window.build_take_menu(take)
    labels = [item.text() for item in menu.actions()]
    assert labels.index('Save for Voice Lab...') == labels.index('Save a copy...') + 1
    dialogs.save_answer = str(tmp_path / 'saved')
    action(menu, 'Save for Voice Lab...').trigger()
    settle(window, qapp)
    assert dialogs.asked[-2:] == [('handoff_save', 1), ('handoff_destination', 'smoke-speaker-Save this line.voiceslate')]
    with zipfile.ZipFile(tmp_path / 'saved.voiceslate') as bundle:
        assert bundle.read('clip.wav') == take.export_path.read_bytes()
    assert not (tmp_path / 'suite' / 'inbox').exists()


def test_several_clips_are_saved_to_one_folder_without_replacing_anything(window, session, qapp, dialogs, tmp_path):
    takes = [keep(session, f'Line {number}') for number in range(1, 4)]
    folder = tmp_path / 'for the lab'
    folder.mkdir()
    taken = folder / 'smoke-speaker-Line 2.voiceslate'
    taken.write_bytes(b'already here')
    dialogs.folder_answer = str(folder)
    action(window.build_selection_menu(takes), 'Save for Voice Lab...').trigger()
    settle(window, qapp)
    assert ('handoff_save', 3) in dialogs.asked
    assert not [question for question in dialogs.asked if question[0] == 'handoff_destination']
    assert sorted(path.name for path in folder.iterdir()) == [
        'smoke-speaker-Line 1.voiceslate', 'smoke-speaker-Line 2-2.voiceslate', 'smoke-speaker-Line 2.voiceslate',
        'smoke-speaker-Line 3.voiceslate']
    assert taken.read_bytes() == b'already here'
    with zipfile.ZipFile(folder / 'smoke-speaker-Line 2-2.voiceslate') as bundle:
        assert bundle.read('clip.wav') == takes[1].export_path.read_bytes()
    assert any(f'Saved 3 files for Voice Lab in {folder}.' in notice.text for notice in dialogs.notices)


def test_a_folder_inside_the_output_folder_is_refused_before_anything_is_saved(window, session, qapp, dialogs):
    takes = [keep(session, name) for name in ('One', 'Two')]
    before = sorted(path for path in session.lib.root.rglob('*'))
    dialogs.folder_answer = str(session.lib.root)
    window.lab_handoff(takes)
    settle(window, qapp)
    assert sorted(path for path in session.lib.root.rglob('*')) == before
    assert any('outside the output folder' in notice.text for notice in dialogs.notices)


def test_cancelling_the_folder_saves_nothing(window, session, qapp, dialogs, tmp_path):
    takes = [keep(session, name) for name in ('One', 'Two')]
    dialogs.folder_answer = None
    window.lab_handoff(takes)
    settle(window, qapp)
    assert list(tmp_path.rglob('*.voiceslate')) == []
    assert window.status_text.full_text == 'Voice Lab handoff stopped.'


def test_save_dialog_offers_a_keyboard_accessible_voice_lab_link(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from recorder.ui import dialogs

    opened = []
    monkeypatch.setattr(dialogs, 'QDesktopServices', SimpleNamespace(openUrl=opened.append))
    dialog = dialogs.HandoffSaveDialog()
    link = next(label for label in dialog.findChildren(QLabel) if label.accessibleName() == 'Get Voice Lab')
    assert link.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert link.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    link.linkActivated.emit(dialogs.VOICE_LAB_URL)
    assert [url.toString() for url in opened] == ['https://github.com/Fel101/fels-voice-lab']
    dialog.close()
    several = dialogs.HandoffSaveDialog(count=3)
    texts = {label.text() for label in several.findChildren(QLabel)}
    assert any(text.startswith('Save 3 handoff files in one folder') for text in texts)
    assert 'Choose folder...' in {button.text() for button in several.findChildren(dialogs.QPushButton)}
    several.close()


def test_bad_clip_in_selection_prevents_every_write(window, session, qapp, dialogs, tmp_path):
    valid, invalid = [keep(session, name) for name in ('Valid', 'Needs permission')]
    data = json.loads(invalid.sidecar_path.read_bytes())
    data.pop('consent_confirmed')
    invalid.sidecar_path.write_text(json.dumps(data), encoding='utf-8')
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)
    window.lab_handoff([valid, invalid])
    settle(window, qapp)
    assert list(inbox.iterdir()) == []
    assert any('Needs permission' in notice.text and 'confirmed permission' in notice.text for notice in dialogs.notices)


def test_open_transcript_and_send_anyway_for_an_unchecked_draft(window, session, qapp, dialogs, tmp_path):
    draft, other = [keep(session, name) for name in ('Unchecked', 'Another line')]
    data = json.loads(draft.sidecar_path.read_bytes())
    data['transcript_status'] = 'asr_draft'
    draft.sidecar_path.write_text(json.dumps(data), encoding='utf-8')
    assert session.current is other
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)
    dialogs.handoff_draft_answer = 'review'
    window.lab_handoff([draft, other])
    settle(window, qapp)
    assert session.current is draft and window.inspector.clip.transcript.isVisible()
    assert list(inbox.iterdir()) == []
    assert ('handoff_draft', True) in dialogs.asked
    dialogs.handoff_draft_answer = 'proceed'
    window.lab_handoff([draft])
    settle(window, qapp)
    [path] = list(inbox.iterdir())
    with zipfile.ZipFile(path) as bundle:
        assert json.loads(bundle.read('clip.json'))['transcript_status'] == 'asr_draft'


def test_stop_during_checks_prevents_any_write(window, session, qapp, tmp_path):
    take = keep(session, 'While checking')
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)
    window.lab_handoff([take])
    session.tick()
    window.flush()
    assert window.status_stop.isVisible()
    window.status_stop.click()
    settle(window, qapp)
    assert list(inbox.iterdir()) == []


def test_closing_during_a_send_finishes_only_the_current_clip(window, session, qapp, tmp_path):
    takes = [keep(session, name) for name in ('First clip', 'Next clip')]
    inbox = tmp_path / 'suite' / 'inbox' / 'voice-lab'
    inbox.mkdir(parents=True)
    window.lab_handoff(takes)
    for _ in range(12):
        session.tick()
        qapp.processEvents()
        if session.batch.title == 'Sending clips to Voice Lab' and session.batch.finishing_handoff:
            break
    assert session.batch.finishing_handoff
    assert not window.close()
    settle(window, qapp)
    assert not window.isVisible()
    [sent] = inbox.glob('*.voiceslate')
    assert sent.name.endswith('-First-clip.voiceslate')
