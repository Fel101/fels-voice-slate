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
        sq = np.empty(len(starts), dtype=np.float64)
        step = ac.BLOCK // block * block            # a block at a time: no 64-bit copy of a long recording
        for first in range(0, self.n, step):
            chunk = self.mono[first:first + step].astype(np.float64)
            sums = np.add.reduceat(chunk ** 2, np.arange(0, len(chunk), block))
            sq[first // block:first // block + len(sums)] = sums
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
    half, n = n_fft // 2, len(mono)
    hop = max(1, int(rate * 0.005), n // max_frames)
    if n > n_fft:
        source, shift = mono, half      # the frames are read from the recording itself, mirrored at its ends, not from a copy
    else:
        source, shift = np.pad(mono.astype(np.float32), (half, half)), 0
    starts = np.arange(0, max(n + 2 * half - n_fft + 1, 1), hop)
    window = np.hanning(n_fft).astype(np.float32)
    bins = int(min(max_hz, rate / 2) / (rate / n_fft)) + 1
    out = np.empty((bins, len(starts)), dtype=np.float32)
    scale = n_fft / 4.0                                     # a full-scale sine reads 0 dB
    for first in range(0, len(starts), 512):
        idx = starts[first:first + 512, None] + np.arange(n_fft)[None, :] - shift
        if shift:
            idx = np.abs(idx)                               # as np.pad's 'reflect': -1 reads sample 1
            past = idx >= n
            idx[past] = 2 * (n - 1) - idx[past]
        mag = np.abs(np.fft.rfft(source[idx].astype(np.float32) * window, axis=1))[:, :bins]
        out[:, first:first + len(idx)] = (20.0 * np.log10(mag / scale + 1e-9)).T
    return Spectrogram(out, rate / n_fft, hop / rate)


# ---- levels -----------------------------------------------------------------------------------------------------------

def frame_db(x, rate, energy=None):
    """RMS level of each 20 ms frame in dB (the last partial frame is dropped). `energy` is ac.frame_energy of the same
    clip, when it has been worked out already: the measures below all start from it, so one look at a clip shares it."""
    return ac.energy_db(ac.frame_energy(x, rate) if energy is None else energy)


QUIET_STRETCH_FRAMES = 10       # 0.2 s of frames in a row is enough to read the noise floor from


def noise_floor_db(x, rate, energy=None):
    """The level of the quietest tenth of the frames, or of the quietest 0.2 s stretch if that is lower, ignoring digital
    silence. An estimate: it needs a pause. The stretch keeps the estimate from rising when the pauses are cut short
    (Auto-trim, Shorten pauses), which would leave the pauses under a tenth of the frames."""
    db = frame_db(x, rate, energy)
    db = db[db > -110.0]
    if not db.size:
        return -120.0
    floor = float(np.percentile(db, 10))
    if db.size >= QUIET_STRETCH_FRAMES:
        stretches = np.lib.stride_tricks.sliding_window_view(db, QUIET_STRETCH_FRAMES)
        floor = min(floor, float(np.median(stretches, axis=1).min()))
    return round(floor, 1)


def snr_db(x, rate, energy=None):
    energy = ac.frame_energy(x, rate) if energy is None else energy
    return round(ac.voiced_rms_db(x, rate, energy) - noise_floor_db(x, rate, energy), 1)


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
    energy = ac.frame_energy(x, rate)
    speech = frame_db(x, rate, energy)[:count] > speech_threshold_db(x, rate, energy)
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
    """Integrated loudness of a mono signal, ITU-R BS.1770-4 (K-weighting, 400 ms blocks, absolute and relative gates).

    Worked out a piece at a time: the filters carry their state from one piece to the next, and the energy is summed per
    100 ms hop, of which each 400 ms block holds four. The result is the one a single pass over everything gives."""
    m = ac.to_mono(x)
    pieces = ac.resample_blocks(m, rate, 48000) if rate != 48000 else (m[i:i + ac.BLOCK] for i in range(0, len(m), ac.BLOCK))
    shelf = np.zeros(len(_K_SHELF[1]) - 1)
    highpass = np.zeros(len(_K_HIGHPASS[1]) - 1)
    block, hop = 19200, 4800
    hops, left = [], np.zeros(0)
    total, length = 0.0, 0
    for piece in pieces:
        y, shelf = lfilter(*_K_SHELF, np.asarray(piece, dtype=np.float64), zi=shelf)
        y, highpass = lfilter(*_K_HIGHPASS, y, zi=highpass)
        total += float(np.dot(y, y))
        length += len(y)
        y = np.concatenate([left, y])
        whole = len(y) // hop * hop
        hops.append(np.sum(y[:whole].reshape(-1, hop) ** 2, axis=1))
        left = y[whole:]
    if length < block:
        ms = np.array([total / length]) if length else np.zeros(1)
    else:
        sums = np.concatenate(hops)
        count = (length - block) // hop + 1
        ms = (sums[:count] + sums[1:count + 1] + sums[2:count + 2] + sums[3:count + 3]) / block
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


BANDWIDTH_PIECE_FRAMES = 3000          # voiced frames looked at together: a minute at most, so a long take needs little memory


def bandwidth_hz(x, rate, energy=None):
    """The frequency below which 99.9% of the energy of the voiced frames lies: a band-limited source shows here.

    The spectrum is averaged over pieces of up to a minute of voiced frames, weighted by their length, which comes to the
    same as one spectrum over all of them for anything but a few segments where two pieces meet."""
    m = ac.to_mono(x)
    db = frame_db(m, rate, energy)
    if db.size == 0:
        return 0.0
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    voiced = np.nonzero(db > max(ac.SILENCE_FRAME_DB, db.max() - 30.0))[0]
    if voiced.size == 0:
        return 0.0
    nperseg = min(2048, voiced.size * frame)
    power, weight, freqs = None, 0, None
    for first in range(0, voiced.size, BANDWIDTH_PIECE_FRAMES):
        frames = voiced[first:first + BANDWIDTH_PIECE_FRAMES]
        signal = m[(frames[:, None] * frame + np.arange(frame)[None, :]).reshape(-1)].astype(np.float64)
        if len(signal) < nperseg:
            if power is not None:
                break                       # a short remainder after full pieces changes nothing that matters
            nperseg = len(signal)
        freqs, piece = welch(signal, fs=rate, nperseg=nperseg)
        power = piece * len(signal) if power is None else power + piece * len(signal)
        weight += len(signal)
    power = power / weight
    cumulative = np.cumsum(power)
    return round(float(freqs[min(int(np.searchsorted(cumulative, 0.999 * cumulative[-1])), len(freqs) - 1)]), 0)


def _runs(flags):
    """Runs of True in a boolean array as (first, one past last) index pairs."""
    padded = np.concatenate([[False], flags, [False]])
    changes = np.nonzero(padded[1:] != padded[:-1])[0]
    return list(zip(changes[::2], changes[1::2]))


def detect_hum(x, rate, energy=None):
    """(mains frequency, strength in dB) when 50 or 60 Hz hum stands out in the longest quiet stretch, else None."""
    m = ac.to_mono(x)
    db = frame_db(m, rate, energy)
    if db.size < 15:
        return None
    frame = max(1, int(rate * ac.FRAME_SECONDS))
    runs = _runs(db <= np.percentile(db[db > -110.0], 10) + 6.0) if (db > -110.0).any() else []
    if not runs:
        return None
    first, last = max(runs, key=lambda r: r[1] - r[0])
    if (last - first) * frame < 0.3 * rate:
        return None
    quiet = m[first * frame:last * frame].astype(np.float64)
    freqs, power = welch(quiet, fs=rate, nperseg=min(1 << 15, (last - first) * frame))
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

SPEECH_ABOVE_NOISE_DB = 15.0    # the loudest tenth of the frames is speech when this far above the noise floor: a room whose
                                # noise rises and falls by 6 dB comes within 12 dB of its own floor


def speech_threshold_db(x, rate, energy=None):
    """Frames at or below this level count as pauses: well above the noise floor, well below the speech.

    The speech level is that of the loudest tenth of the frames. In a take that is mostly pause, such as one short word
    in ten seconds, that tenth is still pause, and the pauses would be taken for speech: then the loudest hundredth is
    the speech. The threshold stays below the frames the speech level is read from, so some of every take is speech, and
    a take is never all pause, however quiet the voice.
    """
    energy = ac.frame_energy(x, rate) if energy is None else energy
    db = frame_db(x, rate, energy)
    if db.size == 0:
        return ac.SILENCE_FRAME_DB
    noise = noise_floor_db(x, rate, energy)
    speech = float(np.percentile(db, 90))
    if speech < noise + SPEECH_ABOVE_NOISE_DB:
        speech = float(np.percentile(db, 99))
    return min(max(noise + 6.0, speech - 25.0, ac.SILENCE_FRAME_DB - 15.0), speech - 10.0)


LIVE_ABOVE_ROOM_DB = 12.0       # while recording, a block this far above the room is the voice
LIVE_FLOOR_DB = -55.0           # and never below this, so that hiss on a silent input is not taken for a voice
LIVE_SPEECH_SECONDS = 0.3       # this much voice before a pause can end a take: the click of the record key is not speech


def live_pause(rows):
    """Whether a voice has been heard in a take being recorded, and for how many seconds it has not been since.

    `rows` are (end second, RMS dBFS) per block, as Capture.loudness gives them. The room is the quietest tenth of the
    blocks, which the half second kept before Record makes sure of; the voice is well above it. Short sounds do not count
    until 0.3 s of them have been heard, so a click or a bump cannot end a take before the line has been read."""
    rows = np.asarray(rows, dtype=np.float64).reshape(-1, 2)
    if not len(rows):
        return False, 0.0
    ends, db = rows[:, 0], rows[:, 1]
    lengths = np.diff(ends, prepend=0.0)
    loud = db >= max(float(np.percentile(db, 10)) + LIVE_ABOVE_ROOM_DB, LIVE_FLOOR_DB)
    if lengths[loud].sum() < LIVE_SPEECH_SECONDS:
        return False, 0.0
    return True, float(ends[-1] - ends[np.nonzero(loud)[0][-1]])


def silent_runs(x, rate, min_seconds=0.0, energy=None):
    """Runs of pause frames as (start, end) seconds on the signal's own timeline. Includes the ends of the clip."""
    energy = ac.frame_energy(x, rate) if energy is None else energy
    db = frame_db(x, rate, energy)
    if db.size == 0:
        return []
    runs = [(a * ac.FRAME_SECONDS, b * ac.FRAME_SECONDS) for a, b in _runs(db <= speech_threshold_db(x, rate, energy))]
    return [(a, b) for a, b in runs if b - a >= min_seconds]


def edge_silence(x, rate, energy=None):
    """(seconds of pause before the first speech, seconds of pause after the last)."""
    energy = ac.frame_energy(x, rate) if energy is None else energy
    total = len(ac.to_mono(x)) / rate
    runs = silent_runs(x, rate, energy=energy)
    lead = runs[0][1] if runs and runs[0][0] <= 1e-9 else 0.0
    tail = total - runs[-1][0] if runs and runs[-1][1] >= len(energy) * ac.FRAME_SECONDS - 1e-9 else 0.0
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


WORD_EDGE_REACH = 0.08          # seconds either side of where a recognizer says a word starts or ends
WORD_EDGE_FRAME = 0.005


def quietest_near(x, rate, seconds, reach=WORD_EDGE_REACH):
    """The moment within `reach` of `seconds` where the sound is quietest. The times a recognizer gives a word are rough, and a
    cut made exactly on them can leave the first sound of the word behind. The gap between two words is where it belongs."""
    m = ac.to_mono(x)
    frame = max(1, int(rate * WORD_EDGE_FRAME))
    first, last = max(int((seconds - reach) * rate), 0), min(int((seconds + reach) * rate), len(m))
    count = (last - first) // frame
    if count < 2:
        return seconds
    energy = np.sum(m[first:first + count * frame].astype(np.float64).reshape(-1, frame) ** 2, axis=1)
    return (first + (int(np.argmin(energy)) + 0.5) * frame) / rate


SPLIT_PAUSE_SECONDS = 0.35      # a pause at least this long is a place to split a take


def split_ranges(x, rate, shortest, keep_min, keep_max, energy=None):
    """Where to split a long take: (first, end) sample ranges that cover all of it, cut in the middle of its pauses.

    Each part closes at the first pause after it has reached `shortest` seconds, which gives parts of about that length
    that end where the speaker stopped. A last part too short to keep (`keep_min`) joins the one before, if the two together
    are not too long to keep (`keep_max`). One range, the whole take, means there is nowhere to split it.
    """
    n = len(ac.to_mono(x))
    total = n / rate
    pauses = [(a + b) / 2 for a, b in silent_runs(x, rate, min_seconds=SPLIT_PAUSE_SECONDS, energy=energy)
              if a > 1e-9 and b < total - 0.05]
    parts, start = [], 0.0
    for point in pauses:
        if point - start >= shortest:
            parts.append((start, point))
            start = point
    if parts and total - start < keep_min and total - parts[-1][0] <= keep_max:
        parts[-1] = (parts[-1][0], total)
    else:
        parts.append((start, total))
    return [(int(round(a * rate)), min(int(round(b * rate)), n)) for a, b in parts]


# ---- the Check tab ----------------------------------------------------------------------------------------------------

Check = namedtuple('Check', 'key label value status hint')     # status: ok, warn, bad or info


# The limits of the Check tab, which the room check uses too, so that the two never disagree.
SPEECH_OK_DB = 3.0              # the voiced level this close to ac.TARGET_VOICED_DB is right
SPEECH_WARN_DB = 8.0            # and this close is worth a note
PEAK_OK_DBFS = -1.0             # a peak above this leaves no headroom
CLIPPED_MAX = 0.001             # more of the samples than this at full scale is clipping
NOISE_OK_DB = -55.0             # a noise floor at or below this is quiet
NOISE_WARN_DB = -45.0           # and at or below this is worth a note


def rank_speech(speech_db):
    """'ok', 'warn' or 'bad' for a voiced level, by its distance from the target."""
    off = abs(speech_db - ac.TARGET_VOICED_DB)
    return 'ok' if off <= SPEECH_OK_DB else 'warn' if off <= SPEECH_WARN_DB else 'bad'


def rank_noise(floor_db):
    """'ok', 'warn' or 'bad' for a noise floor."""
    return 'ok' if floor_db <= NOISE_OK_DB else 'warn' if floor_db <= NOISE_WARN_DB else 'bad'


def quality_report(x, rate=ac.EXPORT_RATE, recorded_clipped=0.0, purpose=None):
    """The measurements of a finished clip, each with a status, in the order the Check tab shows them.

    `recorded_clipped` is the share of the recording behind it that is at full scale (render.recorded_clipping), which the
    finished clip can hide. `purpose` gives the length rules (a Reference's if none is given).
    """
    rules = purpose or ac.PURPOSES[ac.DEFAULT_PURPOSE]
    energy = ac.frame_energy(x, rate)           # worked out once, for every measure below
    stats = ac.measure(x, rate, energy)
    seconds = stats['seconds']
    speech = ac.voiced_rms_db(x, rate, energy)
    floor = noise_floor_db(x, rate, energy)
    lead, tail = edge_silence(x, rate, energy)
    hum = detect_hum(x, rate, energy)
    band = bandwidth_hz(x, rate, energy)
    clipped = max(stats['clipped_fraction'], recorded_clipped)

    def rank(value, ok, warn):
        return 'ok' if ok(value) else 'warn' if warn(value) else 'bad'

    rows = [
        Check('length', 'Length', f'{seconds:.1f} s',
              rank(seconds, lambda v: rules.best_from <= v <= rules.best_to,
                   lambda v: rules.min_seconds <= v <= rules.max_seconds),
              f'Between {rules.min_seconds:g} and {rules.max_seconds:g} s can be kept; {rules.best_from:g} to '
              f'{rules.best_to:g} s works best.'),
        Check('speech', 'Speech level', f'{speech:.1f} dB', rank_speech(speech),
              f'Loudness of the voiced parts. The target is {ac.TARGET_VOICED_DB:.0f} dB; Normalize sets it.'),
        Check('peak', 'Peak', f"{stats['peak_dbfs']:.1f} dBFS",
              'ok' if stats['peak_dbfs'] <= PEAK_OK_DBFS else 'warn',
              'Peaks above -1 dBFS leave no headroom; the limiter keeps them under the ceiling.'),
        Check('clipping', 'Clipping', f'{clipped * 100:.3f}% of samples', 'ok' if clipped <= CLIPPED_MAX else 'bad',
              'More than 0.1% of samples at full scale is flagged as clipped. The recording counts too: gain and the '
              'limiter can hide clipping, but not repair it.'),
        Check('loudness', 'Loudness', f'{loudness_lufs(x, rate):.1f} LUFS', 'info',
              'Integrated loudness (ITU-R BS.1770). For comparison only.'),
        Check('noise', 'Noise floor', f'{floor:.1f} dB', rank_noise(floor),
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


# ---- the room and the level, before a session -------------------------------------------------------------------------

Room = namedtuple('Room', 'heard floor_db speech_db floor_at_target_db peak_dbfs clipped hum room level headroom')


def room_check(quiet, spoken, rate, out_rate=ac.EXPORT_RATE):
    """What a few seconds of the room and then a spoken sentence say, judged by the Check tab's limits.

    `quiet` and `spoken` are the two parts of the recording, mono, at `rate`. The whole of it is measured as the Check tab
    measures a take of it, at `out_rate`, so that the two cannot disagree; peaks and clipping are read on the recording
    itself, as the Check tab counts the recording's clipping too, and hum in the quiet part. The room is judged where it
    will be once the voice is at the target level, as Normalize puts it: a quiet voice is raised, and the room with it.
    `room`, `level` and `headroom` are 'ok', 'warn' or 'bad'; with no voice heard, `level` is 'bad' and `room` is judged
    as it is.
    """
    quiet, spoken = np.asarray(quiet, dtype=np.float32), np.asarray(spoken, dtype=np.float32)
    whole = np.concatenate([quiet, spoken])
    stats = ac.measure(whole, rate)
    x = ac.resample(whole, rate, out_rate) if rate != out_rate else whole
    q = ac.resample(quiet, rate, out_rate) if rate != out_rate else quiet
    energy = ac.frame_energy(x, out_rate)
    floor = noise_floor_db(x, out_rate, energy)
    speech = ac.voiced_rms_db(x, out_rate, energy) if len(x) else -240.0
    heard = speech > max(floor + LIVE_ABOVE_ROOM_DB, LIVE_FLOOR_DB)
    at_target = round(floor + (ac.TARGET_VOICED_DB - speech), 1) if heard else floor
    clipped = stats['clipped_fraction']
    headroom = 'bad' if clipped > CLIPPED_MAX else 'ok' if stats['peak_dbfs'] <= PEAK_OK_DBFS else 'warn'
    return Room(heard=heard, floor_db=floor, speech_db=round(speech, 1), floor_at_target_db=at_target,
                peak_dbfs=stats['peak_dbfs'], clipped=clipped, hum=detect_hum(q, out_rate), room=rank_noise(at_target),
                level=rank_speech(speech) if heard else 'bad', headroom=headroom if heard else 'ok')
