"""Pure audio helpers: measurements, cuts with joins, loudness, keep rules. No devices and no files.

Times in an edit list are seconds on the master timeline (the untouched capture), so edits survive undo and redo.
The edit recipe itself (core/edits.py), the processing chain (core/chain.py) and the render pipeline (core/render.py)
build on these.
"""
import math
from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy.signal import resample_poly

EXPORT_RATE = 24000
MIN_SECONDS = 6.0               # below this a clip cannot be kept as a reference
TARGET_MIN_SECONDS = 10.0       # the range the meter marks green
TARGET_MAX_SECONDS = 25.0       # above this a warning, still fine up to the hard maximum
MAX_SECONDS = 40.0              # longer clips cannot be kept
MIN_SELECTION_SECONDS = 0.020   # shorter selections are ignored (accidental clicks)
SNAP_SECONDS = 0.005            # cut points move to the nearest zero crossing within this window
DEFAULT_FADE_MS = 10
MAX_FADE_MS = 50
CLIP_FLAG = 0.999               # any sample this close to full scale flags the take
CLIP_LAB = 0.995                # the clipped-sample measure: more than 0.1% of samples this close to full scale is flagged
SILENT_DB = -50.0               # RMS below this cannot be kept
QUIET_DB = -35.0                # advisory
FRAME_SECONDS = 0.02
SILENCE_FRAME_DB = -45.0        # a 20 ms frame quieter than this counts as silence
MOSTLY_SILENT = 0.40
TRANSCRIPT_MIN, TRANSCRIPT_MAX = 3, 600     # characters; a clip needs a transcript of this length to be kept


def to_mono(x):
    x = np.asarray(x, dtype=np.float32)
    return x if x.ndim == 1 else x.mean(axis=1, dtype=np.float32)


def _db(value):
    return 20.0 * math.log10(max(float(value), 1e-12))


def measure(x, rate):
    """Level and length measurements on the mono mix. Clipping is counted at the flag level and at the 0.1% level."""
    m = to_mono(x)
    n = len(m)
    if n == 0 or not rate:
        return {'seconds': 0.0, 'peak_dbfs': -240.0, 'rms_dbfs': -240.0, 'clipped': False,
                'clipped_fraction': 0.0, 'silence_fraction': 1.0}
    m64 = m.astype(np.float64)
    frame = max(1, int(rate * FRAME_SECONDS))
    usable = n // frame * frame
    frames = m64[:usable].reshape(-1, frame) if usable else m64.reshape(1, -1)
    frame_db = 20.0 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) + 1e-12)
    return {
        'seconds': round(n / rate, 3),
        'peak_dbfs': round(_db(np.max(np.abs(m64))), 2),
        'rms_dbfs': round(_db(math.sqrt(float(np.mean(m64 * m64)))), 2),
        'clipped': bool(np.any(np.abs(m64) >= CLIP_FLAG)),
        'clipped_fraction': round(float(np.mean(np.abs(m64) >= CLIP_LAB)), 6),
        'silence_fraction': round(float(np.mean(frame_db < SILENCE_FRAME_DB)), 3),
    }


