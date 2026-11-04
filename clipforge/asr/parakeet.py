"""Parakeet TDT 0.6B v3 through parakeet-mlx, the default speech engine (SPEC 7.3): 25 European
languages with punctuation and word times. The engine class runs in ClipForge; ``run`` runs in the
worker, where parakeet-mlx and MLX are imported."""

from __future__ import annotations

from importlib import metadata
from importlib.util import find_spec
from pathlib import Path

from clipforge.asr.base import RawWord, WorkerEngine

MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
REVISION = "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15"
FILES = ("config.json", "model.safetensors")
CHUNK_S = 60.0  # the default 120 s chunks dropped sentences in the benchmark
OVERLAP_S = 10.0
BEAM_SIZE = 5


class ParakeetEngine(WorkerEngine):
    name = "parakeet"
    files = FILES
    size = "about 2.5 GB"
    missing_code = "parakeet_missing"
    missing_message = "ClipForge's speech engine isn't installed. Reinstall its Python packages with: uv sync"

    def __init__(self, hf_home: Path, *, model_id: str = MODEL, revision: str = REVISION) -> None:
        super().__init__(hf_home, model_id=model_id, revision=revision)

    def available(self) -> bool:
        return find_spec("parakeet_mlx") is not None


def words_from_tokens(tokens: list[tuple[str, float, float, float | None]]) -> list[RawWord]:
    """Parakeet's sub-word tokens (text, start, end, confidence) joined into words. A token starting with
    a space starts a new word, and a lone space belongs to the token after it; a word's confidence is
    its least confident token's."""
    words: list[RawWord] = []
    parts: list[tuple[str, float, float, float | None]] = []

    def finish() -> None:
        texty = [part for part in parts if part[0].strip()]
        if texty:
            confs = [part[3] for part in texty if part[3] is not None]
            words.append(
                RawWord(
                    text="".join(part[0] for part in texty).strip(),
                    start=texty[0][1],
                    end=texty[-1][2],
                    conf=min(confs) if confs else None,
                )
            )

    for token in tokens:
        if token[0].startswith(" "):
            finish()
            parts = [token]
        else:
            parts.append(token)
    finish()
    return words


def decoding(beam: bool):
    """parakeet-mlx's decoding settings: beam search with 5 beams, or greedy."""
    from parakeet_mlx import Beam, DecodingConfig, Greedy

    return DecodingConfig(decoding=Beam(beam_size=BEAM_SIZE) if beam else Greedy())


def version(model_id: str, revision: str) -> str:
    return f"parakeet-mlx {metadata.version('parakeet-mlx')}, {model_id}@{revision[:7]}"


def run(
    model_dir: Path, pieces: list[tuple[Path | None, float]], *, language: str | None, beam: bool
) -> tuple[list[list[RawWord]], str | None]:
    """Worker side: hear each piece with the model in ``model_dir``, loading it only once a piece has
    audio. Parakeet finds the language itself and doesn't say which, so ``language`` is unused and None
    comes back."""
    model = None
    results: list[list[RawWord]] = []
    for path, offset in pieces:
        if path is None:
            results.append([])
            continue
        if model is None:
            from parakeet_mlx import from_pretrained

            model = from_pretrained(str(model_dir))
        heard = model.transcribe(
            str(path), decoding_config=decoding(beam), chunk_duration=CHUNK_S, overlap_duration=OVERLAP_S
        )
        tokens = [(t.text, t.start + offset, t.end + offset, t.confidence) for t in heard.tokens]
        results.append(words_from_tokens(tokens))
    return results, None
