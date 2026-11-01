"""A copy of a kept clip as WAV, FLAC, MP3 or Ogg at another sample rate: made from the kept file, never touching it."""
import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from helpers import tone
from recorder import audio_core as ac
from recorder import share
from recorder.share import FORMATS, RATES, CopyError, save_copy

ROOT = Path(__file__).resolve().parent.parent


def kept_wav(folder, seconds=12.0, freq=440.0, amp=0.3, name='clip.wav'):
    """A stand-in for a kept clip: a steady tone, mono, 16-bit, at the export rate."""
    path = folder / name
    sf.write(str(path), tone(seconds, freq=freq, amp=amp, rate=ac.EXPORT_RATE), ac.EXPORT_RATE, subtype='PCM_16', format='WAV')
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pitch(samples, rate):
    """The strongest frequency in the clip, in Hz."""
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.argmax(spectrum) * rate / len(samples))


def rms_db(samples):
    return 20 * np.log10(np.sqrt(np.mean(np.square(samples, dtype=np.float64))) + 1e-12)


@pytest.mark.parametrize('kind', list(FORMATS))
@pytest.mark.parametrize('rate', RATES)
def test_every_format_at_every_rate_reads_back_as_the_same_sound(tmp_path, kind, rate):
    source = kept_wav(tmp_path)
    copy = save_copy(source, tmp_path / f'copy{FORMATS[kind][1]}', kind, rate)
    assert copy == tmp_path / f'copy{FORMATS[kind][1]}' and copy.is_file()
    info = sf.info(str(copy))
    assert (info.samplerate, info.channels) == (rate, 1)
    assert info.duration == pytest.approx(12.0, abs=0.12 if kind in ('mp3', 'ogg') else 0.001)
    back, _ = sf.read(str(copy), dtype='float32')
    assert pitch(back, rate) == pytest.approx(440.0, abs=3.0)
    assert rms_db(back) == pytest.approx(rms_db(tone(12.0, 440.0, 0.3, ac.EXPORT_RATE)), abs=1.0)


@pytest.mark.parametrize('rate', RATES)
def test_an_mp3_copy_has_the_bit_rate_its_name_promises_at_every_sample_rate(tmp_path, rate):
    """The encoder's quality setting means another bit rate for each sample rate (0.6 gives 160 kbit/s at 44.1 kHz and 64
    at 24 kHz), so the label is checked on the file."""
    source = kept_wav(tmp_path, seconds=20.0)
    copy = save_copy(source, tmp_path / 'c.mp3', 'mp3', rate)
    kbit = copy.stat().st_size * 8 / sf.info(str(copy)).duration / 1000
    assert kbit == pytest.approx(share.MP3_KBPS, rel=0.06) and f'{share.MP3_KBPS} kbit/s' in FORMATS['mp3'][0]


def test_a_wav_copy_at_the_kept_rate_is_the_kept_samples(tmp_path):
    source = kept_wav(tmp_path)
    copy = save_copy(source, tmp_path / 'same.wav', 'wav', ac.EXPORT_RATE)
    a, _ = sf.read(str(source), dtype='int16')
    b, _ = sf.read(str(copy), dtype='int16')
    assert np.array_equal(a, b)


def test_a_flac_copy_loses_nothing(tmp_path):
    source = kept_wav(tmp_path)
    copy = save_copy(source, tmp_path / 'same.flac', 'flac', ac.EXPORT_RATE)
    a, _ = sf.read(str(source), dtype='int16')
    b, _ = sf.read(str(copy), dtype='int16')
    assert np.array_equal(a, b)


def test_the_kept_clip_is_not_changed_by_making_a_copy(tmp_path):
    source = kept_wav(tmp_path)
    before = sha(source)
    for kind in FORMATS:
        save_copy(source, tmp_path / f'out{FORMATS[kind][1]}', kind, 44100)
    assert sha(source) == before


def test_a_copy_cannot_be_written_over_the_kept_clip_itself(tmp_path):
    source = kept_wav(tmp_path)
    before = sha(source)
    with pytest.raises(CopyError, match='kept clip itself'):
        save_copy(source, source, 'wav', 48000)
    with pytest.raises(CopyError, match='kept clip itself'):
        save_copy(source, tmp_path / '.' / 'CLIP.WAV', 'mp3', 48000)             # the same file by another spelling
    assert sha(source) == before and not list(tmp_path.glob('*.tmp'))


def test_lossy_copies_are_smaller_and_the_lossless_ones_are_not_bigger(tmp_path):
    source = kept_wav(tmp_path)
    sizes = {kind: save_copy(source, tmp_path / f'c{FORMATS[kind][1]}', kind, ac.EXPORT_RATE).stat().st_size
             for kind in FORMATS}
    assert sizes['flac'] < sizes['wav'] and sizes['mp3'] < sizes['wav'] and sizes['ogg'] < sizes['wav']


