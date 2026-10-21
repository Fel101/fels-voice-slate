"""Playback: the edited clip and the original, from the playhead, with pause, seek, loop, A/B and the join check."""
import pytest

from helpers import RATE, record


@pytest.fixture
def cut_take(session):
    """A 10 s take with its first two seconds cut: master 2 to 10 s plays as edited 0 to 8 s."""
    take = record(session, 10.0)
    take.history.cut(0.0, 2.0)
    return take


def played(player):
    return [entry for entry in player.log if entry[0] == 'play']


def near(value):
    """Cut points snap to a zero crossing, so times agree to a few samples rather than exactly."""
    return pytest.approx(value, abs=0.005)


# ---- play -------------------------------------------------------------------------------------------------------------

def test_play_edited_starts_at_the_playhead_on_the_edited_timeline(session, player, cut_take):
    session.editor.set_playhead(3.0)
    assert session.transport.play(edited=True)
    assert player.rate == RATE and len(player.buffer) == pytest.approx(8 * RATE, abs=2)
    assert played(player)[-1][1] == near(1.0)       # master 3 s is 1 s into the edited clip
    assert session.transport.mode == 'edited' and session.transport.playing


def test_play_original_plays_the_untouched_recording_from_the_playhead(session, player, cut_take):
    session.editor.set_playhead(3.0)
    session.transport.play(edited=False)
    assert len(player.buffer) == 10 * RATE and played(player)[-1][1] == near(3.0)
    assert session.transport.mode == 'original'


def test_the_playhead_follows_the_sound_across_cuts(session, player, cut_take):
    session.editor.set_playhead(0.0)
    session.transport.play(edited=True)
    player.pos = 0.5                                                    # half a second into the edited clip
    assert session.transport.head == near(2.5)                 # master seconds: past the 2 s cut
    assert session.transport.paused is False


def test_nothing_left_to_play_is_reported(session, player):
    take = record(session, 10.0)
    take.history.cut(9.0, 10.0)
    session.editor.set_playhead(9.5)
    assert session.transport.play(edited=True) is False
    assert 'Nothing is left' in session.status_text and played(player) == []
    assert session.transport.mode is None


def test_play_with_no_take_does_nothing(make_session, player):
    s = make_session()
    assert s.transport.play() is False and played(player) == []


# ---- pause, resume, stop ----------------------------------------------------------------------------------------------

def test_space_plays_then_pauses_then_resumes(session, player, cut_take):
    session.transport.toggle()
    assert session.transport.playing and len(played(player)) == 1
    session.transport.toggle()
    assert session.transport.paused and not session.transport.playing
    session.transport.toggle()
    assert session.transport.playing and len(played(player)) == 1       # resumed, not restarted


def test_stop_ends_playback_and_forgets_the_mode(session, player, cut_take):
    session.transport.play()
    session.transport.stop()
    assert session.transport.mode is None and not session.transport.active and session.transport.head is None


def test_stop_when_nothing_plays_is_harmless(session):
    record(session)
    session.transport.stop()
    assert session.transport.mode is None


def test_the_end_of_the_clip_stops_playback_on_the_next_tick(session, player, cut_take):
    session.transport.play()
    player.finished = True
    session.tick()
    assert session.transport.mode is None


def test_opening_another_take_stops_playback(session, player, cut_take):
    session.transport.play()
    record(session, 6.0)
    assert session.transport.mode is None and not player.active


# ---- seeking and looping ----------------------------------------------------------------------------------------------

def test_seeking_while_playing_moves_the_sound_to_the_same_moment(session, player, cut_take):
    session.transport.play()
    session.transport.seek(6.0)
    assert session.editor.playhead == near(6.0)
    assert ('seek', near(4.0)) in player.log                   # master 6 s is 4 s into the edited clip


def test_seeking_when_stopped_only_moves_the_playhead(session, player, cut_take):
    session.transport.seek(6.0)
    assert session.editor.playhead == near(6.0) and not any(e[0] == 'seek' for e in player.log)


def test_looping_a_selection_plays_just_that_range(session, player, cut_take):
    session.editor.set_selection(4.0, 6.0)
    session.transport.toggle_loop()
    session.transport.play()
    loop = played(player)[-1][2]
    assert loop == (near(2.0), near(4.0))             # edited-clip seconds
    session.transport.toggle_loop()
    assert player.loop is None


def test_looping_with_no_selection_loops_the_whole_clip(session, player, cut_take):
    session.transport.toggle_loop()
    session.transport.play()
    assert played(player)[-1][2] == (0.0, near(8.0))


def test_a_loop_follows_a_selection_changed_while_playing(session, player, cut_take):
    session.transport.toggle_loop()
    session.transport.play()
    session.editor.set_selection(5.0, 7.0)
    session.transport.toggle_loop()
    session.transport.toggle_loop()
    assert player.loop == (near(3.0), near(5.0))


