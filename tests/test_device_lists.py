"""Each device once in the lists, by its full name, with every Windows audio interface a setting away.

The sound library is replaced by a stand-in laid out as Windows lays a PC out: one USB microphone and one headset through
MME (which cuts names at 31 letters), DirectSound, WASAPI and WDM-KS. A second layout is this PC's in a remote desktop
session, where WASAPI offers outputs but no input. Nothing here opens a real device."""
from types import SimpleNamespace

import numpy as np
import pytest

from helpers import speech
from recorder import audio_io, capture as cap
from recorder import devices, settings

REAL = {name: getattr(cap, name) for name in ('list_microphones', 'default_microphone', 'input_channels')}
MME, DS, WASAPI, KS = 'MME', 'Windows DirectSound', 'Windows WASAPI', 'Windows WDM-KS'
STUDIO = 'Microphone (Studio USB Microphone)'
HEADSET = 'Headset Microphone (USB Headset Stereo Audio)'


def device(name, api, inputs=0, outputs=0, rate=44100.0):
    return {'name': name, 'hostapi': [MME, DS, WASAPI, KS].index(api), 'max_input_channels': inputs,
            'max_output_channels': outputs, 'default_samplerate': rate}


DESK = [
    device('Microsoft Sound Mapper - Input', MME, inputs=2),                        # 0
    device(STUDIO[:31], MME, inputs=2),                                             # 1: MME cuts the name
    device(HEADSET[:31], MME, inputs=1),                                            # 2
    device('Primary Sound Capture Driver', DS, inputs=2),                           # 3
    device(STUDIO, DS, inputs=2),                                                   # 4
    device(HEADSET, DS, inputs=1),                                                  # 5
    device(STUDIO, WASAPI, inputs=2, rate=48000.0),                                 # 6
    device(HEADSET, WASAPI, inputs=1, rate=16000.0),                                # 7
    device(STUDIO, KS, inputs=2, rate=48000.0),                                     # 8
    device('Microsoft Sound Mapper - Output', MME, outputs=2),                      # 9
    device('Speakers (Realtek(R) Audio)', MME, outputs=2),                          # 10
    device('Speakers (Realtek(R) Audio)', WASAPI, outputs=2, rate=48000.0),         # 11
    device('Headphones (USB Headset Stereo Audio)', WASAPI, outputs=2),             # 12
]
DESK_DEFAULTS = {MME: (0, 9), DS: (3, -1), WASAPI: (6, 11), KS: (-1, -1)}

# This PC in a remote desktop session: WASAPI has the remote outputs only, and the inputs are WDM-KS's.
REMOTE = [
    device('Remote Audio', MME, outputs=2),                                         # 0
    device('Remote Audio', WASAPI, outputs=2),                                      # 1
    device('Microphone (HD Audio Microphone 2)', KS, inputs=2),                     # 2
    device('Microphone (Steam Streaming Microphone Wave)', KS, inputs=8),           # 3
]
REMOTE_DEFAULTS = {MME: (-1, 0), DS: (-1, -1), WASAPI: (-1, 1), KS: (2, -1)}


class FakeStream:
    def __init__(self, **options):
        self.options = options
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass


def sound_library(monkeypatch, layout=DESK, defaults=DESK_DEFAULTS):
    """Put a stand-in for sounddevice in place, laid out as `layout`. Returns the streams it is asked to open."""
    streams = []
    apis = [{'name': name, 'default_input_device': defaults[name][0], 'default_output_device': defaults[name][1]}
            for name in (MME, DS, WASAPI, KS)]

    def query_devices(index=None, kind=None):
        if index is None:
            return layout
        found = layout[index]
        if kind and found[f'max_{kind}_channels'] < 1:
            raise ValueError(f'No {kind} channels')
        return found

    def stream(**options):
        streams.append(FakeStream(**options))
        return streams[-1]

    fake = SimpleNamespace(query_devices=query_devices, query_hostapis=lambda index=None: apis if index is None else apis[index],
                           default=SimpleNamespace(device=[defaults[MME][0], defaults[MME][1]]),
                           InputStream=stream, OutputStream=stream,
                           WasapiSettings=lambda **options: SimpleNamespace(**options))
    monkeypatch.setitem(__import__('sys').modules, 'sounddevice', fake)
    return streams


@pytest.fixture
def real_lists(isolated, monkeypatch):
    """The session's device lists read the stand-in, not the fixed list the other tests use."""
    for name, function in REAL.items():
        monkeypatch.setattr(cap, name, function)


# ---- the lists ------------------------------------------------------------------------------------------------------------

def test_each_microphone_is_listed_once_by_its_full_name(monkeypatch):
    """Before, the studio microphone came up four times, and MME's entry was cut at 31 letters."""
    sound_library(monkeypatch)
    assert cap.list_microphones() == [(6, STUDIO), (7, HEADSET)]


