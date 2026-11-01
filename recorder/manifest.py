"""A list of the kept clips for other programs: one row for each clip, with its file, its transcript and its length,
as JSON Lines or as CSV.

The rows come from the clips' sidecars, and each clip's WAV is read once to check that it is still the file that was
kept (its SHA-256 is the one in the sidecar). A clip that fails any check is left out and named, never listed with
details that no longer fit its file. Nothing is changed or copied: the manifest only points at the clips where they are.
The column names `audio_filepath`, `text` and `duration` are the ones speech toolkits such as NeMo read.
"""
import csv
import json
import os
from pathlib import Path

import soundfile as sf

from .store import KNOWN_SCHEMAS, Library, SidecarProblem, clean_label, file_sha256

FORMATS = {'jsonl': ('JSON Lines', '.jsonl'), 'csv': ('CSV', '.csv')}
COLUMNS = ('audio_filepath', 'text', 'duration', 'speaker', 'speaker_id', 'label', 'sample_rate', 'source', 'created', 'id',
           'origin', 'allowed_use', 'transcript_status', 'sha256')
# A spreadsheet reads a cell that starts with one of these as a formula, and a transcript can start with anything.
_FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r')
# Text that some programs treat as the end of a line, although JSON does not: written as escapes so a row is one line.
_LINE_BREAKS = {'\u2028': '\\u2028', '\u2029': '\\u2029', '\x85': '\\u0085'}


def kind_for(path):
    """'jsonl' or 'csv' by the file name's ending, or None."""
    ending = Path(path).suffix.lower()
    return next((kind for kind, (_, suffix) in FORMATS.items() if suffix == ending), None)


def _path_text(wav, base):
    """The clip's path for the row: relative to the manifest's folder when there is one that works, else the full path."""
    if base is not None:
        try:
            return Path(os.path.relpath(wav, base)).as_posix()
        except ValueError:                           # another drive: there is no relative way
            pass
    return Path(wav).resolve().as_posix()


def _row(library, folder, data, slug, speaker, base):
    export = data.get('export')
    if not isinstance(export, dict) or not isinstance(data.get('transcript'), str):
        raise SidecarProblem('it is incomplete')
    if data.get('consent_confirmed') is not True:
        raise SidecarProblem('it does not record the permission to use the voice')
    wav = Library.inside(folder, export.get('file'))
    if wav.name != export.get('file'):
        raise SidecarProblem('its WAV is not next to it')
    if not wav.is_file():
        raise SidecarProblem('its WAV file is missing')
    recorded = export.get('sha256')
    if not isinstance(recorded, str):
        raise SidecarProblem('it holds no SHA-256 of its WAV')
    try:
        actual = file_sha256(wav)
        info = sf.info(str(wav))
    except (OSError, RuntimeError):
        raise SidecarProblem('its WAV could not be read') from None
    if actual != recorded.lower():
        raise SidecarProblem('its WAV has changed since it was kept (the SHA-256 differs)')
    return {
        'audio_filepath': _path_text(wav, base),
        'text': data['transcript'].strip(),
        'duration': round(info.duration, 3),
        'speaker': speaker,
        'speaker_id': slug,
        'label': clean_label(data.get('label')) if isinstance(data.get('label'), str) else '',
        'sample_rate': int(info.samplerate),
        'source': str(data.get('source') or ''),
        'created': str(data.get('created') or ''),
        'id': str(data.get('id') or ''),
        'origin': str(data.get('origin') or '').strip(),
        'allowed_use': str(data.get('allowed_use') or '').strip(),
        'transcript_status': str(data.get('transcript_status') or ''),
        'sha256': actual,
    }


def collect(library, slugs=None, base=None):
    """The kept clips of the given speakers (all of them when None) as rows, and the clips left out as (file, reason).

    `base` is the folder the manifest will be written to: the paths in the rows are relative to it. Rows are in the order
    of the speakers' names, then of when the clips were made.
    """
    speakers = library.speakers()
    chosen = sorted(speakers) if slugs is None else [slug for slug in slugs if slug in speakers]
    rows, left_out = [], []
    for slug in chosen:
        folder = library.folder(slug)
        for sidecar in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            try:
                data = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                left_out.append((sidecar.name, 'it could not be read'))
                continue
            if not isinstance(data, dict) or data.get('schema') not in KNOWN_SCHEMAS:
                continue                             # some other JSON file that happens to sit there
            try:
                rows.append(_row(library, folder, data, slug, speakers[slug]['name'], base))
            except SidecarProblem as e:
                left_out.append((sidecar.name, str(e)))
            except (OSError, TypeError, ValueError):
                left_out.append((sidecar.name, 'it holds a value that cannot be read'))
    rows.sort(key=lambda row: (row['speaker'].casefold(), row['speaker_id'], row['created'], row['id']))
    return rows, left_out


def _cell(name, value):
    """A value for a CSV cell. Text that a spreadsheet would run as a formula gets an apostrophe in front. The path and
    the hash are left alone: a program reading the file needs them exactly, and neither can start that way."""
    if isinstance(value, str) and name not in ('audio_filepath', 'sha256') and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def _jsonl_line(row):
    line = json.dumps(row, ensure_ascii=False)
    for char, escape in _LINE_BREAKS.items():
        line = line.replace(char, escape)
    return line + '\n'


def write(path, rows, kind=None):
    """Write the rows to `path` as JSON Lines or CSV (by `kind`, else by the file's ending) and return the path.

    The file is UTF-8 with no byte-order mark, and appears whole or not at all. A CSV cell that would be read as a
    formula gets a leading apostrophe; the JSON Lines file holds every text exactly as it is. Raises ValueError for a
    file ending that is neither, and OSError when the file cannot be written.
    """
    path = Path(path)
    kind = kind or kind_for(path)
    if kind not in FORMATS:
        raise ValueError('A manifest is a .jsonl or a .csv file')
    tmp = path.with_name(path.name + '.tmp')
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as out:
            if kind == 'jsonl':
                out.writelines(_jsonl_line(row) for row in rows)
            else:
                writer = csv.writer(out)
                writer.writerow(COLUMNS)
                writer.writerows([_cell(name, row[name]) for name in COLUMNS] for row in rows)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return path
