"""Transcript drafts from the optional speech recognizer, run in the background.

A draft is only ever a suggestion. It fills an empty transcript, or replaces a typed one only when the person asked
for that and has not touched the text since asking. A result for a take that has since been asked again is ignored.

While the transcript is still the recognizer's own text it follows the edits: once the cutting pauses, the draft is made
again from the edited audio, so the words match the clip. Text that was typed or corrected is never replaced that way.

A take read from a script keeps its script line as its transcript, and the draft becomes the reading check: what the
recognizer heard is set against what was written (core/reading.py), and made again after a cut. The times of the words come
back in seconds of the edited clip and are kept in seconds of the master, as the edits are, so they survive undo.
"""
import time

from .. import settings, transcript
from ..clean import CleanError
from ..core import reading, render
from ..core.edits import words_key
from .measure import frozen
from .workspace import reading_of

FOLLOW_DELAY_SECONDS = 1.5          # how long the cuts must stay still before the draft is made again


class Drafts:
    def __init__(self, bus, workspace, jobs, settings_file=None, clock=time.monotonic):
        self.bus = bus
        self.workspace = workspace
        self.jobs = jobs
        self.settings_file = settings_file
        self.clock = clock
        self._due = None                # (take, time) of a draft wanted after the latest edits

    def _recognizer(self):
        data = settings.load(self.settings_file)
        return data.get('asr_python'), data.get('asr_model')

    def _language(self):
        language = settings.load(self.settings_file).get('asr_language')
        return language.strip() if isinstance(language, str) and language.strip() else None

    def available(self):
        return transcript.available(*self._recognizer())

    @staticmethod
    def machine_text(take):
        """True when the transcript is still exactly what the recognizer wrote (nobody typed or corrected it)."""
        text = take.meta['transcript'].strip()
        return bool(text) and text == str(take.meta.get('draft_text') or '').strip()

    def request(self, take, replace=False, automatic=False):
        """Draft the transcript in the background from the edited audio. Filled in when the draft arrives.

        An automatic request (a new take was just added) says nothing when the recognizer is not set up; one the person
        asked for does.
        """
        python, model = self._recognizer()
        if not transcript.available(python, model):
            if not automatic:
                self.bus.status(transcript.NOT_SET_UP)
            return False
        request = (object(), take.meta['transcript'], words_key(take.history))
        try:
            export = self.workspace.lib.export_for(take, frozen(take.history))
        except CleanError as e:                 # cleaning is on and FFmpeg is missing or failed: say so, change nothing
            self.bus.status(f'Transcript draft not started: {e}')
            return False
        take.draft_request = request
        segments = render.segments_of(take.samples, take.rate, take.history.edits)
        language = self._language()
        self.bus.status('Drafting the transcript from the audio. This takes a few seconds...')
        self.bus.notify('meta')                 # the Clip tab shows that a draft is on its way

        rate = take.export_rate

        def work():
            return transcript.suggest(export, python, model, language, rate)

        self.jobs.submit(
            work,
            lambda result: self.receive(take, result['text'], None, replace, request, result.get('tidied', False),
                                        self._on_master(result.get('words'), segments, take.rate)),
            lambda error: self.receive(take, None, str(error) if isinstance(error, transcript.DraftUnavailable)
                                       else f'Transcript draft failed: {error}', replace, request))
        return True

    @staticmethod
    def _on_master(words, segments, rate):
        """Words with times in the edited clip, as (word, start, end) with times on the master's timeline."""
        return [(word, round(render.edited_to_master(start, segments, rate), 3), round(render.edited_to_master(end, segments, rate), 3))
                for word, start, end in words or ()]

    def receive(self, take, text, error, replace=False, request=None, tidied=False, words=None):
        """Apply a finished draft (or its error) to the take it was made for."""
        if request is not None and take.draft_request is not request:
            return False                        # a newer request superseded this result
        if request is not None:
            take.draft_request = None
        if error:
            self.bus.status(error)
            self.bus.notify('meta')
            return False
        if not text:
            self.bus.status('The draft came back empty. Type the transcript instead.')
            self.bus.notify('meta')
            return False
        take.meta['draft_text'] = text
        take.meta['draft_heard'] = request[2] if request is not None else words_key(take.history)
        take.meta['draft_words'] = list(words or ())
        unchanged = replace and (request is None or take.meta['transcript'] == request[1])
        if unchanged or not take.meta['transcript'].strip():
            take.meta['transcript'] = text
            take.meta['transcript_status'] = 'asr_draft'
            take.draft_key = request[2] if request is not None else words_key(take.history)
            if take.draft_key == words_key(take.history):
                take.transcript_checked()                   # made from the clip as it is now: it matches the cuts
            self.bus.status('Draft transcript filled in. Listen to the clip and correct any wrong words before Keep.'
                            + (' A repeated ending was removed.' if tidied else ''))
        else:
            self.bus.status(self._reading_message(take))
        self.bus.notify('meta', take)           # the take whose text changed, which may not be the one in front
        return True

    @staticmethod
    def _reading_message(take):
        check = reading_of(take)
        if check is None:
            return 'Draft ready. Your typed transcript was kept; use Suggest from audio to replace it.'
        count = reading.differences(check)
        if not count:
            return 'Reading check: the line was read as written.'
        return (f'Reading check: {count} place{"s differ" if count != 1 else " differs"} from the script. The Clip tab shows '
                'where. Listen: the recognizer can be wrong too.')

    # ---- following the edits ------------------------------------------------------------------------------------------

    def edited(self):
        """The recipe changed. If the words in the clip are no longer the words the draft was made from, and the
        transcript is still the recognizer's own, ask for a new draft once the editing pauses."""
        take = self.workspace.current
        if take is None or not self.available():
            return
        if self._follows(take):
            self._due = (take, self.clock() + FOLLOW_DELAY_SECONDS)

    def _follows(self, take):
        """True when a new draft is wanted for the words now in the clip: the transcript is the recognizer's own, or the
        draft is the reading check of a script line."""
        if take.draft_key is not None and take.draft_key != words_key(take.history) and self.machine_text(take):
            return True
        return bool(take.meta.get('script_line') and take.meta.get('draft_text')) \
            and take.meta.get('draft_heard') != words_key(take.history)

    def tick(self):
        if self._due is None:
            return
        take, due = self._due
        if self.clock() < due:
            return
        self._due = None
        if take is self.workspace.current and self._follows(take):
            # A script line that was read as written matches the draft word for word, and is still not the recognizer's.
            self.request(take, replace=self.machine_text(take) and not take.meta.get('script_line'), automatic=True)