def test_each_output_is_listed_once(monkeypatch):
    sound_library(monkeypatch)
    assert audio_io.list_outputs() == ['Speakers (Realtek(R) Audio)', 'Headphones (USB Headset Stereo Audio)']


def test_every_interface_can_be_listed(monkeypatch):
    sound_library(monkeypatch)
    listed = cap.list_microphones(every=True)
    assert len(listed) == 9 and (1, f'{STUDIO[:31]}  [MME]') in listed and (6, f'{STUDIO}  [{WASAPI}]') in listed
    assert audio_io.list_outputs(every=True)[:2] == ['Microsoft Sound Mapper - Output  [MME]',
                                                     'Speakers (Realtek(R) Audio)  [MME]']


def test_without_wasapi_inputs_every_interface_is_listed_without_asking(monkeypatch):
    """This PC in a remote desktop session: WASAPI has the remote outputs and no input at all."""
    sound_library(monkeypatch, REMOTE, REMOTE_DEFAULTS)
    assert cap.list_microphones() == [(2, f'Microphone (HD Audio Microphone 2)  [{KS}]'),
                                      (3, f'Microphone (Steam Streaming Microphone Wave)  [{KS}]')]
    assert audio_io.list_outputs() == ['Remote Audio']


def test_the_microphone_chosen_at_first_is_windows_own_default(monkeypatch):
    """The sound library's own default is MME's sound mapper, at 44.1 kHz whatever the microphone runs at."""
    sound_library(monkeypatch)
    assert cap.default_microphone() == 6


def test_with_no_wasapi_default_another_interface_gives_one(monkeypatch):
    sound_library(monkeypatch, REMOTE, REMOTE_DEFAULTS)
    assert cap.default_microphone() == 2


def test_no_default_anywhere_is_none(monkeypatch):
    sound_library(monkeypatch, REMOTE, {name: (-1, -1) for name in (MME, DS, WASAPI, KS)})
    assert cap.default_microphone() is None


# ---- names kept by 0.6 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('kept', [f'{STUDIO}  [{WASAPI}]', f'{STUDIO}  [{DS}]', f'{STUDIO[:31]}  [MME]', STUDIO])
def test_a_name_kept_by_0_6_finds_the_same_device(kept):
    assert devices.match(kept, [(6, STUDIO), (7, HEADSET)]) == 6


def test_a_name_finds_its_own_interface_in_the_full_list(monkeypatch):
    sound_library(monkeypatch)
    listed = cap.list_microphones(every=True)
    assert devices.match(f'{STUDIO}  [{DS}]', listed) == 4
    assert devices.match(STUDIO, listed) == 6                   # chosen from the short list: WASAPI's entry
    assert devices.match(f'{STUDIO[:31]}  [MME]', listed) == 1


def test_a_cut_name_that_fits_two_devices_finds_neither():
    cut = 'Microphone (USB Audio Device) -'                              # 31 letters: MME's cut of both
    assert devices.match(f'{cut}  [MME]', [(1, f'{cut} left'), (2, f'{cut} right')]) is None
    assert devices.match(f'{cut}  [MME]', [(1, f'{cut} left'), (2, HEADSET)]) == 1


@pytest.mark.parametrize('kept', [None, '', 'A microphone that has gone  [MME]', 'Microsoft Sound Mapper - Input  [MME]'])
def test_a_name_that_fits_nothing_finds_nothing(kept):
    assert devices.match(kept, [(6, STUDIO), (7, HEADSET)]) is None


# ---- the session --------------------------------------------------------------------------------------------------------------

def test_the_session_lists_each_microphone_once_and_starts_on_windows_default(make_session, monkeypatch, real_lists):
    sound_library(monkeypatch)
    capturing = make_session().capturing
    assert capturing.devices == [(6, STUDIO), (7, HEADSET)] and capturing.device == 6


@pytest.mark.parametrize('kept, found', [(f'{HEADSET[:31]}  [MME]', 7), (f'{HEADSET}  [{DS}]', 7),
                                         ('Microsoft Sound Mapper - Input  [MME]', 6), ('A microphone that has gone', 6)])
def test_a_microphone_chosen_in_0_6_is_found_again_or_the_default_is_used(make_session, monkeypatch, real_lists, tmp_path,
                                                                           kept, found):
    sound_library(monkeypatch)
    settings.update({'input_device': {'mic': kept}}, tmp_path / 'settings.json')
    assert make_session().capturing.device == found


