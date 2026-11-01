"""Transcribe one clip with faster-whisper and print one JSON object.

The recorder runs this file with the Python that has faster-whisper installed (the "asr_python" setting), so the
recorder itself does not need that large package. It reads a local model folder and never downloads anything:

    python -I asr_worker.py --model-dir <folder with a faster-whisper model> --audio <clip.wav> [--language en]

The text is a draft. A person must check every word before it becomes a transcript.
"""
import argparse
import json
import os
import re
import sys

# Silence is cut out before the model hears the clip. Without this a small model invents words over the quiet at the
# end of a clip (or repeats "And... And..."), and can lose the first words after a pause.
VOICE_ACTIVITY = {'min_silence_duration_ms': 500, 'speech_pad_ms': 250}


def transcribe(model, audio, language, filtered):
    """The text and the information of one pass. `filtered` cuts out the silence first."""
    segments, info = model.transcribe(audio, language=language, beam_size=5, condition_on_previous_text=False,
                                      vad_filter=filtered, vad_parameters=VOICE_ACTIVITY if filtered else None)
    return ' '.join(segment.text.strip() for segment in segments).strip(), info


def language_code(text):
    """A language code such as en or pt, or None to let the model detect the language."""
    text = (text or '').strip().lower()
    if text in ('', 'auto'):
        return None
    if not re.fullmatch(r'[a-z]{2,3}', text):
        raise ValueError(f'The language must be a code such as en or es, not {text!r}')
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-dir', required=True, help='local faster-whisper model folder (nothing is downloaded)')
    parser.add_argument('--audio', required=True, help='the clip to transcribe')
    parser.add_argument('--language', default='', help='language code such as en; empty detects it')
    args = parser.parse_args(argv)
    if not os.path.isdir(args.model_dir):
        sys.stderr.write(f'The speech model folder does not exist: {args.model_dir}\n')
        return 2
    try:
        language = language_code(args.language)
    except ValueError as e:
        sys.stderr.write(f'{e}\n')
        return 2

    from faster_whisper import WhisperModel

    model = WhisperModel(args.model_dir, device='cpu', compute_type='int8', local_files_only=True)
    try:
        text, info = transcribe(model, args.audio, language, filtered=True)
        filtered = True
    except Exception as e:                  # the voice-activity model (onnxruntime) is missing or broken
        sys.stderr.write(f'The silence filter is unavailable ({e}); transcribing without it.\n')
        text, info = transcribe(model, args.audio, language, filtered=False)
        filtered = False
    # Plain ASCII, with every other letter as an escape: a pipe on Windows uses the code page, which most languages do not fit.
    json.dump({
        'text': text,
        'language': info.language,
        'language_probability': round(float(info.language_probability), 3),
        'duration_seconds': round(float(info.duration), 3),
        'silence_filter': filtered,
    }, sys.stdout)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
