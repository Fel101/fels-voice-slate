"""Files for the recorder: speakers, original captures, kept clips and their sidecars.

Each original capture is written to `<speaker>/masters/` the moment it is made (Stop, or an imported file), so nothing
depends on keeping it. Keep writes the export WAV and the sidecar JSON beside it. A capture with no sidecar is
not kept yet. New names are never overwritten: a clash gets a numeric suffix. A kept take that is kept again rewrites
only its own export and sidecar. Discarding a take moves its files to the Recycle Bin. Moving a take to another speaker
copies its files and checks the copies before it removes the old ones.

The work on a take that is not kept yet (its cuts, Process settings, text and label) is saved to an autosave file in
`masters/`, beside the original, shortly after each change, so a crash or a close does not lose it. The take comes back
with that work when the program starts again. The autosave file never holds the permission tick: that is never
remembered, and is given again at Keep. Keep deletes the autosave file, because the sidecar holds the work from then on.
A kept take that is changed again gets one too, until Keep writes the changes or they are dropped. It names the kept
clip it builds on, so one that was left behind by a later Keep is not read.

A speaker can have a signed release on record: the file, when it was signed and when it runs out, in `release/` in their
folder, so that it stays with them. Keep is refused once it has run out, because the statement that goes with every clip
would no longer be true. Deleting a speaker sends their whole folder to the Recycle Bin.

A take being recorded goes to a spool file in `masters/` as it happens (see Spool), so a crash in the middle of a take
costs a moment and not the whole take. Every file that matters is forced to the disk before it is renamed into place.

A take only reads its audio from its original when something asks for it, and the takes not used lately let go of it again
(see _Resident), so a library of thousands of takes opens in a moment and fits in memory.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import threading
import uuid
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import __version__
from . import audio_core as ac
from . import clean
from .core import chain as chain_mod
from .core import render
from .core.edits import History, validate_edits, words_key
from .core.reading import clean_words
from .settings import default_root

SCHEMA = 'voice-recorder-clip-v2'
KNOWN_SCHEMAS = ('voice-recorder-clip-v1', SCHEMA)
MASTER_SUBTYPE = 'PCM_24'
STEM_PATTERN = re.compile(r'(\d{8})-(\d{6})_(mic|system|import)_([0-9a-f]{8})\.raw\.wav$')
AUTOSAVE_SCHEMA = 'voice-slate-autosave-v1'
AUTOSAVE_SUFFIX = '.autosave.json'
SPOOL_SCHEMA = 'voice-slate-spool-v1'
SPOOL_SUFFIX = '.spool'
AUTOSAVE_TEXT_MAX = 20000           # a text field in an autosave file is cut to this when it is read back
TRANSCRIPT_STATUSES = ('typed', 'read_from_script', 'asr_draft', 'edited')
LABEL_MAX = 80
RELEASE_FOLDER = 'release'
RELEASE_FILE = 'release.json'
RELEASE_SCHEMA = 'voice-slate-release-v1'
RELEASE_WARNING_DAYS = 30           # a release this close to running out is mentioned
# What Keep works out afresh each time. Anything else among a take's flags is a fact about how it was recorded.
WORKED_OUT_FLAGS = {'recovered', 'imported', 'clipped', 'edited', 'filtered', 'cleaned', 'level_changed', 'limited'}
RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(10)), *(f'LPT{n}' for n in range(10))}


class KeepBlocked(ValueError):
    """Raised when a take fails the keep rules. `problems` says why, in the words the user sees."""

    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = list(problems)


class SidecarProblem(ValueError):
    """A kept clip's sidecar cannot be used. The message says why, in words the user sees."""


class TrashUnavailable(OSError):
    """The Recycle Bin could not take the files (for example on a network folder). Nothing was deleted."""


def send_to_trash(paths):
    try:
        from send2trash import send2trash
    except ImportError:
        raise TrashUnavailable('The send2trash package is not installed.') from None
    try:
        send2trash([os.path.abspath(p) for p in paths])
    except OSError as e:
        raise TrashUnavailable(str(e)) from e


def file_sha256(path):
    """The SHA-256 of a file's bytes, as the sidecars hold it."""
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def sync_file(path):
    """Put what has been written to a file on the disk now. A file renamed while it is still only in Windows' cache can be
    left empty under its final name by a power cut."""
    with open(path, 'rb+') as f:
        os.fsync(f.fileno())


def _write_audio(path, samples, rate, subtype):
    """Write a WAV file and put it on the disk. SoundFile reports its failures as RuntimeError; this raises OSError."""
    try:
        sf.write(str(path), samples, int(rate), subtype=subtype, format='WAV')
    except RuntimeError as e:
        raise OSError(f'{path.name} could not be written: {e}') from e
    sync_file(path)


def audio_shape(path):
    """(frames, sample rate, channels) of an audio file, with none of its audio read. Raises OSError, or SoundFile's own
    RuntimeError, for a file that cannot be read."""
    info = sf.info(str(path))
    return int(info.frames), int(info.samplerate), int(info.channels)


def _remove(path):
    """Delete a leftover if it is there. Failing to is not worth reporting: it is not anybody's work."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def slugify(name):
    slug = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')[:48] or 'speaker'
    return f'speaker-{slug}' if slug.upper() in RESERVED_NAMES else slug        # Windows keeps these names for devices


def new_take_id():
    return uuid.uuid4().hex[:8]


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def _iso_date(text):
    """A date as YYYY-MM-DD text, checked, or '' for none. Raises ValueError for anything else."""
    text = str(text or '').strip()
    return dt.date.fromisoformat(text).isoformat() if text else ''


def clean_label(value):
    """A take's label as it is shown and saved: one line, single spaces, no control characters, at most LABEL_MAX long."""
    text = ' '.join(str(value or '').split())
    return ''.join(ch for ch in text if ch.isprintable())[:LABEL_MAX].strip()


def _label_of(data):
    label = data.get('label')
    return clean_label(label) if isinstance(label, str) else ''


def _work_of(take):
    """What Keep writes of a take, apart from its label, which reaches the sidecar by its own way. Every card in the list
    asks this on every keystroke, so nothing is copied: a state of the recipe is replaced and never changed in place."""
    history, meta = take.history, take.meta
    text = ('transcript', 'origin', 'allowed_use', 'source_url', 'notes')
    return {'edits': history.edits, 'chain': history.chain, 'fade_ms': history.fade_ms, 'purpose': meta.get('purpose'),
            **{key: str(meta.get(key) or '').strip() for key in text}}


def _split_of(data):
    """Where a part of a split take came from: {'take', 'start_seconds', 'end_seconds'}, or None."""
    found = data.get('split_from')
    if isinstance(found, dict) and isinstance(found.get('take'), str) \
            and all(isinstance(found.get(key), (int, float)) for key in ('start_seconds', 'end_seconds')):
        return {key: found[key] for key in ('take', 'start_seconds', 'end_seconds')}
    return None


def _moves_of(data):
    """The `moves` of a sidecar: where the clip was filed before, as {'from', 'to', 'at'} text. Anything else is dropped."""
    found = data.get('moves')
    if not isinstance(found, list):
        return []
    return [{key: move[key] for key in ('from', 'to', 'at')} for move in found
            if isinstance(move, dict) and all(isinstance(move.get(key), str) for key in ('from', 'to', 'at'))]


