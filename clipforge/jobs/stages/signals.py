"""The signals stage (SPEC 7.4): the evidence about every sentence before any LLM call, from the audio's
loudness, YouTube's replay heatmap, chapters, caption sound events, comments and live chat, with the tags
that evidence gives written into the transcript."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from clipforge.brain.signals import apply_tags, compute_signals
from clipforge.ingest.youtube_signals import chat_bursts, comment_hotspots, parse_live_chat
from clipforge.jobs.stages import (
    missing,
    read_captions,
    read_comments,
    read_meta,
    read_transcript,
    write_text,
)
from clipforge.media.audio import read_wav16k, rms_per_second, wav_seconds

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.jobs.runner import JobContext
    from clipforge.paths import ProjectPaths

log = logging.getLogger(__name__)

BLOCK_S = 600.0  # the audio is read 10 minutes at a time, so a six hour source stays small in memory


def compute(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """signals.json, and transcript.json again with each sentence's tags and chapter."""
    pp = engine.paths.project(ctx.job.ref_id)
    if pp.signals_json.is_file():
        return {"skipped": True}
    meta, tr = read_meta(pp), read_transcript(pp)
    if not pp.audio16k.is_file():
        raise missing(pp.audio16k)
    _words, events, _track = read_captions(pp)
    sig = compute_signals(
        tr,
        audio_rms(pp.audio16k),
        heatmap=meta.heatmap,
        events=events,
        hotspots=comment_hotspots(read_comments(pp), tr.duration),
        chapters=meta.chapters,
        chat_bursts=_chat_bursts(pp, tr.duration),
    )
    write_text(pp.transcript_json, apply_tags(tr, sig, meta.chapters).model_dump_json())
    write_text(pp.signals_json, sig.model_dump_json())  # last: its being there means the stage is done
    return {
        "heatmap": sig.has_heatmap,
        "comments": sig.has_comments,
        "chat": sig.has_chat,
        "events": sig.has_events,
    }


def audio_rms(wav: Path) -> np.ndarray:
    """The loudness of every second of ``wav``."""
    total = wav_seconds(wav)
    parts, start = [], 0.0
    while start < total:
        end = min(start + BLOCK_S, total)
        parts.append(rms_per_second(read_wav16k(wav, start=start, end=end)))
        start = end
    return np.concatenate(parts) if parts else np.zeros(0)


def _chat_bursts(pp: ProjectPaths, duration_s: float) -> list[tuple[float, float]]:
    if not pp.live_chat_json.is_file():
        return []
    try:
        return chat_bursts(parse_live_chat(pp.live_chat_json), duration_s)
    except Exception as err:  # untrusted data: a chat file that can't be read is no chat
        log.warning("the live chat can't be read (%s), so it is left out", err)
        return []
