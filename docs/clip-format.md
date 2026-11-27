# Clip format

What Voice Slate writes to disk, for people who want to use the clips in other programs. The kept WAV and its JSON
sidecar together define the Fel's Studio suite clip format. A [`.voiceslate` handoff](#voice-slate-handoff-files) carries
both files in a ZIP archive.

## Folder layout

```text
voice-clips\                                    the output folder: in your own folder by default (Settings > Output folder)
  speakers.json                                 the list of speakers
  my-voice\                                     one folder per speaker
    masters\
      20261020-161204_mic_3f9a1c2e.raw.wav      the original recording
      20261020-161204_mic_3f9a1c2e.autosave.json  work in progress on a take that is not kept yet (temporary)
    20261020-161204_mic_3f9a1c2e.wav            the kept clip
    20261020-161204_mic_3f9a1c2e.json           the sidecar for the kept clip
    release\
      release.json                              what is on record about the speaker's signed release
      signed-release.pdf                        the signed release itself
```

A clip's three files share a name: `YYYYMMDD-HHMMSS_<source>_<id>`, the local date and time the take was made, the source
(`mic`, `system` or `import`), and a random 8-character id. A name that is already taken gets `-2`, `-3` and so on, so
nothing is overwritten.

| File | What it is |
|---|---|
| `masters\*.raw.wav` | The original recording, written the moment recording stops (or a file is imported). **24-bit PCM at the device's own sample rate**, one or two channels. It is never modified. A master with no sidecar next to it is a take that has not been kept yet. |
| `*.wav` | The **kept clip**: edited, processed and resampled, mono PCM. A Reference or a Sentence is **24 kHz, 16-bit**, and an Audition **up to 48 kHz, 24-bit**, never above the rate it was recorded at. `export` in the sidecar says which. This is the file to hand to other tools. |
| `*.json` | The **sidecar**, described below. |
| `masters\*.autosave.json` | **Temporary, and not part of the format.** The cuts, processing settings and text of a take that Keep has not written yet, saved so that a crash or a close loses nothing. That includes a kept clip changed since, whose WAV and sidecar stay as they were until Keep. Voice Slate deletes it when the clip is kept, and it never holds the permission tick. It sits in `masters`, not beside the sidecars, and its `schema` is not `voice-recorder-clip-*`, so a program that reads the clips as described [below](#reading-the-clips-from-another-program) never meets it. |
| `masters\*.spool` | **Temporary, and not part of the format.** A take while it is being recorded: one line of JSON, then the samples as 32-bit floats. It is gone once the original is written. One that is still there when the program starts is a take that was cut short, and becomes a take. |
| `speakers.json` | `{ "my-voice": { "name": "My voice", "origin": "...", "allowed_use": "..." } }`. The origin and allowed use remembered for each speaker. The key is the folder name. |
| `release\release.json` | Optional. `{ "schema": "voice-slate-release-v1", "file": "signed-release.pdf", "sha256": "...", "signed": "2026-09-02", "expires": "2027-09-02", "notes": "...", "recorded": "..." }`. Dates are `YYYY-MM-DD`, or empty. An earlier release file stays in the folder when a new one is put on record. |

## The sidecar

A UTF-8 JSON object. An example, written compactly (the program writes it with one value per line, in this order):

```json
{
  "schema": "voice-recorder-clip-v2",
  "software": { "name": "voice-slate", "version": "1.0.0" },
  "id": "3f9a1c2e",
  "created": "2026-10-20T16:12:04+01:00",
  "speaker": "my-voice",
  "source": "mic",
  "purpose": "reference",
  "device": { "name": "Studio Microphone", "hostapi": "Windows WASAPI", "native_rate": 48000, "channels": 1 },
  "export": {
    "file": "20261020-161204_mic_3f9a1c2e.wav",
    "sample_rate": 24000, "channels": 1, "bit_depth": 16,
    "duration_seconds": 13.3,
    "sha256": "4269d3b7421de2c740a6c23134683ae224c1a13026a1fdab7b8995c2f8abed4e"
  },
  "master": {
    "file": "masters/20261020-161204_mic_3f9a1c2e.raw.wav",
    "sample_rate": 48000, "bit_depth": 24, "channels": 1
  },
  "measurements": { "peak_dbfs": -3.1, "rms_dbfs": -22.4, "clipped_fraction": 0.0, "silence_fraction": 0.21,
                    "recording_clipped_fraction": 0.0 },
  "transcript": "The morning ferry left the harbour just as the fog began to lift.",
  "transcript_status": "typed",
  "origin": "Recorded by me at my desk",
  "allowed_use": "Personal narration voice, not shared",
  "consent_confirmed": true,
  "consent_confirmed_at": "2026-10-20T16:15:31+01:00",
  "source_url": null,
  "master_seconds": 14.0,
  "edits": [
    { "op": "cut", "region": "middle", "start_seconds": 4.2, "end_seconds": 4.9 }
  ],
  "join_fade_ms": 10.0,
  "chain": {
    "highpass": { "enabled": true, "hz": 80.0 },
    "hum": { "enabled": false, "hz": 60.0, "harmonics": 3 },
    "denoise": { "enabled": false, "nf": -45.0 },
    "level": { "gain_db": 5.1 },
    "limiter": { "enabled": true, "ceiling_db": -1.0 }
  },
  "needs_transcript_review": false,
  "notes": "",
  "flags": ["edited", "filtered", "level_changed", "limited"],
  "label": "Welcome back, take two"
}
```

### Fields

| Field | Meaning |
|---|---|
| `schema` | `voice-recorder-clip-v2`. The `voice-recorder-clip-` prefix is permanent for compatibility. A change that removes a field or changes its meaning gets a new version number. Readers should ignore fields they do not recognise. |
| `software` | The program and version that wrote the file. |
| `id`, `created` | The take's id, and when it was made (ISO 8601 with the local UTC offset). |
| `speaker` | The speaker's folder name. |
| `source` | `mic`, `system` (what the computer played), or `import` (a file brought in). |
| `purpose` | What the clip is for: `reference`, `sentence` or `audition`, which set its length rules and format. Clips kept by earlier versions do not have it, and are references. |
| `device` | What captured the audio: the device name, the Windows audio interface, the native sample rate and channel count. For an imported file, only its file name. |
| `export` | The kept WAV: its file name (in the same folder), format, duration, and the **SHA-256** of its bytes, so you can check a file has not changed since it was kept. |
| `master` | The original recording: its path relative to the sidecar's folder, and its format. |
| `measurements` | Measured on the kept clip: `peak_dbfs`, `rms_dbfs` (whole clip), `clipped_fraction` (share of samples within 0.5% of full scale), and `silence_fraction` (share of 20 ms frames quieter than -45 dB). `recording_clipped_fraction` is the same share measured on the recording, on its loudest channel and leaving out what was cut or silenced: gain and the limiter can bring `clipped_fraction` to 0 without repairing anything, so this is the number to trust. Clips kept by earlier versions do not have it. |
| `transcript` | What is said in the clip, 3 to 600 characters. |
| `transcript_status` | How the transcript came about: `typed`, `read_from_script` (a script line, unchanged), `asr_draft` (the optional speech recognizer's draft, unchanged), or `edited` (a script line or draft that was then changed). A draft is a guess, so `asr_draft` means nobody has corrected it. |
| `origin`, `allowed_use` | The person's own words: where the recording came from, and what the voice may be used for. |
| `consent_confirmed` | Always `true` in a kept clip. Voice Slate will not write a sidecar otherwise. It is the person's statement that they have the right to use the voice, **not** a verified or legal consent record. Changing the `origin` or `allowed_use` of a kept clip needs the statement made again. |
| `consent_confirmed_at` | When the person made that statement (ISO 8601 with the local UTC offset). Clips kept by earlier versions do not have it. |
| `consent_batch` | Optional. Present when the statement was made once for several takes kept together: how many it covered. Each clip's sidecar still holds the statement, and the time is the same on all of them. |
| `release` | Optional. The speaker's signed release when the clip was kept: `file` (relative to the sidecar's folder), `sha256`, `signed` and `expires` (`YYYY-MM-DD`), each `null` if there was none. It is a pointer to what the person holds, **not** a verified legal document. |
| `source_url` | An optional link to the original, or `null`. |
| `master_seconds` | The length of the original recording. |
| `edits` | The edit list, below. |
| `join_fade_ms` | The crossfade at each cut, in milliseconds. |
| `chain` | The processing settings, below. |
| `needs_transcript_review` | Always `false` in a kept clip: Keep requires the transcript to be confirmed after cuts. |
| `notes` | Free text. |
| `flags` | Facts about the take, worked out again at each Keep: `edited`, `filtered` (high-pass or hum notch on), `cleaned` (noise reduction on), `level_changed`, `limited` (limiter on), `clipped` (a sample of the kept clip, or of the recording it was made from, reaches full scale), `interrupted` (the device stopped while recording, or the program did), `dropouts` (the device reported losing audio while recording), `split` (a part of a longer take). |
| `label` | Optional. A name the person gave the take to find it by: one line, at most 80 characters. The field is absent when the take has no label. It can change after the clip was kept (renaming a kept take rewrites only this field of the sidecar), and the WAV never changes with it, so the SHA-256 in `export` still holds. |
| `markers` | Optional. Moments the person marked, in order: a list of `{ "seconds": 12.345 }`, in seconds on the master's timeline like the edits, to the millisecond. The field is absent when the take has none. Like `label`, it can change after the clip was kept (only this field of the sidecar is rewritten), and the WAV never changes with it. A marker can fall inside a part that was cut. |
| `moves` | Optional. Present only on a clip that was moved to another speaker: a list, oldest first, of `{ "from": "<speaker folder>", "to": "<speaker folder>", "at": "<ISO 8601 time>" }`. `speaker` always names where the clip is now. The WAV is the same bytes, so the SHA-256 in `export` still holds. |
| `script_line` | Optional. The line of a script the take was read from, exactly as it stood. |
| `words` | Optional. What the speech recognizer heard, as `[word, start, end]` rows, with times in seconds on the master's timeline. A draft: the words can be wrong, and the times are rough. Present when the take had a draft. |
| `split_from` | Optional. On a part of a take that was split: `{ "take": "<id of the long take>", "start_seconds": ..., "end_seconds": ... }`, on that take's timeline. |
| `dataset` | Only on the copy of a sidecar in an exported dataset: `{ "made_from": "original" }` or `"kept clip"`, and `"split": "train"` or `"validation"` when the dataset was split. There, `export` describes the WAV in the dataset, and `master` still names the file in the library, which is not part of the dataset. |

### Edits

Each entry has an `op` and times in **seconds on the master recording's timeline**, so they refer to the untouched
original, whatever order they were made in.

| `op` | Other fields | Effect |
|---|---|---|
| `cut` | `region`: `start`, `middle` or `end` | Remove that part. Joins are crossfaded by `join_fade_ms`, and each cut point moves to the nearest zero crossing within 5 ms. |
| `silence` | | Replace that part with silence, keeping the length. |
| `fade` | `direction`: `in` or `out` | Fade that part in from, or out to, silence. |
| `gain` | `gain_db` (-60 to 24) | Raise or lower that part. |

### Processing chain

The kept clip is built from the master in a fixed order: the master is mixed down to mono; silence, fades and region
gain are applied; the cuts are made and joined; then the chain below runs (high-pass, hum notch, noise reduction, gain,
limiter); and last the clip is resampled to the rate it is kept at, with the limiter, if it is on, applied once more at that
rate so that resampling cannot push a peak above its ceiling.

| Section | Settings |
|---|---|
| `highpass` | `enabled`, `hz` (20 to 300). A 4th-order Butterworth filter, run forward and backward so it adds no delay. |
| `hum` | `enabled`, `hz` (30 to 120, normally 50 or 60), `harmonics` (1 to 6). Narrow notches at the mains frequency and its harmonics. |
| `denoise` | `enabled`, `nf` (-80 to -20 dB). FFmpeg's `afftdn` filter at that noise floor. When enabled the sidecar also holds `filter` (the exact filter text) and `identity_checked: false`, since nobody checks that the cleaned voice still matches the original. |
| `level` | `gain_db` (-24 to 36). A gain for the whole clip. |
| `limiter` | `enabled`, `ceiling_db` (-12 to 0). A look-ahead peak limiter. |

Everything needed to rebuild the kept clip from the master is in the sidecar, but you do not need it to use the clip:
the `.wav` file is complete.

## Inside the WAV

A kept clip's WAV carries one line of plain text, so that the clip still says what it may be used for when it is copied
somewhere without its sidecar. It is in the standard information chunk (`LIST` `INFO`, as `ICMT`, the comment), after the
audio, which is the only place Windows' own file properties look for it:

```text
Fel's Voice Slate clip 3f9a1c2e, described in 20261020-161204_mic_3f9a1c2e.json. Allowed use: my own voice references. Permission given 2026-10-20.
```

- The id and the sidecar's name are the clip's own, also in a copy or a dataset where the WAV has another name.
- The allowed use is on one line. One longer than 400 characters is cut there and ends `... (the rest is in the sidecar)`.
- *Permission given* is the date part of `consent_confirmed_at`, and is left out when the sidecar has none.
- The origin is not in it: it can say who the person is. Read it from the sidecar.

The line is written whenever a clip is kept or kept again, so `export.sha256` is of the file with the line in it. Clips
kept before version 0.7 do not have it, and are not changed to add it. Copies (**Save a copy...**) and the clips of a
dataset carry it too, made from the sidecar, whenever the clip was kept: in WAV the same chunk, in FLAC and Ogg Vorbis
the `COMMENT` field, and in MP3 its ID3 comment.

The chunk names no character set. The line is written in UTF-8, as typed. What was seen on Windows 11 with the
allowed use "réunion d’équipe, Señora Núñez":

| Read by | WAV | FLAC | Ogg Vorbis | MP3 |
|---|---|---|---|---|
| libsndfile (SoundFile, and the programs built on it) | as typed | as typed | as typed | without accents |
| FFmpeg 8.1 (`ffprobe`) | as typed | as typed | | without accents |
| Windows Explorer, *Properties > Details > Comments* | accents wrong ("rÃ©union") | not shown | as typed | without accents |

Windows reads a WAV's comment as the Windows code page, so an accented letter shows as two wrong ones there; the sidecar
has the text as it was typed. FLAC and Ogg Vorbis define their comments as UTF-8. An MP3's tag is written as Latin-1 by the
encoder inside libsndfile, which garbled every accent in every program, so in an MP3 copy the line has its accents taken
off ("é" becomes "e", "’" becomes "'") and any other letter outside plain ASCII is a `?`.

## Reading the clips from another program

For each `*.json` file in a speaker's folder whose `schema` starts with `voice-recorder-clip-`:

1. Read the JSON.
2. Check `consent_confirmed` is `true` and `allowed_use` covers what you plan to do.
3. Optionally check the SHA-256 of `export.file` against `export.sha256`.
4. Use `export.file` and `transcript`.

`speaker` is the name of the folder the sidecar is in. If you move a clip's files between speaker folders by hand,
change `speaker` in the sidecar as well, or use **Move to speaker** in the program, which does it for you and keeps a
note in `moves`.

A short Python example:

```python
import hashlib
import json
from pathlib import Path

for sidecar in Path("voice-clips/my-voice").glob("*.json"):      # the output folder, relative to where you run this
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if not str(data.get("schema", "")).startswith("voice-recorder-clip-"):
        continue
    wav = sidecar.with_name(data["export"]["file"])
    intact = hashlib.sha256(wav.read_bytes()).hexdigest() == data["export"]["sha256"]
    print(wav.name, "intact" if intact else "CHANGED", "|", data["allowed_use"], "|", data["transcript"])
```

## Voice Slate handoff files

A `.voiceslate` file carries one kept clip in the Fel's Studio suite clip format. Put exactly two case-sensitive entries at
the root of a ZIP archive. Use ZIP method 0 (`Stored`) and leave encryption disabled for both entries. Entry order can vary.

| ZIP entry | Contents | Maximum size |
|---|---|---|
| `clip.wav` | The exact bytes of the kept WAV, including its metadata chunks | 32 MiB (33,554,432 bytes) |
| `clip.json` | A UTF-8 JSON object without a byte-order mark, carrying the kept sidecar's fields. A handoff can add `speaker_display_name` with the current Unicode display name. | 1 MiB (1,048,576 bytes) |

The exported display name can reflect a rename made after the clip was kept. `speaker` remains the stable identifier and
the fallback for bundles without a display name. Slate leaves the original sidecar and the kept WAV in place. The audio
hash in the copied sidecar still describes `clip.wav`. Review or hash the sidecar inside the bundle when checking a handoff.

Voice Lab caps the whole ZIP at 33 MiB plus 4,096 bytes for ZIP overhead. Copy the WAV byte for byte and preserve the
sidecar's existing fields. The fixed ZIP entry names leave the original file names in the sidecar intact. The master
recording and any release documents stay in the speaker's folder.

### What the receiver checks

- `schema` is `voice-recorder-clip-v1` or `voice-recorder-clip-v2`. The numeric suffix identifies the version.
- `consent_confirmed` is the JSON boolean `true`. `transcript`, `origin` and `allowed_use` are strings with content after
  trimming whitespace.
- `id` is a string of 1 to 64 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit.
- `export.file` is the kept WAV's portable file name. It ends in `.wav` (case insensitive) and has 1 to 176 characters
  before the extension. Exclude `< > : " / \ | ? *` and control characters U+0000 through U+001F. The stem before the first
  dot must also avoid Windows device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, and the `COM` and
  `LPT` forms with superscript `¹`, `²` or `³`), ignoring case and trailing spaces.
