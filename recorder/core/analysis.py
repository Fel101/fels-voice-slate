"""Measurements for the editor and the Check tab. Pure functions on sample arrays.

- PeakPyramid: min and max per block at several scales, so a waveform of any zoom is drawn from a few hundred values.
- spectrogram: dB magnitudes for the picture under the waveform.
- loudness_lufs, noise_floor_db, snr_db, bandwidth_hz, detect_hum, edge_silence: the quality checks.
- silent_runs and the two range finders behind Auto-trim and Shorten pauses.
- quality_report: the rows of the Check tab.
"""
import math
from collections import namedtuple

import numpy as np
from scipy.signal import lfilter, welch

from .. import audio_core as ac


# ---- waveform drawing -------------------------------------------------------------------------------------------------

class PeakPyramid:
    """Block minima, maxima and energy of a mono signal at block sizes 64, 256, 1024, ... (each level 4 times coarser)."""

    BASE = 64
    FACTOR = 4

    def __init__(self, mono):
        self.mono = np.asarray(mono, dtype=np.float32)
        self.n = len(self.mono)
        self.levels = []                       # (block size, lo, hi)
        self._energy = []                      # (sum of squares, samples) of each block, parallel to levels
        if self.n == 0:
            return
        block = self.BASE
        lo, hi = self._reduce(self.mono, self.mono, block)
        starts = np.arange(0, self.n, block)
        sq = np.add.reduceat(self.mono.astype(np.float64) ** 2, starts)
        count = np.diff(np.append(starts, self.n))
        self.levels.append((block, lo, hi))
        self._energy.append((sq, count))
        while len(lo) > self.FACTOR:
            block *= self.FACTOR
            lo, hi = self._reduce(lo, hi, self.FACTOR)
            groups = np.arange(0, len(sq), self.FACTOR)
            sq, count = np.add.reduceat(sq, groups), np.add.reduceat(count, groups)
            self.levels.append((block, lo, hi))
            self._energy.append((sq, count))

    @staticmethod
    def _reduce(lo, hi, size):
        m = len(lo) // size * size
        out_lo = lo[:m].reshape(-1, size).min(axis=1) if m else lo[:0]
        out_hi = hi[:m].reshape(-1, size).max(axis=1) if m else hi[:0]
        if m < len(lo):
            out_lo = np.append(out_lo, lo[m:].min())
            out_hi = np.append(out_hi, hi[m:].max())
        return out_lo, out_hi

    def _plan(self, start, end, count):
        """The window clipped to the signal, the sample edge of each column, and the samples per column."""
        start, end = max(0, int(start)), min(self.n, int(end))
        per = (end - start) / count
        edges = np.round(start + np.arange(count + 1) * per).astype(np.int64)
        edges[-1] = end
        return start, end, edges, per

    def _level(self, per):
        """The index of the coarsest level whose blocks are at most an eighth of a column wide."""
        chosen = 0
        for index, (block, _, _) in enumerate(self.levels):
            if block <= per / 8:
                chosen = index
        return chosen

    def _empty(self, start, end, count):
        return self.n == 0 or min(self.n, int(end)) <= max(0, int(start)) or count <= 0

    def columns(self, start, end, count):
        """(lo, hi) for `count` columns spread evenly over samples [start, end)."""
        if self._empty(start, end, count):
            return np.zeros(max(count, 0), np.float32), np.zeros(max(count, 0), np.float32)
        start, end, edges, per = self._plan(start, end, count)
        if per < 8 * self.BASE:                     # zoomed in: the samples themselves are cheap enough and exact
            seg = self.mono[start:end]
            idx = np.minimum(edges[:-1] - start, len(seg) - 1)
            return np.minimum.reduceat(seg, idx), np.maximum.reduceat(seg, idx)
        block, lo, hi = self.levels[self._level(per)]
        first_block, last_block = start // block, -(-end // block)       # only the blocks inside the window
        lo, hi = lo[first_block:last_block], hi[first_block:last_block]
        first = np.minimum(edges[:-1] // block - first_block, len(lo) - 1)
        return np.minimum.reduceat(lo, first), np.maximum.reduceat(hi, first)

    def body(self, start, end, count):
        """RMS for `count` columns spread evenly over samples [start, end): the solid body drawn inside the peaks."""
        if self._empty(start, end, count):
            return np.zeros(max(count, 0), np.float32)
        start, end, edges, per = self._plan(start, end, count)
        if per < 8 * self.BASE:
            squares = self.mono[start:end].astype(np.float64) ** 2
            idx = np.minimum(edges[:-1] - start, len(squares) - 1)
            sums = np.add.reduceat(squares, idx)
            counts = np.maximum(np.diff(np.append(idx, len(squares))), 1)
        else:
            index = self._level(per)
            block = self.levels[index][0]
            sq, samples = self._energy[index]
            first_block, last_block = start // block, -(-end // block)
            sq, samples = sq[first_block:last_block], samples[first_block:last_block]
            first = np.minimum(edges[:-1] // block - first_block, len(sq) - 1)
            sums, counts = np.add.reduceat(sq, first), np.add.reduceat(samples, first)
        return np.sqrt(sums / np.maximum(counts, 1)).astype(np.float32)


Spectrogram = namedtuple('Spectrogram', 'db bin_hz hop_seconds')


def spectrogram(x, rate, max_hz=12000.0, max_frames=12000):
    """dB below full scale per frequency bin (rows, low to high) and frame (columns), up to max_hz."""
    mono = ac.to_mono(x)
    n_fft = int(2 ** round(math.log2(max(rate * 0.0256, 64))))
    hop = max(1, int(rate * 0.005), len(mono) // max_frames)
    padded = np.pad(mono.astype(np.float32), (n_fft // 2, n_fft // 2), mode='reflect' if len(mono) > n_fft else 'constant')
    starts = np.arange(0, max(len(padded) - n_fft + 1, 1), hop)
    window = np.hanning(n_fft).astype(np.float32)
    bins = int(min(max_hz, rate / 2) / (rate / n_fft)) + 1
    out = np.empty((bins, len(starts)), dtype=np.float32)
    scale = n_fft / 4.0                                     # a full-scale sine reads 0 dB
    for first in range(0, len(starts), 512):
        idx = starts[first:first + 512, None] + np.arange(n_fft)[None, :]
        mag = np.abs(np.fft.rfft(padded[idx] * window, axis=1))[:, :bins]
        out[:, first:first + len(idx)] = (20.0 * np.log10(mag / scale + 1e-9)).T
    return Spectrogram(out, rate / n_fft, hop / rate)


# ---- levels -----------------------------------------------------------------------------------------------------------

def frame_db(x, rate):
    """RMS level of each 20 ms frame in dB (the last partial frame is dropped)."""
    m = ac.to_mono(x).astype(np.float64)
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    usable = len(m) // frame * frame
    if usable == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(m[:usable].reshape(-1, frame) ** 2, axis=1))
    return 20.0 * np.log10(rms + 1e-12)


QUIET_STRETCH_FRAMES = 10       # 0.2 s of frames in a row is enough to read the noise floor from


def noise_floor_db(x, rate):
    """The level of the quietest tenth of the frames, or of the quietest 0.2 s stretch if that is lower, ignoring digital
    silence. An estimate: it needs a pause. The stretch keeps the estimate from rising when the pauses are cut short
    (Auto-trim, Shorten pauses), which would leave the pauses under a tenth of the frames."""
    db = frame_db(x, rate)
    db = db[db > -110.0]
    if not db.size:
        return -120.0
    floor = float(np.percentile(db, 10))
    if db.size >= QUIET_STRETCH_FRAMES:
        stretches = np.lib.stride_tricks.sliding_window_view(db, QUIET_STRETCH_FRAMES)
        floor = min(floor, float(np.median(stretches, axis=1).min()))
    return round(floor, 1)


def snr_db(x, rate):
    return round(ac.voiced_rms_db(x, rate) - noise_floor_db(x, rate), 1)


VOICE_BANDS = ((1000.0, 4000.0), (4000.0, 11000.0))     # where the consonants are, which noise cleaning takes first
VOICE_CHECK_SECONDS = 60.0                              # plenty to judge by, and a long import stays quick


def voice_loss_db(before, after, rate):
    """How far a cleaned clip has fallen below the voice in the clip it was made from, in its worst band, in dB. Zero when it
    has not fallen, or when the clip has no pauses or no speech to tell them apart.

    A measure of the noise alone cannot see this harm: taking the voice away along with the noise lowers the noise too, so
    speech above the noise goes up. There is no clean voice to compare with, so one is worked out: the speech holds the
    voice and the noise together and the pauses hold the noise alone, and the difference in each band is the voice. A band
    where the voice is buried in the noise is left out, since nothing can be said about it.
    """
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    x, y = ac.to_mono(before), ac.to_mono(after)
    count = min(min(len(x), len(y)) // frame, int(VOICE_CHECK_SECONDS / ac.FRAME_SECONDS))
    speech = frame_db(x, rate)[:count] > speech_threshold_db(x, rate)
    if not speech.any() or speech.all():
        return 0.0
    window = np.hanning(frame).astype(np.float32)
    freqs = np.fft.rfftfreq(frame, 1.0 / rate)

    def power(signal):
        return np.abs(np.fft.rfft(signal[:count * frame].reshape(-1, frame) * window, axis=1)) ** 2

    was, now = power(x), power(y)
    worst = 0.0
    for low, high in VOICE_BANDS:
        band = (freqs >= low) & (freqs < high)
        if not band.any():
            continue
        mixed = float(was[speech][:, band].sum(axis=1).mean())
        noise = float(was[~speech][:, band].sum(axis=1).mean())
        left = float(now[speech][:, band].sum(axis=1).mean())
        voice = mixed - noise
        if voice > noise:
            worst = max(worst, 10.0 * math.log10(voice / max(left, 1e-30)))
    return round(worst, 1)


_K_SHELF = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_K_HIGHPASS = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def loudness_lufs(x, rate):
    """Integrated loudness of a mono signal, ITU-R BS.1770-4 (K-weighting, 400 ms blocks, absolute and relative gates)."""
    m = ac.to_mono(x)
    if rate != 48000:
        m = ac.resample(m, rate, 48000)
    y = lfilter(*_K_HIGHPASS, lfilter(*_K_SHELF, m.astype(np.float64)))
    block, hop = 19200, 4800
    if len(y) < block:
        ms = np.array([float(np.mean(y * y))]) if len(y) else np.zeros(1)
    else:
        csum = np.concatenate([[0.0], np.cumsum(y * y)])
        starts = np.arange(0, len(y) - block + 1, hop)
        ms = (csum[starts + block] - csum[starts]) / block
    with np.errstate(divide='ignore'):
        block_lufs = -0.691 + 10.0 * np.log10(ms)
    kept = block_lufs > -70.0
    if not kept.any():
        return -120.0
    relative = -0.691 + 10.0 * math.log10(float(np.mean(ms[kept]))) - 10.0
    kept &= block_lufs > relative
    if not kept.any():
        return -120.0
    return round(-0.691 + 10.0 * math.log10(float(np.mean(ms[kept]))), 1)


def bandwidth_hz(x, rate):
    """The frequency below which 99.9% of the energy of the voiced frames lies: a band-limited source shows here."""
    m = ac.to_mono(x).astype(np.float64)
    db = frame_db(m, rate)
    if db.size == 0:
        return 0.0
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    voiced = np.nonzero(db > max(ac.SILENCE_FRAME_DB, db.max() - 30.0))[0]
    if voiced.size == 0:
        return 0.0
    signal = np.concatenate([m[i * frame:(i + 1) * frame] for i in voiced])
    freqs, power = welch(signal, fs=rate, nperseg=min(2048, len(signal)))
    cumulative = np.cumsum(power)
    return round(float(freqs[min(int(np.searchsorted(cumulative, 0.999 * cumulative[-1])), len(freqs) - 1)]), 0)


def _runs(flags):
    """Runs of True in a boolean array as (first, one past last) index pairs."""
    padded = np.concatenate([[False], flags, [False]])
    changes = np.nonzero(padded[1:] != padded[:-1])[0]
    return list(zip(changes[::2], changes[1::2]))


def detect_hum(x, rate):
    """(mains frequency, strength in dB) when 50 or 60 Hz hum stands out in the longest quiet stretch, else None."""
    m = ac.to_mono(x).astype(np.float64)
    db = frame_db(m, rate)
    if db.size < 15:
        return None
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    runs = _runs(db <= np.percentile(db[db > -110.0], 10) + 6.0) if (db > -110.0).any() else []
    if not runs:
        return None
    first, last = max(runs, key=lambda r: r[1] - r[0])
    if (last - first) * frame < 0.3 * rate:
        return None
    freqs, power = welch(m[first * frame:last * frame], fs=rate, nperseg=min(1 << 15, (last - first) * frame))
    level = 10.0 * np.log10(power + 1e-30)
    best = None
    for mains in (50.0, 60.0):
        strengths = []
        for k in (1, 2, 3):
            f = mains * k
            if f >= rate / 2:
                break
            peak = level[(freqs > f - 1.0) & (freqs < f + 1.0)]
            around = level[((freqs > f - 8) & (freqs < f - 3)) | ((freqs > f + 3) & (freqs < f + 8))]
            if peak.size and around.size:
                strengths.append(float(peak.max() - np.median(around)))
        # Hum is a family of lines: one steady tone that happens to fall on a harmonic (a held vowel) is not enough.
        if sum(s >= 8.0 for s in strengths) >= 2 and (best is None or max(strengths) > best[1]):
            best = (mains, max(strengths))
    return (best[0], round(best[1], 1)) if best and best[1] >= 12.0 else None


# ---- silence and pauses -----------------------------------------------------------------------------------------------

def speech_threshold_db(x, rate):
    """Frames at or below this level count as pauses: well above the noise floor, well below the speech."""
    db = frame_db(x, rate)
    if db.size == 0:
        return ac.SILENCE_FRAME_DB
    speech = float(np.percentile(db, 90))
    return min(max(noise_floor_db(x, rate) + 6.0, speech - 25.0, ac.SILENCE_FRAME_DB - 15.0), speech - 10.0)


def silent_runs(x, rate, min_seconds=0.0):
    """Runs of pause frames as (start, end) seconds on the signal's own timeline. Includes the ends of the clip."""
    db = frame_db(x, rate)
    if db.size == 0:
        return []
    runs = [(a * ac.FRAME_SECONDS, b * ac.FRAME_SECONDS) for a, b in _runs(db <= speech_threshold_db(x, rate))]
    return [(a, b) for a, b in runs if b - a >= min_seconds]


def edge_silence(x, rate):
    """(seconds of pause before the first speech, seconds of pause after the last)."""
    total = len(ac.to_mono(x)) / rate
    runs = silent_runs(x, rate)
    lead = runs[0][1] if runs and runs[0][0] <= 1e-9 else 0.0
    tail = total - runs[-1][0] if runs and runs[-1][1] >= len(frame_db(x, rate)) * ac.FRAME_SECONDS - 1e-9 else 0.0
    return round(lead, 3), round(max(tail, 0.0), 3)


def auto_trim_ranges(x, rate, margin=0.15):
    """Ranges (seconds) to cut so that `margin` seconds of pause remain before the first speech and after the last."""
    total = len(ac.to_mono(x)) / rate
    lead, tail = edge_silence(x, rate)
    ranges = []
    if lead > margin + 0.05:
        ranges.append((0.0, lead - margin))
    if tail > margin + 0.05:
        ranges.append((total - tail + margin, total))
    return ranges


def shorten_pause_ranges(x, rate, keep_seconds=0.5):
    """Ranges (seconds) to cut from the middle of pauses longer than keep_seconds, leaving keep_seconds of each."""
    total = len(ac.to_mono(x)) / rate
    ranges = []
    for a, b in silent_runs(x, rate, min_seconds=keep_seconds + 0.1):
        if a <= 1e-9 or b >= total - ac.FRAME_SECONDS:
            continue                               # the ends of the clip are Auto-trim's business
        cut_a, cut_b = a + keep_seconds / 2.0, b - keep_seconds / 2.0
        if cut_b - cut_a >= ac.MIN_SELECTION_SECONDS:
            ranges.append((cut_a, cut_b))
    return ranges


# ---- the Check tab ----------------------------------------------------------------------------------------------------

Check = namedtuple('Check', 'key label value status hint')     # status: ok, warn, bad or info


def quality_report(x, rate=ac.EXPORT_RATE, recorded_clipped=0.0):
    """The measurements of a finished clip, each with a status, in the order the Check tab shows them.

    `recorded_clipped` is the share of the recording behind it that is at full scale (render.recorded_clipping), which the
    finished clip can hide.
    """
    stats = ac.measure(x, rate)
    seconds = stats['seconds']
    speech = ac.voiced_rms_db(x, rate)
    floor = noise_floor_db(x, rate)
    lead, tail = edge_silence(x, rate)
    hum = detect_hum(x, rate)
    band = bandwidth_hz(x, rate)
    clipped = max(stats['clipped_fraction'], recorded_clipped)

    def rank(value, ok, warn):
        return 'ok' if ok(value) else 'warn' if warn(value) else 'bad'

    rows = [
        Check('length', 'Length', f'{seconds:.1f} s',
              rank(seconds, lambda v: 10 <= v <= 25, lambda v: ac.MIN_SECONDS <= v <= ac.MAX_SECONDS),
              'Between 6 and 40 s can be kept; 10 to 25 s works best.'),
        Check('speech', 'Speech level', f'{speech:.1f} dB',
              rank(abs(speech - ac.TARGET_VOICED_DB), lambda v: v <= 3, lambda v: v <= 8),
              f'Loudness of the voiced parts. The target is {ac.TARGET_VOICED_DB:.0f} dB; Normalize sets it.'),
        Check('peak', 'Peak', f"{stats['peak_dbfs']:.1f} dBFS",
              'ok' if stats['peak_dbfs'] <= -1.0 else 'warn',
              'Peaks above -1 dBFS leave no headroom; the limiter keeps them under the ceiling.'),
        Check('clipping', 'Clipping', f'{clipped * 100:.3f}% of samples', 'ok' if clipped <= 0.001 else 'bad',
              'More than 0.1% of samples at full scale is flagged as clipped. The recording counts too: gain and the '
              'limiter can hide clipping, but not repair it.'),
        Check('loudness', 'Loudness', f'{loudness_lufs(x, rate):.1f} LUFS', 'info',
              'Integrated loudness (ITU-R BS.1770). For comparison only.'),
        Check('noise', 'Noise floor', f'{floor:.1f} dB',
              rank(floor, lambda v: v <= -55, lambda v: v <= -45),
              f'Level of the quietest tenth of the clip. Speech is about {max(speech - floor, 0):.0f} dB above it.'),
        Check('bandwidth', 'Bandwidth', f'{band / 1000:.1f} kHz',
              rank(band, lambda v: v >= 4500, lambda v: v >= 3500),
              'Frequency below which 99.9% of the speech energy lies. Phone-quality sources stop below about 4 kHz.'),
        Check('hum', 'Mains hum',
              'none found' if hum is None else f'{hum[0]:.0f} Hz, {hum[1]:.0f} dB above its surroundings',
              'ok' if hum is None else 'warn',
              'Turn on the hum notch in Process if it is found.'),
        Check('ends', 'Silence at the ends', f'{lead:.2f} s before, {tail:.2f} s after',
              'ok' if max(lead, tail) <= 0.8 else 'warn',
              'Long pauses at the ends are wasted reference time. Auto-trim keeps 0.15 s.'),
        Check('silence', 'Silence overall', f"{stats['silence_fraction'] * 100:.0f}% of frames",
              'ok' if stats['silence_fraction'] <= ac.MOSTLY_SILENT else 'warn',
              'More than 40% pauses leaves little speech.'),
    ]
    return rows
