"""The speech to text stage (SPEC 7.3): the engine for the video's language, a full pass with the gaps heard
again and filled from YouTube's captions, and the transcript as sentences. When no engine knows the
language, YouTube's captions in it are the transcript. A video with no speech in it is refused."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clipforge.asr.base import base_language, choose_engine
from clipforge.asr.coverage import transcribe_with_guard
from clipforge.asr.segment import build_transcript
from clipforge.asr.youtube_captions import transcript_from_captions
from clipforge.errors import SourceRefused
from clipforge.jobs.stages import AS_IS_POINTER, missing, read_captions, read_meta, write_text
from clipforge.media.audio import wav_seconds

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.jobs.runner import JobContext

NO_SPEECH = f"ClipForge heard no speech in this video, so there is nothing to clip. {AS_IS_POINTER}"


def transcribe(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """transcript.json, and the engine's warning, if any, as the project's message. The language is the
    one set in Settings, else the one YouTube gave, else the caption track's, else the one the engine heard.
    Captions in another language than the one set are left unread."""
    pid = ctx.job.ref_id
    pp = engine.paths.project(pid)
    project = engine.project(pid)
    meta = read_meta(pp)
    words, _events, track = read_captions(pp)
    wanted = None if ctx.settings.asr_language == "auto" else ctx.settings.asr_language
    if wanted and base_language(track) not in (None, wanted):
        words = []
    language = wanted or meta.language or base_language(track)
    available = {name: speech.available() for name, speech in engine.asr_engines.items()}
    name, warning = choose_engine(language, ctx.settings.asr_engine, available, has_captions=bool(words))
    updates: dict[str, Any] = {"message": warning}
    if language and (project.language is None or wanted):
        updates["language"] = language
    engine.update_project(pid, **updates)
    result = {"engine": name, "warning": warning, "language": language}
    if pp.transcript_json.is_file():
        return result | {"skipped": True}
    duration = wav_seconds(pp.audio16k) if pp.audio16k.is_file() else meta.duration_s
    if name == "captions":
        heard, ranges = transcript_from_captions(words, track or ""), []
    else:
        if not pp.audio16k.is_file():
            raise missing(pp.audio16k)
        with ctx.beating():  # the speech worker says nothing until it is done, within its own time limit
            heard, ranges = transcribe_with_guard(
                engine.asr_engines[name],
                pp.audio16k,
                language=language,
                captions=words or None,
                duration=duration,
            )
    tr = build_transcript(heard, duration, ranges)
    if not tr.sentences:  # refused before the transcript is saved, so trying again listens again
        raise SourceRefused("no_speech", NO_SPEECH)
    write_text(pp.transcript_json, tr.model_dump_json())
    if "language" not in updates and project.language is None and tr.language:
        engine.update_project(pid, language=tr.language)  # the engine heard which one it is
    return result | {"engine": tr.engine, "language": tr.language, "sentences": len(tr.sentences)}
