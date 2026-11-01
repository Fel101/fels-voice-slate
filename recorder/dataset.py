"""A dataset: kept clips gathered in one new folder, for a tool that trains or tests on speech.

Two layouts. `clips` is a folder for each speaker with every clip's WAV and its sidecar beside it, so the permission travels
with the audio. `ljspeech` is the layout many text-to-speech tools read: a `wavs` folder and a `metadata.csv` of
`name|text|text` lines. Both also get a `manifest.jsonl`, which says for every clip where it came from and what it may be used
for (recorder/manifest.py).

Each clip is either the kept WAV, resampled if another rate is wanted, or made again from its original recording by its own
recipe. The second is what to use for a rate above the one the clip was kept at: resampling a 24 kHz clip up adds no detail,
and the original still has it. The clips that go in are the ones manifest.collect vouches for, so one that does not check out
is left out and named. Nothing in the library is changed.
"""
import json
import os
from pathlib import Path

import soundfile as sf

from . import audio_core as ac
from . import manifest
from .core import render
from .store import Library, _write_audio, file_sha256

LAYOUTS = {'clips': 'Clips with their sidecars', 'ljspeech': 'LJSpeech (wavs and metadata.csv)'}
RATES = (16000, 22050, ac.EXPORT_RATE, 44100, 48000)
DEFAULT_RATE = {'clips': ac.EXPORT_RATE, 'ljspeech': 22050}     # 22.05 kHz is what the LJSpeech set itself is


class DatasetProblem(OSError):
    """A clip could not go in. The message says why, in words the person can read."""


def check_folder(folder, library):
    """Refuse a folder that is inside the library, or that already holds something. Raises ValueError saying why."""
    folder = Path(folder)
    try:
        folder.resolve().relative_to(library.root.resolve())
    except (ValueError, OSError):
        pass
    else:
        raise ValueError('Choose a place outside the output folder, so that the dataset is not mistaken for kept clips.')
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError('Choose an empty folder: a dataset is one set of files, and nothing in the folder is overwritten.')


def _clean_text(text):
    """A transcript as one line with no `|`, which is what separates the columns of metadata.csv."""
    return ' '.join(str(text).replace('|', ' ').split())


class Export:
    """A dataset being written, one clip at a time. `add` each row of manifest.collect, then `finish`."""

    def __init__(self, library, folder, layout, rate, from_originals):
        if layout not in LAYOUTS:
            raise ValueError(f'There is no layout called {layout!r}')
        if rate not in RATES:
            raise ValueError(f'A dataset at {rate} Hz is not offered')
        check_folder(folder, library)
        self.library = library
        self.folder = Path(folder)
        self.layout = layout
        self.rate = rate
        self.from_originals = from_originals
        self.rows = []
        self.metadata = []
        self.folder.mkdir(parents=True, exist_ok=True)

    def _target(self, row, stem):
        if self.layout == 'ljspeech':
            return self.folder / 'wavs' / f"{row['speaker_id']}_{stem}.wav"
        return self.folder / row['speaker_id'] / f'{stem}.wav'

    def _audio(self, wav, data):
        """The clip at the rate wanted, and how it is written."""
        if self.from_originals:
            return self._from_original(wav, data)
        samples, rate = sf.read(str(wav), dtype='float32', always_2d=False)
        return ac.resample(ac.to_mono(samples), rate, self.rate)

    def _from_original(self, wav, data):
        folder = wav.parent
        master = Library.inside(folder, data['master']['file'])
        if not master.is_file():
            raise DatasetProblem('its original recording is missing')
        samples, rate = sf.read(str(master), dtype='float32', always_2d=False)
        recipe = Library._recipe_from(data, len(samples), rate, Library._chain_from_sidecar)
        return render.export_samples(samples, rate, recipe, self.library._denoiser(), export_rate=self.rate)

    def _subtype(self, data):
        return 'PCM_16' if self.layout == 'ljspeech' else ac.purpose_of(data.get('purpose')).subtype

    def add(self, row):
        """Write one clip. Raises DatasetProblem, or the CleanError or OSError of a step that failed."""
        wav = Path(row['audio_filepath'])
        sidecar = wav.with_suffix('.json')
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        target = self._target(row, wav.stem)
        target.parent.mkdir(parents=True, exist_ok=True)
        audio = self._audio(wav, data)
        _write_audio(target, audio, self.rate, self._subtype(data))
        sha = file_sha256(target)
        duration = round(len(audio) / self.rate, 3)
        if self.layout == 'clips':
            data['export'].update(file=target.name, sample_rate=self.rate, bit_depth=int(self._subtype(data)[-2:]),
                                  duration_seconds=duration, sha256=sha)
            data['dataset'] = {'made_from': 'original' if self.from_originals else 'kept clip'}
            Library._write_json(target.with_suffix('.json'), data)
        self.metadata.append(f'{target.stem}|{_clean_text(row["text"])}|{_clean_text(row["text"])}\n')
        self.rows.append({**row, 'audio_filepath': Path(os.path.relpath(target, self.folder)).as_posix(), 'duration': duration,
                          'sample_rate': self.rate, 'sha256': sha})

    def finish(self):
        """Write the files that list the clips. Returns how many clips there are."""
        if self.rows:
            manifest.write(self.folder / 'manifest.jsonl', self.rows)
            if self.layout == 'ljspeech':
                with open(self.folder / 'metadata.csv', 'w', encoding='utf-8', newline='') as out:
                    out.writelines(self.metadata)
        return len(self.rows)
