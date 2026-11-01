"""Auto analyze and Auto fix: measure a clip, say what is wrong in plain words, and plan the repairs.

The plan uses only what the editor already offers (the high-pass and hum filters, noise cleaning, cuts at the ends and
inside long pauses, gain and the limiter) in the order the clip is cleaned up: filters first, because rumble and hum
hide the silence and the noise; then noise, then the cuts, which are found on the cleaned clip; then the level. Each
repair is tried on a copy of the recipe, and noise cleaning is measured before it is kept, so the plan is rendered
rather than guessed. Noise cleaning can change how a voice sounds, so it is used only on a clearly noisy clip, set from
the noise that clip actually has, and dropped if it takes too much of the voice along with the noise. Applied to the
take, the whole plan is one undo step.

`diagnose` takes the recipe and a function that renders it. Nothing here touches a file or a window.
"""
from collections import namedtuple
from types import SimpleNamespace

import numpy as np
from scipy.signal import welch

from .. import audio_core as ac
from . import analysis
from . import chain as chain_mod
from . import render

TRIM_MARGIN = 0.15              # seconds of pause left at a trimmed end (the Auto-trim button leaves the same)
EDGE_LIMIT = 0.8                # a pause longer than this at either end is trimmed
PAUSE_LIMIT = 1.2               # a pause longer than this inside the clip is shortened...
PAUSE_KEEP = 0.6                # ...to this
HIGHPASS_HZ = 80.0
RUMBLE_LIMIT = -24.0            # dB of energy under 60 Hz against the speech band; more than this is too much
SNR_CLEAN, SNR_NOISY = 35.0, 25.0       # speech this far above the noise is clean; below the second it is clearly noisy.
#                                         The Check tab's noise-floor limits (-55 and -45 dB) at the -20 dB speech target.
NOISE_ABOVE_FLOOR = 5.0         # noise cleaning does best set about this far above the clip's own noise floor
NOISE_MIN_GAIN = 3.0            # noise cleaning is kept only if it lowers the noise by at least this many dB
VOICE_LOSS_LIMIT = 4.0          # and only if it takes no more than this many dB from the voice in any band
LEVEL_TOLERANCE = 3.0           # speech within this many dB of the target needs no change
PEAK_CEILING = -1.0             # dBFS; a higher peak leaves no headroom
NARROW_HZ = 3500.0
NO_SPEECH_DB = -100.0           # voiced_rms_db gives -240 when no frame is loud enough to be speech
SHOWN_AFTER = {                 # the Check rows each repair can change, for the before and after list
    'rumble': ('peak',), 'hum': ('hum',), 'ends': ('length', 'ends'), 'pauses': ('length',),
    'level': ('speech', 'peak'), 'peak': ('peak',),
}
SNR_SHOWN = 1.0                 # the "speech above noise" line is shown when it moves by at least this many dB

Finding = namedtuple('Finding', 'key status title detail fix')     # status: ok, warn or bad; fix: what Auto fix does, or ''
Change = namedtuple('Change', 'label before after improved')       # one Check row that Auto fix changes


class Diagnosis:
    """What Auto analyze found, and what Auto fix would do about it."""

    def __init__(self, findings, ops, chain, steps, before, after, done=(), snr=(0.0, 0.0)):
        self.findings = findings        # [Finding], the worst first
        self.ops = ops                  # edit operations for History.add_step, on the master timeline
        self.chain = chain              # chain settings for History.add_step: {section: {name: value}}
        self.steps = steps              # what Auto fix will do, one short line each
        self.before = before            # the Check rows of the clip as it is
        self.after = after              # the Check rows with the plan applied (the same as before when there is none)
        self.done = set(done)           # the findings the plan answers
        self.snr = snr                  # speech above noise in dB: (as it is, with the plan applied)

    @property
    def has_fixes(self):
        return bool(self.ops or self.chain)

    @property
    def label(self):
        return f'Auto fix ({len(self.steps)} step{"" if len(self.steps) == 1 else "s"})'

    @property
    def changes(self):
        """The Check rows the plan changes, as (label, before, after, improved). A row is listed only when a repair
        that can change it was made and its value differs; "improved" is False if its status gets worse."""
        rank = {'ok': 0, 'info': 0, 'warn': 1, 'bad': 2}
        shown = {key for found in self.done for key in SHOWN_AFTER.get(found, ())}
        was = {row.key: row for row in self.before}
        out = []
        for row in self.after:
            old = was.get(row.key)
            if row.key in shown and old is not None and old.value != row.value:
                out.append(Change(row.label, old.value, row.value, rank[row.status] <= rank[old.status]))
        before, after = self.snr
        if after - before >= SNR_SHOWN:
            out.append(Change('Speech above the noise', f'{before:.0f} dB', f'{after:.0f} dB', True))
        return out