- `master.file` is a relative path inside the speaker's folder, such as `masters/take.raw.wav`. Reject drive prefixes,
  `..` segments, colons and control characters. It names a different file from `export.file`. The receiver only needs this
  reference in the sidecar.
- `export.sha256` is a 64-digit hexadecimal SHA-256 that matches the bytes of `clip.wav`. The WAV container must be readable
  and its sample rate, channel count and bit depth must match `export.sample_rate`, `export.channels` and `export.bit_depth`.
  Voice Lab recognises 16-, 24- and 32-bit PCM for the bit-depth check.
- `transcript_status: "asr_draft"` means the speech recognizer's transcript is unchecked. The receiver requires the person's
  explicit acceptance before importing it.

### Writing a handoff

Starting with a kept WAV and its sidecar that pass those checks, create a new `.voiceslate` file with Python's standard
library. Save this example as `make_handoff.py` and give it the two input paths and an output path such as
`python make_handoff.py take.wav take.json take.voiceslate`.

```python
import sys
from zipfile import ZIP_STORED, ZipFile

wav_path, sidecar_path, output_path = sys.argv[1:]
with ZipFile(output_path, "x", compression=ZIP_STORED) as handoff:
    handoff.write(wav_path, "clip.wav")
    handoff.write(sidecar_path, "clip.json")
```

