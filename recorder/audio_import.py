"""Reading a recording to import.

WAV, FLAC, Ogg, Opus, MP3 and AIFF are read by soundfile. Other formats, such as the M4A that phones and Windows' Sound
Recorder make, or the sound of a phone video, are decoded by FFmpeg, the optional program noise cleaning uses, when it is
installed. FFmpeg writes a temporary WAV, which is read and deleted; the file itself is only read, and nothing is downloaded.

A file in one of soundfile's own formats is never handed to FFmpeg when soundfile refuses it. FFmpeg decodes around
damage, so a damaged WAV would come in with whatever it made of the damage instead of being refused.
"""
import subprocess
import tempfile
from collections import namedtuple
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
    with open_audio(path) as source:
        return source.read(dtype='float32', always_2d=False), source.samplerate


def needs_decoding(path):
    """True when `path` is read by FFmpeg, which writes it out first: its length is known only after that."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in FFMPEG_SUFFIXES:
        return True
    if suffix in SOUNDFILE_SUFFIXES:
        return False
    try:
        sf.info(str(path))
    except RuntimeError:
        return path.is_file()
    return False


def shape_of(path):
    """(frames, sample rate, channels) of a recording soundfile reads, with none of its audio read; None for one FFmpeg
    has to decode first. Raises soundfile's own RuntimeError for a file it cannot read."""
    if needs_decoding(path):
        return None
    info = sf.info(str(path))
    return int(info.frames), int(info.samplerate), int(info.channels)


class open_audio:
    """The recording at `path`, open for reading a block at a time, as a soundfile.SoundFile; used with `with`. A format
    soundfile does not read is decoded by FFmpeg into a temporary WAV first, which goes when it is closed."""

    def __init__(self, path):
        self._tmp = None
        path = Path(path)
        if needs_decoding(path):
            self._tmp = tempfile.TemporaryDirectory(prefix='recorder-import-')
            try:
                path = _decode(path, Path(self._tmp.name) / 'decoded.wav')
            except BaseException:
                self._tmp.cleanup()
                raise
        try:
            self.file = sf.SoundFile(str(path))
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self.file

    def __exit__(self, *exc):
        self.close()

    def close(self):
        file = getattr(self, 'file', None)
        if file is not None:
            file.close()
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def _decode(path, decoded):
    """Have FFmpeg write the sound of `path` to the WAV `decoded`: its first audio stream, at its own sample rate and
    channels. Returns `decoded`."""
    try:
        ffmpeg = clean.find_ffmpeg()
    except clean.NotFound:
        raise AudioImportError(NEEDS_FFMPEG) from None
    except clean.CleanError as e:              # the path in Settings is wrong
        raise AudioImportError(str(e)) from None
    command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(path),
               '-map', '0:a:0', '-c:a', 'pcm_f32le', str(decoded)]
    try:
        # A name such as Zoë.m4a comes back in FFmpeg's messages as UTF-8, and one letter must not lose the message.
        reply = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=TIMEOUT_SECONDS, check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise AudioImportError('FFmpeg took too long to read it.') from None
    except OSError as e:
        raise AudioImportError(f'FFmpeg could not be started: {e}') from None
    if reply.returncode:
        detail = reply.stderr.strip()
        if 'matches no streams' in detail:      # -map 0:a:0 found no sound: a video without any, say
            raise AudioImportError('the file holds no audio')
        raise AudioImportError('FFmpeg could not read it: ' + (detail or f'exit code {reply.returncode}')[-300:])
    return decoded


# ---- whether a recording fits in this computer's memory ----------------------------------------------------------------

# What the window holds of a take in front, and works out from it, for each frame of audio: the recording itself (4 bytes
# a channel), its mono mix (4) and what a render and a measurement need on top (16, measured: a 45-minute stereo import at
# 44.1 kHz, measured and split, peaked at 2.8 GB).
BYTES_PER_FRAME = 4 + 16
FITS = 0.7                      # of this computer's memory: more than this and the program refuses, so Windows is not starved
WARN = 0.35                     # more than this, or longer than WARN_SECONDS, and the person is asked first
WARN_SECONDS = 3600.0


def memory_needed(frames, channels):
    """About how many bytes the window needs to work on a recording of this many frames and channels."""
    return int(frames) * (4 * int(channels) + BYTES_PER_FRAME)


def physical_memory():
    """This computer's memory in bytes, or None when Windows does not say."""
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong), ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    try:
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    except (AttributeError, OSError):
        pass
    return None


Fit = namedtuple('Fit', 'verdict seconds needed memory longest')     # verdict: ok, warn or refuse


def fit(frames, rate, channels, memory=None):
    """Whether a recording of this size can be worked on here: 'ok', 'warn' (ask first) or 'refuse', with its length in
    seconds, the bytes it needs, this computer's memory, and the longest recording of its kind that fits, in seconds."""
    memory = physical_memory() if memory is None else memory
    seconds = frames / rate if rate else 0.0
    needed = memory_needed(frames, channels)
    if not memory:
        return Fit('warn' if seconds > WARN_SECONDS else 'ok', seconds, needed, None, None)
    longest = FITS * memory / (4 * channels + BYTES_PER_FRAME) / rate if rate else None
    if needed > FITS * memory:
        verdict = 'refuse'
    elif needed > WARN * memory or seconds > WARN_SECONDS:
        verdict = 'warn'
    else:
        verdict = 'ok'
    return Fit(verdict, seconds, needed, memory, longest)


def duration_text(seconds):
    """A length as a person says it: 2 h 5 min, 45 min, 12 s."""
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600} h {seconds % 3600 // 60} min'
    if seconds >= 60:
        return f'{seconds // 60} min'
    return f'{seconds} s'
