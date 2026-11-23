"""Package a kept clip for Voice Lab using exactly the bytes checked for the handoff."""
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import soundfile as sf

from .fels_suite import inbox_dir, inbox_write
from .i18n import tr
from .share import filename_for
from .store import KNOWN_SCHEMAS, RESERVED_NAMES


EXTENSION = '.voiceslate'
VOICE_LAB = 'voice-lab'
MAX_CLIP_BYTES = 32 << 20
MAX_SIDECAR_BYTES = 1 << 20
_DEVICES = (RESERVED_NAMES - {'COM0', 'LPT0'}) | {f'{kind}{digit}' for kind in ('COM', 'LPT') for digit in '¹²³'}
_BAD_PATH = re.compile(r'[:\x00-\x1f]')
_BIT_DEPTH = {'PCM_16': 16, 'PCM_24': 24, 'PCM_32': 32}


class HandoffError(ValueError):
    """The reason the clip could not be sent or saved, with what to try next."""


def inbox_exists(library):
    try:
        return _inbox(library).is_dir()
    except (HandoffError, OSError):
        return False


def _read(path, limit, too_large):
    try:
        with path.open('rb') as source:
            raw = source.read(limit + 1)
    except OSError as error:
        raise HandoffError(tr('A kept file could not be read. Keep the clip again, then try again.')) from error
    if len(raw) > limit:
        raise HandoffError(too_large)
    return raw


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _export_fields(data, wav):
    export, master = data.get('export'), data.get('master')
    name = export.get('file') if isinstance(export, dict) else None
    original = master.get('file') if isinstance(master, dict) else None
    if (not isinstance(name, str) or not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,176}\.wav', name, re.I)
            or name.split('.', 1)[0].rstrip().upper() in _DEVICES or PureWindowsPath(name) != PureWindowsPath(wav.name)):
        raise HandoffError(tr('The clip has an invalid WAV name. Keep it again, then try again.'))
    if not isinstance(original, str) or not original.strip() or _BAD_PATH.search(original):
        raise HandoffError(tr('The clip has an invalid original file reference. Keep it again, then try again.'))
    original = PureWindowsPath(original)
    if original.anchor or not original.parts or '..' in original.parts or original == PureWindowsPath(name):
        raise HandoffError(tr('The clip has an invalid original file reference. Keep it again, then try again.'))
    return export


def _outside_library(path, library):
    if path.resolve().is_relative_to(library.resolve()):
        raise HandoffError(tr('Choose a place outside the output folder for Voice Lab files.'))


def _inbox(library):
    # The shared path helper may create FELS_SUITE_DIR. Check an explicit location before calling it.
    configured = Path(os.environ.get('FELS_SUITE_DIR', '').strip())
    if configured.is_absolute():
        _outside_library(configured, library)
    folder = inbox_dir(VOICE_LAB)
    _outside_library(folder, library)
    return folder


@dataclass(frozen=True)
class Clip:
    name: str
    wav: Path
    sidecar: Path

    @classmethod
    def from_take(cls, take):
        wav = Path(take.export_path)
        label = take.meta.get('saved_label') or wav.stem
        name = filename_for(f'{take.speaker[:24]}-{label}')
        return cls(name, wav, Path(take.sidecar_path) if take.sidecar_path else wav.with_suffix('.json'))

    def read(self):
        wav = _read(self.wav, MAX_CLIP_BYTES, tr('This audition is too long for Voice Lab. Send a reference or sentence '
                                                 'clip, or trim it first.'))
        sidecar = _read(self.sidecar, MAX_SIDECAR_BYTES, tr('The clip details exceed Voice Lab’s 1 MiB limit. Shorten the '
                                                         'clip details and keep it again.'))
        return wav, sidecar

    def check(self):
        wav, sidecar = self.read()
        details_error = tr('The clip details could not be read. Keep the clip again, then try again.')
        try:
            data = json.loads(sidecar.decode('utf-8'))
        except (ValueError, RecursionError) as error:
            raise HandoffError(details_error) from error
        if not isinstance(data, dict) or data.get('schema') not in KNOWN_SCHEMAS:
            raise HandoffError(details_error)
        if data.get('consent_confirmed') is not True:
            raise HandoffError(tr('This clip has no confirmed permission. Check the permission and keep it again.'))
        for field, message in (
            ('transcript', tr('This clip has no transcript. Add one and keep the clip again.')),
            ('origin', tr('This clip has no origin. Enter where it came from and keep it again.')),
            ('allowed_use', tr('This clip has no allowed use. Enter how it may be used and keep it again.')),
        ):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise HandoffError(message)
        clip_id = data.get('id')
        transcript_status = data.get('transcript_status')
        if (not isinstance(clip_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', clip_id)
                or isinstance(transcript_status, (dict, list))):
            raise HandoffError(details_error)
        export = _export_fields(data, self.wav)
        expected = export.get('sha256')
        wav_digest = _digest(wav)
        if not isinstance(expected, str) or expected.lower() != wav_digest:
            raise HandoffError(tr('The kept WAV does not match its clip details. Keep the clip again, then try again.'))
        format_error = tr('The kept WAV format does not match its clip details. Keep the clip again, then try again.')
        try:
            info = sf.info(io.BytesIO(wav))
        except (OSError, RuntimeError) as error:
            raise HandoffError(format_error) from error
        if (info.format != 'WAV' or (info.samplerate, info.channels, _BIT_DEPTH.get(info.subtype)) !=
                (export.get('sample_rate'), export.get('channels'), export.get('bit_depth'))):
            raise HandoffError(format_error)
        return CheckedClip(self, wav_digest, _digest(sidecar), transcript_status == 'asr_draft')


@dataclass(frozen=True)
class CheckedClip:
    clip: Clip
    wav_digest: str
    sidecar_digest: str
    draft: bool

    @property
    def name(self):
        return self.clip.name

    def write(self, library, destination=None):
        """Publish one complete archive. A missing destination means the Voice Lab inbox."""
        wav, sidecar = self.clip.read()
        if (_digest(wav), _digest(sidecar)) != (self.wav_digest, self.sidecar_digest):
            raise HandoffError(tr('The kept clip changed during the handoff. Try again to check the latest version.'))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr('clip.wav', wav)
            bundle.writestr('clip.json', sidecar)
        data = output.getvalue()
        if destination is None:
            try:
                _inbox(library)
                return inbox_write(VOICE_LAB, 'voice-slate', self.name + EXTENSION, data)
            except FileNotFoundError as error:
                raise HandoffError(tr('The Voice Lab inbox is missing. Open Voice Lab or use Save for Voice Lab.')) from error
            except OSError as error:
                raise HandoffError(tr('Could not write to the Voice Lab inbox. Check that it is writable and try again.')) from error
        try:
            _outside_library(destination, library)
            return _save(destination, data)
        except FileExistsError as error:
            raise HandoffError(tr('That file already exists. Choose a different name and try again.')) from error
        except OSError as error:
            raise HandoffError(tr('Could not save the Voice Lab file. Choose a writable folder and try again.')) from error


def _save(destination, data):
    handle, temporary = tempfile.mkstemp(prefix='.voiceslate-', suffix='.part', dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(handle, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if os.name == 'nt':
            os.rename(temporary, destination)  # Windows refuses to replace an existing file with rename.
        else:
            os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