# ---- A/B --------------------------------------------------------------------------------------------------------------

def test_ab_switches_between_the_edited_clip_and_the_original_at_the_same_moment(session, player, cut_take):
    session.editor.set_playhead(3.0)
    session.transport.play(edited=True)
    player.pos = 2.0                                                    # 2 s into the edited clip = master 4 s
    session.transport.ab()
    assert session.transport.mode == 'original' and len(player.buffer) == 10 * RATE
    assert ('swap', 10 * RATE, near(4.0)) in player.log
    player.pos = 4.0
    session.transport.ab()
    assert session.transport.mode == 'edited'
    assert ('swap', len(player.buffer), near(2.0)) in player.log


def test_ab_when_nothing_is_playing_chooses_what_space_will_play(session, player, cut_take):
    session.transport.ab()
    assert session.transport.listen == 'original' and 'Space will play the original' in session.status_text
    assert not any(e[0] == 'swap' for e in player.log)
    session.transport.toggle()
    assert session.transport.mode == 'original' and len(player.buffer) == 10 * RATE
    session.transport.stop()
    session.transport.ab()
    assert session.transport.listen == 'edited'


def test_the_listening_choice_switches_live_and_is_remembered(session, player, cut_take):
    session.transport.play()
    session.transport.set_listen('original')
    assert session.transport.mode == 'original' and any(e[0] == 'swap' for e in player.log)
    session.transport.stop()
    session.transport.play()
    assert session.transport.mode == 'original'                   # it stayed on the original
    session.transport.set_listen('edited')
    assert session.transport.mode == 'edited' and session.transport.listen == 'edited'
    with pytest.raises(ValueError):
        session.transport.set_listen('both')


def test_an_edit_made_while_the_edited_clip_plays_continues_with_the_new_render(session, player, clock):
    record(session, 10.0)
    session.transport.play()
    player.pos = 4.0
    session.editor.set_selection(1.0, 2.0)
    session.editor.cut_selection()                                      # the clip is now 1 s shorter
    clock.advance(0.2)
    session.tick()
    swaps = [e for e in player.log if e[0] == 'swap']
    assert swaps and swaps[-1][1] == pytest.approx(9 * RATE, abs=0.02 * RATE)   # minus the 10 ms join overlap
    assert swaps[-1][2] == near(3.0)                 # still at master 4 s, which is now 3 s in


def test_many_edits_in_a_moment_are_heard_as_one_new_render(session, player, clock):
    record(session, 10.0)
    session.transport.play()
    for hz in (60, 80, 100, 120):                                       # a slider being dragged
        session.editor.set_chain('highpass', 'High-pass', merge='hp', enabled=True, hz=float(hz))
        clock.advance(0.03)
        session.tick()
    assert not any(e[0] == 'swap' for e in player.log)                  # not yet: the edits are still arriving
    clock.advance(0.2)
    session.tick()
    session.tick()
    assert len([e for e in player.log if e[0] == 'swap']) == 1


def test_a_pending_render_is_dropped_when_playback_stops(session, player, clock):
    record(session, 10.0)
    session.transport.play()
    session.editor.set_chain('highpass', 'High-pass', enabled=True)
    session.transport.stop()
    clock.advance(1)
    session.tick()
    assert not any(e[0] == 'swap' for e in player.log)


def test_an_edit_made_while_the_original_plays_changes_nothing(session, player):
    record(session, 10.0)
    session.transport.play(edited=False)
    count = len(player.log)
    session.editor.set_selection(1.0, 2.0)
    session.editor.cut_selection()
    assert len(player.log) == count


# ---- checking the joins -----------------------------------------------------------------------------------------------

def test_check_cuts_plays_around_each_join(session, player):
    take = record(session, 12.0)
    take.history.cut(3.0, 4.0)
    take.history.cut(8.0, 9.0)
    assert session.transport.check_cuts()
    assert session.transport.mode == 'joins'
    assert len(player.buffer) == 2 * (2 * RATE + RATE // 2)             # 2 s either side + a half-second gap, twice
    assert session.transport.head is None


def test_check_cuts_with_no_cuts_says_so(session, player):
    record(session, 12.0)
    assert session.transport.check_cuts() is False
    assert 'No cuts' in session.status_text and played(player) == []


def test_the_playhead_cannot_be_followed_or_seeked_during_the_join_check(session, player):
    take = record(session, 12.0)
    take.history.cut(3.0, 4.0)
    session.transport.check_cuts()
    session.transport.seek(5.0)
    assert not any(e[0] == 'seek' for e in player.log)
    session.transport.ab()
    assert session.transport.mode == 'joins'                     # A/B does not apply to the join check
