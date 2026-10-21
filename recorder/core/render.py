"""The render pipeline: master samples plus a recipe -> the clip, with a stage cache, and the maps between time bases.

Stages, in order: silence, fades and region gain on the master timeline; the cuts and their joins; high-pass and hum
notch; noise cleaning; gain and the limiter; then, for the export, resampling to 24 kHz. Each stage is cached by
what decides it, so moving the level slider does not run FFmpeg again.
"""
import json
import threading
from collections import OrderedDict

import numpy as np

from .. import audio_core as ac
from . import chain as chain_mod

EDGE_RAMP_SECONDS = 0.003        # silence and region gain meet the surrounding audio over this long, outside the region


# ---- region operations ----------------------------------------------------------------------------------------------

def _smoothstep(count):
    t = (np.arange(count, dtype=np.float64) + 1.0) / (count + 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * t)


def region_envelope(n, rate, edits):
    """The gain curve (length n) of the silence, fade and gain operations, or None when there are none."""
    ops = [e for e in edits or [] if e.get('op') in ('silence', 'fade', 'gain')]
    if not ops:
        return None
    env = np.ones(n, dtype=np.float64)
    ramp = max(0, int(round(EDGE_RAMP_SECONDS * rate)))
    smooth = _smoothstep(ramp) if ramp else None
    for e in ops:
        a = min(max(int(round(float(e['start_seconds']) * rate)), 0), n)
        b = min(max(int(round(float(e['end_seconds']) * rate)), 0), n)
        if b <= a:
            continue
        if e['op'] == 'fade':
            t = np.arange(b - a, dtype=np.float64) / max(b - a - 1, 1)
            curve = 0.5 - 0.5 * np.cos(np.pi * t)            # 0 -> 1
            env[a:b] *= curve if e['direction'] == 'in' else curve[::-1]
            continue
        level = 0.0 if e['op'] == 'silence' else 10.0 ** (float(e['gain_db']) / 20.0)
        env[a:b] *= level
        if smooth is not None:
            lo = max(0, a - ramp)
            if a > lo:
                env[lo:a] *= 1.0 + (level - 1.0) * smooth[ramp - (a - lo):]
            hi = min(n, b + ramp)
            if hi > b:
                env[b:hi] *= 1.0 + (level - 1.0) * (1.0 - smooth[:hi - b])
    return env


def apply_regions(x, rate, edits):
    """Silence, fades and region gain applied to the samples. Returns the input itself when there are none."""
    x = np.asarray(x, dtype=np.float32)
    env = region_envelope(len(x), rate, edits)
    if env is None:
        return x
    env = env.astype(np.float32)
    return x * (env if x.ndim == 1 else env[:, None])


# ---- the pipeline ---------------------------------------------------------------------------------------------------

class RenderCache:
    """The most recent stage results. Keys say what decided each one, so a stale result is never returned."""

    def __init__(self, limit=12):
        self.limit = limit
        self._items = OrderedDict()
        self._lock = threading.Lock()            # background jobs and the window both use it

    def get(self, key):
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key, value):
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)

    def drop(self, ident):
        with self._lock:
            for key in [k for k in self._items if k[0] == ident]:
                del self._items[key]

    def __len__(self):
        return len(self._items)


def _edits_key(recipe):
    return json.dumps([recipe.edits, recipe.fade_ms], sort_keys=True)


def cut_stage(master, rate, recipe, cache=None, ident=None):
    """Regions and cuts: the clip at its own rate, mono, before any filter."""
    key = (ident, 'cuts', _edits_key(recipe))
    hit = cache.get(key) if cache is not None and ident is not None else None
    if hit is not None:
        return hit
    mono = ac.to_mono(master)
    shaped = apply_regions(mono, rate, recipe.edits)
    out = ac.render(shaped, rate, recipe.edits, recipe.fade_ms, snap_to=mono)
    if cache is not None and ident is not None:
        cache.put(key, out)
    return out


def before_level(master, rate, recipe, denoise=None, cache=None, ident=None):
    """Everything up to, but not including, gain and the limiter. This is what Normalize measures."""
    chain = recipe.chain
    cuts = cut_stage(master, rate, recipe, cache, ident)
    filters_key = (ident, 'filters', _edits_key(recipe), chain_mod.filter_key(chain))
    hit = cache.get(filters_key) if cache is not None and ident is not None else None
    if hit is not None:
        filtered = hit
    else:
        filtered = chain_mod.apply_filters(cuts, rate, chain)
        if cache is not None and ident is not None:
            cache.put(filters_key, filtered)
    if not chain['denoise']['enabled']:
        return filtered
    nf = chain['denoise']['nf']
    clean_key = (ident, 'denoise', _edits_key(recipe), chain_mod.filter_key(chain), nf)
    hit = cache.get(clean_key) if cache is not None and ident is not None else None
    if hit is not None:
        return hit
    if denoise is None:
        raise ValueError('Noise cleaning is on but no cleaner is available')
    cleaned = np.asarray(denoise(filtered, rate, nf), dtype=np.float32)
    if cache is not None and ident is not None:
        cache.put(clean_key, cleaned)
    return cleaned


def process(master, rate, recipe, denoise=None, cache=None, ident=None):
    """The finished clip at its own rate, mono. `recipe` has .edits, .chain and .fade_ms (a core.edits.History does)."""
    return chain_mod.apply_level(before_level(master, rate, recipe, denoise, cache, ident), rate, recipe.chain)


def finish(processed, rate, chain):
    """A processed clip as the export holds it: 24 kHz, clipped to full scale. Resampling can lift a peak a little above
    the limiter's ceiling, so with the limiter on the resampled clip gets one more pass (a no-op when it is already under)."""
    out = ac.resample(processed, rate, ac.EXPORT_RATE)
    limiter = chain['limiter']
    if limiter['enabled']:
        out = chain_mod.limit(out, ac.EXPORT_RATE, limiter['ceiling_db'])
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def export_samples(master, rate, recipe, denoise=None, cache=None, ident=None):
    """What the export WAV holds: mono, everything applied, 24 kHz, clipped to full scale."""
    return finish(process(master, rate, recipe, denoise, cache, ident), rate, recipe.chain)


# ---- master time and edited time ------------------------------------------------------------------------------------

def segments_of(master, rate, edits):
    """The kept stretches of the master as sample ranges, after snapping the cuts to zero crossings."""
    mono = ac.to_mono(master)
    return ac.kept_segments(ac.cut_ranges(edits, rate, len(mono), mono), len(mono))


def edited_to_master(elapsed, segments, rate):
    """Map a time in the edited clip back to the master timeline (ignores the few-millisecond join overlaps)."""
    pos = 0.0
    for a, b in segments:
        length = (b - a) / rate
        if elapsed < pos + length:
            return a / rate + (elapsed - pos)
        pos += length
    return segments[-1][1] / rate if segments else 0.0


def master_to_edited(master_seconds, segments, rate):
    """Map a time on the master timeline to the edited clip. A point inside a cut starts at the next kept part."""
    pos = 0.0
    for a, b in segments:
        start, end = a / rate, b / rate
        if master_seconds < start:
            return pos
        if master_seconds < end:
            return pos + (master_seconds - start)
        pos += end - start
    return pos


def join_positions(segments):
    """Sample offsets in the edited clip where two kept stretches meet."""
    pos, joins = 0, []
    for a, b in segments[:-1]:
        pos += b - a
        joins.append(pos)
    return joins
