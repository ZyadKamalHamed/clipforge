"""Whisper large-v3-turbo through mlx-whisper, the optional engine for languages Parakeet doesn't know
(SPEC 7.3), installed with: uv sync --extra whisper. The engine class runs in ClipForge; ``run`` runs
in the worker."""

from __future__ import annotations

from importlib import metadata
from importlib.util import find_spec
from pathlib import Path

from clipforge.asr.base import RawWord, WorkerEngine

MODEL = "mlx-community/whisper-large-v3-turbo"
REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
FILES = ("config.json", "weights.safetensors")


class WhisperEngine(WorkerEngine):
    name = "whisper"
    files = FILES
    size = "about 1.6 GB"
    missing_code = "whisper_missing"
    missing_message = "The Whisper add-on isn't installed. Install it with: uv sync --extra whisper"

    def __init__(self, hf_home: Path, *, model_id: str = MODEL, revision: str = REVISION) -> None:
        super().__init__(hf_home, model_id=model_id, revision=revision)

    def available(self) -> bool:
        return find_spec("mlx_whisper") is not None


def version(model_id: str, revision: str) -> str:
    try:
        installed = metadata.version("mlx-whisper")
    except metadata.PackageNotFoundError:
        installed = "unknown"
    return f"mlx-whisper {installed}, {model_id}@{revision[:7]}"


def run(
    model_dir: Path, pieces: list[tuple[Path | None, float]], *, language: str | None, beam: bool
) -> tuple[list[list[RawWord]], str | None]:
    """Worker side: hear each piece with the model in ``model_dir``. mlx-whisper has no beam search, so
    ``beam`` changes nothing; the language comes back as Whisper detected it when none was given."""
    import mlx_whisper

    results: list[list[RawWord]] = []
    found = None
    for path, offset in pieces:
        if path is None:
            results.append([])
            continue
        heard = mlx_whisper.transcribe(
            str(path),
            path_or_hf_repo=str(model_dir),
            word_timestamps=True,
            condition_on_previous_text=False,
            language=language,
        )
        found = found or heard.get("language")
        words = []
        for segment in heard.get("segments", []):
            for word in segment.get("words", []):
                text = word["word"].strip()
                if text:
                    start, end = word["start"] + offset, word["end"] + offset
                    words.append(RawWord(text=text, start=start, end=end, conf=word.get("probability")))
        results.append(words)
    return results, found
