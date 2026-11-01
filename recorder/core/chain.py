"""The processing chain: take-wide settings and the filters that run after the cuts.

Order, fixed: high-pass, hum notch, noise cleaning (FFmpeg, see clean.py), then gain and the peak limiter.
All filters here are zero-phase (they run forward and backward), so they do not shift the audio in time.
"""
import copy
import math

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import butter, filtfilt, iirnotch, sosfiltfilt

DEFAULT_CHAIN = {
    'highpass': {'enabled': False, 'hz': 80.0},
    'hum': {'enabled': False, 'hz': 60.0, 'harmonics': 3},
    'denoise': {'enabled': False, 'nf': -45.0},
    'level': {'gain_db': 0.0},
    'limiter': {'enabled': False, 'ceiling_db': -1.0},
}
LIMITS = {
    ('highpass', 'hz'): (20.0, 300.0),
    ('hum', 'hz'): (30.0, 120.0),
    ('hum', 'harmonics'): (1, 6),
    ('denoise', 'nf'): (-80.0, -20.0),
    ('level', 'gain_db'): (-24.0, 36.0),
    ('limiter', 'ceiling_db'): (-12.0, 0.0),
}
NOTCH_Q = 30.0
LOOKAHEAD_MS = 5.0
RELEASE_MS = 80.0


def normalize(chain=None):
    """A complete, checked chain: defaults filled in, numbers held to their limits. Unknown keys are ignored."""
    out = copy.deepcopy(DEFAULT_CHAIN)
    if chain is None:
        return out
    if not isinstance(chain, dict):
        raise ValueError('the chain must be an object')
    for section, values in chain.items():
        if section not in out:
            continue
        if not isinstance(values, dict):
            raise ValueError(f'chain.{section} must be an object')
        for key, value in values.items():
            if key not in out[section]:
                continue
            out[section][key] = _checked(section, key, value, type(out[section][key]))
    return out


def _checked(section, key, value, kind):
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f'chain.{section}.{key} must be true or false')
        return value
    if isinstance(value, bool):
        raise ValueError(f'chain.{section}.{key} must be a number')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'chain.{section}.{key} must be a number') from None
    low, high = LIMITS[(section, key)]
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'chain.{section}.{key} must be between {low:g} and {high:g}')
    return int(round(number)) if kind is int else number


def with_values(chain, section, **values):
    """A copy of the chain with some values of one section changed and checked."""
    changed = copy.deepcopy(chain)
    changed.setdefault(section, {}).update(values)
    return normalize(changed)


def uses_filters(chain):
    return bool(chain['highpass']['enabled'] or chain['hum']['enabled'])


def filter_key(chain):
    """What decides the output of the filter stage, for caching."""
    return (chain['highpass']['enabled'] and chain['highpass']['hz'],
            chain['hum']['enabled'] and (chain['hum']['hz'], chain['hum']['harmonics']))


def highpass(x, rate, hz):
    x = np.asarray(x, dtype=np.float32)
    if len(x) < 256:
        return x
    sos = butter(4, hz, btype='highpass', fs=rate, output='sos')
    return sosfiltfilt(sos, x.astype(np.float64)).astype(np.float32)


def hum_notch(x, rate, hz, harmonics):
    """Narrow notches at the mains frequency and its first harmonics (50 or 60 Hz mains)."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < 256:
        return x
    y = x.astype(np.float64)
    for k in range(1, int(harmonics) + 1):
        f = hz * k
        if f >= rate / 2 - 100:
            break
        b, a = iirnotch(f, NOTCH_Q, fs=rate)
        y = filtfilt(b, a, y)
    return y.astype(np.float32)


def apply_filters(x, rate, chain):
    """High-pass, then the hum notch, when they are on. Returns the input itself when both are off."""
    if chain['highpass']['enabled']:
        x = highpass(x, rate, chain['highpass']['hz'])
    if chain['hum']['enabled']:
        x = hum_notch(x, rate, chain['hum']['hz'], chain['hum']['harmonics'])
    return x


def limit(x, rate, ceiling_db=-1.0):
    """Look-ahead peak limiter. No sample ends above the ceiling; the gain falls ahead of a peak and recovers in ~80 ms.

    The gain needed at each sample is spread backward by a minimum filter, smoothed by a moving average of the same
    width (so the gain is already down when the peak arrives), then allowed to recover only slowly.
    """
    x = np.asarray(x, dtype=np.float32)
    ceiling = 10.0 ** (float(ceiling_db) / 20.0)
    peak = np.abs(x)
    if not len(x) or float(peak.max()) <= ceiling:
        return x
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12)).astype(np.float64)
    width = max(3, int(rate * LOOKAHEAD_MS / 1000.0)) | 1
    gain = uniform_filter1d(minimum_filter1d(need, size=width, mode='nearest'), size=width, mode='nearest')
    deficit = _slow_release(1.0 - gain, math.exp(-1.0 / (rate * RELEASE_MS / 1000.0)))
    return (x * (1.0 - deficit)).astype(np.float32)


def _slow_release(deficit, decay, block=1 << 17):
    """d[i] = max(deficit[i], d[i-1] * decay), without a Python loop: a running max in a rescaled domain, block by block."""
    out = np.empty_like(deficit)
    carry = 0.0
    scale_up = decay ** -np.arange(block, dtype=np.float64)
    for start in range(0, len(deficit), block):
        piece = deficit[start:start + block].copy()
        piece[0] = max(piece[0], carry * decay)
        up = scale_up[:len(piece)]
        out[start:start + len(piece)] = np.maximum.accumulate(piece * up) / up
        carry = out[start + len(piece) - 1]
    return out


def apply_level(x, rate, chain):
    """Gain, then the limiter if it is on. Without the limiter the result is clipped at full scale, as before."""
    x = np.asarray(x, dtype=np.float32)
    gain_db = chain['level']['gain_db']
    limiter = chain['limiter']
    if gain_db:
        x = x * np.float32(10.0 ** (gain_db / 20.0))
    if limiter['enabled']:
        x = limit(x, rate, limiter['ceiling_db'])
    return np.clip(x, -1.0, 1.0).astype(np.float32, copy=False)