## Manifest files

**Export manifest...** in the program writes one file that lists the kept clips, for programs that read a whole set of
clips at once. It is made from the sidecars, so it holds nothing they do not. It is a snapshot: write it again after you
keep, move or discard clips. Choose JSON Lines (`.jsonl`, one JSON object per line) or CSV (`.csv`, with a header row).

| Column | Meaning |
|---|---|
| `audio_filepath` | The kept WAV. Relative to the folder the manifest is saved in, with `/` between the names; the full path when there is no relative one (another drive). |
| `text` | The transcript, trimmed. |
| `duration` | The length in seconds to three decimals, read from the WAV. |
| `speaker`, `speaker_id` | The speaker's name as shown, and the speaker's folder name. |
| `label` | The take's label, or empty. |
| `sample_rate` | Of the WAV. |
| `source`, `created`, `id` | As in the sidecar: `mic`, `system` or `import`; when the take was made; the take's id. |
| `origin`, `allowed_use`, `transcript_status` | As in the sidecar. |
| `sha256` | The SHA-256 of the WAV, checked against the sidecar's when the manifest was written. |
| `purpose` | `reference`, `sentence` or `audition`. |
| `release_signed`, `release_expires` | The dates of the speaker's signed release, or empty. |

`audio_filepath`, `text` and `duration` are the names that speech toolkits such as NeMo read. A clip is only listed when
its sidecar can be read and records the permission, its WAV is next to it, readable, and still the file that was
kept, and the speaker's release, if there is one, has not run out; any other clip is left out and named to the person who
exports. The file is UTF-8 with no byte-order mark, so the
first column name is exactly `audio_filepath`.

