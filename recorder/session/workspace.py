"""The workspace: speakers, the takes of the session, the take being edited, and keeping or discarding it.

Takes are kept by output folder and speaker for the whole session, so switching speakers or folders never loses a
take that could not be written to disk. The take being edited holds its own text fields (`take.meta`): the window
writes them as they are typed, so nothing arriving later (a draft transcript, say) can overwrite what was entered.
"""
from collections import namedtuple
from pathlib import Path

import numpy as np

from .. import audio_core as ac
from .. import settings
from ..store import Library, Take

Step = namedtuple('Step', 'key label done detail')           # a rule that must be met before Keep
Advice = namedtuple('Advice', 'key text')                     # something worth knowing that does not block Keep
Readiness = namedtuple('Readiness', 'steps ready advice')

META_FIELDS = ('transcript', 'origin', 'allowed_use', 'source_url', 'consent_confirmed', 'notes', 'label')


class Workspace:
    def __init__(self, bus, library, settings_file=None, is_recording=lambda: False):
        self.bus = bus
        self.lib = library
        self.settings_file = settings_file
        self.is_recording = is_recording
        self.speakers = []              # [(slug, name)], sorted by name
        self.speaker = None             # slug of the selected speaker
        self.takes = []                 # the selected speaker's takes in this folder, newest first
        self.current = None
        self.closing = False
        self.on_take_added = None       # called with each new take, once it is listed (the session asks for a draft)
        self._session_takes = {}        # (resolved folder, slug) -> that scope's takes
        self._scope = None

    # ---- speakers -----------------------------------------------------------------------------------------------------

    def refresh_speakers(self, select=None):
        data = self.lib.speakers()
        self.speakers = sorted(((slug, v['name']) for slug, v in data.items()), key=lambda item: item[1])
        slugs = [slug for slug, _ in self.speakers]
        if select in slugs:
            self.speaker = select
        elif self.speaker not in slugs:
            self.speaker = slugs[0] if slugs else None
        self.bus.notify('speakers')

    def name_of(self, slug):
        return dict(self.speakers).get(slug, '')

    def select_speaker(self, slug):
        """Switch speaker. Refused while recording, so each recording keeps the speaker and folder it started with."""
        if self.is_recording():
            self.bus.status('Stop the recording before changing the speaker.')
            self.bus.notify('speakers')
            return False
        self.speaker = slug
        self._speaker_changed()
        return True

    def add_speaker(self, name):
        """Register a speaker by name and select them. Returns the speaker's folder name; raises ValueError if blank."""
        slug = self.lib.add_speaker(name)
        self.refresh_speakers(select=slug)
        self._speaker_changed()
        return slug

    def rename_speaker(self, slug, name):
        """Show a speaker under another name. Their folder and their clips are not touched. Raises ValueError."""
        self.lib.rename_speaker(slug, name)
        self.refresh_speakers(select=self.speaker)

    def ensure_speaker(self):
        return self.add_speaker('My voice')

    def _speaker_changed(self):
        self.load_speaker_takes()
        self.bus.notify('takes')
        if self.takes:
            self.open_take(self.takes[0])
        else:
            self.bus.notify('current')

    def set_root(self, path):
        """Use another output folder. Refused while recording."""
        if self.is_recording():
            self.bus.status('Stop the recording before changing the output folder.')
            return False
        self.lib = Library(path, trash=self.lib.trash)
        settings.update({'root': str(path)}, self.settings_file)
        self.refresh_speakers()
        self._speaker_changed()
        self.bus.notify('settings')
        return True

    # ---- the take list ------------------------------------------------------------------------------------------------

    @staticmethod
    def _identity(take):
        path = take.master_path or (take.device.get('file') if take.device.get('legacy_pending') else None)
        return Path(path).resolve() if path else (take.id, take.created)

    def _remember_takes(self):
        """Retain every speaker's session state, including originals that could not be written."""
        if self._scope is not None:
            self._session_takes[self._scope] = self.takes

    def load_speaker_takes(self):
        """The selected speaker's takes: those held in memory this session plus those on disk, newest first."""
        self._remember_takes()
        self.current = None
        slug = self.speaker
        if not slug:
            self.takes, self._scope = [], None
            return
        self._scope = (self.lib.root.resolve(), slug)
        in_memory = self._session_takes.get(self._scope, [])
        known = {self._identity(t) for t in in_memory}
        extra = [t for t in self.lib.load_takes(slug) if self._identity(t) not in known]
        self.takes = sorted(in_memory + extra, key=lambda t: t.created, reverse=True)
        self._remember_takes()
        notes = []
        if self.lib.skipped:
            detail = '; '.join(f'{name}: {reason}' for name, reason in self.lib.skipped)
            notes.append(f'{len(self.lib.skipped)} saved clip(s) not loaded. Their files were not changed. {detail}')
        unusable = [t.autosave_problem for t in extra if t.autosave_problem]
        if unusable:
            notes.append(f'The saved changes of {len(unusable)} take(s) could not be used, so they show as recorded. '
                         f'The files were not changed. {"; ".join(unusable)}')
        restored = sum(1 for t in extra if t.restored)
        if restored:
            notes.append(f'Picked up your saved edits and text on {restored} take{"s" if restored != 1 else ""}.')
        if notes:
            self.bus.status(' '.join(notes))

    def add_take(self, samples, rate, source, device, flags=(), script_line=None):
        """A finished capture becomes a take: its original goes to the masters folder, then it is listed and opened."""
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            self.bus.notice('warning', 'Nothing captured',
                            'No audio arrived. For system audio, check the device and that something is playing.')
            return None
        limit = int(ac.MAX_SECONDS * rate)
        if len(samples) > limit:
            samples = samples[:limit]
        slug = self.speaker
        take = Take(source=source, samples=samples, rate=int(rate), device=device, flags=list(flags),
                    speaker=slug, unsaved=True,
                    meta={'transcript': script_line or '',
                          'transcript_status': 'read_from_script' if script_line else 'typed',
                          'origin': '', 'allowed_use': '', 'consent_confirmed': False, 'source_url': '',
                          'notes': '', 'script_line': script_line})
        self.takes.insert(0, take)      # own the audio before any registry read, disk write, or window update can fail
        try:
            take.meta.update(self.lib.speaker_defaults(slug))
            self.lib.save_master(slug, take)
        except (OSError, RuntimeError) as e:    # a full disk or a locked folder: keep the take in memory
            # Keep writes the original once the problem is fixed, so the take is not lost.
            take.unsaved = True
            take.save_error = str(e)
        self.bus.notify('takes')
        self.open_take(take)
        if not self.closing and self.on_take_added is not None:
            self.on_take_added(take)
        return take

    def import_file(self, path):
        """Copy a recording from anywhere into the speaker's folder as a take. Returns None, with a notice, on failure."""
        slug = self.speaker or self.ensure_speaker()
        try:
            take = self.lib.import_file(slug, path)
        except Exception as e:                  # unreadable or unsupported file: say so, keep running
            self.bus.notice('error', 'Could not import', f'{Path(path).name}: {e}')
            return None
        self.takes.insert(0, take)
        self.bus.notify('takes')
        self.open_take(take)
        self.bus.status(f'Imported {Path(path).name}. Edit it, then Keep.')
        if self.on_take_added is not None:
            self.on_take_added(take)
        return take

    def open_take(self, take):
        self.current = take
        self.bus.notify('current')

    @staticmethod
    def state_of(take):
        return 'kept' if take.kept else 'not saved to disk' if take.unsaved else 'not kept'

    # ---- the take's text fields ---------------------------------------------------------------------------------------

    def set_meta(self, **fields):
        take = self.current
        if take is None:
            return
        changed = False
        for key, value in fields.items():
            if key not in META_FIELDS:
                raise KeyError(key)
            value = bool(value) if key == 'consent_confirmed' else ('' if value is None else str(value))
            if take.meta.get(key) != value:
                take.meta[key] = value
                changed = True
        if changed:
            self.bus.notify('meta', take)

    def confirm_transcript(self):
        take = self.current
        if take is not None and take.needs_transcript_review:
            take.needs_transcript_review = False
            self.bus.notify('meta', take)

    def readiness(self, derived=None):
        """The live "Ready to keep?" list for the current take. `derived` is its measurement, or None while measuring."""
        take = self.current
        if take is None:
            return Readiness([], False, [])
        meta = take.meta
        text = str(meta.get('transcript') or '').strip()
        steps = []
        if derived is None or derived.stats is None:
            detail = 'measuring...' if derived is None else derived.error
            steps.append(Step('length', 'Length 6 to 40 s', False, detail))
            steps.append(Step('audible', 'Not silent', False, detail))
        else:
            seconds, rms = derived.stats['seconds'], derived.stats['rms_dbfs']
            steps.append(Step('length', 'Length 6 to 40 s', ac.MIN_SECONDS <= seconds <= ac.MAX_SECONDS,
                              f'{seconds:.1f} s'))
            steps.append(Step('audible', 'Not silent', rms >= ac.SILENT_DB, f'{rms:.0f} dB'))
        steps.append(Step('transcript', f'Transcript {ac.TRANSCRIPT_MIN} to {ac.TRANSCRIPT_MAX} characters',
                          ac.TRANSCRIPT_MIN <= len(text) <= ac.TRANSCRIPT_MAX, f'{len(text)} characters'))
        if take.history.words_changed:
            steps.append(Step('checked', 'Transcript matches the kept words', not take.needs_transcript_review,
                              'Read it against the edits, then confirm'))
        steps.append(Step('origin', 'Origin entered', bool(str(meta.get('origin') or '').strip()), ''))
        steps.append(Step('allowed_use', 'Allowed use entered', bool(str(meta.get('allowed_use') or '').strip()), ''))
        steps.append(Step('permission', 'Permission confirmed', meta.get('consent_confirmed') is True, ''))
        return Readiness(steps, all(s.done for s in steps), self._advice(take, derived, text))

    @staticmethod
    def _advice(take, derived, text):
        advice = []
        if take.unsaved:
            advice.append(Advice('unsaved', 'The original is only in memory. Keep writes it once the folder is writable.'))
        if derived is not None and derived.stats is not None:
            advice.extend(Advice(issue.code, issue.text) for issue in ac.take_warnings(derived.stats, take.flags)
                          if not issue.blocks)
            speech = next((row for row in derived.report or [] if row.key == 'speech'), None)
            if speech is not None and speech.status != 'ok':
                advice.append(Advice('level', f'Speech is at {speech.value}; the target is '
                                              f'{ac.TARGET_VOICED_DB:.0f} dB. Process has a Normalize button.'))
        draft = str(take.meta.get('draft_text') or '').strip()
        if draft and text == draft:
            advice.append(Advice('draft', 'The transcript is an automatic draft. Listen to the clip and correct any '
                                          'wrong words.'))
        if take.history.chain['denoise']['enabled']:
            advice.append(Advice('denoise', 'Noise cleaning is on. Listen to the result: the voice identity is not '
                                            'checked.'))
        return advice

    # ---- keeping and discarding ---------------------------------------------------------------------------------------

    @staticmethod
    def _transcript_status(take):
        text = take.meta['transcript'].strip()
        line = take.meta.get('script_line')
        if line is not None:
            return 'read_from_script' if text == line.strip() else 'edited'
        draft = str(take.meta.get('draft_text') or '').strip()
        if draft and text == draft:
            return 'asr_draft'
        saved = take.meta.get('saved_transcript')
        if saved is not None and text == saved.strip():
            return take.meta.get('transcript_status', 'typed')
        return 'edited' if take.meta.get('transcript_status') == 'read_from_script' else 'typed'

    def keep(self):
        """Write the current take's export and sidecar. Raises KeepBlocked, CleanError or OSError with the reason."""
        take = self.current
        if take is None:
            return None
        slug = take.speaker or self.speaker
        take.meta['transcript_status'] = self._transcript_status(take)
        paths = self.lib.keep(slug, take)
        self.bus.notify('takes')
        self.bus.notify('meta')
        self.bus.status(f"Kept. Saved to {paths['export']}")
        return paths

    def move_take(self, slug, take=None):
        """File a take (the open one unless another is given) under another speaker. Returns the old files that could
        not be removed, or None if nothing was moved. Raises ValueError or OSError, with the take where it was.

        The take keeps its edits, text and undo history. It leaves this speaker's list and joins the other's, and if it
        was the open take, the next one in the list opens.
        """
        take = take or self.current
        if take is None:
            return None
        if self.is_recording():
            self.bus.status('Stop the recording before moving a take.')
            return None
        index = self.takes.index(take) if take in self.takes else 0
        left = self.lib.move_take(take, slug)
        if take in self.takes:
            self.takes.remove(take)
        there = self._session_takes.setdefault((self.lib.root.resolve(), slug), [])
        there.append(take)
        there.sort(key=lambda t: t.created, reverse=True)
        self._remember_takes()
        was_open = self.current is take
        if was_open:
            self.current = self.takes[min(index, len(self.takes) - 1)] if self.takes else None
        name = self.name_of(slug) or slug
        self.bus.notify('takes')
        if was_open:
            self.bus.notify('current')
        self.bus.status(f'Moved to {name}.' if not left else
                        f'Moved to {name}, but these old files could not be removed: {", ".join(p.name for p in left)}.')
        return left

    def discard(self, permanent=False):
        """Move the current take's files to the Recycle Bin (or delete them) and drop it. Raises TrashUnavailable."""
        take = self.current
        if take is None:
            return False
        self.lib.discard(take, permanent=permanent)
        if take in self.takes:
            self.takes.remove(take)
        self.current = None
        self.bus.notify('takes')
        self.bus.notify('current')
        self.bus.status('Take deleted.' if permanent else 'Take moved to the Recycle Bin.')
        return True

    def close(self):
        """Write every original that is still only in memory, in every folder and for every speaker.

        Returns the failures as text; an empty list means nothing is left only in memory.
        """
        self._remember_takes()
        failures = []
        for (root, slug), takes in self._session_takes.items():
            library = self.lib if self.lib.root.resolve() == root else Library(root, trash=self.lib.trash)
            for take in takes:
                if not take.unsaved:
                    continue
                try:
                    library.save_master(slug, take)
                except (OSError, RuntimeError) as e:
                    take.save_error = str(e)
                    failures.append(f'{slug}: {e}')
        self.bus.notify('takes')
        return failures
