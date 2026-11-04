"""Reading a recording to import.

WAV, FLAC, Ogg, Opus, MP3 and AIFF are read by soundfile. Other formats, such as the M4A that phones and Windows' Sound
Recorder make, or the sound of a phone video, are decoded by FFmpeg, the optional program noise cleaning uses, when it is
installed. FFmpeg writes a temporary WAV, which is read and deleted; the file itself is only read, and nothing is downloaded.

A file in one of soundfile's own formats is never handed to FFmpeg when soundfile refuses it. FFmpeg decodes around
damage, so a damaged WAV would come in with whatever it made of the damage instead of being refused.
"""
import subprocess
import tempfile
from pathlib import Path

import soundfile as sf

from . import clean

SOUNDFILE_SUFFIXES = ('.wav', '.flac', '.ogg', '.opus', '.mp3', '.aif', '.aiff')
FFMPEG_SUFFIXES = ('.m4a', '.aac', '.mp4', '.mov', '.3gp', '.amr', '.wma', '.webm')
SUFFIXES = SOUNDFILE_SUFFIXES + FFMPEG_SUFFIXES
TIMEOUT_SECONDS = 300
NEEDS_FFMPEG = ('this format is read with FFmpeg, which was not found. Install it (for example: winget install Gyan.FFmpeg) '
                'and restart the recorder, or set its path in Settings. Or save the recording as WAV.')


class AudioImportError(ValueError):
    pass


def read_audio(path):
    """(samples, rate) of the recording at `path`: float32, shape (N,) or (N, channels)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in FFMPEG_SUFFIXES:
        try:
            return sf.read(str(path), dtype='float32', always_2d=False)
        except RuntimeError:                    # soundfile's LibsndfileError: not a file, or not a format it reads
            if suffix in SOUNDFILE_SUFFIXES or not path.is_file():
                raise
    return _decode(path)


def _decode(path):
    """The sound of `path` as FFmpeg decodes it: its first audio stream, at its own sample rate and channels."""
    try:
        ffmpeg = clean.find_ffmpeg()
    except clean.NotFound:
        raise AudioImportError(NEEDS_FFMPEG) from None
    except clean.CleanError as e:              # the path in Settings is wrong
        raise AudioImportError(str(e)) from None
    with tempfile.TemporaryDirectory(prefix='recorder-import-') as tmp:
        decoded = Path(tmp) / 'decoded.wav'
        command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(path),
                   '-map', '0:a:0', '-c:a', 'pcm_f32le', str(decoded)]
        try:
            # A name such as Zoë.m4a comes back in FFmpeg's messages as UTF-8, and one letter must not lose the message.
            reply = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   timeout=TIMEOUT_SECONDS, check=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise AudioImportError('FFmpeg took too long to read it.') from None
        except OSError as e:
            raise AudioImportError(f'FFmpeg could not be started: {e}') from None
        if reply.returncode:
            detail = reply.stderr.strip()
            if 'matches no streams' in detail:      # -map 0:a:0 found no sound: a video without any, say
                raise AudioImportError('the file holds no audio')
            raise AudioImportError('FFmpeg could not read it: ' + (detail or f'exit code {reply.returncode}')[-300:])
        return sf.read(str(decoded), dtype='float32', always_2d=False)
