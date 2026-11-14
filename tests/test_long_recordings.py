"""Long recordings: one too long for this computer's memory is refused in words, one long enough to slow everything down is
asked about first, and importing and splitting go a piece at a time, with how far they have got and Stop, so the window
never freezes over them. Keep and Play of a long take show that the window is busy.

The recordings are real files and the memory rule is the program's own; only how much memory this computer has is set,
so that a small file can stand for a long one."""
import threading

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, HeldJobs, bursts, fill, finish_batch, record, speech
from recorder import audio_core as ac
from recorder import audio_import, store
from recorder.session import measure
from recorder.store import Importing


class Ticking:
    """A clock that moves on each time it is read, so a batch does a piece or two in each tick, as on a slow computer."""

    def __init__(self, step=0.03):
        self.now, self.step = 100.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def wav(path, seconds, channels=1):
    x = speech(seconds)
    sf.write(str(path), np.column_stack([x, 0.5 * x]) if channels == 2 else x, RATE, subtype='PCM_16')
    return path


def audio_files(root):
    """Every audio file under the recordings folder, finished or half written."""
    return sorted(p.name for p in root.rglob('*') if p.is_file() and '.wav' in p.name)


def three_sentences():
    return bursts(18.0, [(0.3, 5.0), (5.6, 11.0), (11.6, 17.7)], amp=0.14, f0=137.0)


# ---- the memory rule ---------------------------------------------------------------------------------------------------

def test_the_rule_says_ok_warn_or_refuse_by_the_memory_it_needs():
    frames, gb = 45 * 60 * 44100, 2 ** 30            # 45 minutes of stereo: about 3.3 GB to work on, as measured
    assert audio_import.fit(frames, 44100, 2, memory=16 * gb).verdict == 'ok'
    assert audio_import.fit(frames, 44100, 2, memory=8 * gb).verdict == 'warn'
    assert audio_import.fit(frames, 44100, 2, memory=4 * gb).verdict == 'refuse'


def test_the_longest_it_names_is_one_that_fits():
    found = audio_import.fit(10 ** 9, 48000, 2, memory=8 * 2 ** 30)
    assert found.verdict == 'refuse'
    longest = int(found.longest * 48000)
    assert audio_import.fit(longest, 48000, 2, memory=8 * 2 ** 30).verdict != 'refuse'


def test_over_an_hour_is_asked_about_whatever_the_memory():
    assert audio_import.fit(2 * 3600 * 48000, 48000, 1, memory=2 ** 40).verdict == 'warn'


def test_without_a_memory_reading_nothing_is_refused_and_over_an_hour_is_asked_about(monkeypatch):
    monkeypatch.setattr(audio_import, 'physical_memory', lambda: None)      # Windows did not say
    assert audio_import.fit(10 ** 10, 48000, 2).verdict == 'warn'
    assert audio_import.fit(60 * 48000, 48000, 2).verdict == 'ok'


def test_this_computer_says_how_much_memory_it_has():
    assert audio_import.physical_memory() > 2 ** 30


# ---- refused, and asked about ------------------------------------------------------------------------------------------

def test_a_recording_too_long_for_this_computer_is_refused_in_words(session, tmp_path, monkeypatch):
    path = wav(tmp_path / 'lecture.wav', 20.0)
    monkeypatch.setattr(audio_import, 'physical_memory', lambda: 8 << 20)      # 20 seconds needs about 23 MB
    assert session.workspace.import_file(path) is None
    notice = session.bus.notices[-1]
    assert notice.title == 'Could not import' and notice.text.startswith('lecture.wav: it is 20 s long')
    assert 'Cut it into parts shorter than' in notice.text and 'import those' in notice.text
    assert session.workspace.takes == [] and audio_files(session.lib.root) == []


