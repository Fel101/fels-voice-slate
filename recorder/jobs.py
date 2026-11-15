"""Background work whose results are applied on the window's thread.

A worker thread runs the function; its result waits in a queue until `drain()` is called from the main thread (the
session's tick does this), so callers never need locks. With `inline=True` the function runs at once in the caller's
thread and its result still waits for `drain()`: the tests use this to stay deterministic.

Jobs run in lanes. Measuring, drawing and advice share the `work` lane and its workers. Draft transcripts have a lane of
their own with one worker, because there is one recognizer and a draft takes seconds: in one queue, ten imported takes'
drafts held up the Check tab and the spectrogram of the take in front for most of a minute. In each lane the jobs for the
take in front (`front`) go first, the rest in the order they came. A job whose `wanted()` says no by the time its turn
comes is dropped without running: a draft for a take that has been discarded, or asked for again since.
"""
import itertools
import queue
import threading

LANES = ('work', 'drafts')


class Jobs:
    def __init__(self, workers=2, inline=False):
        self.inline = inline
        self.on_unhandled = None            # called with the exception of a job that had no on_error
        self.front = lambda: None           # the take in front, whose jobs go first; read from the worker threads
        self._done = queue.Queue()
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._open = 0                      # submitted and not yet delivered
        self._queued = {lane: [] for lane in LANES}
        self._running = {lane: 0 for lane in LANES}
        self._order = itertools.count()
        self._closed = False
        self._threads = []
        if not inline:
            for lane, count in (('work', workers), ('drafts', 1)):
                for number in range(count):
                    thread = threading.Thread(target=self._worker, args=(lane,), name=f'recorder-{lane}-{number}', daemon=True)
                    thread.start()
                    self._threads.append(thread)

    @property
    def pending(self):
        return self._open

    def waiting(self, lane):
        """How many jobs of a lane are queued or running."""
        with self._lock:
            return len(self._queued[lane]) + self._running[lane]

    def submit(self, fn, on_done, on_error=None, lane='work', about=None, wanted=None):
        """Run `fn()` in the background. Later, in drain(): `on_done(result)`, or `on_error(exception)` if it raised.

        `about` is the take the job is for, so that the take in front goes first. `wanted()`, if given, is asked as the job's
        turn comes, on the worker's thread; when it says no the job is dropped and neither callback is called."""
        if lane not in LANES:
            raise ValueError(lane)
        with self._lock:
            self._open += 1
            job = (next(self._order), fn, on_done, on_error, about, wanted)
            if not self.inline:
                self._queued[lane].append(job)
                self._ready.notify_all()
        if self.inline:
            self._run(job)

    def _worker(self, lane):
        while True:
            with self._lock:
                while not self._queued[lane] and not self._closed:
                    self._ready.wait()
                if self._closed:
                    return
                job = self._take_next(self._queued[lane])
                self._running[lane] += 1
            try:
                self._run(job)
            finally:
                with self._lock:
                    self._running[lane] -= 1

    def _take_next(self, jobs):
        """The first job for the take in front, else the oldest. Called with the lock held."""
        try:
            front = self.front()
        except Exception:                   # the window is going away: the order no longer matters
            front = None
        index = next((i for i, job in enumerate(jobs) if front is not None and job[4] is front), 0)
        return jobs.pop(index)

    def _run(self, job):
        _, fn, on_done, on_error, _, wanted = job
        try:
            if wanted is not None and not wanted():
                self._done.put((None, None, False))         # dropped: counted as delivered, with nothing to call
                return
            result = fn()
        except Exception as e:              # reported to the caller, never raised in a worker
            self._done.put((on_error, e, True))
        else:
            self._done.put((on_done, result, False))

    def drain(self, wait=None):
        """Deliver every finished job's result. Call from the main thread. Returns how many were delivered.

        `wait` is how long to wait for the first result when none is ready yet, in seconds; the tests use it instead of
        sleeping."""
        delivered = 0
        while True:
            try:
                if wait is not None and not delivered:
                    callback, value, failed = self._done.get(timeout=wait)
                else:
                    callback, value, failed = self._done.get_nowait()
            except queue.Empty:
                return delivered
            delivered += 1
            with self._lock:
                self._open -= 1
            if callback is not None:
                callback(value)
            elif failed and self.on_unhandled is not None:
                self.on_unhandled(value)

    def close(self):
        """Stop. What is queued is dropped, so closing the window waits for nothing; a job already running finishes on its
        own thread, and its result is never delivered."""
        with self._lock:
            self._closed = True
            for jobs in self._queued.values():
                jobs.clear()
            self._ready.notify_all()