# ---- measuring --------------------------------------------------------------------------------------------------------

def noise_setting(floor):
    """The noise-cleaning setting for a clip with this noise floor (dB): in whole dB, within what the filter takes."""
    low, high = chain_mod.LIMITS[('denoise', 'nf')]
    return float(min(max(round(floor + NOISE_ABOVE_FLOOR), low), high))


def _snapshot(history):
    """The recipe as a render function wants it: edits, chain and join fade."""
    return SimpleNamespace(**history.state_copy())


def _export(x, rate, chain):
    """What the export would hold for audio that has every step but gain and the limiter."""
    return render.finish(chain_mod.apply_level(x, rate, chain), rate, chain)


def rumble_db(x, rate):
    """Energy under 60 Hz against the speech band (100 Hz to 4 kHz), in dB. Speech has almost none down there."""
    m = ac.to_mono(x).astype(np.float64)
    if len(m) < 2048:
        return -120.0
    freqs, power = welch(m, fs=rate, nperseg=min(1 << 15, len(m)))
    low = float(power[(freqs >= 15.0) & (freqs < 60.0)].sum())
    mid = float(power[(freqs >= 100.0) & (freqs < 4000.0)].sum())
    return round(10.0 * np.log10((low + 1e-20) / (mid + 1e-20)), 1)


def _inner_pauses(e, min_seconds):
    """Pauses of at least `min_seconds` inside a clip at the export rate, not at its ends, as (start, end) seconds."""
    total = len(e) / ac.EXPORT_RATE
    return [(a, b) for a, b in analysis.silent_runs(e, ac.EXPORT_RATE, min_seconds=min_seconds)
            if a > 1e-9 and b < total - ac.FRAME_SECONDS]


def _snr(x, rate):
    """How far the speech is above the noise floor, in dB."""
    e = ac.resample(x, rate, ac.EXPORT_RATE)
    return ac.voiced_rms_db(e, ac.EXPORT_RATE) - analysis.noise_floor_db(e, ac.EXPORT_RATE)


def _look(x, rate):
    """The numbers the decisions are made from: the clip at the export rate, with no gain applied."""
    e = ac.resample(x, rate, ac.EXPORT_RATE)
    r = ac.EXPORT_RATE
    lead, tail = analysis.edge_silence(e, r)
    return SimpleNamespace(seconds=len(e) / r, lead=lead, tail=tail, speech=ac.voiced_rms_db(e, r),
                           hum=analysis.detect_hum(e, r), rumble=rumble_db(e, r), pauses=_inner_pauses(e, PAUSE_LIMIT))


def _output(export):
    """The clip as it would be kept: its statistics, speech level and bandwidth."""
    return SimpleNamespace(stats=ac.measure(export, ac.EXPORT_RATE), speech=ac.voiced_rms_db(export, ac.EXPORT_RATE),
                           band=analysis.bandwidth_hz(export, ac.EXPORT_RATE))


# ---- the plan ---------------------------------------------------------------------------------------------------------

def _cut_ops(master, rate, history, ranges):
    """Cut ranges on the edited clip (seconds) as operations on the master timeline, for History.add_step."""
    segments = render.segments_of(master, rate, history.edits)
    return [('cut', render.edited_to_master(a, segments, rate), render.edited_to_master(b, segments, rate), {})
            for a, b in ranges]


def _fits(seconds, ranges):
    """False when cutting these ranges would take a clip that is long enough to keep below the minimum."""
    return seconds < ac.MIN_SECONDS or seconds - sum(b - a for a, b in ranges) >= ac.MIN_SECONDS