class _Resident:
    """Which takes hold their audio in memory, the most recently used last. Past MAX_BYTES the oldest let go of it, along
    with what was worked out from it for drawing, and read it from their original when it is next asked for. The take in
    use always stays, however large, and so does a take whose original is not on disk."""

    MAX_BYTES = 256 << 20
    FROM_THE_AUDIO = ('mono', 'pyramid', 'spectrogram', 'spectrogram_image', 'spectrogram_requested')

    def __init__(self):
        self._lock = threading.Lock()
        self._takes = OrderedDict()             # id of a take -> (a weak reference to it, the bytes it holds)

    def used(self, take, nbytes):
        with self._lock:
            self._takes[id(take)] = (weakref.ref(take), nbytes)    # weak: a discarded take must not stay here for ever
            self._takes.move_to_end(id(take))
            for key, (ref, _) in list(self._takes.items()):
                if ref() is None:
                    del self._takes[key]
            total = sum(held for _, held in self._takes.values())
            for key, (ref, held) in list(self._takes.items()):
                old = ref()
                if total <= self.MAX_BYTES or old is take:
                    break
                if old is not None and old.can_let_go:
                    del self._takes[key]
                    total -= held
                    old.let_go()


_resident = _Resident()


# eq=False: a take is compared by identity. The default field-by-field comparison reaches the sample arrays, and
# comparing two takes of different lengths raised ValueError in the window's list lookups.
@dataclass(eq=False)
class Take:
    source: str                       # 'mic', 'system', or 'import'
    samples: np.ndarray = field(repr=False)   # master capture: (N,) or (N, channels), float32, at `rate`. None: read it from the file
    rate: int
    device: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    id: str = field(default_factory=new_take_id)
    created: str = field(default_factory=now_iso)
    history: History = None           # the edit recipe: operations, chain settings, join fade, and their undo steps
    needs_transcript_review: bool = False
    master_path: Path = None
    export_path: Path = None
    sidecar_path: Path = None
    speaker: str = ''                 # the speaker the take was recorded for, so Keep files it under them
    unsaved: bool = False             # the original could not be written to disk: the take lives in memory only
    save_error: str = ''
    draft_request: object = field(default=None, repr=False)     # the draft being made, if any (see session/drafts.py)
    draft_key: str = field(default=None, repr=False)            # the cuts the current draft transcript was made from
    cache: dict = field(default_factory=dict, repr=False)      # derived data the window reuses: peaks, spectrogram
    restored: bool = field(default=False, repr=False)          # its edits and text came back from an autosave file
    autosave_problem: str = field(default='', repr=False)      # why an autosave file beside its original was not used
    moves: list = field(default_factory=list, repr=False)      # where a kept clip has been filed before: from, to, at
    confirmed_words: str = field(default=None, repr=False)     # the cuts and silences the transcript was checked against
    kept_work: dict = field(default=None, repr=False)          # what the sidecar holds, to tell when a kept take has changed
    kept_sha256: str = field(default=None, repr=False)         # the kept WAV's SHA-256, which names the Keep it came from
    spool: Path = field(default=None, repr=False)              # the recording on disk, until its original is written
    frames: int = 0                   # how long the original is, known without reading it
    channels: int = 1
    in_use: bool = field(default=False, repr=False)            # it is the take in front, so its audio stays in memory

    def __post_init__(self):
        held = self.__dict__.get('_samples')
        if held is not None:                # the fields above were filled in after `samples`, so they are set again here
            _set_samples(self, held)
        if self.history is None:
            self.history = History(seconds=self.seconds)
        self.meta.setdefault('purpose', ac.DEFAULT_PURPOSE)
        self.meta.setdefault('label', '')
        self.meta.setdefault('transcript', '')
        self.meta.setdefault('transcript_status', 'typed')
        self.meta.setdefault('origin', '')
        self.meta.setdefault('allowed_use', '')
        self.meta.setdefault('consent_confirmed', False)
        self.meta.setdefault('source_url', '')
        self.meta.setdefault('notes', '')

    @property
    def seconds(self):
        return self.frames / self.rate if self.rate else 0.0

    @property
    def purpose(self):
        """What the clip is for, which sets its length rules and how it is kept."""
        return ac.purpose_of(self.meta.get('purpose'))

    @property
    def export_rate(self):
        """The sample rate the kept clip has."""
        return self.purpose.rate_for(self.rate)

    @property
    def can_let_go(self):
        """True when the audio can be dropped from memory, because it is not in use and can be read back from the original."""
        master = self._audio_file()
        return not self.unsaved and not self.in_use and master is not None and Path(master).is_file()

    def let_go(self):
        self.__dict__['_samples'] = None
        for key in _Resident.FROM_THE_AUDIO:
            self.cache.pop(key, None)

    def _audio_file(self):
        return self.master_path or (self.device.get('file') if self.device.get('legacy_pending') else None)

    def _read_audio(self):
        path = self._audio_file()
        if path is None:
            raise OSError('The original recording of this take is not on disk.')
        try:
            samples, _ = sf.read(str(path), dtype='float32', always_2d=False)
        except RuntimeError as e:                   # SoundFile's own errors
            raise OSError(f'{Path(path).name} could not be read: {e}') from e
        return samples

    @property
    def mono(self):
        """The master's mono mix, computed once."""
        mono = self.cache.get('mono')               # read once: another thread can drop it (see _Resident)
        if mono is None:
            mono = self.cache['mono'] = ac.to_mono(self.samples)
        return mono

    @property
    def stem(self):
        stamp = dt.datetime.fromisoformat(self.created).strftime('%Y%m%d-%H%M%S')
        return f'{stamp}_{self.source}_{self.id}'

    @property
    def kept(self):
        return self.export_path is not None

    @property
    def changed(self):
        """True when a kept take holds edits or text that its kept clip and sidecar do not have yet."""
        return self.kept and self.kept_work is not None and self.kept_work != _work_of(self)

    @property
    def join_fade_ms(self):
        return self.history.fade_ms

    def transcript_checked(self):
        """The transcript has been compared with the words left in the clip as it is now."""
        self.needs_transcript_review = False
        self.confirmed_words = words_key(self.history)


def _get_samples(take):
    samples = take.__dict__.get('_samples')
    if samples is None:
        samples = take.__dict__['_samples'] = take._read_audio()
    _resident.used(take, samples.nbytes)
    return samples


def _set_samples(take, samples):
    take.__dict__['_samples'] = samples
    if samples is not None:
        take.frames = len(samples)
        take.channels = 1 if samples.ndim == 1 else samples.shape[1]


Take.samples = property(_get_samples, _set_samples)     # after the class: a dataclass cannot have a field and a property


class Spool:
    """A take being recorded, on disk as it happens. If the program stops, the next start makes a take of it.

    The file is one line of JSON saying what it is, then the samples as 32-bit floats. There is no length to fill in at the
    end, so it can be read however it stops. `write` hands the audio to Windows, which is enough if the program crashes;
    `sync` puts it on the disk, which is what a power cut needs.
    """

    def __init__(self, path, about):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, 'wb')
        self._file.write(json.dumps(about, ensure_ascii=False).encode('utf-8') + b'\n')
        self._file.flush()

    def write(self, blocks):
        for block in blocks:
            self._file.write(np.asarray(block, dtype='<f4').tobytes())
        self._file.flush()

    def sync(self):
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self):
        try:
            self._file.close()
        except OSError:
            pass                                    # the audio is in memory: this file is only the copy for a crash

    def remove(self):
        self.close()
        _remove(self.path)


