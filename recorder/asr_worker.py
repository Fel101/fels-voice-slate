"""Transcribe clips with faster-whisper and print one JSON object for each.

The recorder runs this file with the Python that has faster-whisper installed (the "asr_python" setting), so the
recorder itself does not need that large package. It reads a local model folder and never downloads anything:

    python -I asr_worker.py --model-dir <folder with a faster-whisper model> --audio <clip.wav> [--language en]
    python -I asr_worker.py --model-dir <folder with a faster-whisper model> --serve

With --serve it keeps running and loads the model once, for the first clip. Each line it reads is a request,
{"id": 1, "audio": "<clip.wav>", "language": "en"}, and each line it writes answers one, with the same id: the draft, or
{"id": 1, "error": "..."} when that clip could not be done. It stops at the end of its input. The window keeps one running
for its drafts, so a draft made again after a cut does not wait for the model to load again.

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
    """The text, the words with their times, and the information of one pass. `filtered` cuts out the silence first."""
    segments, info = model.transcribe(audio, language=language, beam_size=5, condition_on_previous_text=False,
                                      word_timestamps=True, vad_filter=filtered,
                                      vad_parameters=VOICE_ACTIVITY if filtered else None)
    segments = list(segments)
    words = [[word.word.strip(), round(float(word.start), 3), round(float(word.end), 3)]
             for segment in segments for word in getattr(segment, 'words', None) or ()]
    return ' '.join(segment.text.strip() for segment in segments).strip(), words, info


def language_code(text):
    """A language code such as en or pt, or None to let the model detect the language."""
    text = (text or '').strip().lower()
    if text in ('', 'auto'):
        return None
    if not re.fullmatch(r'[a-z]{2,3}', text):
        raise ValueError(f'The language must be a code such as en or es, not {text!r}')
    return text


def load(model_dir):
    from faster_whisper import WhisperModel

    return WhisperModel(model_dir, device='cpu', compute_type='int8', local_files_only=True)


def answer(model, audio, language):
    """The draft of one clip, ready to print."""
    try:
        text, words, info = transcribe(model, audio, language, filtered=True)
        filtered = True
    except Exception as e:                  # the voice-activity model (onnxruntime) is missing or broken
        sys.stderr.write(f'The silence filter is unavailable ({e}); transcribing without it.\n')
        text, words, info = transcribe(model, audio, language, filtered=False)
        filtered = False
    return {
        'text': text,
        'words': words,
        'language': info.language,
        'language_probability': round(float(info.language_probability), 3),
        'duration_seconds': round(float(info.duration), 3),
        'silence_filter': filtered,
    }


def serve(model_dir, requests=None, replies=None):
    """Answer each request line of `requests` (stdin) with one line on `replies` (stdout), until the input ends.

    A clip that fails is answered with its error, and the next one is still done: a model that would not load is tried
    again for the next clip.
    """
    requests = sys.stdin if requests is None else requests
    replies = sys.stdout if replies is None else replies
    model = None
    for line in requests:
        if not line.strip():
            continue
        reply = {}
        try:
            request = json.loads(line)
            reply['id'] = request.get('id')
            language = language_code(request.get('language'))
            if model is None:
                model = load(model_dir)
            reply.update(answer(model, request['audio'], language))
        except Exception as e:
            reply['error'] = f'{type(e).__name__}: {e}'
        # Plain ASCII, with every other letter as an escape: a pipe on Windows uses the code page, which most languages do not fit.
        replies.write(json.dumps(reply) + '\n')
        replies.flush()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-dir', required=True, help='local faster-whisper model folder (nothing is downloaded)')
    parser.add_argument('--audio', help='the clip to transcribe')
    parser.add_argument('--language', default='', help='language code such as en; empty detects it')
    parser.add_argument('--serve', action='store_true', help='keep running: read requests as JSON lines and answer each')
    args = parser.parse_args(argv)
    if not args.serve and not args.audio:
        parser.error('give --audio, or --serve')
    if not os.path.isdir(args.model_dir):
        sys.stderr.write(f'The speech model folder does not exist: {args.model_dir}\n')
        return 2
    if args.serve:
        return serve(args.model_dir)
    try:
        language = language_code(args.language)
    except ValueError as e:
        sys.stderr.write(f'{e}\n')
        return 2
    reply = answer(load(args.model_dir), args.audio, language)
    # Plain ASCII, with every other letter as an escape: a pipe on Windows uses the code page, which most languages do not fit.
    json.dump(reply, sys.stdout)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