class _Plan:
    """The repairs chosen so far, each tried on a copy of the take's recipe."""

    def __init__(self, history):
        self.start = history.chain  # the chain the take had before any repair
        self.history = history.copy()
        self.ops, self.chain, self.steps = [], {}, []
        self.done = {}              # finding key -> what will be done about it
        self.skipped = {}           # finding key -> why nothing was done
        self.snr = 99.0             # speech above noise, once the filters are on and before noise cleaning
        self.cleaned = None         # the look at the clip once filtered and cleaned: its ends and pauses

    def add(self, keys, text, ops=(), chain=None):
        """Apply a repair to the copy; `keys` are the findings it answers. False, with the reason kept, if refused."""
        try:
            changed = self.history.add_step(list(ops), chain or {}, 'Auto fix')
        except ValueError as e:
            self.skipped.update({key: str(e) for key in keys})
            return False
        if not changed:
            return False
        self.ops.extend(ops)
        for section, values in (chain or {}).items():
            self.chain.setdefault(section, {}).update(values)
        if text not in self.steps:
            self.steps.append(text)
        self.done.update({key: text for key in keys})
        return True

    def trial(self, chain):
        """A copy of the recipe with some chain settings changed, to render and measure before deciding."""
        copy = self.history.copy()
        copy.add_step([], chain, 'trial')
        return copy


def _filters(plan, look, x, before_level):
    """Step 1: rumble and hum, decided from the clip as it was. Returns the filtered audio."""
    chain = plan.history.chain
    wanted = {}                     # finding key -> (chain change, what it does)
    if look.rumble > RUMBLE_LIMIT and not chain['highpass']['enabled']:
        wanted['rumble'] = ({'highpass': {'enabled': True, 'hz': HIGHPASS_HZ}}, f'High-pass filter at {HIGHPASS_HZ:.0f} Hz')
    if look.hum is not None:
        hz = float(look.hum[0])
        if not (chain['hum']['enabled'] and round(chain['hum']['hz']) == round(hz)):
            wanted['hum'] = ({'hum': {'enabled': True, 'hz': hz}}, f'{hz:.0f} Hz hum filter')
    if wanted:
        merged = {section: values for change, _ in wanted.values() for section, values in change.items()}
        what = ' and '.join(text for _, text in wanted.values())
        if plan.add(list(wanted), what, chain=merged):
            return before_level(_snapshot(plan.history))
    return x


def _denoise(plan, x, rate, before_level, denoise_ok):
    """Step 2: noise cleaning on a clearly noisy clip, tried on a copy, and kept only if it lowers the noise and leaves
    the voice."""
    if plan.snr >= SNR_NOISY or plan.history.chain['denoise']['enabled']:
        return x
    if not denoise_ok:
        plan.skipped['noise'] = 'FFmpeg was not found, and noise reduction needs it'
        return x
    nf = noise_setting(analysis.noise_floor_db(x, rate))
    setting = {'denoise': {'enabled': True, 'nf': nf}}
    try:
        cleaned = before_level(_snapshot(plan.trial(setting)))
    except ValueError as e:                         # FFmpeg failed or timed out
        plan.skipped['noise'] = str(e)
        return x
    if _snr(cleaned, rate) - plan.snr < NOISE_MIN_GAIN:
        plan.skipped['noise'] = 'noise cleaning did not lower it enough to be worth the change to the voice'
        return x
    if analysis.voice_loss_db(x, cleaned, rate) > VOICE_LOSS_LIMIT:
        plan.skipped['noise'] = 'noise cleaning would take too much of the voice along with the noise'
        return x
    plan.add(['noise'], f'Noise reduction (noise floor {nf:.0f} dB)', chain=setting)
    return cleaned


def _trim(plan, master, rate, x, before_level):
    """Step 3: the silence at either end, cut down to a short margin. Returns the audio.

    What counts as a pause depends on the whole clip, so cutting one end can show up silence at the other that a blip of
    leftover noise was hiding. The clip is looked at once more, so that a second look at the result finds nothing to do.
    """
    look = plan.cleaned
    for _ in range(2):
        ranges = []
        if look.lead > EDGE_LIMIT:
            ranges.append((0.0, look.lead - TRIM_MARGIN))
        if look.tail > EDGE_LIMIT:
            ranges.append((look.seconds - look.tail + TRIM_MARGIN, look.seconds))
        if not ranges:
            break
        if not _fits(look.seconds, ranges):
            plan.skipped['ends'] = 'the clip would be left shorter than 6 s'
            break
        if not plan.add(['ends'], 'Trim the silence at the ends', ops=_cut_ops(master, rate, plan.history, ranges)):
            break
        x = before_level(_snapshot(plan.history))
        look = _look(x, rate)
    return x