- **JSON Lines**: one object per line, the keys in the order above. The characters U+2028, U+2029 and U+0085, which some
  programs treat as a line end although JSON does not, are written as `\u` escapes, so one clip is always one line.
- **CSV**: standard quoting, `\r\n` line ends. A text that a spreadsheet could run as a formula, one that starts with
  `=`, `+`, `-`, `@`, a tab or a carriage return, has a `'` put in front of it, in every column except `audio_filepath`
  and `sha256`; the JSON Lines file never changes a text. Excel opens a CSV that has no byte-order mark as if it were in
  the local code page, so accented letters can come out wrong when you double-click the file; use *Data > From Text/CSV*
  and choose UTF-8.

## Datasets

**Export dataset...** writes a new folder, and nothing in the library changes. The clips in it are the ones a manifest would list.

```text
my-dataset\                     Clips with their sidecars
  manifest.jsonl                every clip, as above, with paths relative to this folder
  my-voice\
    20261020-161204_mic_3f9a1c2e.wav
    20261020-161204_mic_3f9a1c2e.json

my-dataset\                     LJSpeech
  metadata.csv                  one clip per line: name|text|text
  manifest.jsonl                the same, with the origin and allowed use of each clip
  wavs\
    my-voice_20261020-161204_mic_3f9a1c2e.wav

my-dataset\                     Hugging Face audio folder
  manifest.jsonl
  train\
    metadata.csv                file_name,text,speaker_id,allowed_use
    my-voice_20261020-161204_mic_3f9a1c2e.wav

my-dataset\                     Kaldi
  wav.scp                       my_voice-20261020-161204_mic_3f9a1c2e wavs/my-voice_20261020-161204_mic_3f9a1c2e.wav
  text                          my_voice-20261020-161204_mic_3f9a1c2e The harbour lights came on at dusk.
  utt2spk                       my_voice-20261020-161204_mic_3f9a1c2e my_voice
  spk2utt                       my_voice my_voice-20261020-161204_mic_3f9a1c2e ...
  manifest.jsonl
  wavs\
    my-voice_20261020-161204_mic_3f9a1c2e.wav
```

