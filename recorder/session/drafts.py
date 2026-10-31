"""Transcript drafts from the optional speech recognizer, run in the background.

A draft is only ever a suggestion. It fills an empty transcript, or replaces a typed one only when the person asked
for that and has not touched the text since asking. A result for a take that has since been asked again is ignored.

While the transcript is still the recognizer's own text it follows the edits: once the cutting pauses, the draft is made
again from the edited audio, so the words match the clip. Text that was typed or corrected is never replaced that way.
"""
import json
import time

from .. import settings, transcript
from ..clean import CleanError
from ..core.edits import WORD_OPS
from .measure import frozen

FOLLOW_DELAY_SECONDS = 1.5          # how long the cuts must stay still before the draft is made again


def words_key(history):
    """What decides the spoken words of a clip: its cuts and silences. Level, filters and fades do not change them."""
    return json.dumps([[e['op'], e['start_seconds'], e['end_seconds']] for e in history.edits if e['op'] in WORD_OPS])


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
        language = self._language()
        self.bus.status('Drafting the transcript from the audio. This takes a few seconds...')
        self.bus.notify('meta')                 # the Clip tab shows that a draft is on its way

        def work():
            return transcript.suggest(export, python, model, language)

        self.jobs.submit(
            work,
            lambda result: self.receive(take, result['text'], None, replace, request, result.get('tidied', False)),
            lambda error: self.receive(take, None, str(error) if isinstance(error, transcript.DraftUnavailable)
                                       else f'Transcript draft failed: {error}', replace, request))
        return True

    def receive(self, take, text, error, replace=False, request=None, tidied=False):
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
        unchanged = replace and (request is None or take.meta['transcript'] == request[1])
        if unchanged or not take.meta['transcript'].strip():
            take.meta['transcript'] = text
            take.meta['transcript_status'] = 'asr_draft'
            take.draft_key = request[2] if request is not None else words_key(take.history)
            if take.draft_key == words_key(take.history):
                take.needs_transcript_review = False        # made from the clip as it is now: it matches the cuts
            self.bus.status('Draft transcript filled in. Listen to the clip and correct any wrong words before Keep.'
                            + (' A repeated ending was removed.' if tidied else ''))
        else:
            self.bus.status('Draft ready. Your typed transcript was kept; use Suggest from audio to replace it.')
        self.bus.notify('meta', take)           # the take whose text changed, which may not be the one in front
        return True

    # ---- following the edits ------------------------------------------------------------------------------------------

    def edited(self):
        """The recipe changed. If the words in the clip are no longer the words the draft was made from, and the
        transcript is still the recognizer's own, ask for a new draft once the editing pauses."""
        take = self.workspace.current
        if take is None or take.draft_key is None or take.draft_key == words_key(take.history):
            return
        if self.machine_text(take) and self.available():
            self._due = (take, self.clock() + FOLLOW_DELAY_SECONDS)

    def tick(self):
        if self._due is None:
            return
        take, due = self._due
        if self.clock() < due:
            return
        self._due = None
        if take is self.workspace.current and take.draft_key != words_key(take.history) and self.machine_text(take):
            self.request(take, replace=True, automatic=True)
