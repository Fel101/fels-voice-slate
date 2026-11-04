"""Importing: soundfile reads its own formats, and FFmpeg, when it is installed, the rest (a phone's M4A, say)."""
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from helpers import RATE, speech
from recorder import audio_import, clean

HAS_FFMPEG = clean.available()


@pytest.fixture(autouse=True)
def ffmpeg_setting():
    """The FFmpeg path a test gives is gone after it."""
    yield
    clean.set_ffmpeg_path(None)


@pytest.fixture
def ffmpeg(tmp_path, monkeypatch):
    """A stand-in FFmpeg that turns any file into the WAV the test gives it, and records each command."""
    program = tmp_path / 'ffmpeg.exe'
    program.write_bytes(b'')
    clean.set_ffmpeg_path(program)
    made = SimpleNamespace(commands=[], samples=np.full(4800, 0.25, np.float32), rate=24000, returncode=0, stderr='')

    def run(command, **kwargs):
        made.commands.append(command)
        if made.returncode == 0:
            sf.write(command[-1], made.samples, made.rate, subtype='FLOAT')
        return subprocess.CompletedProcess(command, made.returncode, stdout='', stderr=made.stderr)

    monkeypatch.setattr(audio_import.subprocess, 'run', run)
    return made


def test_a_wav_is_read_as_it_always_was(tmp_path, ffmpeg):
    path = tmp_path / 'clip.wav'
    sf.write(str(path), speech(1.0), RATE)
    samples, rate = audio_import.read_audio(path)
    assert rate == RATE and len(samples) == RATE and ffmpeg.commands == []


def test_an_m4a_is_decoded_by_ffmpeg(tmp_path, ffmpeg):
    source = tmp_path / 'Voice memo.m4a'
    source.write_bytes(b'not really an m4a')
    samples, rate = audio_import.read_audio(source)
    assert rate == 24000 and len(samples) == 4800 and samples[0] == pytest.approx(0.25)
    command = ffmpeg.commands[-1]
    assert command[0] == str(tmp_path / 'ffmpeg.exe') and str(source) in command
    assert command[command.index('-map') + 1] == '0:a:0'        # the sound only, and only the first sound of a video


def test_a_damaged_wav_is_refused_and_never_handed_to_ffmpeg(tmp_path, ffmpeg):
    """FFmpeg decodes around damage, so a broken WAV would come in as whatever it made of it."""
    path = tmp_path / 'broken.wav'
    path.write_bytes(b'RIFF\x00\x00\x00\x00WAVEnot a wav at all')
    with pytest.raises(RuntimeError):
        audio_import.read_audio(path)
    assert ffmpeg.commands == []


def test_a_format_soundfile_reads_under_another_name_needs_no_ffmpeg(tmp_path, ffmpeg):
    path = tmp_path / 'clip.w64'
    sf.write(str(path), speech(1.0), RATE, format='W64')
    samples, rate = audio_import.read_audio(path)
    assert rate == RATE and len(samples) == RATE and ffmpeg.commands == []


def test_an_unknown_format_is_tried_with_ffmpeg(tmp_path, ffmpeg):
    path = tmp_path / 'clip.mkv'
    path.write_bytes(b'a video')
    assert audio_import.read_audio(path)[1] == 24000 and len(ffmpeg.commands) == 1


def test_without_ffmpeg_the_message_says_how_to_get_it(tmp_path, monkeypatch):
    clean.set_ffmpeg_path(None)
    monkeypatch.setattr(clean, '_on_path', lambda: None)
    monkeypatch.setattr(clean, '_winget_candidates', lambda: [])
    source = tmp_path / 'phone.m4a'
    source.write_bytes(b'not really an m4a')
    with pytest.raises(audio_import.AudioImportError, match='winget install Gyan.FFmpeg') as raised:
        audio_import.read_audio(source)
    assert 'save the recording as WAV' in str(raised.value)


def test_an_ffmpeg_path_in_settings_that_is_gone_is_said(tmp_path):
    clean.set_ffmpeg_path(tmp_path / 'moved' / 'ffmpeg.exe')
    source = tmp_path / 'phone.m4a'
    source.write_bytes(b'not really an m4a')
    with pytest.raises(audio_import.AudioImportError, match='path in settings does not exist'):
        audio_import.read_audio(source)


def test_what_ffmpeg_says_when_it_fails_is_passed_on(tmp_path, ffmpeg):
    ffmpeg.returncode, ffmpeg.stderr = 1, 'phone.m4a: Invalid data found when processing input\n'
    source = tmp_path / 'phone.m4a'
    source.write_bytes(b'not really an m4a')
    with pytest.raises(audio_import.AudioImportError, match='could not read it: phone.m4a: Invalid data'):
        audio_import.read_audio(source)


def test_a_video_without_sound_holds_no_audio(tmp_path, ffmpeg):
    ffmpeg.returncode = 1
    ffmpeg.stderr = "Stream map '0:a:0' matches no streams.\nTo ignore this, add a trailing '?' to the map.\n"
    source = tmp_path / 'silent film.mp4'
    source.write_bytes(b'a video')
    with pytest.raises(audio_import.AudioImportError, match='^the file holds no audio$'):
        audio_import.read_audio(source)


@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg is not installed')
def test_a_real_m4a_with_a_name_in_any_letters_comes_in(tmp_path):
    """Real FFmpeg makes an AAC file from a WAV, and the import reads it back. The name is not plain ASCII."""
    wav, m4a = tmp_path / 'source.wav', tmp_path / 'Zoë – mémo.m4a'
    sf.write(str(wav), speech(2.0), RATE)
    subprocess.run([clean.find_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', str(wav),
                    '-c:a', 'aac', str(m4a)], check=True, capture_output=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    samples, rate = audio_import.read_audio(m4a)
    assert rate == RATE and abs(len(samples) / rate - 2.0) < 0.05
    assert float(np.sqrt(np.mean(samples ** 2))) == pytest.approx(float(np.sqrt(np.mean(speech(2.0) ** 2))), rel=0.1)


@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg is not installed')
def test_a_real_video_without_sound_is_refused_in_words(tmp_path):
    video = tmp_path / 'no sound.mp4'
    done = subprocess.run([clean.find_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error', '-f', 'lavfi', '-i',
                           'color=c=black:s=64x64:d=1', '-c:v', 'mpeg4', str(video)], capture_output=True,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode or not video.exists():
        pytest.skip('this FFmpeg cannot make a test video')
    with pytest.raises(audio_import.AudioImportError, match='holds no audio'):
        audio_import.read_audio(video)
