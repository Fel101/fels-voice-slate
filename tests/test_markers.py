"""Markers: moments on a take's original timeline, dropped while listening or while recording, gone back to with a key, and
kept with the take and in its sidecar, where a kept clip's markers change without the clip changing."""
import json

import numpy as np
import pytest

from helpers import RATE, fill, record, speech
from recorder import capture as cap
from recorder.store import MARKERS_MAX, Library, Take, clean_markers

KEEPABLE = {'transcript': 'The harbour lights came on at dusk.', 'origin': 'recorded in my room',
            'allowed_use': 'my own voice references', 'consent_confirmed': True}


# ---- what a marker is -------------------------------------------------------------------------------------------------

def test_markers_are_moments_to_the_millisecond_each_once_in_order_inside_the_recording():
    assert clean_markers([3.0004, 1.25, 3.0, -0.5, 12.5, float('nan'), 'four', True, None, 2], seconds=12.0) == \
        [1.25, 2.0, 3.0]
    assert clean_markers(None) == [] and clean_markers([0.0, 12.0], seconds=12.0) == [0.0, 12.0]
    assert len(clean_markers(range(MARKERS_MAX + 50))) == MARKERS_MAX


# ---- dropping them, and going to them ---------------------------------------------------------------------------------

def test_m_drops_a_marker_at_the_playhead_and_on_a_marker_takes_it_away(session):
    take = record(session, 12.0)
    editor = session.editor
    editor.set_playhead(4.2)
    editor.toggle_marker()
    editor.toggle_marker(9.87654)
    assert take.meta['markers'] == [4.2, 9.877]
    assert session.status_text.startswith('Marker 2 of 2 at 9.88 s')
    editor.toggle_marker(4.23)                          # near enough to the first: it is taken away
    assert take.meta['markers'] == [9.877] and 'taken away' in session.status_text
    editor.toggle_marker(4.3)
    assert take.meta['markers'] == [4.3, 9.877]


def test_the_keys_go_from_marker_to_marker_and_say_where_they_are(session):
    take = record(session, 12.0)
    take.meta['markers'] = [2.0, 5.5, 9.0]
    editor = session.editor
    assert editor.marker_beside(0.0, True) == 2.0
    assert editor.marker_beside(2.0, True) == 5.5            # from on a marker to the next, not the same one
    assert editor.marker_beside(5.5, False) == 2.0
    assert editor.marker_beside(9.0, True) is None and editor.marker_beside(2.0, False) is None
    editor.went_to_marker(5.5, True)
    assert session.status_text == 'Marker 2 of 3, at 5.50 s.'
    editor.went_to_marker(None, True)
    assert session.status_text == 'There is no marker after the playhead.'


def test_markers_do_not_make_a_kept_clip_changed_and_go_into_its_sidecar_alone(session, clock):
    from test_session_autosave import settle
    take = record(session, 12.0)
    fill(session)
    session.keep()
    wav = take.export_path.read_bytes()
    before = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    session.editor.toggle_marker(3.5)
    assert not take.changed
    settle(session, clock)
    after = json.loads(take.sidecar_path.read_text(encoding='utf-8'))
    assert after.pop('markers') == [{'seconds': 3.5}] and after == before
    assert take.export_path.read_bytes() == wav and not take.changed
    session.editor.toggle_marker(3.5)
    settle(session, clock)
    assert 'markers' not in json.loads(take.sidecar_path.read_text(encoding='utf-8'))


# ---- while recording ----------------------------------------------------------------------------------------------------

def test_a_marker_dropped_while_recording_stays_with_the_take(session, open_fake, clock):
    session.capturing.start()
    fake = open_fake[0]                                 # it records two seconds
    fake.recording = True
    fake.elapsed = 1.6
    assert session.capturing.add_marker() is True
    assert session.status_text == 'Marker 1 at 1.6 s. It stays with the take.'
    fake.elapsed = 0.5
    session.capturing.add_marker()
    fake.elapsed = 2.4                                  # in a tail cut short: past the end of what was recorded
    session.capturing.add_marker()
    session.capturing.stop()
    clock.advance(1.0)
    session.tick()
    [take] = session.workspace.takes
    assert take.meta['markers'] == [0.5, 1.6]
    assert session.capturing.markers == []


def test_the_markers_of_one_recording_do_not_reach_the_next(session, open_fake, clock):
    for elapsed in (0.5, None):
        session.capturing.start()
        fake = open_fake[-1]
        fake.recording = True
        if elapsed is not None:
            fake.elapsed = elapsed
            session.capturing.add_marker()
        session.capturing.stop()
        clock.advance(1.0)
        session.tick()
    newest, older = session.workspace.takes
    assert older.meta['markers'] == [0.5] and newest.meta['markers'] == []


def test_nothing_is_marked_when_nothing_is_being_recorded(session):
    assert session.capturing.add_marker() is False and session.capturing.markers == []


def test_the_time_of_a_marker_while_recording_counts_the_half_second_from_before_record():
    class Backend:
        kind, name, hostapi, rate, channels = 'mic', 'fake microphone', 'test', 48000, 1

        def open(self, on_block):
            self.on_block = on_block

        def close(self):
            pass

    backend = Backend()
    capture = cap.Capture(backend, pre_roll_seconds=0.5)
    capture.arm()
    for _ in range(100):                                 # a second while armed: the last half of it is kept
        backend.on_block(np.zeros((480, 1), dtype=np.float32))
    capture.start()
    for _ in range(100):                                 # a second recorded
        backend.on_block(np.zeros((480, 1), dtype=np.float32))
    assert capture.elapsed == pytest.approx(1.5, abs=0.011)
    assert len(capture.stop()) / 48000 == pytest.approx(capture.elapsed, abs=1e-9)


