"""Transcript drafts: a suggestion only. It never overwrites typed text, and a newer request beats an older result."""
import json

import pytest

from helpers import HeldJobs, fill, record
from recorder import clean, settings, transcript
from recorder.session import drafts as drafts_mod


@pytest.fixture
def recognizer(monkeypatch, tmp_path):
    """Pretend a speech recognizer is set up. `replies` holds what each draft will say, in the order they are made."""
    replies = []
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', lambda *a: {'text': replies.pop(0)})
    return replies


def test_a_draft_fills_an_empty_transcript_and_is_marked_as_a_draft(session):
    take = record(session, 10.0)
    assert session.drafts.receive(take, 'A draft from the audio.', None) is True
    assert take.meta['transcript'] == 'A draft from the audio.' and take.meta['transcript_status'] == 'asr_draft'
    assert 'Draft transcript filled in' in session.status_text


def test_a_draft_never_overwrites_a_typed_transcript(session):
    take = record(session, 10.0)
    fill(session, transcript='Typed by me.')
    session.drafts.receive(take, 'A draft from the audio.', None)
    assert take.meta['transcript'] == 'Typed by me.' and take.meta['draft_text'] == 'A draft from the audio.'
    assert 'typed transcript was kept' in session.status_text


def test_a_draft_preserves_every_field_entered_while_it_was_running(session):
    take = record(session, 8.0)
    fill(session, transcript='My typed transcript.')
    session.workspace.set_meta(source_url='local provenance')
    session.drafts.receive(take, 'A new draft.', None)
    assert take.meta['transcript'] == 'My typed transcript.'
    assert take.meta['origin'] == 'recorded in my room' and take.meta['allowed_use'] == 'my own voice references'
    assert take.meta['source_url'] == 'local provenance' and take.meta['consent_confirmed'] is True


def test_a_draft_error_is_shown_and_changes_nothing(session):
    take = record(session, 8.0)
    assert session.drafts.receive(take, None, 'The speech recognizer could not be started.') is False
    assert 'could not be started' in session.status_text and take.meta['transcript'] == ''


def test_an_empty_draft_says_so(session):
    take = record(session, 8.0)
    assert session.drafts.receive(take, '', None) is False
    assert 'came back empty' in session.status_text


def test_asking_when_it_is_not_set_up_explains_how(session):
    take = record(session, 8.0)
    assert session.drafts.request(take) is False
    assert 'not set up' in session.status_text


def test_a_new_take_does_not_nag_when_it_is_not_set_up(session):
    session.bus.status('Some other status.')
    record(session, 8.0)
    assert session.status_text == 'Some other status.'


def test_a_new_take_asks_for_a_draft_when_it_is_set_up(session, recognizer):
    recognizer.append('Hello from the recognizer.')
    take = record(session, 8.0)
    assert 'Drafting the transcript' in session.status_text
    session.tick()
    assert take.meta['transcript'] == 'Hello from the recognizer.'


def test_the_drafting_runs_on_the_edited_audio(session, monkeypatch):
    sent = []
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest',
                        lambda audio, python, model, language=None: sent.append(len(audio)) or {'text': 'ok'})
    take = record(session, 12.0)
    session.drafts.request(take)
    first = sent[-1]
    session.editor.set_selection(1.0, 3.0)
    session.editor.cut_selection()
    session.drafts.request(take)
    assert sent[-1] == pytest.approx(first - 2 * 24000, abs=400)      # less the 10 ms the join overlaps


def test_a_replacement_draft_does_not_overwrite_later_typing(make_session, recognizer):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    take = record(s, 8.0)
    fill(s, transcript='Text before request.')
    recognizer.append('Automatic draft.')
    s.drafts.request(take, replace=True)
    fill(s, transcript='Corrected while waiting.')
    held.run(held.pending - 1)
    assert take.meta['transcript'] == 'Corrected while waiting.'
    assert take.meta['draft_text'] == 'Automatic draft.'