def test_a_recording_too_long_is_refused_before_any_of_it_is_read(session, tmp_path, monkeypatch):
    path = wav(tmp_path / 'lecture.wav', 20.0)
    monkeypatch.setattr(audio_import, 'physical_memory', lambda: 8 << 20)
    importing = Importing(session.lib, session.workspace.ensure_speaker(), path)
    with pytest.raises(ValueError, match='Cut it into parts'):
        importing.step()
    assert importing.done == 0 and audio_files(session.lib.root) == []


def test_a_long_recording_is_asked_about_first(window, session, dialogs, tmp_path, monkeypatch):
    path = wav(tmp_path / 'talk.wav', 10.0)
    needed = audio_import.memory_needed(10 * RATE, 1)
    monkeypatch.setattr(audio_import, 'physical_memory', lambda: 2 * needed)   # half of it: more than enough, but a lot
    dialogs.confirm_answer = False
    window.import_files([path])
    asked = dialogs.confirmations[-1]
    assert asked.title == 'Import a long recording?' and asked.text.startswith('talk.wav is 10 s long.')
    assert asked.yes == 'Import it' and session.workspace.takes == []
    dialogs.confirm_answer = True
    window.import_files([path])
    finish_batch(session)
    window.flush()
    assert [take.device['name'] for take in session.workspace.takes] == ['talk.wav']


def test_a_short_recording_is_not_asked_about(window, session, dialogs, tmp_path):
    window.import_files([wav(tmp_path / 'hello.wav', 3.0)])
    assert dialogs.confirmations == [] and len(session.workspace.takes) == 1


# ---- importing a piece at a time ---------------------------------------------------------------------------------------

def test_a_long_import_says_how_far_it_has_got_and_stop_leaves_nothing(session, tmp_path, monkeypatch):
    path = wav(tmp_path / 'talk.wav', 30.0)
    monkeypatch.setattr(Importing, 'BLOCK_FRAMES', RATE)                       # a second at a time
    session.batch.clock = Ticking()
    session.batch.import_files([path])
    session.batch.tick()
    assert session.batch.running and 0 < session.batch.part < 0.5
    assert session.bus.status_text.startswith('Importing recordings: ') and '%' in session.bus.status_text
    assert [name for name in audio_files(session.lib.root) if name.endswith('.tmp')]       # being written
    session.batch.cancel()
    assert not session.batch.running and session.workspace.takes == [] and audio_files(session.lib.root) == []
    assert session.bus.status_text == 'Stopped. Imported 0 of 1 recordings.'


def test_a_long_import_comes_in_whole(session, tmp_path, monkeypatch):
    path = wav(tmp_path / 'talk.wav', 30.0, channels=2)
    monkeypatch.setattr(Importing, 'BLOCK_FRAMES', RATE)
    session.batch.clock = Ticking()
    session.batch.import_files([path])
    finish_batch(session)
    take = session.current
    original, _ = sf.read(str(path), dtype='float32')
    assert take is not None and np.array_equal(take.samples, original) and take.seconds == pytest.approx(30.0)
    assert audio_files(session.lib.root) == [take.master_path.name] and not take.unsaved


def test_an_import_works_out_the_mono_mix_as_it_copies(session, tmp_path, monkeypatch):
    """So that the window does not, all at once, when the take is first drawn: 1.5 s for 45 minutes of stereo."""
    path = wav(tmp_path / 'talk.wav', 30.0, channels=2)
    monkeypatch.setattr(Importing, 'BLOCK_FRAMES', RATE)
    importing = Importing(session.lib, session.workspace.ensure_speaker(), path)
    take = importing.run()
    assert 'mono' in take.cache and np.array_equal(take.cache['mono'], ac.to_mono(take.samples))


def slow_disk(monkeypatch):
    """A disk that takes its time to put a file on it: until the returned event is set."""
    gate, real = threading.Event(), store.sync_file
    monkeypatch.setattr(store, 'sync_file', lambda path: (gate.wait(10), real(path)))
    return gate