def test_the_stereo_or_other_rate_source_is_mixed_down_and_resampled(tmp_path):
    path = tmp_path / 'odd.wav'
    left, right = tone(3.0, 300.0, 0.2, 44100), tone(3.0, 300.0, 0.2, 44100)
    sf.write(str(path), np.stack([left, right], axis=1), 44100, subtype='PCM_16', format='WAV')
    copy = save_copy(path, tmp_path / 'odd-copy.wav', 'wav', 48000)
    info = sf.info(str(copy))
    assert (info.samplerate, info.channels, info.frames) == (48000, 1, 144000)


def test_loud_audio_is_held_at_full_scale_not_wrapped(tmp_path):
    path = tmp_path / 'loud.wav'
    sf.write(str(path), np.clip(tone(2.0, 100.0, 1.0, ac.EXPORT_RATE) * 1.0, -1, 1), ac.EXPORT_RATE, subtype='PCM_16')
    copy = save_copy(path, tmp_path / 'loud-copy.wav', 'wav', 44100)
    back, _ = sf.read(str(copy), dtype='float32')
    assert np.abs(back).max() <= 1.0 and rms_db(back) > -5


def test_a_copy_is_written_in_small_pieces(tmp_path, monkeypatch):
    """The Ogg encoder kills the program if it is handed a whole long clip in one call, so no call may get more than a
    small piece."""
    source = kept_wav(tmp_path, seconds=40.0)
    sizes = []
    real = sf.SoundFile.write

    def spy(self, data):
        sizes.append(len(data))
        return real(self, data)

    monkeypatch.setattr(sf.SoundFile, 'write', spy)
    save_copy(source, tmp_path / 'long.ogg', 'ogg', 48000)
    assert len(sizes) > 100 and max(sizes) <= share.CHUNK_FRAMES and sum(sizes) == 40 * 48000


@pytest.mark.parametrize('kind', ['ogg', 'mp3', 'flac', 'wav'])
def test_a_forty_second_clip_survives_every_encoder_in_a_separate_process(tmp_path, kind):
    """The real thing, in a child process, because a crash of the encoder would take a test run with it."""
    source = kept_wav(tmp_path, seconds=40.0)
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from recorder.share import save_copy; '
            'save_copy(sys.argv[2], sys.argv[3], sys.argv[4], 48000)')
    out = tmp_path / f'long{FORMATS[kind][1]}'
    run = subprocess.run([sys.executable, '-I', '-c', code, str(ROOT), str(source), str(out), kind], capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-500:]
    assert sf.info(str(out)).duration == pytest.approx(40.0, abs=0.2)


def test_a_failure_part_way_leaves_no_file_and_an_older_copy_untouched(tmp_path, monkeypatch):
    source = kept_wav(tmp_path)
    target = tmp_path / 'copy.mp3'
    target.write_bytes(b'an earlier copy')
    calls = []
    real = sf.SoundFile.write

    def failing(self, data):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError('Internal psf_fwrite failed')
        return real(self, data)

    monkeypatch.setattr(sf.SoundFile, 'write', failing)
    with pytest.raises(CopyError, match='could not be made'):
        save_copy(source, target, 'mp3', 44100)
    assert target.read_bytes() == b'an earlier copy' and not list(tmp_path.glob('*.tmp'))


def test_problems_with_the_source_or_the_place_are_copy_errors(tmp_path):
    with pytest.raises(CopyError):
        save_copy(tmp_path / 'missing.wav', tmp_path / 'x.wav', 'wav', 48000)
    source = kept_wav(tmp_path)
    with pytest.raises(CopyError):
        save_copy(source, tmp_path / 'no-such-folder' / 'x.wav', 'wav', 48000)
    with pytest.raises(CopyError, match='not offered'):
        save_copy(source, tmp_path / 'x.aac', 'aac', 48000)
    with pytest.raises(CopyError, match='not offered'):
        save_copy(source, tmp_path / 'x.wav', 'wav', 12345)
    assert not list(tmp_path.glob('x.*'))


@pytest.mark.parametrize('text, expected', [
    ('Welcome back', 'Welcome back'),
    ('Welcome: back? <take> "2"', 'Welcome back take 2'),
    ('a/b\\c|d*e', 'a b c d e'),
    ('  ...trailing dots...  ', 'trailing dots'),
    ('', 'clip'), (None, 'clip'), ('???', 'clip'), ('...', 'clip'),
    ('con', '_con'), ('NUL.txt', '_NUL.txt'), ('com3', '_com3'), ('console', 'console'),
    ('x' * 200, 'x' * 80),
    ('tab\tand\nnewline', 'tab and newline'),
])
def test_names_made_from_a_label_are_valid_windows_file_names(text, expected):
    assert share.filename_for(text) == expected


def test_the_offered_rates_are_the_ones_programs_ask_for():
    assert RATES == (16000, 22050, 24000, 44100, 48000) and ac.EXPORT_RATE in RATES
    assert [share.rate_text(r) for r in RATES] == ['16 kHz', '22.05 kHz', '24 kHz', '44.1 kHz', '48 kHz']
    assert share.DEFAULT_KIND in FORMATS and share.DEFAULT_RATE in RATES