def test_the_input_chosen_in_0_6_is_kept(make_session, monkeypatch, real_lists, tmp_path):
    sound_library(monkeypatch)
    settings.update({'input_device': {'mic': f'{STUDIO}  [{WASAPI}]'}, 'input_channels': {f'{STUDIO}  [{WASAPI}]': 1}},
                    tmp_path / 'settings.json')
    capturing = make_session().capturing
    assert capturing.device == 6 and capturing.inputs == 2 and capturing.channel == 1
    capturing.select_channel(0)
    assert settings.load(tmp_path / 'settings.json')['input_channels'] == {STUDIO: 0}


def test_the_setting_lists_every_interface(make_session, monkeypatch, real_lists, tmp_path):
    sound_library(monkeypatch)
    settings.update({'all_interfaces': True}, tmp_path / 'settings.json')
    capturing = make_session().capturing
    assert len(capturing.devices) == 9 and capturing.device_label() == f'{STUDIO}  [{WASAPI}]'


def test_an_output_chosen_in_0_6_plays_through_the_same_device(make_session, monkeypatch, tmp_path, player):
    sound_library(monkeypatch)
    settings.update({'output_device': 'Headphones (USB Headset Stereo Audio)  [Windows WASAPI]'}, tmp_path / 'settings.json')
    make_session()
    assert player.device == 12


def test_an_output_kept_from_mme_finds_the_device_and_not_mme(monkeypatch):
    sound_library(monkeypatch)
    assert audio_io.find_output('Speakers (Realtek(R) Audio)  [MME]') == 11
    assert audio_io.find_output('Speakers (Realtek(R) Audio)  [MME]', every=True) == 10


# ---- the microphone's own rate ---------------------------------------------------------------------------------------------------

def record_a_take(session, streams, clock, seconds=3.0):
    """Record from the stand-in's stream as the sound card would deliver it, and stop. Returns the take."""
    assert session.capturing.start() is True
    stream = streams[-1]
    rate, inputs = stream.options['samplerate'], stream.options['channels']
    voice = speech(seconds, rate=rate)
    flags = SimpleNamespace(input_overflow=False, input_underflow=False)
    for start in range(0, len(voice), 1024):
        block = np.repeat(voice[start:start + 1024, None], inputs, axis=1)
        stream.options['callback'](block, len(block), None, flags)
    session.capturing.stop()
    clock.advance(1.5)
    session.tick()
    return session.workspace.current


def test_a_take_from_the_microphone_chosen_at_first_is_at_its_own_rate(make_session, monkeypatch, real_lists, clock):
    """The sound library's own default is MME's sound mapper, which reports 44.1 kHz whatever the microphone runs at, so
    an Audition from a 48 kHz microphone came out at 44.1 kHz after Windows had resampled it."""
    streams = sound_library(monkeypatch)
    session = make_session()
    session.workspace.set_purpose('audition')
    take = record_a_take(session, streams, clock)
    assert streams[0].options['device'] == 6 and streams[0].options['samplerate'] == 48000
    assert take.rate == 48000 and take.export_rate == 48000


def test_a_microphone_chosen_through_mme_in_0_6_now_records_at_its_own_rate(make_session, monkeypatch, real_lists, clock,
                                                                              tmp_path):
    streams = sound_library(monkeypatch)
    settings.update({'input_device': {'mic': f'{STUDIO[:31]}  [MME]'}}, tmp_path / 'settings.json')
    session = make_session()
    session.workspace.set_purpose('audition')
    assert record_a_take(session, streams, clock).rate == 48000


def test_a_16_khz_headset_is_recorded_at_16_khz_and_not_made_up_to_more(make_session, monkeypatch, real_lists, clock):
    """An Audition is never kept above the rate it was recorded at: that would add no detail."""
    streams = sound_library(monkeypatch)
    session = make_session()
    session.capturing.select_device(7)
    session.workspace.set_purpose('audition')
    take = record_a_take(session, streams, clock)
    assert take.rate == 16000 and take.export_rate == 16000


# ---- several inputs ------------------------------------------------------------------------------------------------------------

def test_the_mix_opens_every_input_and_records_the_first_two(monkeypatch):
    """WASAPI may refuse to open two inputs of a device that has eight, so all eight are opened and two are kept."""
    streams = sound_library(monkeypatch, REMOTE, REMOTE_DEFAULTS)
    capture = cap.open_capture('mic', 3)
    capture.start()
    assert streams[0].options['channels'] == 8 and capture.channels == 2
    block = np.tile(np.arange(8, dtype=np.float32) / 10, (480, 1))
    streams[0].options['callback'](block, 480, None, SimpleNamespace(input_overflow=False, input_underflow=False))
    took = capture.stop()
    assert took.shape == (480, 2) and took[0].tolist() == pytest.approx([0.0, 0.1])


def test_a_two_input_device_is_opened_as_it_is(monkeypatch):
    streams = sound_library(monkeypatch)
    capture = cap.open_capture('mic', 6)
    capture.start()
    assert streams[0].options['channels'] == 2 and capture.channels == 2 and capture.rate == 48000
