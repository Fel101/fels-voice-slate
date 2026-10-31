"""Files for the recorder: speakers, original captures, kept clips and their sidecars.

Each original capture is written to `<speaker>/masters/` the moment it is made (Stop, or an imported file), so nothing
depends on keeping it. Keep writes the 24 kHz export WAV and the sidecar JSON beside it. A capture with no sidecar is
not kept yet. New names are never overwritten: a clash gets a numeric suffix. A kept take that is kept again rewrites
only its own export and sidecar. Discarding a take moves its files to the Recycle Bin.

The work on a take that is not kept yet (its cuts, Process settings, text and label) is saved to an autosave file in
`masters/`, beside the original, shortly after each change, so a crash or a close does not lose it. The take comes back
with that work when the program starts again. The autosave file never holds the permission tick: that is never
remembered, and is given again at Keep. Keep deletes the autosave file, because the sidecar holds the work from then on.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import __version__
from . import audio_core as ac
from . import clean
from .core import chain as chain_mod
from .core import render
from .core.edits import History, validate_edits
from .settings import default_root

SCHEMA = 'voice-recorder-clip-v2'
KNOWN_SCHEMAS = ('voice-recorder-clip-v1', SCHEMA)
MASTER_SUBTYPE = 'PCM_24'
STEM_PATTERN = re.compile(r'(\d{8})-(\d{6})_(mic|system|import)_([0-9a-f]{8})\.raw\.wav$')
AUTOSAVE_SCHEMA = 'voice-slate-autosave-v1'
AUTOSAVE_SUFFIX = '.autosave.json'
AUTOSAVE_TEXT_MAX = 20000           # a text field in an autosave file is cut to this when it is read back
TRANSCRIPT_STATUSES = ('typed', 'read_from_script', 'asr_draft', 'edited')
LABEL_MAX = 80


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


def slugify(name):
    return re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')[:48] or 'speaker'


def new_take_id():
    return uuid.uuid4().hex[:8]


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def clean_label(value):
    """A take's label as it is shown and saved: one line, single spaces, no control characters, at most LABEL_MAX long."""
    text = ' '.join(str(value or '').split())
    return ''.join(ch for ch in text if ch.isprintable())[:LABEL_MAX].strip()


def _label_of(data):
    label = data.get('label')
    return clean_label(label) if isinstance(label, str) else ''


# eq=False: a take is compared by identity. The default field-by-field comparison reaches the sample arrays, and
# comparing two takes of different lengths raised ValueError in the window's list lookups.
@dataclass(eq=False)
class Take:
    source: str                       # 'mic', 'system', or 'import'
    samples: np.ndarray               # master capture: (N,) or (N, channels), float32, at `rate`
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

    def __post_init__(self):
        if self.history is None:
            self.history = History(seconds=self.seconds)
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
        return len(self.samples) / self.rate if self.rate else 0.0

    @property
    def mono(self):
        """The master's mono mix, computed once."""
        if 'mono' not in self.cache:
            self.cache['mono'] = ac.to_mono(self.samples)
        return self.cache['mono']

    @property
    def stem(self):
        stamp = dt.datetime.fromisoformat(self.created).strftime('%Y%m%d-%H%M%S')
        return f'{stamp}_{self.source}_{self.id}'

    @property
    def kept(self):
        return self.export_path is not None

    @property
    def join_fade_ms(self):
        return self.history.fade_ms