In the LJSpeech `metadata.csv` the first column is the file's name without `.wav`, and the second and third are the transcript
on one line, with any `|` replaced by a space. The LJSpeech and Kaldi layouts' WAVs are mono and 16-bit; in the other layouts
an audition stays 24-bit.

The Hugging Face `metadata.csv` is a CSV with a header row, UTF-8 with no byte-order mark. `file_name` is the path from the
folder it is in, as the `datasets` library wants, and the transcript is on one line, exactly as it is: unlike the manifest's
CSV, nothing is put in front of a text that starts like a formula, because a training tool needs the words as they are. The
clips are always in a folder named for their split, `train` when there is no split, because the library also takes a split
from a file's name: a speaker called "Test" would otherwise make their clips the test split.

The Kaldi lists are what Kaldi's own data preparation describes: UTF-8, one line each, `\n` line ends, sorted by byte (as with
`LC_ALL=C`). An utterance's id is the speaker's id, a dash, and the clip's name. A speaker's id is the folder name with each
`-` made `_`, so that the utterances in that sort come out in the order of their speakers, which `utils/validate_data_dir.sh`
checks. The paths in `wav.scp` are relative to the dataset's folder, with `/`.

### A split

With a split, some clips are put aside for validation. A clip goes aside by a hash of its speaker's folder and its file name,
so the same clips go aside every time; the number is the share chosen of the clips, rounded, at least one when there are two
clips or more, and never all of them. Each row of `manifest.jsonl` then has `split`, `"train"` or `"validation"`.

