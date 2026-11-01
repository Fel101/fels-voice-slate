"""Background-noise cleaning with FFmpeg's afftdn filter.

The noise floor tells afftdn how loud the noise is. Set above the real noise it takes the quiet parts of the voice as
well: on speech with a noise floor of -50 dB, a setting of -25 left the clip further from the clean voice than doing
nothing did, and -45 left it closer. So the default is -45 dB, which suits a quiet room, and settings closer to 0 dB
remove more and change the voice more. The best setting is about 5 dB above the noise (Auto fix uses that). The recorder
has no original voice to compare the cleaned one against, so it does not check that the cleaned voice still matches.
Sidecars record that as `identity_checked: false`, and the window says so. Listen before you keep.

FFmpeg is not part of this program. Install it yourself (for example `winget install Gyan.FFmpeg`); it is run as a
separate program and nothing is linked.
"""
import math
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import correlate

NF_MIN, NF_MAX, DEFAULT_NF = -80.0, -20.0, -45.0
TIMEOUT_SECONDS = 300
PROBE_SECONDS = 0.5             # how much noise is sent through the filter to measure its delay
NOT_FOUND = ('FFmpeg was not found. Install it (for example: winget install Gyan.FFmpeg) and restart the recorder, '
             'or set its path in Settings. Noise cleaning needs it.')

_configured = None
_delays = {}                    # (ffmpeg.exe, sample rate) -> how many samples afftdn holds the audio back


class CleanError(ValueError):
    pass


def set_ffmpeg_path(path):
    """Use this ffmpeg.exe instead of searching for one (the "ffmpeg" setting). None goes back to searching."""
    global _configured
    _configured = str(path) if path else None


def validate_nf(nf):
    try:
        value = float(nf)
    except (TypeError, ValueError):
        raise CleanError('The noise floor must be a number') from None
    if isinstance(nf, bool) or not math.isfinite(value) or not NF_MIN <= value <= NF_MAX:
        raise CleanError(f'The noise floor must be between {NF_MIN:g} and {NF_MAX:g} dB')
    return value


def _winget_candidates():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        return []
    winget = Path(base) / 'Microsoft' / 'WinGet'
    found = sorted((winget / 'Packages').glob('*FFmpeg*/ffmpeg-*/bin/ffmpeg.exe')) if (winget / 'Packages').is_dir() else []
    link = winget / 'Links' / 'ffmpeg.exe'
    return ([str(link)] if link.is_file() else []) + [str(p) for p in reversed(found)]


def _on_path():
    """ffmpeg.exe from a folder on PATH, or None.

    `shutil.which` looks in the current folder first on Windows, so a file named ffmpeg.exe in whatever folder the
    program was started from would be run instead of the real one. Only folders named by their full path count here:
    an empty entry or a relative one such as "." means the current folder, and is skipped.
    """
    name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    for entry in os.environ.get('PATH', '').split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry and os.path.isabs(entry) and (Path(entry) / name).is_file():
            return str(Path(entry) / name)
    return None


def locate(configured=None):
    """Where ffmpeg.exe is: `configured` if given, else the one on PATH, else the newest one installed by WinGet.
    None when it is not there. Unlike find_ffmpeg this never raises and ignores the saved setting."""
    if configured:
        return str(configured) if Path(configured).is_file() else None
    return _on_path() or next(iter(_winget_candidates()), None)


def find_ffmpeg():
    """The configured ffmpeg.exe, else the one on PATH, else the newest one installed by WinGet."""
    if _configured:
        if Path(_configured).is_file():
            return _configured
        raise CleanError(f'The ffmpeg path in settings does not exist: {_configured}')
    found = _on_path()
    if found:
        return found
    candidates = _winget_candidates()
    if candidates:
        return candidates[0]
    raise CleanError(NOT_FOUND)


def available():
    try:
        find_ffmpeg()
    except CleanError:
        return False
    return True


def _delay(ffmpeg, rate):
    """How many samples afftdn holds the audio back. Its output is as long as its input, so without this every clip comes
    out that much late and loses that much of its end.

    It is 25 ms at every rate and setting in FFmpeg 8.1, but it is the filter's own business, so it is measured rather than
    assumed: once for each FFmpeg and sample rate, on a burst of noise, which lines up with itself at one place only.
    """
    key = (ffmpeg, int(rate))
    if key not in _delays:
        burst = np.random.default_rng(0).normal(0.0, 0.1, int(rate * PROBE_SECONDS)).astype(np.float32)
        out = _afftdn(ffmpeg, np.concatenate([burst, np.zeros_like(burst)]), rate, DEFAULT_NF)
        match = correlate(out.astype(np.float64), burst.astype(np.float64), mode='full', method='fft')
        _delays[key] = int(np.argmax(np.abs(match[len(burst) - 1:])))
    return _delays[key]


def denoise(mono, rate, nf=DEFAULT_NF):
    """Mono samples -> mono samples of the same length and rate, with afftdn applied at noise floor `nf` (dB), in step with
    the input. The padding lets the end of the clip through the filter's delay; it is a reflection and not silence, so the
    filter does not hear the clip stop."""
    nf = validate_nf(nf)
    ffmpeg = find_ffmpeg()
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    if not len(x):
        return x
    delay = _delay(ffmpeg, rate)
    y = _afftdn(ffmpeg, np.pad(x, (0, delay), mode='reflect'), rate, nf)[delay:delay + len(x)]
    if len(y) < len(x):
        y = np.concatenate([y, np.zeros(len(x) - len(y), dtype=np.float32)])
    return y


def _afftdn(ffmpeg, x, rate, nf):
    """What FFmpeg's afftdn filter makes of these samples."""
    with tempfile.TemporaryDirectory(prefix='recorder-clean-') as tmp:
        source, cleaned = Path(tmp) / 'in.wav', Path(tmp) / 'out.wav'
        sf.write(str(source), x, int(rate), subtype='FLOAT', format='WAV')
        command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(source),
                   '-af', f'afftdn=nf={nf:g}', '-c:a', 'pcm_f32le', str(cleaned)]
        try:
            reply = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise CleanError('FFmpeg took too long to clean the audio.') from None
        except OSError as e:
            raise CleanError(f'FFmpeg could not be started: {e}') from None
        if reply.returncode:
            raise CleanError('FFmpeg could not clean the audio: ' + (reply.stderr.strip() or str(reply.returncode))[-300:])
        y, out_rate = sf.read(str(cleaned), dtype='float32', always_2d=False)
    if int(out_rate) != int(rate):
        raise CleanError('FFmpeg changed the sample rate; the clip was not cleaned.')
    return np.asarray(y, dtype=np.float32).reshape(-1)