class Library:
    def __init__(self, root=None, trash=None):
        self.root = Path(root or default_root())
        self.recovery_note = None         # set when speakers.json was damaged and rebuilt, for the window to show
        self.skipped = []                 # (sidecar name, reason) for clips the last load could not use
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

    def speaker_defaults(self, slug):
        """Origin and allowed-use text remembered for a speaker. Consent is never remembered."""
        speaker = self.speakers().get(slug, {})
        return {'origin': speaker.get('origin', ''), 'allowed_use': speaker.get('allowed_use', '')}

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
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(tmp, path)

    # ---- original captures --------------------------------------------------------------------------------------------

    @staticmethod
    def _write_master(path, samples, rate):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        sf.write(str(tmp), np.asarray(samples, dtype=np.float32), int(rate), subtype=MASTER_SUBTYPE, format='WAV')
        os.replace(tmp, path)
        return path

    def save_master(self, slug, take):
        """Write the original capture now, so it survives a crash and is in the speaker's folder straight away."""
        take.master_path = self._write_master(self.folder(slug) / 'masters' / f'{take.stem}.raw.wav',
                                              take.samples, take.rate)
        take.unsaved = False
        take.save_error = ''
        return take.master_path

    def import_file(self, slug, path):
        """Copy a recording from anywhere on disk into the speaker's masters, as a take ready to edit."""
        samples, rate = sf.read(str(path), dtype='float32', always_2d=False)
        # Only the file's name goes in the take, and so into the sidecar that travels with the clip: where the file was
        # on this computer (a user name, say) is nobody else's business.
        take = Take(source='import', samples=samples, rate=int(rate),
                    device={'name': Path(path).name}, flags=['imported'],
                    meta=self.speaker_defaults(slug), speaker=slug)
        self.save_master(slug, take)
        return take

    def discard(self, take, permanent=False):
        """Remove the take's files: to the Recycle Bin, or for good when `permanent`.

        Raises TrashUnavailable, with nothing removed, when the Recycle Bin cannot take them.
        """
        paths = [Path(p) for p in (take.master_path, take.export_path, take.sidecar_path, self.autosave_file(take))
                 if p is not None and Path(p).exists()]
        if paths:
            if permanent:
                for path in paths:
                    path.unlink()
            else:
                self.trash(paths)
        take.master_path = take.export_path = take.sidecar_path = None
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

    def load_unkept(self, slug, path):
        path = Path(path)
        match = STEM_PATTERN.match(path.name)
        samples, rate = sf.read(str(path), dtype='float32', always_2d=False)
        if samples.size == 0:
            raise ValueError('the original contains no audio')
        if match:
            created = dt.datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S').astimezone().isoformat(timespec='seconds')
        else:
            created = now_iso()
        take = Take(source=match.group(3) if match else 'mic', samples=samples, rate=int(rate),
                    flags=['recovered'], id=match.group(4) if match else new_take_id(), created=created,
                    meta=self.speaker_defaults(slug), speaker=slug)
        if path.parent.name == '_pending':
            take.device = {'legacy_pending': True}
            take.master_path = None
            take.device.update(file=str(path))
        else:
            take.master_path = path
        self._restore_autosave(take, path)
        return take

    def load_takes(self, slug):
        """Load usable kept and unkept takes, reporting individual damaged files without modifying them."""
        takes = self.load_kept(slug)
        for path in self.list_unkept(slug):
            try:
                takes.append(self.load_unkept(slug, path))
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
                takes.append(self._take_from_sidecar(folder, sidecar, data))
            except SidecarProblem as e:
                self.skipped.append((sidecar.name, str(e)))
            except (OSError, KeyError, TypeError, ValueError):
                self.skipped.append((sidecar.name, 'it is incomplete or holds a value that cannot be read'))
        return takes

    @staticmethod
    def _inside(folder, name):
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

    def _take_from_sidecar(self, folder, sidecar, data):
        try:
            master_name, export_name = data['master']['file'], data['export']['file']
            source, take_id, created = data['source'], data['id'], data['created']
        except (KeyError, TypeError):
            raise SidecarProblem('it is incomplete') from None
        master_path, export_path = self._inside(folder, master_name), self._inside(folder, export_name)
        if not master_path.exists():
            raise SidecarProblem('its master file is missing')
        try:
            samples, rate = sf.read(str(master_path), dtype='float32', always_2d=False)
        except (OSError, RuntimeError):
            raise SidecarProblem('its master file could not be read') from None
        history = self._recipe_from(data, samples, rate, self._chain_from_sidecar)
        label = _label_of(data)
        take = Take(source=source, samples=samples, rate=int(rate), device=data.get('device') or {},
                    flags=list(data.get('flags') or []), id=take_id, created=created, speaker=folder.name,
                    history=history,
                    meta={'transcript': data.get('transcript', ''),
                          'transcript_status': data.get('transcript_status', 'typed'),
                          'origin': data.get('origin', ''), 'allowed_use': data.get('allowed_use', ''),
                          'consent_confirmed': bool(data.get('consent_confirmed')),
                          'source_url': data.get('source_url') or '', 'notes': data.get('notes', ''),
                          'saved_transcript': data.get('transcript', ''), 'label': label, 'saved_label': label})
        take.master_path = master_path
        take.export_path = export_path
        take.sidecar_path = sidecar
        return take

    @staticmethod
    def _recipe_from(data, samples, rate, chain_of):
        """The edit recipe that a sidecar or an autosave file describes, checked against the master it belongs to.

        `chain_of(data)` gives the processing settings. Raises SidecarProblem, saying what is wrong.
        """
        seconds = len(samples) / rate
        edits = data.get('edits') or []
        try:
            validate_edits(edits, seconds)
            ranges = ac.cut_ranges(edits, rate, len(samples), ac.to_mono(samples))
            if not ac.kept_segments(ranges, len(samples)):
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
                or (folder / 'masters' / f'{candidate}.raw.wav').exists():
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
        """What the export WAV would hold right now: the same chain as process_native, at 24 kHz."""
        return render.export_samples(take.samples, take.rate, recipe or take.history, self._denoiser(), self.cache,
                                     take.id)

    def keep(self, slug, take):
        """Write the export and sidecar for a take. Raises KeepBlocked when the take fails the rules."""
        if take.needs_transcript_review:
            raise KeepBlocked(['Confirm that the transcript still matches the words kept after the cuts.'])
        export = self.export_for(take)
        stats = ac.measure(export, ac.EXPORT_RATE)
        problems = ac.keep_problems(take.meta, ac.take_warnings(stats, take.flags))
        if problems:
            raise KeepBlocked(problems)

        folder = self.folder(slug)
        if take.export_path is None:
            if take.master_path is not None and Path(take.master_path).exists():
                stem = Path(take.master_path).name[:-len('.raw.wav')]
            else:
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
            take.export_path = folder / f'{stem}.wav'
            take.sidecar_path = folder / f'{stem}.json'

        take.unsaved = False              # the original is on disk from here on, whether it was just written or already was
        chain = take.history.chain
        flags = set(take.flags) - {'recovered', 'imported'}
        if stats['clipped']:
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
        sidecar = self._sidecar(slug, take, stats, export, sorted(flags))

        export_tmp = take.export_path.with_name(take.export_path.name + '.tmp')
        sf.write(str(export_tmp), export, ac.EXPORT_RATE, subtype='PCM_16', format='WAV')
        os.replace(export_tmp, take.export_path)
        sidecar['export']['sha256'] = hashlib.sha256(take.export_path.read_bytes()).hexdigest()
        sidecar_tmp = take.sidecar_path.with_name(take.sidecar_path.name + '.tmp')
        sidecar_tmp.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(sidecar_tmp, take.sidecar_path)
        take.meta['saved_label'] = clean_label(take.meta.get('label'))
        self.remove_autosave(take)                  # the sidecar holds the work from here on
        self.remember(slug, take.meta['origin'], take.meta['allowed_use'])
        take.save_error = ''
        return {'export': take.export_path, 'master': take.master_path, 'sidecar': take.sidecar_path}

    def _sidecar(self, slug, take, stats, export, flags):
        channels = 1 if take.samples.ndim == 1 else take.samples.shape[1]
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
            'device': take.device,
            'export': {'file': take.export_path.name, 'sample_rate': ac.EXPORT_RATE, 'channels': 1, 'bit_depth': 16,
                       'duration_seconds': round(len(export) / ac.EXPORT_RATE, 3), 'sha256': ''},
            'master': {'file': f'masters/{take.master_path.name}', 'sample_rate': take.rate,
                       'bit_depth': 24, 'channels': channels},
            'measurements': {k: stats[k] for k in ('peak_dbfs', 'rms_dbfs', 'clipped_fraction', 'silence_fraction')},
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
        label = clean_label(take.meta.get('label'))
        if label:                                   # a take with no label has no such field, as before
            sidecar['label'] = label
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
        """Where the work on a take that is not kept yet is saved: beside its original. None while it has no original."""
        master = self._master_file(take)
        return self._autosave_beside(master) if master is not None else None

    def autosave(self, take):
        """Save what has changed on a take since Keep last wrote it. Returns True if a file was written.

        A take that is not kept gets an autosave file beside its original, holding its cuts, Process settings, text and
        label. A kept take has its sidecar already, so only a changed label is written there. A take whose original is
        not on disk (yet, or any more) has nowhere to be saved, and nothing is written. Raises OSError or ValueError
        when the write fails.
        """
        if take.kept:
            return self._save_label(take)
        master = self._master_file(take)
        if master is None or not master.is_file():
            return False
        self._write_json(self._autosave_beside(master), self._autosave_data(take, master))
        return True

    def remove_autosave(self, take):
        """Delete the take's autosave file, if there is one."""
        path = self.autosave_file(take)
        try:
            if path is not None:
                path.unlink(missing_ok=True)
        except OSError:
            pass                                    # a leftover is harmless: a take with a sidecar never reads it

    @staticmethod
    def _autosave_data(take, master):
        meta, recipe = take.meta, take.history.to_json()
        text = {key: str(meta.get(key) or '') for key in ('transcript', 'origin', 'allowed_use', 'source_url', 'notes',
                                                             'draft_text')}
        line = meta.get('script_line')
        return {
            'schema': AUTOSAVE_SCHEMA,
            'software': {'name': 'voice-slate', 'version': __version__},
            'saved': now_iso(),
            'master': {'file': master.name, 'sample_rate': int(take.rate), 'frames': int(len(take.samples))},
            'device': {} if take.device.get('legacy_pending') else take.device,
            'flags': [flag for flag in take.flags if isinstance(flag, str)],
            'edits': recipe['edits'],
            'join_fade_ms': recipe['join_fade_ms'],
            'chain': recipe['chain'],
            'label': clean_label(meta.get('label')),
            **text,
            'transcript_status': str(meta.get('transcript_status') or 'typed'),
            'script_line': line if isinstance(line, str) else None,
            'needs_transcript_review': bool(take.needs_transcript_review),
            'draft_key': take.draft_key if isinstance(take.draft_key, str) else None,
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
        about = data.get('master')
        if not isinstance(about, dict) or about.get('file') != master.name or about.get('frames') != len(take.samples) \
                or about.get('sample_rate') != take.rate:
            raise SidecarProblem('it was saved for a different recording')
        history = self._recipe_from(data, take.samples, take.rate, lambda d: d.get('chain'))
        # Everything is checked before anything is applied, so a file that fails halfway leaves the take as recorded.
        meta = {key: data[key][:AUTOSAVE_TEXT_MAX] for key in
                ('transcript', 'origin', 'allowed_use', 'source_url', 'notes', 'draft_text') if isinstance(data.get(key), str)}
        meta['label'] = _label_of(data)
        meta['transcript_status'] = data['transcript_status'] if data.get('transcript_status') in TRANSCRIPT_STATUSES \
            else 'typed'
        if isinstance(data.get('script_line'), str):
            meta['script_line'] = data['script_line']
        take.history = history
        take.meta.update(meta)
        take.needs_transcript_review = bool(data.get('needs_transcript_review')) and history.words_changed
        take.draft_key = data['draft_key'] if isinstance(data.get('draft_key'), str) else None
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
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(tmp, path)
        take.meta['saved_label'] = label
        return True