def test_a_replacement_draft_replaces_text_that_has_not_changed(make_session, recognizer):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    take = record(s, 8.0)
    fill(s, transcript='Text before request.')
    recognizer.append('Automatic draft.')
    s.drafts.request(take, replace=True)
    held.run(held.pending - 1)
    assert take.meta['transcript'] == 'Automatic draft.' and take.meta['transcript_status'] == 'asr_draft'


def test_a_newer_request_supersedes_an_older_result(make_session, recognizer):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    take = record(s, 8.0)
    fill(s)
    recognizer.extend(['Newest requested draft.', 'Old draft arrives last.'])
    s.drafts.request(take, replace=True)
    s.drafts.request(take, replace=True)
    first, second = held.pending - 2, held.pending - 1
    held.run(second)                                    # the newer request finishes first, and takes the first reply
    held.run(first)
    assert take.meta['transcript'] == 'Newest requested draft.'
    assert take.meta['draft_text'] == 'Newest requested draft.'


def test_a_draft_that_fails_to_start_reports_the_cleaning_error(session, monkeypatch):
    take = record(session, 10.0)
    session.editor.set_denoise(True)

    def no_ffmpeg():
        raise clean.CleanError('FFmpeg was not found')

    monkeypatch.setattr(clean, 'find_ffmpeg', no_ffmpeg)
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    assert session.drafts.request(take) is False
    assert 'Transcript draft not started: FFmpeg was not found' in session.status_text


def test_a_recognizer_that_raises_is_reported_in_the_status(session, monkeypatch):
    def broken(*a):
        raise transcript.DraftUnavailable('The transcript draft took too long. Type the transcript instead.')

    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', broken)
    take = record(session, 10.0)
    session.drafts.request(take)
    session.tick()
    assert 'took too long' in session.status_text and take.meta['transcript'] == ''


def test_a_draft_on_its_way_is_visible_on_the_take_until_it_arrives(make_session, recognizer):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')
    recognizer.append('Hello.')
    heard = []
    s.bus.listen(lambda topic, payload: heard.append(topic))
    take = record(s, 8.0)
    assert take.draft_request is not None and 'meta' in heard
    held.run(held.pending - 1)
    assert take.draft_request is None and take.meta['transcript'] == 'Hello.'


def test_a_draft_that_fails_is_no_longer_marked_as_on_its_way(make_session, monkeypatch):
    held = HeldJobs()
    s = make_session(jobs=held)
    s.slug = s.workspace.add_speaker('Smoke Speaker')

    def broken(*a):
        raise transcript.DraftUnavailable('The speech recognizer could not be started.')

    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', broken)
    take = record(s, 8.0)
    assert take.draft_request is not None
    held.run(held.pending - 1)
    assert take.draft_request is None and 'could not be started' in s.status_text


def test_a_repeated_ending_that_was_removed_is_mentioned(session, recognizer, monkeypatch):
    monkeypatch.setattr(transcript, 'suggest', lambda *a: {'text': 'Things I want', 'tidied': True})
    take = record(session, 8.0)
    session.tick()
    assert take.meta['transcript'] == 'Things I want' and 'repeated ending was removed' in session.status_text


def test_the_language_setting_reaches_the_recognizer(session, monkeypatch):
    seen = []
    settings.update({'asr_language': ' es '}, session.settings_file)
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest',
                        lambda audio, python, model, language=None: seen.append(language) or {'text': 'Hola.'})
    record(session, 8.0)
    session.tick()
    settings.update({'asr_language': None}, session.settings_file)
    record(session, 8.0)
    session.tick()
    assert seen == ['es', None]


# ---- following the edits -------------------------------------------------------------------------------------------------

def test_a_machine_draft_follows_the_cuts_once_the_editing_pauses(session, recognizer, clock):
    recognizer.extend(['First words and the stumble.', 'First words.'])
    take = record(session, 12.0)
    session.tick()
    assert take.meta['transcript'] == 'First words and the stumble.'
    session.editor.set_selection(6.0, 9.0)
    session.editor.cut_selection()
    assert take.needs_transcript_review is True
    clock.advance(drafts_mod.FOLLOW_DELAY_SECONDS - 0.5)
    session.tick()
    assert take.meta['transcript'] == 'First words and the stumble.'          # the cutting may go on
    clock.advance(1.0)
    session.tick()
    assert take.meta['transcript'] == 'First words.' and take.meta['transcript_status'] == 'asr_draft'
    assert take.needs_transcript_review is False and recognizer == []


