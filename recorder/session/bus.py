"""Messages from the session to whatever shows it: a topic name and an optional payload.

Topics: status, notice, speakers, devices, takes, current, meta, edits, selection, playhead, recording, transport,
analysis, script, settings. A listener refreshes whatever it draws from the topic it hears; the session never
calls into a window.
"""
from collections import namedtuple

Notice = namedtuple('Notice', 'level title text')      # level: info, warning or error


class Bus:
    def __init__(self):
        self.status_text = 'Ready.'
        self.notices = []                       # the most recent notices, for tests and for the status line's detail
        self._listeners = []

    def listen(self, listener):
        self._listeners.append(listener)
        return listener

    def notify(self, topic, payload=None):
        for listener in list(self._listeners):
            listener(topic, payload)

    def status(self, text):
        self.status_text = text
        self.notify('status', text)

    def notice(self, level, title, text):
        notice = Notice(level, title, text)
        self.notices = (self.notices + [notice])[-50:]
        self.notify('notice', notice)
        return notice