def resample(x, rate_in, rate_out):
    """Polyphase resampling (SciPy)."""
    x = np.asarray(x, dtype=np.float64)
    if rate_in == rate_out:
        return x.astype(np.float32)
    g = gcd(int(rate_in), int(rate_out))
    return resample_poly(x, int(rate_out) // g, int(rate_in) // g).astype(np.float32)


def _snap(mono, index, rate):
    """Move a cut point to the nearest sign change of the mono mix within SNAP_SECONDS. Edges of the clip stay put."""
    n = len(mono)
    if index <= 0 or index >= n:
        return index
    w = max(1, int(SNAP_SECONDS * rate))
    lo, hi = max(1, index - w), min(n - 1, index + w)
    signs = np.signbit(mono[lo - 1:hi + 1])
    changes = np.nonzero(signs[1:] != signs[:-1])[0]
    if changes.size == 0:
        return index
    candidates = lo + changes          # first sample after each sign change
    return int(candidates[np.argmin(np.abs(candidates - index))])


def cut_ranges(edits, rate, n, mono=None):
    """Edit list (master seconds) -> sorted, merged sample ranges [(start, end)] of its cuts, snapped to zero crossings.

    Entries with another operation (silence, fade, gain) are not cuts and are ignored here.
    """
    ranges = []
    for e in edits or []:
        if e.get('op', 'cut') != 'cut':
            continue
        a = max(0, int(round(float(e['start_seconds']) * rate)))
        b = min(n, int(round(float(e['end_seconds']) * rate)))
        if b - a >= MIN_SELECTION_SECONDS * rate:
            ranges.append((a, b))
    if mono is not None:
        ranges = [(_snap(mono, a, rate), _snap(mono, b, rate)) for a, b in ranges]
    ranges.sort()
    merged = []
    for a, b in ranges:
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def kept_segments(ranges, n):
    segments, pos = [], 0
    for a, b in ranges:
        if a > pos:
            segments.append((pos, a))
        pos = b
    if pos < n:
        segments.append((pos, n))
    return segments


def render(x, rate, edits=(), fade_ms=DEFAULT_FADE_MS, snap_to=None):
    """Master samples plus cuts -> edited samples in the same channel layout. Each join is a short crossfade.

    Cut points snap to zero crossings of `snap_to` (the master's mono mix by default), so a caller that has changed
    the samples first (fades, silence) still cuts at the same places as everything that draws or maps the cuts.
    """
    x = np.asarray(x, dtype=np.float32)
    mono = to_mono(x) if snap_to is None else snap_to
    n = len(mono)
    ranges = cut_ranges(edits, rate, n, mono)
    if not ranges:
        return x.copy()
    segments = kept_segments(ranges, n)
    if not segments:
        raise ValueError('A cut cannot remove the whole clip')
    fade = int(round(min(max(float(fade_ms), 0.0), MAX_FADE_MS) / 1000.0 * rate))
    pieces = [x[a:b] for a, b in segments]
    result = pieces[0].copy()
    for nxt in pieces[1:]:
        k = min(fade, len(result) // 2, len(nxt) // 2)
        if k < 1:
            result = np.concatenate([result, nxt])
            continue
        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
        if result.ndim == 2:
            ramp = ramp[:, None]
        seam = result[-k:] * (1.0 - ramp) + nxt[:k] * ramp
        result = np.concatenate([result[:-k], seam, nxt[k:]])
    return result


TARGET_VOICED_DB = -20.0        # Normalize level aims the voiced part at this RMS
NORMALIZE_PEAK = 0.95           # and, without the limiter, never lets a peak pass this


def voiced_rms_db(x, rate):
    """RMS of the voiced part only: 20 ms frames within 30 dB of the loudest frame and above the silence floor."""
    m = to_mono(x).astype(np.float64)
    frame = max(1, int(rate * FRAME_SECONDS))
    usable = len(m) // frame * frame
    if usable == 0:
        return _db(math.sqrt(float(np.mean(m * m))) if len(m) else 0.0)
    rms = np.sqrt(np.mean(m[:usable].reshape(-1, frame) ** 2, axis=1))
    frame_db = 20.0 * np.log10(rms + 1e-12)
    voiced = frame_db > max(SILENCE_FRAME_DB, frame_db.max() - 30.0)
    if not voiced.any():
        return -240.0
    return round(10.0 * math.log10(float(np.mean(rms[voiced] ** 2)) + 1e-24), 2)


def normalize_gain_db(x, rate, cap_peak=True):
    """Gain in dB that brings the voiced part to TARGET_VOICED_DB.

    With `cap_peak` (the default) the gain also stops short of a peak above NORMALIZE_PEAK. Turn it off when a peak
    limiter will follow: one loud pop then no longer decides how loud the speech can be.
    """
    m = to_mono(x)
    peak = float(np.max(np.abs(m))) if len(m) else 0.0
    # The voiced set depends on the level (frames near the silence floor join or leave), so refine a few times.
    gain = 0.0
    for _ in range(4):
        level = voiced_rms_db(apply_gain(m, gain), rate)
        if level < -200:
            break
        gain += TARGET_VOICED_DB - level
    if cap_peak and peak > 0:
        gain = min(gain, 20.0 * math.log10(NORMALIZE_PEAK / peak))
    return round(gain, 2)


def apply_gain(x, gain_db):
    """Multiply by a gain in dB, clipped to full scale. Zero dB returns the samples unchanged."""
    x = np.asarray(x, dtype=np.float32)
    if not gain_db:
        return x
    return np.clip(x * np.float32(10.0 ** (float(gain_db) / 20.0)), -1.0, 1.0).astype(np.float32)


def script_lines(text):
    """Script text -> prompts for script mode: one prompt per non-empty line, whitespace trimmed."""
    return [line.strip() for line in str(text).splitlines() if line.strip()]


@dataclass
class Issue:
    text: str
    blocks: bool
    code: str = ''


def take_warnings(stats, flags=()):
    """Warnings for a measured take. Items with blocks=True stop Keep."""
    seconds = stats['seconds']
    items = []
    if seconds < MIN_SECONDS:
        items.append(Issue(f'Too short ({seconds:.1f} s; 6 s minimum): cannot be kept as a reference', True, 'short'))
    if seconds > MAX_SECONDS:
        items.append(Issue(f'Too long ({seconds:.1f} s; 40 s maximum): cannot be kept', True, 'long'))
    elif seconds > TARGET_MAX_SECONDS:
        items.append(Issue(f'Long ({seconds:.1f} s): fine up to 40 s, 10 to 25 s works best', False, 'target'))
    if stats['rms_dbfs'] < SILENT_DB:
        items.append(Issue('Silent: cannot be kept', True, 'silent'))
    elif stats['rms_dbfs'] < QUIET_DB:
        items.append(Issue(f"Quiet ({stats['rms_dbfs']:.0f} dBFS RMS): advisory", False, 'quiet'))
    if stats.get('clipped'):
        items.append(Issue('Clipped: some samples reach full scale; consider re-recording', False, 'clipped'))
    if stats.get('silence_fraction', 0) > MOSTLY_SILENT:
        items.append(Issue('Mostly silence', False, 'mostly_silent'))
    if 'interrupted' in flags:
        items.append(Issue('Interrupted: the device stopped during this take', False, 'interrupted'))
    return items


def keep_problems(meta, warnings):
    """Reasons a take cannot be kept yet. An empty list means it can be kept."""
    problems = []
    text = str(meta.get('transcript') or '').strip()
    if not (TRANSCRIPT_MIN <= len(text) <= TRANSCRIPT_MAX):
        problems.append(f'The transcript must be {TRANSCRIPT_MIN} to {TRANSCRIPT_MAX} characters.')
    if not str(meta.get('origin') or '').strip():
        problems.append('Enter where the recording came from (origin).')
    if not str(meta.get('allowed_use') or '').strip():
        problems.append('Enter what the voice may be used for (allowed use).')
    if meta.get('consent_confirmed') is not True:
        problems.append('Tick the permission box: you have the right to use this voice for the purpose you entered.')
    problems.extend(w.text for w in warnings if w.blocks)
    return problems
