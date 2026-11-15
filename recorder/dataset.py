"""A dataset: kept clips gathered in one new folder, for a tool that trains or tests on speech.

Four layouts, each in the form that tool's own documentation gives:

- `clips`: a folder for each speaker with every clip's WAV and its sidecar beside it, so the permission travels with the
  audio.
- `ljspeech`: what many text-to-speech tools read, a `wavs` folder and a `metadata.csv` of `name|text|text` lines.
- `audiofolder`: Hugging Face's AudioFolder, the audio in a folder named for its split with a `metadata.csv` beside it whose
  `file_name` column is the path from there. The audio is always in a split folder, `train` when there is no split: the
  datasets library takes a split from a file name too, and a speaker called "Test" would otherwise be its test split.
- `kaldi`: a Kaldi data folder, `wav.scp`, `text`, `utt2spk` and `spk2utt`, sorted by byte as Kaldi wants, with the audio in
  `wavs`.

Every layout also gets a `manifest.jsonl`, which says for every clip where it came from and what it may be used for
(recorder/manifest.py).

A split puts some clips aside for validation. It is the same every time for the same clips: they are ranked by a hash of
their file's name and the first ones go to validation, so it does not depend on the order they come in or on chance.

Each clip is either the kept WAV, resampled if another rate is wanted, or made again from its original recording by its own
recipe. The second is what to use for a rate above the one the clip was kept at: resampling a 24 kHz clip up adds no detail,
and the original still has it. The clips that go in are the ones manifest.collect vouches for, so one that does not check out
is left out and named. Nothing in the library is changed.
"""
import csv
import hashlib
import json
import os
from pathlib import Path

import soundfile as sf

from . import audio_core as ac
from . import manifest
from .core import render
from .store import Library, _write_audio, file_sha256

LAYOUTS = {'clips': 'Clips with their sidecars', 'ljspeech': 'LJSpeech (wavs and metadata.csv)',
           'audiofolder': 'Hugging Face audio folder (metadata.csv)', 'kaldi': 'Kaldi (wav.scp, text, utt2spk)'}
RATES = (16000, 22050, ac.EXPORT_RATE, 44100, 48000)
# 22.05 kHz is what the LJSpeech set itself is, and 16 kHz is what Kaldi's recipes expect.
DEFAULT_RATE = {'clips': ac.EXPORT_RATE, 'ljspeech': 22050, 'audiofolder': ac.EXPORT_RATE, 'kaldi': 16000}
SIXTEEN_BIT = ('ljspeech', 'kaldi')          # what their tools read; the other layouts keep an audition's 24 bits
TRAIN, VALIDATION = 'train', 'validation'
# How many clips in a hundred are put aside for validation, and what the choice is called.
SPLITS = {0: 'No split', 5: '95% for training, 5% for validation', 10: '90% for training, 10% for validation',
          20: '80% for training, 20% for validation'}
_SPLIT_SALT = 'voice-slate-split'


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


def _one_line(text):
    return ' '.join(str(text).split())


def _rank(row):
    """Where a clip comes in the order a split takes clips from: by a hash of the speaker and the file's name, which stay
    the same for as long as the clip is kept."""
    key = f'{_SPLIT_SALT}/{row["speaker_id"]}/{Path(row["audio_filepath"]).stem}'
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def split(rows, percent):
    """The rows, each with `split` set to TRAIN or VALIDATION, `percent` in a hundred of them for validation, rounded, and
    at least one once there are two clips, but never all of them. With no split the rows are given back as they are."""
    if not percent:
        return list(rows)
    rows = list(rows)
    count = min(len(rows) - 1, max(1, int(len(rows) * percent / 100 + 0.5))) if len(rows) > 1 else 0
    held = {id(row) for row in sorted(rows, key=_rank)[:count]}
    return [{**row, 'split': VALIDATION if id(row) in held else TRAIN} for row in rows]


def kaldi_speaker(slug):
    """A speaker's id for Kaldi. Kaldi wants each speaker's id at the front of their utterances' ids, with a dash after it,
    and the utterances sorted by id to come out in the order of their speakers too. A folder name holds dashes ("voice-2"),
    which would break that order, and never an underscore, so a dash becomes an underscore and no two speakers meet."""
    return slug.replace('-', '_')


def _write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as out:
        out.writelines(lines)


def _write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(rows)