# ---- kept with the take -------------------------------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / 'voice-clips', trash=lambda paths: None)


def saved_take(lib, slug, **meta):
    take = Take(source='mic', samples=speech(12.0), rate=RATE, device={'name': 'test mic'}, speaker=slug, meta=dict(meta))
    lib.save_master(slug, take)
    return take


def test_a_take_not_kept_yet_comes_back_with_its_markers(lib):
    slug = lib.add_speaker('Test Speaker')
    take = saved_take(lib, slug)
    take.meta['markers'] = [1.5, 7.25]
    lib.autosave(take)
    assert json.loads(lib.autosave_file(take).read_text(encoding='utf-8'))['markers'] == \
        [{'seconds': 1.5}, {'seconds': 7.25}]
    [back] = Library(lib.root, trash=lib.trash).load_takes(slug)
    assert back.meta['markers'] == [1.5, 7.25]


def test_keep_writes_the_markers_and_a_kept_clip_is_read_back_with_them(lib):
    slug = lib.add_speaker('Test Speaker')
    take = saved_take(lib, slug, **KEEPABLE)
    take.meta['markers'] = [0.75, 11.0]
    sidecar = lib.keep(slug, take)['sidecar']
    assert json.loads(sidecar.read_text(encoding='utf-8'))['markers'] == [{'seconds': 0.75}, {'seconds': 11.0}]
    [back] = lib.load_kept(slug)
    assert back.meta['markers'] == [0.75, 11.0] and not back.changed


def test_a_clip_with_no_markers_has_no_field_and_bad_ones_are_left_out(lib):
    slug = lib.add_speaker('Test Speaker')
    take = saved_take(lib, slug, **KEEPABLE)
    sidecar = lib.keep(slug, take)['sidecar']
    data = json.loads(sidecar.read_text(encoding='utf-8'))
    assert 'markers' not in data
    data['markers'] = [{'seconds': 2.0}, {'seconds': 'soon'}, 5.0, {'seconds': 99.0}, {'at': 3.0}]
    sidecar.write_text(json.dumps(data), encoding='utf-8')
    [back] = lib.load_kept(slug)
    assert back.meta['markers'] == [2.0]


def test_the_markers_of_a_kept_take_changed_since_stay_in_its_sidecar(lib):
    slug = lib.add_speaker('Test Speaker')
    take = saved_take(lib, slug, **KEEPABLE)
    lib.keep(slug, take)
    take.history.cut(1.0, 2.0)
    take.transcript_checked()
    take.meta['markers'] = [4.0]
    lib.autosave(take)
    data = json.loads(lib.autosave_file(take).read_text(encoding='utf-8'))
    data['markers'] = [{'seconds': 8.0}]                 # an autosave file never decides a kept take's markers
    lib.autosave_file(take).write_text(json.dumps(data), encoding='utf-8')
    [back] = Library(lib.root, trash=lib.trash).load_takes(slug)
    assert back.changed and back.meta['markers'] == [4.0]


def test_a_part_of_a_split_take_keeps_the_markers_inside_it_on_its_own_timeline(session):
    take = record(session, 12.0)
    take.meta['markers'] = [1.0, 6.5, 11.0]
    part = session.workspace.split_part(take, int(5.0 * RATE), int(9.0 * RATE))
    assert part.meta['markers'] == [1.5]


# ---- in the window ------------------------------------------------------------------------------------------------------

def test_the_keys_drop_and_find_markers_in_the_window(window, session):
    pytest.importorskip('PySide6')
    take = record(session, 12.0)
    window.flush()
    a = window.actions
    assert a.marker.isEnabled() and not a.next_marker.isEnabled()
    assert [k.toString() for k in a.marker.shortcuts()] == ['M']
    assert [k.toString() for k in a.next_marker.shortcuts()] == ['Alt+Right']
    session.editor.set_playhead(3.0)
    a.marker.trigger()
    session.editor.set_playhead(8.0)
    a.marker.trigger()
    window.flush()
    assert take.meta['markers'] == [3.0, 8.0] and a.next_marker.isEnabled()
    session.editor.set_playhead(0.0)
    a.next_marker.trigger()
    assert session.editor.playhead == 3.0
    a.next_marker.trigger()
    assert session.editor.playhead == 8.0 and session.status_text == 'Marker 2 of 2, at 8.00 s.'
    a.prev_marker.trigger()
    assert session.editor.playhead == 3.0
    from recorder.ui.palette import PALETTE
    wave = window.editor_panel.waveform                          # the first marker's numbered flag, at the top of the clip
    flag = wave.grab().toImage().pixelColor(int(wave.x_of(3.0)) + 3, int(wave.plot_rect().top()) + 9)
    assert flag.name() == PALETTE['amber']
    for view in (window.editor_panel.spectrogram, window.editor_panel.minimap):
        assert not view.grab().isNull()


def test_the_marker_key_works_while_recording_and_the_live_view_shows_them(window, session, open_fake):
    pytest.importorskip('PySide6')
    session.capturing.start()
    fake = open_fake[0]
    fake.recording = True
    fake.elapsed = 2.0
    window.flush()
    assert window.actions.marker.isEnabled()
    window.actions.marker.trigger()
    window._tick()
    window.timer.stop()
    assert session.capturing.markers == [2.0]
    assert window.editor_panel.live.wave.markers == [2.0]


def test_the_shortcuts_list_has_the_marker_keys(qapp):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QLabel

    from recorder.ui.dialogs import ShortcutsDialog
    dialog = ShortcutsDialog()
    text = ' '.join(label.text() for label in dialog.findChildren(QLabel))
    assert 'Marker' in text and 'Next marker' in text and 'Alt+Right' in text
