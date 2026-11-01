"""A copy of a kept clip in another format, for sending it to somewhere that wants an MP3, or 48 kHz audio.

The copy is made from the kept clip's own WAV: that file is read, resampled to the rate asked for and encoded. Nothing is
re-rendered from the original recording, so the copy is what was kept and no FFmpeg is needed, and the kept clip itself
is never touched. The encoders are the ones inside SoundFile's libsndfile.
"""
import os
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_core as ac
from .store import RESERVED_NAMES

MP3_KBPS = 128
# kind: (name shown, file ending, SoundFile format, subtype, other settings of the writer)
FORMATS = {
    'wav': ('WAV, 16-bit', '.wav', 'WAV', 'PCM_16', {}),
    'flac': ('FLAC, lossless and smaller', '.flac', 'FLAC', 'PCM_16', {}),
    'mp3': (f'MP3, {MP3_KBPS} kbit/s', '.mp3', 'MP3', 'MPEG_LAYER_III', {'bitrate_mode': 'CONSTANT'}),
    'ogg': ('Ogg Vorbis', '.ogg', 'OGG', 'VORBIS', {'compression_level': 0.3}),
}
RATES = (16000, 22050, ac.EXPORT_RATE, 44100, 48000)
DEFAULT_KIND, DEFAULT_RATE = 'wav', 48000
# The Ogg encoder in libsndfile crashes the whole program (a stack overflow, not an error that can be caught) when it is
# given a clip of half a minute or so in one call. Eight thousand frames at a time is nowhere near the limit.
CHUNK_FRAMES = 8192


class CopyError(OSError):
    """The copy could not be made. The message says why, in words the person can read."""


def rate_text(rate):
    return f'{rate / 1000:g} kHz'


def _mp3_quality(rate):
    """The quality setting that gives MP3_KBPS at this sample rate. libsndfile takes it from 0 (the highest bit rate) to 1
    (the lowest) and uses the nearest bit rate Layer III has: 32 to 320 kbit/s from 32 kHz up, 8 to 160 below that."""
    low, high = (32, 320) if rate >= 32000 else (8, 160)
    return (high - MP3_KBPS) / (high - low)


def filename_for(text, fallback='clip'):
    """A name that Windows accepts as a file name, made from a label or a transcript: the characters it forbids become
    spaces, the length is limited, and a name that is nothing but dots or a reserved device name gets a prefix."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', str(text or ''))
    name = ' '.join(name.split()).strip(' .')[:80].strip(' .')
    if not name:
        return fallback
    return f'_{name}' if name.split('.')[0].upper() in RESERVED_NAMES else name


def save_copy(source, destination, kind=DEFAULT_KIND, rate=DEFAULT_RATE):
    """Write a copy of the kept clip `source` (a WAV file) to `destination`, as `kind` at `rate` Hz, and return the path.

    The file appears whole or not at all, and is never the kept clip itself. Raises CopyError.
    """
    if kind not in FORMATS:
        raise CopyError(f'Copies in {kind!r} are not offered')
    if rate not in RATES:
        raise CopyError(f'A copy at {rate} Hz is not offered')
    source, destination = Path(source), Path(destination)
    try:
        same = destination.exists() and destination.resolve() == source.resolve()
    except OSError:
        same = False
    if same:
        raise CopyError('That is the kept clip itself. Choose another file name.')
    _, _, name, subtype, settings = FORMATS[kind]
    if kind == 'mp3':
        settings = {**settings, 'compression_level': _mp3_quality(rate)}
    tmp = destination.with_name(destination.name + '.tmp')
    try:
        samples, source_rate = sf.read(str(source), dtype='float32', always_2d=False)
        audio = np.clip(ac.resample(ac.to_mono(samples), source_rate, rate), -1.0, 1.0)
        with sf.SoundFile(str(tmp), 'w', samplerate=rate, channels=1, format=name, subtype=subtype, **settings) as out:
            for start in range(0, len(audio), CHUNK_FRAMES):
                out.write(audio[start:start + CHUNK_FRAMES])
        os.replace(tmp, destination)
    except CopyError:
        raise
    except (OSError, RuntimeError) as e:            # SoundFile's own errors are RuntimeErrors
        raise CopyError(f'The copy could not be made: {e}') from e
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return destination