class Export:
    """A dataset being written, one clip at a time. `split` the rows of manifest.collect, `add` each, then `finish`."""

    def __init__(self, library, folder, layout, rate, from_originals, validation=0):
        if layout not in LAYOUTS:
            raise ValueError(f'There is no layout called {layout!r}')
        if rate not in RATES:
            raise ValueError(f'A dataset at {rate} Hz is not offered')
        if validation not in SPLITS:
            raise ValueError(f'A split with {validation}% for validation is not offered')
        check_folder(folder, library)
        self.library = library
        self.folder = Path(folder)
        self.layout = layout
        self.rate = rate
        self.from_originals = from_originals
        self.validation = validation
        self.rows = []
        self._stems = {}            # the clip's own name for each WAV written, for Kaldi's utterance ids
        self.folder.mkdir(parents=True, exist_ok=True)

    def split(self, rows):
        """The rows with the part of the split each one is in, or as they are with no split."""
        return split(rows, self.validation)

    def _target(self, row, stem):
        part, flat = row.get('split'), f"{row['speaker_id']}_{stem}.wav"
        if self.layout in ('ljspeech', 'kaldi'):
            return self.folder / 'wavs' / flat
        if self.layout == 'audiofolder':
            return self.folder / (part or TRAIN) / flat
        return self.folder.joinpath(*([part] if part else []), row['speaker_id'], f'{stem}.wav')

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
        return 'PCM_16' if self.layout in SIXTEEN_BIT else ac.purpose_of(data.get('purpose')).subtype

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
            if row.get('split'):
                data['dataset']['split'] = row['split']
            Library._write_json(target.with_suffix('.json'), data)
        written = Path(os.path.relpath(target, self.folder)).as_posix()
        self._stems[written] = wav.stem
        self.rows.append({**row, 'audio_filepath': written, 'duration': duration, 'sample_rate': self.rate, 'sha256': sha})

    def parts(self):
        """(TRAIN, how many) and (VALIDATION, how many) of the clips written so far, or () with no split."""
        if not self.validation:
            return ()
        return tuple((part, sum(row.get('split') == part for row in self.rows)) for part in (TRAIN, VALIDATION))

    def _in(self, part):
        return [row for row in self.rows if row.get('split') == part]

    def _ljspeech(self):
        def lines(rows):
            return [f'{Path(row["audio_filepath"]).stem}|{_clean_text(row["text"])}|{_clean_text(row["text"])}\n'
                    for row in rows]

        _write_lines(self.folder / 'metadata.csv', lines(self.rows))
        if self.validation:
            for part in (TRAIN, VALIDATION):
                _write_lines(self.folder / f'metadata_{part}.csv', lines(self._in(part)))

    def _audiofolder(self):
        for part in (TRAIN, VALIDATION):
            rows = [row for row in self.rows if (row.get('split') or TRAIN) == part]
            if rows:
                _write_csv(self.folder / part / 'metadata.csv', ('file_name', 'text', 'speaker_id', 'allowed_use'),
                           [(Path(row['audio_filepath']).name, _one_line(row['text']), row['speaker_id'],
                             _one_line(row['allowed_use'])) for row in rows])

    def _kaldi(self):
        def write(folder, rows):
            utterances = []
            for row in rows:
                speaker = kaldi_speaker(row['speaker_id'])
                utterances.append((f'{speaker}-{self._stems[row["audio_filepath"]]}', speaker, row))
            utterances.sort(key=lambda each: each[0])
            speakers = {}
            for utterance, speaker, _ in utterances:
                speakers.setdefault(speaker, []).append(utterance)
            _write_lines(folder / 'wav.scp', [f'{u} {row["audio_filepath"]}\n' for u, _, row in utterances])
            _write_lines(folder / 'text', [f'{u} {_one_line(row["text"])}'.rstrip() + '\n' for u, _, row in utterances])
            _write_lines(folder / 'utt2spk', [f'{u} {s}\n' for u, s, _ in utterances])
            _write_lines(folder / 'spk2utt', [f'{s} {" ".join(us)}\n' for s, us in sorted(speakers.items())])

        if not self.validation:
            write(self.folder, self.rows)
            return
        for part in (TRAIN, VALIDATION):
            if self._in(part):
                write(self.folder / part, self._in(part))

    def finish(self):
        """Write the files that list the clips. Returns how many clips there are."""
        if self.rows:
            manifest.write(self.folder / 'manifest.jsonl', self.rows)
            {'ljspeech': self._ljspeech, 'audiofolder': self._audiofolder, 'kaldi': self._kaldi}.get(self.layout,
                                                                                                    lambda: None)()
        return len(self.rows)