class Relocation:
    """An output folder on its way to a new place, a file at a time. Each file is copied and compared first, and the old files
    go only when every one has arrived, so a whole copy exists at every moment, whatever stops it."""

    def __init__(self, source, target):
        self.source, self.target = Path(source), Path(target)
        source, target = self.source.resolve(), self.target.resolve()
        if target == source or source in target.parents or target in source.parents:
            raise ValueError('The new folder and the old one must not be inside each other.')
        if self.target.exists() and any(self.target.iterdir()):
            raise ValueError('Choose an empty folder: the recordings are not mixed with what is already there.')
        self.files = sorted(path for path in self.source.rglob('*') if path.is_file())
        self.done = []

    def copy(self, path):
        destination = self.target / path.relative_to(self.source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(destination.name + '.tmp')
        try:
            shutil.copyfile(path, tmp)
            sync_file(tmp)
            if file_sha256(tmp) != file_sha256(path):
                raise OSError(f'{path.name} could not be copied exactly')
            os.replace(tmp, destination)
        except BaseException:
            _remove(tmp)
            raise
        self.done.append(path)

    def finish(self):
        """Every file has arrived: remove the old ones and the folders they leave empty. Returns the files that stayed."""
        left = []
        for path in self.files:
            try:
                path.unlink()
            except OSError:
                left.append(path)
        for folder in sorted((p for p in self.source.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                pass
        return left

    def undo(self):
        """Stopped or failed on the way: remove the copies. The old folder was not touched."""
        for path in self.done:
            _remove(self.target / path.relative_to(self.source))
        shutil.rmtree(self.target, ignore_errors=True)


class Library:
    def __init__(self, root=None, trash=None):
        self.root = Path(root or default_root())
        self.recovery_note = None         # set when speakers.json was damaged and rebuilt, for the window to show
        self.skipped = []                 # (sidecar name, reason) for clips the last load could not use
        self.recovered = 0                # recordings the last load made takes of after the program stopped mid-take
        self.cache = render.RenderCache()
        self.trash = trash or send_to_trash

    # ---- speakers -----------------------------------------------------------------------------------------------------

    def _speakers_file(self):
        return self.root / 'speakers.json'

    def speakers(self):
        """The speaker registry. A missing file is rebuilt from the speaker folders. A damaged file is moved aside
        (kept as a backup) and rebuilt the same way, so the window still opens and no clip is lost."""
        try:
            return self._checked_speakers(json.loads(self._speakers_file().read_text(encoding='utf-8')))
        except FileNotFoundError:
            return self._speakers_from_folders()
        except ValueError as e:  # JSON or text decoding failed, or the file is not a speaker registry
            return self._recover_speakers(e)

    @staticmethod
    def _checked_speakers(data):
        """The registry if it has the right shape: speaker folder name -> {"name": ...}. Anything else counts as damage.

        A speaker's key is a folder inside the output folder, so a key that is a path (`..\\x`, `C:\\x`) is refused: the
        file may have come from someone else.
        """
        if not isinstance(data, dict):
            raise ValueError('it is not a list of speakers')
        for slug, speaker in data.items():
            if slug in ('', '.', '..') or slug != Path(slug).name or not isinstance(speaker, dict) \
                    or not isinstance(speaker.get('name'), str):
                raise ValueError(f'the entry {slug!r} is not a speaker')
        return data

    def _speakers_from_folders(self):
        """Speakers rebuilt from the speaker folders on disk: a folder with a masters subfolder is a speaker."""
        found = {}
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                if folder.is_dir() and (folder / 'masters').is_dir():
                    found[folder.name] = {'name': folder.name, 'origin': '', 'allowed_use': ''}
        return found

    def _recover_speakers(self, error):
        path = self._speakers_file()
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup, n = path.with_name(f'speakers.json.damaged-{stamp}'), 2
        while backup.exists():
            backup, n = path.with_name(f'speakers.json.damaged-{stamp}-{n}'), n + 1
        os.replace(path, backup)
        self.recovery_note = (f'speakers.json could not be read ({error}). It was moved to {backup.name} and the '
                              'speakers were rebuilt from their folders. Origin and allowed use need entering again.')
        return self._speakers_from_folders()

    # ---- the signed release of a speaker --------------------------------------------------------------------------------

    def _release_file(self, slug):
        return self.folder(slug) / RELEASE_FOLDER / RELEASE_FILE

    def release(self, slug):
        """What is on record for a speaker: {'file', 'sha256', 'signed', 'expires', 'notes'}, or None. `file` is the name of
        the signed release in their `release` folder, and is empty when only its dates were noted. A record that cannot be
        read is the same as none: it is not something the program made up, and the next save writes a good one."""
        try:
            data = json.loads(self._release_file(slug).read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('schema') != RELEASE_SCHEMA:
                return None
            return {'file': str(data.get('file') or ''), 'sha256': str(data.get('sha256') or ''),
                    'signed': _iso_date(data.get('signed')), 'expires': _iso_date(data.get('expires')),
                    'notes': str(data.get('notes') or '')}
        except (OSError, ValueError):
            return None

    def release_path(self, slug):
        """The signed release of a speaker on disk, or None."""
        record = self.release(slug)
        path = self.folder(slug) / RELEASE_FOLDER / record['file'] if record and record['file'] else None
        return path if path is not None and path.is_file() else None

    def set_release(self, slug, source=None, signed='', expires='', notes=''):
        """Put a speaker's release on record. `source` is a file to copy in, or None to keep the one there. An earlier file
        stays in the folder: a release is not something to lose. Raises ValueError, in words the person can read, for a
        date that is not one, and OSError when the file cannot be copied."""
        if slug not in self.speakers():
            raise ValueError('That speaker is not in this folder')
        try:
            signed, expires = _iso_date(signed), _iso_date(expires)
        except ValueError:
            raise ValueError('Write dates as YYYY-MM-DD') from None
        if signed and expires and expires < signed:
            raise ValueError('The release cannot run out before it was signed')
        record = self.release(slug) or {'file': '', 'sha256': ''}
        folder = self.folder(slug) / RELEASE_FOLDER
        if source:
            source = Path(source)
            name, number = f'{slugify(source.stem)}{source.suffix.lower()}', 2
            while (folder / name).exists():
                name, number = f'{slugify(source.stem)}-{number}{source.suffix.lower()}', number + 1
            self._copy_checked(source, folder / name)
            record = {'file': name, 'sha256': file_sha256(folder / name)}
        self._write_json(folder / RELEASE_FILE, {'schema': RELEASE_SCHEMA, **record, 'signed': signed, 'expires': expires,
                                                 'notes': str(notes or '').strip(), 'recorded': now_iso()})

    def release_ends(self, slug, today=None):
        """(state, date) of a speaker's release: 'none' when it has no end date or there is no release, else 'ok', 'soon'
        (within RELEASE_WARNING_DAYS) or 'over', with the date it runs out."""
        record = self.release(slug)
        if not record or not record['expires']:
            return 'none', ''
        today = today or dt.date.today()
        left = (dt.date.fromisoformat(record['expires']) - today).days
        return ('over' if left < 0 else 'soon' if left <= RELEASE_WARNING_DAYS else 'ok'), record['expires']

    def release_problem(self, slug, today=None):
        """Why clips of this speaker cannot be kept because of their release, or ''."""
        state, date = self.release_ends(slug, today)
        return (f'The release for this voice ran out on {date}. Renew it, and put the new one in Speaker details.'
                if state == 'over' else '')

    # ---- deleting a speaker -----------------------------------------------------------------------------------------------

    def speaker_contents(self, slug):
        """(kept clips, takes not kept) in a speaker's folder, for saying what deleting them would remove."""
        folder = self.folder(slug)
        kept = sum(1 for path in folder.glob('*.json')) if folder.is_dir() else 0
        return kept, max(len(list(folder.glob('masters/*.raw.wav'))) - kept, 0) if folder.is_dir() else 0

    def delete_speaker(self, slug, permanent=False):
        """Remove a speaker and everything in their folder: to the Recycle Bin, or for good when `permanent`. Raises
        TrashUnavailable, with nothing removed, when the Recycle Bin cannot take it."""
        data = self.speakers()
        if slug not in data:
            raise ValueError('That speaker is not in this folder')
        folder = self.folder(slug)
        if folder.exists():
            if permanent:
                shutil.rmtree(folder)
            else:
                self.trash([folder])
        del data[slug]
        self._write_json(self._speakers_file(), data)

    def speaker_defaults(self, slug):
        """Origin and allowed-use text remembered for a speaker. Consent is never remembered."""
        speaker = self.speakers().get(slug, {})
        return {key: speaker[key] if isinstance(speaker.get(key), str) else '' for key in ('origin', 'allowed_use')}

    def add_speaker(self, name):
        name = str(name).strip()
        if not name:
            raise ValueError('Enter a speaker name')
        data = self.speakers()
        for slug, speaker in data.items():          # a speaker renamed since is still found by the name they have now
            if speaker['name'] == name:
                return slug
        base = slugify(name)
        slug, n = base, 2
        while slug in data and data[slug]['name'] != name:
            slug, n = f'{base}-{n}', n + 1
        if slug not in data:
            data[slug] = {'name': name, 'origin': '', 'allowed_use': ''}
            self._write_json(self._speakers_file(), data)
        return slug

    def rename_speaker(self, slug, name):
        """Show a speaker under another name. Their folder keeps its name, so no clip and no sidecar is touched.

        Raises ValueError, in words the person can read, for a blank name, a name another speaker already has, or a
        speaker that is not in this folder.
        """
        name = str(name).strip()
        if not name:
            raise ValueError('Enter a speaker name')
        data = self.speakers()
        if slug not in data:
            raise ValueError('That speaker is not in this folder')
        for other, speaker in data.items():
            if other != slug and speaker['name'].casefold() == name.casefold():
                raise ValueError(f'There is already a speaker called {speaker["name"]}')
        if data[slug]['name'] != name:
            data[slug]['name'] = name
            self._write_json(self._speakers_file(), data)

    def remember(self, slug, origin, allowed_use):
        data = self.speakers()
        if slug in data:
            data[slug].update(origin=str(origin).strip(), allowed_use=str(allowed_use).strip())
            self._write_json(self._speakers_file(), data)

    def folder(self, slug):
        return self.root / slug

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            sync_file(tmp)
            os.replace(tmp, path)
        except BaseException:
            _remove(tmp)                            # a failed write leaves no half-written file behind
            raise

    # ---- original captures --------------------------------------------------------------------------------------------

    @staticmethod
    def _write_master(path, samples, rate):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        try:
            _write_audio(tmp, np.asarray(samples, dtype=np.float32), rate, MASTER_SUBTYPE)
            os.replace(tmp, path)
        finally:
            _remove(tmp)
        return path

    def save_master(self, slug, take):
        """Write the original capture now, so it survives a crash and is in the speaker's folder straight away."""
        take.master_path = self._write_master(self.folder(slug) / 'masters' / f'{take.stem}.raw.wav',
                                              take.samples, take.rate)
        take.unsaved = False
        take.save_error = ''
        self._drop_spool(take)
        return take.master_path

    def import_file(self, slug, path, purpose=ac.DEFAULT_PURPOSE):
        """Copy a recording from anywhere on disk into the speaker's masters, as a take ready to edit."""
        samples, rate = sf.read(str(path), dtype='float32', always_2d=False)
        if samples.size == 0:
            raise ValueError('the file holds no audio')
        if not np.isfinite(samples).all():
            raise ValueError('the file is damaged: some of its samples are not numbers')
        # Only the file's name goes in the take, and so into the sidecar that travels with the clip: where the file was
        # on this computer (a user name, say) is nobody else's business.
        take = Take(source='import', samples=samples, rate=int(rate),
                    device={'name': Path(path).name}, flags=['imported'],
                    meta={**self.speaker_defaults(slug), 'purpose': purpose}, speaker=slug)
        self.save_master(slug, take)
        return take

    def discard(self, take, permanent=False):
        """Remove the take's files: to the Recycle Bin, or for good when `permanent`.

        Raises TrashUnavailable, with nothing removed, when the Recycle Bin cannot take them.
        """
        paths = [Path(p) for p in (take.master_path, take.export_path, take.sidecar_path, take.spool,
                                   self.autosave_file(take)) if p is not None and Path(p).exists()]
        if paths:
            if permanent:
                for path in paths:
                    path.unlink()
            else:
                self.trash(paths)
        take.master_path = take.export_path = take.sidecar_path = take.spool = None
        self.cache.drop(take.id)

    def list_unkept(self, slug):
        """Original captures with no sidecar yet. Includes captures from older builds left in `_pending/`."""
        folder = self.folder(slug)
        found = []
        for sub in ('masters', '_pending'):
            if (folder / sub).is_dir():
                for master in sorted((folder / sub).glob('*.raw.wav')):
                    stem = master.name[:-len('.raw.wav')]
                    if not (folder / f'{stem}.json').exists():
                        found.append(master)
        return found

    def load_unkept(self, slug, path, defaults=None):
        """The take of an original with no sidecar. `defaults` is what speaker_defaults(slug) gives, when it is known."""
        path = Path(path)
        match = STEM_PATTERN.match(path.name)
        frames, rate, channels = audio_shape(path)
        if frames == 0:
            raise ValueError('the original contains no audio')
        if match:
            created = dt.datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S').astimezone().isoformat(timespec='seconds')
        else:
            created = now_iso()
        take = Take(source=match.group(3) if match else 'mic', samples=None, rate=rate, frames=frames, channels=channels,
                    flags=['recovered'], id=match.group(4) if match else new_take_id(), created=created,
                    meta=dict(defaults if defaults is not None else self.speaker_defaults(slug)), speaker=slug)
        if path.parent.name == '_pending':
            take.device = {'legacy_pending': True}
            take.master_path = None
            take.device.update(file=str(path))
        else:
            take.master_path = path
        self._restore_autosave(take, path)
        return take

    def load_takes(self, slug, recover=True):
        """Load usable kept and unkept takes, reporting individual damaged files without modifying them.

        With `recover`, a recording the program was making when it stopped becomes a take first: `self.recovered` says how
        many did. It is off while a recording runs, because that recording's own spool is in the same folder.
        """
        takes = self.load_kept(slug)
        self.recovered = self._recover_spools(slug) if recover else 0
        defaults = self.speaker_defaults(slug)
        for path in self.list_unkept(slug):
            try:
                takes.append(self.load_unkept(slug, path, defaults))
            except (OSError, RuntimeError, ValueError) as e:
                self.skipped.append((path.name, f'its original could not be loaded: {e}'))
        return takes

    # ---- kept clips ---------------------------------------------------------------------------------------------------

    def load_kept(self, slug):
        """Kept takes of a speaker, read back from their sidecars and masters. Each take keeps its edits and transcript.

        A sidecar that cannot be used is skipped, and `self.skipped` lists it with the reason. Its files are not changed.
        """
        folder = self.folder(slug)
        takes = []
        self.skipped = []
        for sidecar in folder.glob('*.json') if folder.is_dir() else []:
            try:
                data = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self.skipped.append((sidecar.name, 'it could not be read'))
                continue
            if not isinstance(data, dict) or data.get('schema') not in KNOWN_SCHEMAS:
                continue
            try:
                take = self._take_from_sidecar(folder, sidecar, data)
            except SidecarProblem as e:
                self.skipped.append((sidecar.name, str(e)))
            except (OSError, KeyError, TypeError, ValueError):
                self.skipped.append((sidecar.name, 'it is incomplete or holds a value that cannot be read'))
            else:
                self._restore_autosave(take, take.master_path)
                takes.append(take)
        return takes

    @staticmethod
    def inside(folder, name):
        """The path a sidecar names, if it lies inside the speaker's folder.

        A sidecar can come from someone else, and Keep and Discard write to and remove the files it names, so an
        absolute path or one that climbs out with `..` is refused.
        """
        if not isinstance(name, str) or not name:
            raise SidecarProblem('it is incomplete')
        path = folder / name
        try:
            path.resolve().relative_to(folder.resolve())
        except (ValueError, OSError):
            raise SidecarProblem('it names a file outside its own folder') from None
        return path

    @staticmethod
    def _check_fields(data):
        """Refuse a sidecar whose fields are not the kinds of value the program works with. A sidecar is a text file that
        can be edited or arrive from elsewhere, and one bad value must cost that clip, not the whole list."""
        source, take_id, created = data['source'], data['id'], data['created']
        if not all(isinstance(value, str) and value for value in (source, take_id, created)):
            raise SidecarProblem('it is incomplete')
        try:
            dt.datetime.fromisoformat(created)
        except ValueError:
            raise SidecarProblem('its creation time cannot be read') from None
        for key in ('transcript', 'origin', 'allowed_use', 'notes', 'source_url'):
            if not isinstance(data.get(key) or '', str):
                raise SidecarProblem(f'its {key} is not text')
        flags = data.get('flags') or []
        if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
            raise SidecarProblem('its flags cannot be read')
        if not isinstance(data.get('device') or {}, dict):
            raise SidecarProblem('its device cannot be read')

    def _take_from_sidecar(self, folder, sidecar, data):
        try:
            master_name, export_name = data['master']['file'], data['export']['file']
            self._check_fields(data)
        except (KeyError, TypeError):
            raise SidecarProblem('it is incomplete') from None
        source, take_id, created = data['source'], data['id'], data['created']
        master_path, export_path = self.inside(folder, master_name), self.inside(folder, export_name)
        if not master_path.exists():
            raise SidecarProblem('its master file is missing')
        try:
            frames, rate, channels = audio_shape(master_path)
        except (OSError, RuntimeError):
            raise SidecarProblem('its master file could not be read') from None
        history = self._recipe_from(data, frames, rate, self._chain_from_sidecar)
        label = _label_of(data)
        status = data.get('transcript_status')
        text = {key: data.get(key) or '' for key in ('transcript', 'origin', 'allowed_use', 'source_url', 'notes')}
        confirmed_at = data.get('consent_confirmed_at')
        take = Take(source=source, samples=None, rate=rate, frames=frames, channels=channels, device=data.get('device') or {},
                    flags=list(data.get('flags') or []), id=take_id, created=created, speaker=folder.name,
                    history=history,
                    meta={**text, 'transcript_status': status if status in TRANSCRIPT_STATUSES else 'typed',
                          'purpose': ac.purpose_of(data.get('purpose')).key,
                          'consent_confirmed': bool(data.get('consent_confirmed')),
                          'consent_confirmed_at': confirmed_at if isinstance(confirmed_at, str) else '',
                          'saved_transcript': text['transcript'], 'label': label, 'saved_label': label,
                          'script_line': self._script_line(data, status)})
        if isinstance(data.get('consent_batch'), int):
            take.meta['consent_batch'] = data['consent_batch']
        take.meta['draft_words'] = clean_words(data.get('words'))
        take.meta['split_from'] = _split_of(data)
        take.master_path = master_path
        take.export_path = export_path
        take.sidecar_path = sidecar
        take.moves = _moves_of(data)
        take.kept_sha256 = data['export'].get('sha256')
        take.kept_work = _work_of(take)
        take.confirmed_words = words_key(history)
        return take

    @staticmethod
    def _script_line(data, status):
        """The line of a script a kept clip was read from. A clip kept before this was recorded has only its transcript to
        go on, which is the line when it was read as written."""
        line = data.get('script_line')
        if isinstance(line, str) and line:
            return line
        return data.get('transcript') if status == 'read_from_script' else None

    @staticmethod
    def _recipe_from(data, frames, rate, chain_of):
        """The edit recipe that a sidecar or an autosave file describes, checked against the master it belongs to, which
        has `frames` frames at `rate`. Its audio is not read for this: a list of takes must not have to.

        `chain_of(data)` gives the processing settings. Raises SidecarProblem, saying what is wrong.
        """
        seconds = frames / rate
        edits = data.get('edits') or []
        try:
            validate_edits(edits, seconds)
            if not ac.kept_segments(ac.cut_ranges(edits, rate, frames), frames):
                raise ValueError('the cuts remove the whole clip')
        except ValueError as e:
            raise SidecarProblem(f'its edit list is invalid: {e}') from None
        fade = float(data.get('join_fade_ms', ac.DEFAULT_FADE_MS))
        if not math.isfinite(fade) or not 0 <= fade <= ac.MAX_FADE_MS:
            raise SidecarProblem('its join fade is invalid')
        try:
            chain = chain_mod.normalize(chain_of(data))
        except ValueError as e:
            raise SidecarProblem(f'its processing settings are invalid: {e}') from None
        return History(edits, seconds=seconds, chain=chain, fade_ms=fade)

    @staticmethod
    def _chain_from_sidecar(data):
        """The chain block of a v2 sidecar, or the equivalent of a v1 sidecar's gain and denoise fields."""
        if data.get('schema') == SCHEMA:
            return data.get('chain')
        denoise = data.get('denoise')
        chain = {'level': {'gain_db': float(data.get('gain_db') or 0.0)}}
        if denoise:
            chain['denoise'] = {'enabled': True, 'nf': denoise.get('nf')}
        return chain

    # ---- keeping ------------------------------------------------------------------------------------------------------

    def _unique_stem(self, folder, stem):
        candidate, n = stem, 2
        while (folder / f'{candidate}.wav').exists() or (folder / f'{candidate}.json').exists() \
                or (folder / 'masters' / f'{candidate}.raw.wav').exists() \
                or (folder / 'masters' / f'{candidate}{AUTOSAVE_SUFFIX}').exists():
            candidate, n = f'{stem}-{n}', n + 1
        return candidate

    @staticmethod
    def _denoiser():
        return lambda mono, rate, nf: clean.denoise(mono, rate, nf)     # looked up at call time

    def before_level(self, take, recipe=None):
        """The clip at its own rate with everything applied except gain and the limiter (what Normalize measures).

        `recipe` (anything with .edits, .chain and .fade_ms) defaults to the take's own; a background job passes a
        frozen copy so that edits made meanwhile cannot change what it renders.
        """
        return render.before_level(take.samples, take.rate, recipe or take.history, self._denoiser(), self.cache,
                                   take.id)

    def process_native(self, take, recipe=None):
        """The edited clip at its own rate, mono: regions, cuts, filters, cleaning, gain and limiter."""
        return render.process(take.samples, take.rate, recipe or take.history, self._denoiser(), self.cache, take.id)

    def export_for(self, take, recipe=None):
        """What the export WAV would hold right now: the same chain as process_native, at the rate the clip is kept at."""
        return render.export_samples(take.samples, take.rate, recipe or take.history, self._denoiser(), self.cache,
                                     take.id, take.export_rate)

    def stats_for(self, take, export, recipe=None):
        """What the keep rules judge: the measurements of `export`, and the clipping of the recording it was made from."""
        return {**ac.measure(export, take.export_rate),
                **render.recorded_clipping(take.samples, take.rate, recipe or take.history)}

    def _place_master(self, folder, take):
        """The stem a take is kept under. Its original is written now if it is not on disk yet."""
        if take.master_path is not None and Path(take.master_path).exists():
            return Path(take.master_path).name[:-len('.raw.wav')]
        stem = self._unique_stem(folder, take.stem)
        target = folder / 'masters' / f'{stem}.raw.wav'
        legacy = take.device.get('file') if take.device.get('legacy_pending') else None
        if legacy and Path(legacy).exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, target)
            take.master_path = target
            take.device = {k: v for k, v in take.device.items() if k not in ('legacy_pending', 'file')}
        else:
            take.master_path = self._write_master(target, take.samples, take.rate)
        return stem

    def keep(self, slug, take):
        """Write the export and sidecar for a take.

        Raises KeepBlocked when the take fails the rules, and OSError when a file cannot be written. The take counts as kept
        only once both files are in place, so a Keep that fails leaves it as it was, still being autosaved.
        """
        if take.needs_transcript_review:
            raise KeepBlocked(['Confirm that the transcript still matches the words kept after the cuts.'])
        export = self.export_for(take)
        stats = self.stats_for(take, export)
        purpose = take.purpose
        problems = ac.keep_problems(take.meta, ac.take_warnings(stats, take.flags, purpose))
        if self.release_problem(slug):
            problems.append(self.release_problem(slug))
        if problems:
            raise KeepBlocked(problems)

        folder = self.folder(slug)
        first = take.export_path is None
        if first:
            stem = self._place_master(folder, take)
            export_path, sidecar_path = folder / f'{stem}.wav', folder / f'{stem}.json'
        else:
            export_path, sidecar_path = Path(take.export_path), Path(take.sidecar_path)
        take.unsaved = False              # the original is on disk from here on, whether it was just written or already was
        self._drop_spool(take)
        chain = take.history.chain
        flags = set(take.flags) - WORKED_OUT_FLAGS
        if stats['clipped'] or stats['recording_clipped']:
            flags.add('clipped')
        if take.history.edits:
            flags.add('edited')
        if chain['highpass']['enabled'] or chain['hum']['enabled']:
            flags.add('filtered')
        if chain['denoise']['enabled']:
            flags.add('cleaned')
        if chain['level']['gain_db']:
            flags.add('level_changed')
        if chain['limiter']['enabled']:
            flags.add('limited')
        sidecar = self._sidecar(slug, take, stats, export, sorted(flags), export_path)

        # Both files are finished under temporary names before either goes in place, so anything that fails on the way leaves
        # the take's files as they were.
        export_tmp = export_path.with_name(export_path.name + '.tmp')
        sidecar_tmp = sidecar_path.with_name(sidecar_path.name + '.tmp')
        try:
            _write_audio(export_tmp, export, take.export_rate, purpose.subtype)
            sidecar['export']['sha256'] = file_sha256(export_tmp)
            sidecar_tmp.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            sync_file(sidecar_tmp)
            os.replace(export_tmp, export_path)
            try:
                os.replace(sidecar_tmp, sidecar_path)
            except OSError:
                if first:
                    _remove(export_path)            # a WAV with no sidecar is not a kept clip
                raise
        finally:
            _remove(export_tmp)
            _remove(sidecar_tmp)
        take.export_path, take.sidecar_path = export_path, sidecar_path
        take.kept_sha256 = sidecar['export']['sha256']
        take.kept_work = _work_of(take)
        take.confirmed_words = words_key(take.history)
        take.meta['saved_label'] = clean_label(take.meta.get('label'))
        take.meta['saved_transcript'] = sidecar['transcript']
        self.remove_autosave(take)                  # the sidecar holds the work from here on
        try:
            self.remember(slug, take.meta['origin'], take.meta['allowed_use'])
        except OSError:
            pass                                    # the clip is safe; only the texts offered for the next take are not
        take.save_error = ''
        return {'export': take.export_path, 'master': take.master_path, 'sidecar': take.sidecar_path}

    def _sidecar(self, slug, take, stats, export, flags, export_path):
        text = str(take.meta['transcript']).strip()
        recipe = take.history.to_json()
        denoise = recipe['chain']['denoise']
        if denoise['enabled']:
            denoise.update(filter=f"afftdn=nf={denoise['nf']:g}", identity_checked=False)
        sidecar = {
            'schema': SCHEMA,
            'software': {'name': 'voice-slate', 'version': __version__},
            'id': take.id,
            'created': take.created,
            'speaker': slug,
            'source': take.source,
            'purpose': take.purpose.key,
            'device': take.device,
            'export': {'file': export_path.name, 'sample_rate': take.export_rate, 'channels': 1,
                       'bit_depth': take.purpose.bit_depth,
                       'duration_seconds': round(len(export) / take.export_rate, 3), 'sha256': ''},
            'master': {'file': f'masters/{take.master_path.name}', 'sample_rate': take.rate,
                       'bit_depth': 24, 'channels': take.channels},
            'measurements': {k: stats[k] for k in ('peak_dbfs', 'rms_dbfs', 'clipped_fraction', 'silence_fraction',
                                                   'recording_clipped_fraction')},
            'transcript': text,
            'transcript_status': take.meta['transcript_status'],
            'origin': str(take.meta['origin']).strip(),
            'allowed_use': str(take.meta['allowed_use']).strip(),
            'consent_confirmed': True,
            'source_url': str(take.meta.get('source_url') or '').strip() or None,
            'master_seconds': round(take.seconds, 3),
            'edits': recipe['edits'],
            'join_fade_ms': recipe['join_fade_ms'],
            'chain': recipe['chain'],
            'needs_transcript_review': False,
            'notes': str(take.meta.get('notes') or ''),
            'flags': flags,
        }
        confirmed_at = take.meta.get('consent_confirmed_at')
        if confirmed_at:                            # a clip kept before this was recorded has none: no date is made up
            sidecar['consent_confirmed_at'] = confirmed_at
        if take.meta.get('consent_batch'):          # the statement was made once, for this many takes together
            sidecar['consent_batch'] = take.meta['consent_batch']
        release = self.release(slug)
        if release:                                 # what the permission rests on, when a signed release is on record
            sidecar['release'] = {'file': f"{RELEASE_FOLDER}/{release['file']}" if release['file'] else None,
                                  'sha256': release['sha256'] or None, 'signed': release['signed'] or None,
                                  'expires': release['expires'] or None}
        if take.meta.get('script_line'):
            sidecar['script_line'] = take.meta['script_line']
        if take.meta.get('draft_words'):
            sidecar['words'] = [list(row) for row in take.meta['draft_words']]
        if take.meta.get('split_from'):
            sidecar['split_from'] = take.meta['split_from']
        label = clean_label(take.meta.get('label'))
        if label:                                   # a take with no label has no such field, as before
            sidecar['label'] = label
        if take.moves:                              # nor does a clip that was never moved to another speaker
            sidecar['moves'] = list(take.moves)
        return sidecar

    # ---- work in progress ---------------------------------------------------------------------------------------------

    @staticmethod
    def _master_file(take):
        """The take's original capture on disk, or None while it has none."""
        master = take.master_path or (take.device.get('file') if take.device.get('legacy_pending') else None)
        return Path(master) if master and str(master).endswith('.raw.wav') else None

    @staticmethod
    def _autosave_beside(master):
        return master.with_name(master.name[:-len('.raw.wav')] + AUTOSAVE_SUFFIX)

    def autosave_file(self, take):
        """Where the work on a take is saved until Keep writes it: beside its original. None while it has no original."""
        master = self._master_file(take)
        return self._autosave_beside(master) if master is not None else None

    def autosave(self, take):
        """Save what has changed on a take since Keep last wrote it. Returns True if a file was written.

        A take that is not kept gets an autosave file beside its original, holding its cuts, Process settings, text and
        label. So does a kept take that has been changed since, until Keep writes the changes; its label goes straight into
        its sidecar, and a kept take that has been put back as it was loses the file. A take whose original is not on disk
        (yet, or any more) has nowhere to be saved, and nothing is written. Raises OSError or ValueError when the write
        fails.
        """
        wrote = self._save_label(take) if take.kept else False
        master = self._master_file(take)
        if master is None or not master.is_file():
            return wrote
        if take.kept and not take.changed:
            self.remove_autosave(take)
            return wrote
        self._write_json(self._autosave_beside(master), self._autosave_data(take, master))
        return True

    def remove_autosave(self, take):
        """Delete the take's autosave file, if there is one."""
        path = self.autosave_file(take)
        try:
            if path is not None:
                path.unlink(missing_ok=True)
        except OSError:
            pass                                    # a leftover is harmless: it names a Keep that is not the latest

    @staticmethod
    def _autosave_data(take, master):
        meta, recipe = take.meta, take.history.to_json()
        text = {key: str(meta.get(key) or '') for key in ('transcript', 'origin', 'allowed_use', 'source_url', 'notes',
                                                             'draft_text')}
        line = meta.get('script_line')
        kept = {'kept_sha256': take.kept_sha256} if take.kept else {}
        return {
            'schema': AUTOSAVE_SCHEMA,
            'software': {'name': 'voice-slate', 'version': __version__},
            'saved': now_iso(),
            'master': {'file': master.name, 'sample_rate': int(take.rate), 'frames': int(take.frames)},
            'device': {} if take.device.get('legacy_pending') else take.device,
            'flags': [flag for flag in take.flags if isinstance(flag, str)],
            'edits': recipe['edits'],
            'join_fade_ms': recipe['join_fade_ms'],
            'chain': recipe['chain'],
            'label': clean_label(meta.get('label')),
            'purpose': take.purpose.key,
            **text,
            'transcript_status': str(meta.get('transcript_status') or 'typed'),
            'script_line': line if isinstance(line, str) else None,
            'draft_heard': meta.get('draft_heard'),
            'draft_words': [list(row) for row in meta.get('draft_words') or ()],
            'split_from': meta.get('split_from'),
            'needs_transcript_review': bool(take.needs_transcript_review),
            'confirmed_words': take.confirmed_words,
            'draft_key': take.draft_key if isinstance(take.draft_key, str) else None,
            **kept,
            # No permission tick: it is never remembered, and is given again when the clip is kept.
        }

    def _restore_autosave(self, take, master):
        """Give a take back the work its autosave file holds. A file that cannot be used is left as it is, and the take
        loads as it was recorded, with the reason in `take.autosave_problem`."""
        path = self._autosave_beside(master)
        if not path.is_file():
            return
        try:
            self._apply_autosave(take, master, json.loads(path.read_text(encoding='utf-8')))
        except SidecarProblem as e:
            take.autosave_problem = f'{path.name}: {e}'
        except (OSError, KeyError, TypeError, ValueError):
            take.autosave_problem = f'{path.name}: it is damaged'

    def _apply_autosave(self, take, master, data):
        if not isinstance(data, dict) or data.get('schema') != AUTOSAVE_SCHEMA:
            raise SidecarProblem('it is not an autosave file of this program')
        if take.kept and data.get('kept_sha256') != take.kept_sha256:
            return                                  # left behind by a Keep that failed to delete it: the sidecar is newer
        about = data.get('master')
        if not isinstance(about, dict) or about.get('file') != master.name or about.get('frames') != take.frames \
                or about.get('sample_rate') != take.rate:
            raise SidecarProblem('it was saved for a different recording')
        history = self._recipe_from(data, take.frames, take.rate, lambda d: d.get('chain'))
        # Everything is checked before anything is applied, so a file that fails halfway leaves the take as recorded.
        meta = {key: data[key][:AUTOSAVE_TEXT_MAX] for key in
                ('transcript', 'origin', 'allowed_use', 'source_url', 'notes', 'draft_text') if isinstance(data.get(key), str)}
        meta['transcript_status'] = data['transcript_status'] if data.get('transcript_status') in TRANSCRIPT_STATUSES \
            else 'typed'
        meta['purpose'] = ac.purpose_of(data.get('purpose') or take.meta.get('purpose')).key
        if isinstance(data.get('script_line'), str):
            meta['script_line'] = data['script_line']
        if isinstance(data.get('draft_heard'), str):
            meta['draft_heard'] = data['draft_heard']
        meta['draft_words'] = clean_words(data.get('draft_words')) or take.meta.get('draft_words') or []
        meta['split_from'] = _split_of(data) or take.meta.get('split_from')
        if take.kept:
            # The sidecar's permission was given for what it says. New words for the same clip need it given again.
            if any(meta.get(key, '').strip() != take.meta[key].strip() for key in ('origin', 'allowed_use')):
                take.meta.update(consent_confirmed=False, consent_confirmed_at='')
        else:
            meta['label'] = _label_of(data)         # a kept take's label went straight into its sidecar
        take.history = history
        take.meta.update(meta)
        take.needs_transcript_review = bool(data.get('needs_transcript_review')) and history.words_changed
        confirmed = data.get('confirmed_words')
        take.confirmed_words = confirmed if isinstance(confirmed, str) else \
            None if take.needs_transcript_review else words_key(history)
        take.draft_key = data['draft_key'] if isinstance(data.get('draft_key'), str) else None
        if not take.kept:
            if isinstance(data.get('device'), dict) and not take.device.get('legacy_pending'):
                take.device = data['device']
            flags = data.get('flags')
            if isinstance(flags, list):
                take.flags = ['recovered'] + [f for f in flags if isinstance(f, str) and f != 'recovered']
        take.restored = True

    def _save_label(self, take):
        """Write a kept take's label into its sidecar, leaving everything else in the file as it is."""
        label = clean_label(take.meta.get('label'))
        path = take.sidecar_path
        if label == clean_label(take.meta.get('saved_label')) or path is None or not Path(path).is_file():
            return False
        path = Path(path)
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('the sidecar is not a clip description')
        if label:
            data['label'] = label
        else:
            data.pop('label', None)
        self._write_json(path, data)
        take.meta['saved_label'] = label
        return True

    def revert(self, take):
        """Put a kept take back as its sidecar describes it, dropping the changes made since Keep (and their undo steps).

        Raises SidecarProblem, OSError or ValueError, with the take as it was, when the sidecar cannot be used.
        """
        sidecar = Path(take.sidecar_path)
        kept = self._take_from_sidecar(sidecar.parent, sidecar, json.loads(sidecar.read_text(encoding='utf-8')))
        take.history, take.meta = kept.history, kept.meta
        take.kept_work, take.kept_sha256 = kept.kept_work, kept.kept_sha256
        take.needs_transcript_review, take.confirmed_words = False, kept.confirmed_words
        take.draft_key = take.draft_request = None
        take.restored = False
        self.remove_autosave(take)

    # ---- a take being recorded ----------------------------------------------------------------------------------------

    def open_spool(self, slug, rate, channels, source, device, script_line=None, purpose=ac.DEFAULT_PURPOSE):
        """Start writing a recording to disk as it is made. Returns the Spool. Raises OSError if the file cannot be made."""
        take_id, created = new_take_id(), now_iso()
        stamp = dt.datetime.fromisoformat(created).strftime('%Y%m%d-%H%M%S')
        return Spool(self.folder(slug) / 'masters' / f'{stamp}_{source}_{take_id}{SPOOL_SUFFIX}',
                     {'schema': SPOOL_SCHEMA, 'id': take_id, 'created': created, 'source': source, 'rate': int(rate),
                      'channels': int(channels), 'device': device, 'script_line': script_line, 'purpose': purpose})

    @staticmethod
    def _drop_spool(take):
        """The take's original is on disk, so the copy made while it was recorded is not needed any more."""
        if take.spool is not None:
            _remove(take.spool)
            take.spool = None

    def _take_from_spool(self, slug, path):
        """The take a spool file holds, or None if it stopped before any audio arrived. Raises ValueError for a file that
        cannot be read, and KeyError or TypeError for one that lacks what it should say."""
        with open(path, 'rb') as f:
            about = json.loads(f.readline())
            raw = f.read()
        if not isinstance(about, dict) or about.get('schema') != SPOOL_SCHEMA:
            raise ValueError('it is not a recording of this program')
        rate, channels = int(about['rate']), int(about['channels'])
        if rate <= 0 or channels <= 0:
            raise ValueError('it does not say how it was recorded')
        dt.datetime.fromisoformat(about['created'])
        purpose = ac.purpose_of(about.get('purpose'))
        samples = np.frombuffer(raw, dtype='<f4', count=len(raw) // (4 * channels) * channels).astype(np.float32)
        samples = samples[:int(purpose.record_limit * rate) * channels]
        if samples.size == 0:
            return None
        line = about.get('script_line') if isinstance(about.get('script_line'), str) else None
        return Take(source=about['source'], samples=samples if channels == 1 else samples.reshape(-1, channels), rate=rate,
                    device=about['device'] if isinstance(about.get('device'), dict) else {}, flags=['interrupted'],
                    id=about['id'], created=about['created'], speaker=slug,
                    meta={**self.speaker_defaults(slug), 'transcript': line or '', 'script_line': line,
                          'transcript_status': 'read_from_script' if line else 'typed', 'purpose': purpose.key})

    def _recover_spools(self, slug):
        """Make takes of the recordings the program was making when it stopped. Returns how many it made. A file that cannot
        be used is left where it is and reported in `self.skipped`."""
        masters = self.folder(slug) / 'masters'
        recovered = 0
        for path in sorted(masters.glob(f'*{SPOOL_SUFFIX}')) if masters.is_dir() else []:
            try:
                take = self._take_from_spool(slug, path)
                if take is not None:
                    self.save_master(slug, take)
                    self.autosave(take)             # how it ended, and the script line it was reading
                    recovered += 1
                path.unlink()
            except (OSError, KeyError, TypeError, ValueError) as e:
                self.skipped.append((path.name, f'the recording could not be recovered: {e}'))
        return recovered

    # ---- moving a take to another speaker -----------------------------------------------------------------------------

    def _copy_checked(self, source, destination):
        """Copy a file and check that the copy is the same bytes. The destination appears whole, or not at all."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(destination.name + '.tmp')
        try:
            shutil.copyfile(source, tmp)
            sync_file(tmp)
            if file_sha256(tmp) != file_sha256(source):
                raise OSError(f'{source.name} could not be copied exactly')
            os.replace(tmp, destination)
        except BaseException:
            _remove(tmp)
            raise

    @staticmethod
    def _remove_old(paths):
        """Remove the files a move has left behind, the sidecar first. Returns those that could not be removed.

        If the sidecar itself stays, so does everything else: the old place then still holds a whole clip, which is a
        duplicate of the moved one but not a broken one.
        """
        left = []
        for index, path in enumerate(paths):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                if Path(path).exists():
                    left.append(Path(path))
                if index == 0:
                    left.extend(Path(p) for p in paths[1:] if Path(p).exists())
                    break
        return left

    def _adopt_defaults(self, take, old, slug):
        """A take still carrying the origin and allowed use remembered for its old speaker takes the new speaker's."""
        before, after = self.speaker_defaults(old), self.speaker_defaults(slug)
        for key in ('origin', 'allowed_use'):
            if str(take.meta.get(key) or '').strip() == before[key].strip():
                take.meta[key] = after[key]

    def move_take(self, take, slug):
        """File a take under another speaker. Returns the old files that could not be removed (none, normally).

        The take's files are copied to the new speaker's folder and checked before any old file is removed, so a whole
        copy of the take exists at every moment, whatever fails. A kept clip keeps its permission, origin and allowed
        use; its sidecar is written for the new speaker and gains a `moves` entry saying where it came from. A take that
        is not kept takes the new speaker's remembered origin and allowed use, unless it was given its own.

        Raises ValueError, in words the person can read, when the take cannot be moved, and OSError when a file cannot
        be written. In both cases the take is where it was.
        """
        if slug not in self.speakers():
            raise ValueError('That speaker is not in this folder')
        old = take.speaker
        if not old:
            raise ValueError('This take is not filed under a speaker')
        if slug == old:
            raise ValueError('The take is already filed under that speaker')
        master = self._master_file(take)
        if take.unsaved or master is None or not master.is_file():
            raise ValueError('The original recording of this take is not saved on disk yet, so the take cannot be moved. '
                             'Once the folder is writable, Keep writes it.')
        target = self.folder(slug)
        if take.kept:
            return self._move_kept(take, old, slug, target, master)
        return self._move_unkept(take, old, slug, target, master)

    def _move_unkept(self, take, old, slug, target, master):
        stem = self._unique_stem(target, master.name[:-len('.raw.wav')])
        new_master = target / 'masters' / f'{stem}.raw.wav'
        old_autosave = self._autosave_beside(master)
        self._copy_checked(master, new_master)
        before = (take.master_path, dict(take.device), take.speaker, dict(take.meta))
        try:
            take.master_path = new_master
            take.device = {k: v for k, v in take.device.items() if k not in ('legacy_pending', 'file')}
            take.speaker = slug
            self._adopt_defaults(take, old, slug)
            self.autosave(take)
        except BaseException:
            take.master_path, take.device, take.speaker = before[:3]
            take.meta.clear()
            take.meta.update(before[3])
            for path in (new_master, self._autosave_beside(new_master)):
                path.unlink(missing_ok=True)
            raise
        return self._remove_old([master, old_autosave])

    def _move_kept(self, take, old, slug, target, master):
        export, sidecar = Path(take.export_path), Path(take.sidecar_path) if take.sidecar_path else None
        if sidecar is None or not sidecar.is_file():
            raise ValueError('The saved description of this clip (its sidecar) is missing, so the clip cannot be moved')
        if not export.is_file():
            raise ValueError(f'{export.name} is missing, so the clip cannot be moved')
        try:
            data = json.loads(sidecar.read_text(encoding='utf-8'))
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get('export'), dict) \
                or not isinstance(data.get('master'), dict):
            raise ValueError(f'{sidecar.name} cannot be read as a clip description, so the clip cannot be moved')
        stem = self._unique_stem(target, export.stem)
        new_master, new_export, new_sidecar = (target / 'masters' / f'{stem}.raw.wav', target / f'{stem}.wav',
                                               target / f'{stem}.json')
        made = []
        try:
            for source, copy in ((master, new_master), (export, new_export)):
                self._copy_checked(source, copy)
                made.append(copy)
            data['speaker'] = slug
            data['master']['file'] = f'masters/{new_master.name}'
            data['export']['file'] = new_export.name
            moves = _moves_of(data) + [{'from': old, 'to': slug, 'at': now_iso()}]
            data['moves'] = moves
            self._write_json(new_sidecar, data)
            made.append(new_sidecar)
        except BaseException:
            for path in made:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        old_files = [sidecar, export, master]
        autosave = self.autosave_file(take)
        if autosave is not None:
            old_files.append(autosave)
        take.master_path, take.export_path, take.sidecar_path, take.speaker = new_master, new_export, new_sidecar, slug
        take.moves = moves
        return self._remove_old(old_files)