def test_the_window_does_not_wait_while_the_original_is_closed(session, tmp_path, monkeypatch):
    importing = Importing(session.lib, session.workspace.ensure_speaker(), wav(tmp_path / 'talk.wav', 3.0))
    gate = slow_disk(monkeypatch)
    assert importing.step() is False and importing.waiting          # every block is in; the original is being closed
    assert importing.step() is False
    gate.set()
    take = importing.run()
    assert take.master_path.is_file() and audio_files(session.lib.root) == [take.master_path.name]


def test_stopped_while_the_original_is_closed_nothing_stays(session, tmp_path, monkeypatch):
    importing = Importing(session.lib, session.workspace.ensure_speaker(), wav(tmp_path / 'talk.wav', 3.0))
    gate = slow_disk(monkeypatch)
    assert importing.step() is False and importing.waiting
    threading.Timer(0.05, gate.set).start()
    importing.cancel()
    assert importing.take is None and audio_files(session.lib.root) == []


def test_closing_during_an_import_leaves_nothing_half_written(session, tmp_path, monkeypatch):
    monkeypatch.setattr(Importing, 'BLOCK_FRAMES', RATE)
    session.batch.clock = Ticking()
    session.batch.import_files([wav(tmp_path / 'talk.wav', 30.0)])
    session.batch.tick()
    assert session.close() == []
    assert audio_files(session.lib.root) == []


def test_one_that_cannot_be_read_is_listed_and_the_rest_come_in(session, tmp_path):
    broken = tmp_path / 'broken.wav'
    broken.write_bytes(b'RIFF and nothing that follows it')
    session.batch.import_files([broken, wav(tmp_path / 'fine.wav', 3.0)])
    finish_batch(session)
    assert [take.device['name'] for take in session.workspace.takes] == ['fine.wav']
    notice = session.bus.notices[-1]
    assert notice.title == 'Importing recordings' and '- broken.wav: ' in notice.text


def test_one_too_long_among_several_is_listed_with_what_to_do(session, tmp_path, monkeypatch):
    long_one, short_one = wav(tmp_path / 'lecture.wav', 20.0), wav(tmp_path / 'hello.wav', 2.0)
    monkeypatch.setattr(audio_import, 'physical_memory', lambda: 8 << 20)      # 2 seconds fits, 20 does not
    session.batch.import_files([long_one, short_one])
    finish_batch(session)
    assert [take.device['name'] for take in session.workspace.takes] == ['hello.wav']
    assert '- lecture.wav: it is 20 s long' in session.bus.notices[-1].text


def test_a_folder_out_of_reach_stops_the_import_with_one_message(make_session, tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('a file, not a folder', encoding='utf-8')
    session = make_session(root=blocker)
    session.bus.notices.clear()
    session.batch.import_files([wav(tmp_path / 'one.wav', 2.0), wav(tmp_path / 'two.wav', 2.0)])
    finish_batch(session)
    assert [notice.title for notice in session.bus.notices] == ['Could not import'] and session.workspace.takes == []
    assert blocker.read_text(encoding='utf-8') == 'a file, not a folder'


def test_more_than_a_few_minutes_of_recordings_come_in_with_stop(window, session, tmp_path, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'SHORT_IMPORT_SECONDS', 5.0)
    window.import_files([wav(tmp_path / 'talk.wav', 12.0)])
    window.flush()
    assert session.batch.running and window.status_stop.isVisibleTo(window)
    finish_batch(session)
    window.flush()
    assert [take.device['name'] for take in session.workspace.takes] == ['talk.wav'] and not window.status_stop.isVisibleTo(window)


def test_a_recording_dropped_during_an_import_waits_its_turn(window, session, tmp_path, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'SHORT_IMPORT_SECONDS', 5.0)
    window.import_files([wav(tmp_path / 'talk.wav', 12.0)])
    window.import_files([wav(tmp_path / 'more.wav', 2.0)])
    assert session.bus.status_text == 'Wait for this to finish, or press Stop, before importing more.'
    finish_batch(session)
    assert [take.device['name'] for take in session.workspace.takes] == ['talk.wav']


# ---- splitting a piece at a time ---------------------------------------------------------------------------------------

def test_the_parts_of_a_split_are_written_and_let_go_of(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=three_sentences())
    parts = session.workspace.split_take()
    assert len(parts) == 3 and all(part.master_path.is_file() for part in parts)
    assert [part.__dict__.get('_samples') is None for part in parts] == [False, True, True]     # the first is in front
    original, _ = sf.read(str(take.master_path), dtype='float32')
    first, end = session.workspace.split_ranges(take)[2]
    assert np.array_equal(parts[2].samples, original[first:end])                # read back when it is wanted


def test_a_split_goes_a_part_at_a_time_and_can_be_stopped(session):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=three_sentences())
    session.batch.clock = Ticking()
    session.batch.split(take, session.workspace.split_ranges(take))
    session.batch.tick()
    made = [t for t in session.workspace.takes if 'split' in t.flags]
    assert session.batch.running and len(made) == 1
    assert session.bus.status_text == 'Splitting the take: 1 of 3...'
    session.batch.cancel()
    made = [t for t in session.workspace.takes if 'split' in t.flags]
    assert len(made) == 1 and made[0].master_path.is_file() and session.current is made[0]
    assert take in session.workspace.takes and take.master_path.is_file()
    assert session.bus.status_text.startswith('Stopped after 1 of 3 parts.')