def test_several_quick_cuts_make_one_new_draft(session, recognizer, clock):
    recognizer.extend(['Whole take.', 'Only one more draft.', 'Never used.'])
    record(session, 14.0)
    session.tick()
    for start in (1.0, 4.0, 7.0):
        session.editor.set_selection(start, start + 1.0)
        session.editor.cut_selection()
        clock.advance(0.5)
        session.tick()
    clock.advance(drafts_mod.FOLLOW_DELAY_SECONDS)
    session.tick()
    assert recognizer == ['Never used.']


def test_a_transcript_that_was_typed_or_corrected_is_never_replaced_by_a_new_draft(session, recognizer, clock):
    recognizer.extend(['Machine words.', 'Must not be asked for.'])
    take = record(session, 12.0)
    session.tick()
    fill(session, transcript='Machine words, corrected by me.')
    session.editor.set_selection(2.0, 4.0)
    session.editor.cut_selection()
    clock.advance(10.0)
    session.tick()
    assert take.meta['transcript'] == 'Machine words, corrected by me.' and recognizer == ['Must not be asked for.']
    assert take.needs_transcript_review is True


def test_a_take_with_no_draft_yet_is_not_given_one_by_editing(session, clock, monkeypatch):
    asked = []
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', lambda *a: asked.append(1) or {'text': ''})
    take = record(session, 12.0)
    session.tick()
    assert take.draft_key is None and asked == [1]            # the first draft came back empty
    session.editor.set_selection(2.0, 4.0)
    session.editor.cut_selection()
    clock.advance(10.0)
    session.tick()
    assert asked == [1]


def test_changing_the_level_or_the_filters_does_not_ask_for_a_new_draft(session, recognizer, clock):
    recognizer.extend(['Whole take.', 'Never asked for.'])
    take = record(session, 12.0)
    session.tick()
    session.editor.set_chain('highpass', 'High-pass on', enabled=True)
    session.editor.normalize_level()
    session.editor.set_fade_ms(20)
    clock.advance(10.0)
    session.tick()
    assert recognizer == ['Never asked for.'] and take.meta['transcript'] == 'Whole take.'


def test_undoing_a_cut_brings_the_draft_back_to_the_whole_take(session, recognizer, clock):
    recognizer.extend(['Whole take.', 'After the cut.', 'Whole take again.'])
    take = record(session, 12.0)
    session.tick()
    session.editor.set_selection(2.0, 5.0)
    session.editor.cut_selection()
    clock.advance(drafts_mod.FOLLOW_DELAY_SECONDS + 0.1)
    session.tick()
    assert take.meta['transcript'] == 'After the cut.'
    session.editor.undo()
    clock.advance(drafts_mod.FOLLOW_DELAY_SECONDS + 0.1)
    session.tick()
    assert take.meta['transcript'] == 'Whole take again.'


def test_a_pending_draft_is_dropped_when_another_take_is_opened(session, recognizer, clock):
    recognizer.extend(['First take.', 'Second take.', 'Must not be asked for.'])
    first = record(session, 12.0)
    session.tick()
    session.editor.set_selection(2.0, 4.0)
    session.editor.cut_selection()
    second = record(session, 12.0)
    session.tick()
    clock.advance(10.0)
    session.tick()
    assert recognizer == ['Must not be asked for.'] and first.meta['transcript'] == 'First take.'
    assert second.meta['transcript'] == 'Second take.'


def test_the_recognizer_settings_come_from_the_settings_file(session, tmp_path, monkeypatch):
    (tmp_path / 'settings.json').write_text(json.dumps({'root': str(session.lib.root), 'asr_python': 'py',
                                                         'asr_model': 'model'}), encoding='utf-8')
    seen = []
    monkeypatch.setattr(transcript, 'available', lambda python, model: seen.append((python, model)) or False)
    session.drafts.available()
    assert seen == [('py', 'model')]