| Layout | With a split |
|---|---|
| Clips with their sidecars | `train\<speaker>\` and `validation\<speaker>\` instead of `<speaker>\` |
| LJSpeech | `metadata.csv` still lists every clip; `metadata_train.csv` and `metadata_validation.csv` list each part |
| Hugging Face audio folder | `train\` and `validation\`, each with its own `metadata.csv` |
| Kaldi | `train\` and `validation\`, each a data folder with its four lists; the clips stay in `wavs\` |

## Backups

**Back up recordings...** writes a copy of the whole output folder, laid out exactly as above, and beside it one more file,
`voice-slate-backup.json`, which lists every file the backup holds and the SHA-256 it had when it was copied and checked:

```json
{
  "schema": "voice-slate-backup-v1",
  "software": { "name": "voice-slate", "version": "1.0.0" },
  "made": "2026-11-14T19:40:12+00:00",
  "files": {
    "my-voice/20261020-161204_mic_3f9a1c2e.json": "…",
    "my-voice/20261020-161204_mic_3f9a1c2e.wav": "…",
    "my-voice/masters/20261020-161204_mic_3f9a1c2e.raw.wav": "…",
    "speakers.json": "…"
  }
}
```

The names are relative to the backup's folder, with `/`. **Bring in a backup...** checks each file it copies against this
list, and refuses a backup that is missing a file the list names. The list itself is not brought into the output folder. A
file still being written when the backup ran, one ending in `.tmp`, is not in it.

## Older sidecars

Sidecars with `schema` `voice-recorder-clip-v1`, from early builds, are still read. Their `gain_db` and `denoise`
fields are treated as the `level` and `denoise` parts of a chain. Voice Slate writes the v2 format whenever such a
clip is kept again.