def test_a_split_stopped_before_its_first_part_makes_nothing(window, session, dialogs):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=three_sentences())
    window.flush()
    window.actions.split_take.trigger()
    window.flush()
    assert session.batch.running and window.status_stop.isVisibleTo(window)
    window.status_stop.click()
    window.flush()
    assert session.workspace.takes == [take] and session.bus.status_text == 'The take was not split.'


def test_a_part_that_cannot_be_written_stops_the_split_and_says_why(session, monkeypatch):
    session.workspace.set_purpose('sentence')
    take = record(session, samples=three_sentences())
    masters = take.master_path.parent
    written = []
    real = session.lib.save_master

    def save_twice(slug, part):                 # then the masters folder is a file: the next part has nowhere to go
        path = real(slug, part)
        written.append(path)
        if len(written) == 2:
            moved = masters.with_name('masters-moved')
            masters.rename(moved)
            masters.write_text('in the way', encoding='utf-8')
        return path

    monkeypatch.setattr(session.lib, 'save_master', save_twice)
    session.batch.split(take, session.workspace.split_ranges(take))
    finish_batch(session)
    assert len([t for t in session.workspace.takes if 'split' in t.flags]) == 2
    notice = session.bus.notices[-1]
    assert notice.title == 'Splitting the take' and '- The part at ' in notice.text
    assert session.bus.status_text.startswith('Stopped after 2 of 3 parts.')


# ---- the waveform of a long take ---------------------------------------------------------------------------------------

def test_a_long_takes_waveform_is_worked_out_in_the_background(make_session, monkeypatch):
    held = HeldJobs()
    session = make_session(jobs=held)
    session.workspace.add_speaker('Ann')
    monkeypatch.setattr(measure, 'PYRAMID_AT_ONCE', RATE)          # a second stands for six minutes
    take = record(session, 3.0)
    before = held.pending
    assert session.measure.pyramid(take) is None and session.measure.pyramid(take) is None
    assert held.pending == before + 1                               # asked for once
    held.run(before)
    assert session.measure.pyramid(take).n == take.frames


def test_a_short_takes_waveform_is_there_at_once(session):
    take = record(session, 3.0)
    assert session.measure.pyramid(take).n == take.frames


