"""Background work whose results are applied on the window's thread.

A worker thread runs the function; its result waits in a queue until `drain()` is called from the main thread (the
session's tick does this), so callers never need locks. With `inline=True` the function runs at once in the caller's
thread and its result still waits for `drain()`: the tests use this to stay deterministic.
"""
import queue
import threading


class Jobs:
    def __init__(self, workers=2, inline=False):
        self.inline = inline
        self.on_unhandled = None            # called with the exception of a job that had no on_error
        self._work = queue.Queue()
        self._done = queue.Queue()
        self._lock = threading.Lock()
        self._open = 0                      # submitted and not yet delivered
        self._threads = []
        if not inline:
            for number in range(workers):
                thread = threading.Thread(target=self._worker, name=f'recorder-job-{number}', daemon=True)
                thread.start()
                self._threads.append(thread)

    @property
    def pending(self):
        return self._open

    def submit(self, fn, on_done, on_error=None):
        """Run `fn()` in the background. Later, in drain(): `on_done(result)`, or `on_error(exception)` if it raised."""
        with self._lock:
            self._open += 1
        job = (fn, on_done, on_error)
        if self.inline:
            self._execute(*job)
        else:
            self._work.put(job)

    def _worker(self):
        while True:
            job = self._work.get()
            if job is None:
                return
            self._execute(*job)

    def _execute(self, fn, on_done, on_error):
        try:
            result = fn()
        except Exception as e:              # reported to the caller, never raised in a worker
            self._done.put((on_error, e, True))
        else:
            self._done.put((on_done, result, False))

    def drain(self):
        """Deliver every finished job's result. Call from the main thread. Returns how many were delivered."""
        delivered = 0
        while True:
            try:
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
        for _ in self._threads:
            self._work.put(None)
