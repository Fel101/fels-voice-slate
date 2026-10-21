"""Normalize level and background-noise cleaning: the maths, the FFmpeg step, and what the sidecar records."""
import json

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE
from recorder import audio_core as ac
from recorder import clean
from recorder.core import render
from recorder.core.edits import History
from recorder.store import Library, Take

HAS_FFMPEG = clean.available()


def quiet_speech(seconds=10.0, amp=0.02):
    t = np.arange(int(round(seconds * RATE))) / RATE
    return (amp * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2.5 * t))).astype(np.float32)


def test_normalize_brings_the_voiced_part_to_the_target():
    x = quiet_speech()
    gain = ac.normalize_gain_db(x, RATE)
    assert gain > 10
    assert ac.voiced_rms_db(ac.apply_gain(x, gain), RATE) == pytest.approx(ac.TARGET_VOICED_DB, abs=0.3)


def test_normalize_never_pushes_a_peak_past_the_limit():
    x = quiet_speech(amp=0.3)
    gain = ac.normalize_gain_db(x, RATE)
    assert float(np.max(np.abs(ac.apply_gain(x, gain)))) <= ac.NORMALIZE_PEAK + 1e-3


def test_without_the_peak_cap_normalize_only_looks_at_the_speech():
    x = quiet_speech(amp=0.02)
    x[1000] = 0.99
    assert ac.normalize_gain_db(x, RATE) < 0 < 10 < ac.normalize_gain_db(x, RATE, cap_peak=False)


def test_silence_has_no_gain_to_add():
    assert np.isfinite(ac.normalize_gain_db(np.zeros(RATE, np.float32), RATE))
    assert ac.apply_gain(np.zeros(10, np.float32), 12.0).max() == 0.0


def test_zero_gain_leaves_samples_untouched():
    x = quiet_speech()
    assert np.array_equal(ac.apply_gain(x, 0.0), x)


def test_export_applies_gain_and_the_cleaner_only_when_asked():
    x = quiet_speech()
    plain = render.export_samples(x, RATE, History(seconds=10.0))
    loud = History(seconds=10.0)
    loud.set_chain('level', 'Level', gain_db=10.0)
    louder = render.export_samples(x, RATE, loud)
    assert np.sqrt(np.mean(louder ** 2)) / np.sqrt(np.mean(plain ** 2)) == pytest.approx(10 ** (10 / 20), rel=0.01)
    calls = []

    def fake_cleaner(mono, rate, nf):
        calls.append(rate)
        return mono

    render.export_samples(x, RATE, History(seconds=10.0), fake_cleaner)
    assert calls == []
    cleaned = History(seconds=10.0)
    cleaned.set_chain('denoise', 'Noise cleaning', enabled=True)
    render.export_samples(x, RATE, cleaned, fake_cleaner)
    assert calls == [RATE]


def test_noise_floor_must_be_inside_afftdn_range():
    for bad in (-10, -85, 'loud', True, float('nan')):
        with pytest.raises(clean.CleanError):
            clean.validate_nf(bad)
    assert clean.validate_nf(-25) == -25.0


@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg is not installed')
def test_cleaning_keeps_length_and_rate_and_lowers_the_background():
    rng = np.random.default_rng(3)
    x = quiet_speech(amp=0.1) + rng.normal(0, 0.01, RATE * 10).astype(np.float32)
    cleaned = clean.denoise(x, RATE, -25)
    assert len(cleaned) == len(x)
    # the noise between the tones is quieter after cleaning
    quiet_before = np.sqrt(np.mean(x[:RATE // 4] ** 2))
    quiet_after = np.sqrt(np.mean(cleaned[:RATE // 4] ** 2))
    assert quiet_after < quiet_before


def test_sidecar_records_gain_and_cleaning_and_load_restores_them(tmp_path, monkeypatch):
    lib = Library(tmp_path / 'clips')
    slug = lib.add_speaker('Alice')
    take = Take(source='import', samples=quiet_speech(12.0), rate=RATE,
                meta={'transcript': 'Alice reads a short line.', 'origin': 'studio', 'allowed_use': 'my voice',
                      'consent_confirmed': True})
    take.history.set_chain('level', 'Level', gain_db=9.5)
    take.history.set_chain('denoise', 'Noise cleaning', enabled=True, nf=-25.0)
    monkeypatch.setattr(clean, 'denoise', lambda mono, rate, nf=-25.0: mono)  # no FFmpeg needed here
    paths = lib.keep(slug, take)
    data = json.loads(paths['sidecar'].read_text(encoding='utf-8'))
    assert data['chain']['level']['gain_db'] == 9.5
    assert data['chain']['denoise'] == {'enabled': True, 'nf': -25.0, 'filter': 'afftdn=nf=-25',
                                        'identity_checked': False}
    assert {'cleaned', 'level_changed'} <= set(data['flags'])
    back = lib.load_kept(slug)[0]
    assert back.history.chain['level']['gain_db'] == 9.5 and back.history.chain['denoise']['nf'] == -25.0
    assert sf.info(str(paths['export'])).samplerate == 24000


def test_finding_ffmpeg_prefers_the_configured_path_then_path_then_winget(tmp_path, monkeypatch):
    configured = tmp_path / 'tools' / 'ffmpeg.exe'
    configured.parent.mkdir()
    configured.write_bytes(b'')
    winget = tmp_path / 'local' / 'Microsoft' / 'WinGet' / 'Packages' / 'Gyan.FFmpeg_x' / 'ffmpeg-7.1-full_build' / 'bin'
    winget.mkdir(parents=True)
    (winget / 'ffmpeg.exe').write_bytes(b'')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setattr(clean.shutil, 'which', lambda name: None)
    clean.set_ffmpeg_path(None)
    try:
        assert clean.find_ffmpeg() == str(winget / 'ffmpeg.exe')
        monkeypatch.setattr(clean.shutil, 'which', lambda name: 'C:/tools/ffmpeg.exe')
        assert clean.find_ffmpeg() == 'C:/tools/ffmpeg.exe'
        clean.set_ffmpeg_path(configured)
        assert clean.find_ffmpeg() == str(configured)
        clean.set_ffmpeg_path(tmp_path / 'missing.exe')
        with pytest.raises(clean.CleanError, match='does not exist'):
            clean.find_ffmpeg()
    finally:
        clean.set_ffmpeg_path(None)


def test_a_missing_ffmpeg_says_how_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(clean.shutil, 'which', lambda name: None)
    clean.set_ffmpeg_path(None)
    with pytest.raises(clean.CleanError, match='winget install'):
        clean.find_ffmpeg()
    assert clean.available() is False