def test_the_waveform_says_it_is_being_drawn_until_it_is(qapp, make_session, dialogs, monkeypatch):
    from recorder.ui.main_window import MainWindow
    held = HeldJobs()
    session = make_session(jobs=held)
    session.workspace.add_speaker('Ann')
    monkeypatch.setattr(measure, 'PYRAMID_AT_ONCE', RATE)
    window = MainWindow(session=session, dialogs=dialogs)
    try:
        window.timer.stop()
        window.resize(1440, 900)
        window.show()
        take = record(session, 3.0)
        window.flush()
        waveform, minimap = window.editor_panel.waveform, window.editor_panel.minimap
        assert waveform._layer_for(take) is None and minimap._layer_for(take) is None
        assert not waveform.grab().isNull() and not minimap.grab().isNull()     # drawn without it
        index = next(i for i, (fn, _, _) in enumerate(held.queue) if i not in held.ran
                     and held.lanes[i][1] is take and 'PeakPyramid' in fn.__code__.co_names)
        held.run(index)
        window.flush()
        assert waveform._layer_for(take) is not None and minimap._layer_for(take) is not None
    finally:
        window.close()
        session.close()


# ---- the window says it is busy ----------------------------------------------------------------------------------------

def watch(window, owner, name, monkeypatch):
    """Record, as `owner.name` starts, whether the wait cursor is up and what the status line says."""
    from PySide6.QtWidgets import QApplication
    seen = []
    real = getattr(owner, name)

    def watching(*args, **kwargs):
        cursor = QApplication.overrideCursor()
        seen.append((cursor.shape().name if cursor is not None else None, window.status_text.full_text))
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, watching)
    return seen


def test_keeping_a_long_take_shows_the_window_is_busy(window, session, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'LONG_TAKE_SECONDS', 5.0)
    record(session, 12.0)
    fill(session)
    window.flush()
    seen = watch(window, session, 'keep', monkeypatch)
    window.actions.keep.trigger()
    assert seen == [('WaitCursor', 'Keeping the take...')] and QApplication.overrideCursor() is None
    assert session.current.kept


def test_what_stops_a_long_take_being_kept_is_said_after_the_busy_cursor(window, session, dialogs, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'LONG_TAKE_SECONDS', 5.0)
    record(session, 12.0)
    fill(session, consent=False)
    window.flush()
    window.actions.keep.trigger()
    assert dialogs.notices[-1].title == 'Cannot keep yet' and not dialogs.notices[-1].busy


def test_playing_a_long_take_shows_the_window_is_busy_and_pausing_does_not(window, session, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'LONG_TAKE_SECONDS', 5.0)
    record(session, 12.0)
    window.flush()
    seen = watch(window, session.transport, 'toggle', monkeypatch)
    window.actions.play.trigger()
    assert session.transport.playing and seen[-1][0] == 'WaitCursor'
    window.actions.play.trigger()
    assert not session.transport.playing and seen[-1][0] is None


def test_the_busy_line_goes_once_the_work_is_done(window, session, dialogs, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'LONG_TAKE_SECONDS', 5.0)
    session.workspace.set_purpose('sentence')
    record(session, samples=three_sentences())
    window.flush()
    before = window.status_text.full_text
    seen = watch(window, session.workspace, 'split_ranges', monkeypatch)
    dialogs.confirm_answer = False
    window.actions.split_take.trigger()
    assert seen == [('WaitCursor', 'Looking for the pauses...')]
    assert window.status_text.full_text == before and not session.batch.running


def test_opening_a_long_take_from_the_list_shows_the_window_is_busy(window, session, monkeypatch):
    from recorder.ui import main_window
    monkeypatch.setattr(main_window, 'LONG_TAKE_SECONDS', 5.0)
    older = record(session, 12.0, source='system')
    record(session, 12.0)
    window.flush()
    older.let_go()                                                  # as a take does that has not been looked at for a while
    seen = watch(window, session.workspace, 'open_take', monkeypatch)
    window._take_picked(older)
    assert seen == [('WaitCursor', 'Opening the take...')] and session.current is older and 'mono' in older.cache


def test_a_short_take_is_quick_enough_to_need_no_busy_cursor(window, session, monkeypatch):
    record(session, 12.0)
    fill(session)
    window.flush()
    seen = watch(window, session, 'keep', monkeypatch)
    window.actions.keep.trigger()
    assert seen[0][0] is None and session.current.kept
