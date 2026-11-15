"""Draft transcripts have a lane of their own, so measuring and drawing the take in front never wait for them.

The queue here is the real one, with real threads. The recognizer is a stand-in held by an event, as a slow one holds the
real one, and released by the test: nothing sleeps. The tests wait for results with `Jobs.drain(wait=...)`, which returns as
soon as one arrives, and give up after a few seconds so that a fault fails instead of hanging."""
import threading

import pytest

from helpers import record
from recorder import transcript
from recorder.jobs import Jobs

PATIENCE = 10.0                 # seconds to wait for something that should come at once


class HeldRecognizer:
    """Stands in for transcript.suggest. Each draft waits until `release` is set; `asked` counts the drafts begun."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.asked = 0
        self._lock = threading.Lock()

    def __call__(self, clip, python, model, language, rate, worker):
        with self._lock:
            self.asked += 1
        self.started.set()
        if not self.release.wait(PATIENCE):
            raise RuntimeError('the test never released the recognizer')
        return {'text': 'Words from the audio.'}


@pytest.fixture
def held(monkeypatch):
    recognizer = HeldRecognizer()
    monkeypatch.setattr(transcript, 'available', lambda python, model: True)
    monkeypatch.setattr(transcript, 'suggest', recognizer)
    yield recognizer
    recognizer.release.set()                # never leave a worker thread waiting


@pytest.fixture
def threaded(make_session):
    """A session on the real background queue, with a speaker."""
    session = make_session(jobs=Jobs())
    session.workspace.add_speaker('Smoke Speaker')
    return session


def deliver_until(session, done):
    """Deliver results as they arrive until `done()`; fails if nothing arrives for PATIENCE seconds."""
    while not done():
        assert session.jobs.drain(wait=PATIENCE) > 0, 'nothing arrived'


def test_the_take_in_front_is_measured_and_drawn_while_drafts_wait(threaded, held):
    takes = [record(threaded, 3.0) for _ in range(10)]          # each asks for a draft, as an import does
    assert held.started.wait(PATIENCE)                          # the first draft holds the recognizer
    front = threaded.current
    assert front is takes[-1]
    threaded.measure.derived(front)
    threaded.measure.spectrogram(front)
    deliver_until(threaded, lambda: threaded.measure.derived(front) is not None and 'spectrogram' in front.cache)
    assert threaded.measure.derived(front).stats is not None
    assert held.asked == 1 and threaded.drafts.waiting == 10    # one being made, nine queued: one recognizer, one at a time
    held.release.set()
    deliver_until(threaded, lambda: threaded.drafts.waiting == 0 and threaded.jobs.pending == 0)
    assert held.asked == 10 and all(take.meta['transcript'] == 'Words from the audio.' for take in takes)


def test_a_draft_for_a_take_discarded_meanwhile_is_dropped(threaded, held):
    takes = [record(threaded, 3.0) for _ in range(3)]
    assert held.started.wait(PATIENCE)
    threaded.workspace.discard_take(takes[1])                   # queued behind the draft being made
    held.release.set()
    deliver_until(threaded, lambda: threaded.drafts.waiting == 0 and threaded.jobs.pending == 0)
    assert held.asked == 2
    assert takes[0].meta['transcript'] == takes[2].meta['transcript'] == 'Words from the audio.'


def test_closing_does_not_wait_for_the_drafts_and_none_starts_after(threaded, held):
    for _ in range(5):
        record(threaded, 3.0)
    assert held.started.wait(PATIENCE)
    assert threaded.close() == [] and threaded.closed       # returns while a draft is still being made
    held.release.set()
    for thread in threaded.jobs._threads:
        thread.join(PATIENCE)
        assert not thread.is_alive()
    assert held.asked == 1


def test_the_window_says_how_many_drafts_are_waiting(qapp, make_session, dialogs, held):
    import ui_helpers
    from recorder.ui.main_window import MainWindow
    session = make_session(jobs=Jobs())
    session.workspace.add_speaker('Smoke Speaker')
    window = MainWindow(session=session, dialogs=dialogs)
    window.timer.stop()
    try:
        for _ in range(4):
            record(session, 3.0)
        assert held.started.wait(PATIENCE)
        session.tick()
        window.flush()
        assert window.status_drafts.text() == 'Draft transcripts: 4 waiting'
        held.release.set()
        deliver_until(session, lambda: session.drafts.waiting == 0 and session.jobs.pending == 0)
        session.tick()
        window.flush()
        assert window.status_drafts.text() == 'Draft transcripts: ready'
    finally:
        window.close()
        ui_helpers.pump(qapp)


# ---- the queue by itself ---------------------------------------------------------------------------------------------------

def test_among_the_other_jobs_the_take_in_front_goes_first():
    jobs, gate, order = Jobs(workers=1), threading.Event(), []
    front, others = object(), [object() for _ in range(5)]
    jobs.front = lambda: front
    try:
        jobs.submit(lambda: gate.wait(PATIENCE), lambda result: None)          # holds the one worker
        for other in others:
            jobs.submit(lambda other=other: order.append(other), lambda result: None, about=other)
        jobs.submit(lambda: order.append(front), lambda result: None, about=front)
        gate.set()
        while jobs.pending:
            assert jobs.drain(wait=PATIENCE) > 0
        assert order[0] is front and order[1:] == others
    finally:
        jobs.close()


def test_a_job_no_longer_wanted_is_dropped_without_a_word():
    jobs, called = Jobs(workers=1), []
    try:
        jobs.submit(lambda: called.append('ran'), lambda result: called.append('done'), lambda e: called.append('failed'),
                    lane='drafts', wanted=lambda: False)
        while jobs.pending:
            assert jobs.drain(wait=PATIENCE) > 0
        assert called == [] and jobs.waiting('drafts') == 0
    finally:
        jobs.close()


def test_an_unknown_lane_is_refused():
    jobs = Jobs(inline=True)
    with pytest.raises(ValueError):
        jobs.submit(lambda: None, lambda result: None, lane='elsewhere')
