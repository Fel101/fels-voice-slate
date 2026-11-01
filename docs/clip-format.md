# Clip format

What Voice Slate writes to disk, for people who want to use the clips in other programs. Everything is a plain WAV or
JSON file, so no special reader is needed.

## Folder layout

```text
voice-clips\                                    the output folder: beside the program by default (Settings > Output folder)
  speakers.json                                 the list of speakers
  my-voice\                                     one folder per speaker
    masters\
      20261020-161204_mic_3f9a1c2e.raw.wav      the original recording
      20261020-161204_mic_3f9a1c2e.autosave.json  work in progress on a take that is not kept yet (temporary)
    20261020-161204_mic_3f9a1c2e.wav            the kept clip
    20261020-161204_mic_3f9a1c2e.json           the sidecar for the kept clip
```

A clip's three files share a name: `YYYYMMDD-HHMMSS_<source>_<id>`, the local date and time the take was made, the source
(`mic`, `system` or `import`), and a random 8-character id. A name that is already taken gets `-2`, `-3` and so on, so
nothing is overwritten.

| File | What it is |
|---|---|
| `masters\*.raw.wav` | The original recording, written the moment recording stops (or a file is imported). **24-bit PCM at the device's own sample rate**, one or two channels. It is never modified. A master with no sidecar next to it is a take that has not been kept yet. |
| `*.wav` | The **kept clip**: edited, processed and resampled to **24 kHz, mono, 16-bit PCM**. This is the file to hand to other tools. |
| `*.json` | The **sidecar**, described below. |
| `masters\*.autosave.json` | **Temporary, and not part of the format.** The cuts, processing settings and text of a take that Keep has not written yet, saved so that a crash or a close loses nothing. That includes a kept clip changed since, whose WAV and sidecar stay as they were until Keep. Voice Slate deletes it when the clip is kept, and it never holds the permission tick. It sits in `masters`, not beside the sidecars, and its `schema` is not `voice-recorder-clip-*`, so a program that reads the clips as described [below](#reading-the-clips-from-another-program) never meets it. |
| `masters\*.spool` | **Temporary, and not part of the format.** A take while it is being recorded: one line of JSON, then the samples as 32-bit floats. It is gone once the original is written. One that is still there when the program starts is a take that was cut short, and becomes a take. |
| `speakers.json` | `{ "my-voice": { "name": "My voice", "origin": "...", "allowed_use": "..." } }`. The origin and allowed use remembered for each speaker. The key is the folder name. |

## The sidecar

A UTF-8 JSON object. An example, written compactly (the program writes it with one value per line, in this order):

```json
{
  "schema": "voice-recorder-clip-v2",
  "software": { "name": "voice-slate", "version": "0.4.0" },
  "id": "3f9a1c2e",
  "created": "2026-10-20T16:12:04+01:00",
  "speaker": "my-voice",
  "source": "mic",
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
| `schema` | `voice-recorder-clip-v2`. Changes if a field is removed or changes meaning. Readers should ignore fields they do not know. The name comes from the program's first name and stays the same, so that programs that already read these files keep working. |
| `software` | The program and version that wrote the file. |
| `id`, `created` | The take's id, and when it was made (ISO 8601 with the local UTC offset). |
| `speaker` | The speaker's folder name. |
| `source` | `mic`, `system` (what the computer played), or `import` (a file brought in). |
| `device` | What captured the audio: the device name, the Windows audio interface, the native sample rate and channel count. For an imported file, only its file name. |
| `export` | The kept WAV: its file name (in the same folder), format, duration, and the **SHA-256** of its bytes, so you can check a file has not changed since it was kept. |
| `master` | The original recording: its path relative to the sidecar's folder, and its format. |
| `measurements` | Measured on the kept clip: `peak_dbfs`, `rms_dbfs` (whole clip), `clipped_fraction` (share of samples within 0.5% of full scale), and `silence_fraction` (share of 20 ms frames quieter than -45 dB). `recording_clipped_fraction` is the same share measured on the recording, on its loudest channel and leaving out what was cut or silenced: gain and the limiter can bring `clipped_fraction` to 0 without repairing anything, so this is the number to trust. Clips kept by earlier versions do not have it. |
| `transcript` | What is said in the clip, 3 to 600 characters. |
| `transcript_status` | How the transcript came about: `typed`, `read_from_script` (a script line, unchanged), `asr_draft` (the optional speech recognizer's draft, unchanged), or `edited` (a script line or draft that was then changed). A draft is a guess, so `asr_draft` means nobody has corrected it. |
| `origin`, `allowed_use` | The person's own words: where the recording came from, and what the voice may be used for. |
| `consent_confirmed` | Always `true` in a kept clip. Voice Slate will not write a sidecar otherwise. It is the person's statement that they have the right to use the voice, **not** a verified or legal consent record. Changing the `origin` or `allowed_use` of a kept clip needs the statement made again. |
| `consent_confirmed_at` | When the person made that statement (ISO 8601 with the local UTC offset). Clips kept by earlier versions do not have it. |
| `source_url` | An optional link to the original, or `null`. |
| `master_seconds` | The length of the original recording. |
| `edits` | The edit list, below. |
| `join_fade_ms` | The crossfade at each cut, in milliseconds. |
| `chain` | The processing settings, below. |
| `needs_transcript_review` | Always `false` in a kept clip: Keep requires the transcript to be confirmed after cuts. |
| `notes` | Free text. |
| `flags` | Facts about the take, worked out again at each Keep: `edited`, `filtered` (high-pass or hum notch on), `cleaned` (noise reduction on), `level_changed`, `limited` (limiter on), `clipped` (a sample of the kept clip, or of the recording it was made from, reaches full scale), `interrupted` (the device stopped while recording, or the program did), `dropouts` (the device reported losing audio while recording). |
| `label` | Optional. A name the person gave the take to find it by: one line, at most 80 characters. The field is absent when the take has no label. It can change after the clip was kept (renaming a kept take rewrites only this field of the sidecar), and the WAV never changes with it, so the SHA-256 in `export` still holds. |
| `moves` | Optional. Present only on a clip that was moved to another speaker: a list, oldest first, of `{ "from": "<speaker folder>", "to": "<speaker folder>", "at": "<ISO 8601 time>" }`. `speaker` always names where the clip is now. The WAV is the same bytes, so the SHA-256 in `export` still holds. |

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
limiter); and last the clip is resampled to 24 kHz, with the limiter, if it is on, applied once more at that rate so
that resampling cannot push a peak above its ceiling.

| Section | Settings |
|---|---|
| `highpass` | `enabled`, `hz` (20 to 300). A 4th-order Butterworth filter, run forward and backward so it adds no delay. |
| `hum` | `enabled`, `hz` (30 to 120, normally 50 or 60), `harmonics` (1 to 6). Narrow notches at the mains frequency and its harmonics. |
| `denoise` | `enabled`, `nf` (-80 to -20 dB). FFmpeg's `afftdn` filter at that noise floor. When enabled the sidecar also holds `filter` (the exact filter text) and `identity_checked: false`, since nobody checks that the cleaned voice still matches the original. |
| `level` | `gain_db` (-24 to 36). A gain for the whole clip. |
| `limiter` | `enabled`, `ceiling_db` (-12 to 0). A look-ahead peak limiter. |

Everything needed to rebuild the kept clip from the master is in the sidecar, but you do not need it to use the clip:
the `.wav` file is complete.

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
| `sample_rate` | Of the WAV: 24000. |
| `source`, `created`, `id` | As in the sidecar: `mic`, `system` or `import`; when the take was made; the take's id. |
| `origin`, `allowed_use`, `transcript_status` | As in the sidecar. |
| `sha256` | The SHA-256 of the WAV, checked against the sidecar's when the manifest was written. |

`audio_filepath`, `text` and `duration` are the names that speech toolkits such as NeMo read. A clip is only listed when
its sidecar can be read and records the permission, and its WAV is next to it, readable, and still the file that was
kept; any other clip is left out and named to the person who exports. The file is UTF-8 with no byte-order mark, so the
first column name is exactly `audio_filepath`.

- **JSON Lines**: one object per line, the keys in the order above. The characters U+2028, U+2029 and U+0085, which some
  programs treat as a line end although JSON does not, are written as `\u` escapes, so one clip is always one line.
- **CSV**: standard quoting, `\r\n` line ends. A text that a spreadsheet could run as a formula, one that starts with
  `=`, `+`, `-`, `@`, a tab or a carriage return, has a `'` put in front of it, in every column except `audio_filepath`
  and `sha256`; the JSON Lines file never changes a text. Excel opens a CSV that has no byte-order mark as if it were in
  the local code page, so accented letters can come out wrong when you double-click the file; use *Data > From Text/CSV*
  and choose UTF-8.

## Older sidecars

Sidecars with `schema` `voice-recorder-clip-v1`, from early builds, are still read. Their `gain_db` and `denoise`
fields are treated as the `level` and `denoise` parts of a chain. Voice Slate writes the v2 format whenever such a
clip is kept again.
