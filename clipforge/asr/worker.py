"""The speech to text worker (SPEC 7.3): a separate Python process that loads one speech model, hears
the audio it is given and writes the words it heard as JSON. ClipForge runs it through
clipforge.asr.base, which reads the result and turns the exit code into a plain message.

Run as: python -I -m clipforge.asr.worker --engine parakeet --model-id ID --revision REV --file NAME ...
then either --prepare (fetch the model only) or --wav WAV --out JSON [--language CODE] [--beam]
[--start S --end E ...]."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path

from clipforge.asr.base import EXIT_MODEL
from clipforge.media.audio import SAMPLE_RATE, read_wav16k, wav_seconds, write_wav16k

ENGINES = {"parakeet": "clipforge.asr.parakeet", "whisper": "clipforge.asr.whisper_mlx"}
MIN_SECONDS = 0.1  # audio shorter than this holds no word


class ModelUnavailable(Exception):
    """The model is neither in the cache nor downloadable right now."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -I -m clipforge.asr.worker")
    parser.add_argument("--engine", required=True, choices=sorted(ENGINES))
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--file", action="append", default=[], dest="files")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--language")
    parser.add_argument("--beam", action="store_true")
    parser.add_argument("--start", type=float, action="append", default=[], dest="starts")
    parser.add_argument("--end", type=float, action="append", default=[], dest="ends")
    args = parser.parse_args(argv)
    if len(args.starts) != len(args.ends):
        parser.error("every --start needs an --end")
    if not args.prepare and (args.wav is None or args.out is None):
        parser.error("--wav and --out are needed unless --prepare is given")
    return args


def _missing(folder: Path, files: list[str]) -> list[str]:
    return [name for name in files if not (folder / name).is_file()]


def fetch_model(model_id: str, revision: str, files: list[str]) -> Path:
    """The model's snapshot folder, from the cache when the files are all there, else downloaded into it."""
    from huggingface_hub import snapshot_download

    patterns = list(files)
    try:
        folder = Path(
            snapshot_download(model_id, revision=revision, allow_patterns=patterns, local_files_only=True)
        )
        if not _missing(folder, files):
            return folder
    except Exception:  # not cached, or cached in part: the download below fetches what is missing
        pass
    try:
        folder = Path(snapshot_download(model_id, revision=revision, allow_patterns=patterns, token=False))
    except Exception as err:
        raise ModelUnavailable(f"{model_id}@{revision} can't be downloaded: {err!r}") from err
    if missing := _missing(folder, files):
        raise ModelUnavailable(f"{model_id}@{revision} downloaded without {missing}")
    return folder


def pieces(wav: Path, windows: list[tuple[float, float]], folder: Path) -> list[tuple[Path | None, float]]:
    """What the model hears, each with the time in the whole file where it starts: the file itself when
    there are no windows, else each window cut into its own file in ``folder``. Audio too short to hold
    a word is None, and the model never sees it."""
    if not windows:
        return [(wav if wav_seconds(wav) >= MIN_SECONDS else None, 0.0)]
    out: list[tuple[Path | None, float]] = []
    for k, (start, end) in enumerate(windows):
        start = max(0.0, start)
        samples = read_wav16k(wav, start=start, end=end)
        if len(samples) < MIN_SECONDS * SAMPLE_RATE:
            out.append((None, start))
        else:
            out.append((write_wav16k(folder / f"window-{k}.wav", samples), start))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        model_dir = fetch_model(args.model_id, args.revision, args.files)
    except ModelUnavailable as err:
        print(f"model unavailable: {err}", file=sys.stderr)
        return EXIT_MODEL
    if args.prepare:
        return 0
    engine = importlib.import_module(ENGINES[args.engine])
    windows = list(zip(args.starts, args.ends, strict=True))
    with tempfile.TemporaryDirectory(prefix="pieces-", dir=args.out.parent) as tmp:
        heard, language = engine.run(
            model_dir, pieces(args.wav, windows, Path(tmp)), language=args.language, beam=args.beam
        )
    data = {
        "engine_version": engine.version(args.model_id, args.revision),
        "language": language,
        "results": [[word.model_dump() for word in words] for words in heard],
    }
    args.out.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