def _shorten(plan, master, rate, x, before_level):
    """Step 4: pauses inside the clip, found again on the trimmed clip and cut down to a short length."""
    pauses = _inner_pauses(ac.resample(x, rate, ac.EXPORT_RATE), PAUSE_LIMIT)
    ranges = [(a + PAUSE_KEEP / 2.0, b - PAUSE_KEEP / 2.0) for a, b in pauses]
    if not ranges:
        return x
    if not _fits(len(x) / rate, ranges):
        plan.skipped['pauses'] = 'the clip would be left shorter than 6 s'
    elif plan.add(['pauses'], 'Shorten the long pauses', ops=_cut_ops(master, rate, plan.history, ranges)):
        return before_level(_snapshot(plan.history))
    return x


def _level(plan, x, rate):
    """Step 5: the gain that puts the speech at the target, with the limiter catching what is left."""
    now = _export(x, rate, plan.history.chain)
    speech, peak = ac.voiced_rms_db(now, ac.EXPORT_RATE), ac.measure(now, ac.EXPORT_RATE)['peak_dbfs']
    if abs(speech - ac.TARGET_VOICED_DB) > LEVEL_TOLERANCE:
        low, high = chain_mod.LIMITS[('level', 'gain_db')]
        gain = min(max(ac.normalize_gain_db(x, rate, cap_peak=False), low), high)
        change, text = {'level': {'gain_db': gain}}, f'Level {gain:+.1f} dB'
        if not plan.history.chain['limiter']['enabled']:
            change['limiter'] = {'enabled': True}
            text += ' with the peak limiter'
        plan.add(['level', 'peak'], text, chain=change)
    elif peak > PEAK_CEILING and not plan.history.chain['limiter']['enabled']:
        plan.add(['peak'], 'Peak limiter', chain={'limiter': {'enabled': True}})


def _repair(master, rate, history, before_level, denoise_ok, look):
    """Choose the repairs for a take. Returns the plan, with what it will do and why it will not do the rest."""
    plan = _Plan(history)
    x = _filters(plan, look, before_level(_snapshot(plan.history)), before_level)
    plan.snr = _snr(x, rate)
    x = _denoise(plan, x, rate, before_level, denoise_ok)
    plan.cleaned = _look(x, rate)
    x = _trim(plan, master, rate, x, before_level)
    x = _shorten(plan, master, rate, x, before_level)
    _level(plan, x, rate)
    return plan


# ---- saying it in words -----------------------------------------------------------------------------------------------

def _findings(look, plan, out, final, recorded):
    """The findings, worst first. `out` describes the clip as it would be kept now, `final` as it would be afterwards, and
    `recorded` is the share of the recording behind them that is at full scale."""
    found = []
    cleaned = plan.cleaned

    def add(key, status, title, detail):
        fix = plan.done.get(key, '')
        if not fix and key in plan.skipped:
            detail += f' Not fixed: {plan.skipped[key]}.'
        found.append(Finding(key, status, title, detail, fix))

    seconds = out.stats['seconds']
    if seconds < ac.MIN_SECONDS:
        add('length', 'bad', 'Too short', f'{seconds:.1f} s long. A kept clip needs {ac.MIN_SECONDS:.0f} to '
            f'{ac.MAX_SECONDS:.0f} s, and Auto fix cannot add speech. Record more.')
    elif seconds > ac.MAX_SECONDS:
        add('length', 'bad', 'Too long', f'{seconds:.1f} s long. A kept clip can be at most {ac.MAX_SECONDS:.0f} s. '
            'Select the part you want and use Trim to selection.')
    elif seconds > ac.TARGET_MAX_SECONDS:
        add('length', 'warn', 'Long clip', f'{seconds:.1f} s long. That is fine up to {ac.MAX_SECONDS:.0f} s, but '
            f'{ac.TARGET_MIN_SECONDS:.0f} to {ac.TARGET_MAX_SECONDS:.0f} s works best.')
    clipped = max(out.stats['clipped_fraction'], recorded)
    if clipped > 0.001:
        add('clipping', 'bad', 'Clipping', f'{clipped * 100:.2f}% of the samples reach full scale. Clipping cannot be '
            'repaired, and gain or the limiter only hide it: lower the microphone level and record again.')
    if look.rumble > RUMBLE_LIMIT:
        detail = f'There is a lot of energy under 60 Hz ({look.rumble:.0f} dB against the speech). Speech has almost ' \
                 'none there: it is rumble, handling noise or music.'
        add('rumble', 'warn', 'Low-frequency rumble', detail + (' The high-pass filter is already on.'
                                                               if plan.start['highpass']['enabled'] else ''))
    if look.hum is not None:
        detail = f'{look.hum[0]:.0f} Hz hum, {look.hum[1]:.0f} dB above its surroundings.'
        add('hum', 'warn', 'Mains hum', detail + (' The hum filter is already on, but some hum is left.'
                                                  if plan.start['hum']['enabled'] and 'hum' not in plan.done else ''))
    if plan.snr < SNR_CLEAN:
        detail = f'The speech is {plan.snr:.0f} dB above the noise; {SNR_CLEAN:.0f} dB or more is clean.'
        if plan.start['denoise']['enabled']:
            detail += ' Noise reduction is already on.'
        elif plan.snr >= SNR_NOISY:
            detail += ' Noise reduction on the Process tab can help, but it can change the voice, so Auto fix leaves ' \
                      'that to you.'
        add('noise', 'bad' if plan.snr < SNR_NOISY else 'warn', 'Background noise', detail)
    if cleaned.lead > EDGE_LIMIT or cleaned.tail > EDGE_LIMIT:
        filtered = ' (measured with the filters on)' if 'rumble' in plan.done or 'hum' in plan.done else ''
        add('ends', 'warn', 'Long silence at the ends',
            f'{cleaned.lead:.1f} s before the first word and {cleaned.tail:.1f} s after the last{filtered}.')
    if cleaned.pauses:
        longest = max(b - a for a, b in cleaned.pauses)
        add('pauses', 'warn', 'Long pauses', f'{len(cleaned.pauses)} pause{"" if len(cleaned.pauses) == 1 else "s"} '
            f'longer than {PAUSE_LIMIT:.1f} s (the longest is {longest:.1f} s).')
    off = abs(out.speech - ac.TARGET_VOICED_DB)
    if off > LEVEL_TOLERANCE:
        add('level', 'bad' if off > 8 else 'warn', 'Speech level',
            f'The speech is at {out.speech:.1f} dB; the target is {ac.TARGET_VOICED_DB:.0f} dB.')
    elif out.stats['peak_dbfs'] > PEAK_CEILING:
        add('peak', 'warn', 'Peaks too high', f"The loudest peak is {out.stats['peak_dbfs']:.1f} dBFS, which leaves "
            'no headroom.')
    if 0 < out.band < NARROW_HZ:
        add('bandwidth', 'warn', 'Narrow bandwidth', f'The speech stops at {out.band / 1000:.1f} kHz, like a phone '
            'call. This cannot be fixed.')
    if final.stats['silence_fraction'] > ac.MOSTLY_SILENT:
        add('silence', 'warn', 'Mostly pauses', f"{final.stats['silence_fraction'] * 100:.0f}% of the clip is pauses, "
            'which leaves little speech.')
    if not found:
        found.append(Finding('ok', 'ok', 'Nothing to fix', 'Every check passes.', ''))
    return sorted(found, key=lambda f: {'bad': 0, 'warn': 1, 'ok': 2}[f.status])


def diagnose(master, rate, history, before_level, denoise_ok=True):
    """Measure a take and plan its repairs.

    `master` and `rate` are the take's samples and rate, `history` its recipe, and `before_level(recipe)` renders a
    recipe (anything with .edits, .chain and .fade_ms) to the clip at `rate` with every step but gain and the limiter,
    as Library.before_level does. `denoise_ok` says whether noise cleaning can run. Returns a Diagnosis.
    """
    x = before_level(_snapshot(history))
    look = _look(x, rate)
    here = _export(x, rate, history.chain)
    recorded = render.recorded_clipping(master, rate, history)['recording_clipped_fraction']
    before = analysis.quality_report(here, recorded_clipped=recorded)
    if look.speech < NO_SPEECH_DB:
        finding = Finding('speech', 'bad', 'No speech found', 'The clip is silent, or too quiet to measure. There is '
                          'nothing to fix.', '')
        return Diagnosis([finding], [], {}, [], before, before)

    plan = _repair(master, rate, history, before_level, denoise_ok, look)
    if not plan.steps:
        return Diagnosis(_findings(look, plan, _output(here), _output(here), recorded), [], {}, [], before, before)
    final = _export(before_level(_snapshot(plan.history)), rate, plan.history.chain)
    left = render.recorded_clipping(master, rate, plan.history)['recording_clipped_fraction']
    return Diagnosis(_findings(look, plan, _output(here), _output(final), recorded), plan.ops, plan.chain, plan.steps,
                     before, analysis.quality_report(final, recorded_clipped=left), plan.done,
                     (analysis.snr_db(here, ac.EXPORT_RATE), analysis.snr_db(final, ac.EXPORT_RATE)))
