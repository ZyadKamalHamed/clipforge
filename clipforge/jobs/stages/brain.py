"""The brain stages (SPEC 9.2): brain.select finds and ranks the moments and saves them as clip ideas, and
brain.package writes the titles and captions of the best of them and has them reviewed. Both work from
scratch each time; the LLM's answers come from the cache when the question hasn't changed."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from clipforge.brain.context import BrainContext
from clipforge.brain.llm.cache import LLMCache
from clipforge.brain.run import rank_key, run_packaging, run_selection
from clipforge.campaigns.rules import campaign_of, lengths_for
from clipforge.db import transaction
from clipforge.errors import LLM_MESSAGES, LLMError
from clipforge.jobs.stages import (
    AS_IS_POINTER,
    clips,
    job_result,
    missing,
    read_meta,
    read_signals,
    read_transcript,
    write_json,
)
from clipforge.models import LENGTH_RANGES, Candidate, ClipRow, Transcript
from clipforge.repo import get_clip, new_id, replace_ideas, update_clip

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.config import Settings
    from clipforge.jobs.runner import JobContext
    from clipforge.models import Project
    from clipforge.paths import ProjectPaths

PACKAGE_MIN = 5
PACKAGE_EXTRA = 2  # packaged past the clips that render, so a clip swapped in has its titles ready
RENDER_COUNTS = {"top5": 5, "top10": 10, "none": 0}  # "all" renders every clip that passes the gates
FOCUS_MISSED = "No strong moment was found within 3 minutes of the timestamp in the link."
LITTLE_TALK_S = 60  # under a minute of talking, the likely reason no clip was found (SPEC 14.2)
RANGE = ("start_s", "end_s", "sentence_ids")


def select(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """candidates.json and the project's clip ideas, best first, with the mode they were found in and a
    plain message when Basic mode had to step in."""
    pid = ctx.job.ref_id
    pp = engine.paths.project(pid)
    base = job_result(ctx.conn, pid, "asr.transcribe").get("warning")
    engine.update_project(pid, message=base)
    bctx, provider_error = _context(engine, ctx, pp)
    cands = run_selection(bctx)
    write_json(pp.candidates_json, [c.model_dump(mode="json") for c in cands])
    rows = [
        ClipRow(id=new_id("c"), project_id=pid, rank=rank, status="idea", **_clip_fields(c, bctx.transcript))
        for rank, c in enumerate(cands, 1)
    ]
    clips.forget(engine, pid, replace_ideas(ctx.conn, pid, rows))  # clips with edits are kept
    mode = "basic" if bctx.provider is None or (cands and all(c.mode == "basic" for c in cands)) else "llm"
    parts = [
        base,
        provider_error and f"{provider_error} These clips come from Basic mode.",
        _basic_note(bctx, cands),
    ]
    if "focus_missed" in bctx.notes:
        parts.append(FOCUS_MISSED)
    if not cands:
        parts.append(_no_clips(bctx.transcript))
    message = " ".join(p for p in parts if p) or None
    engine.update_project(pid, mode=mode, message=message)
    return {
        "clips": [row.id for row in rows],
        "count": len(rows),
        "mode": mode,
        "message": message,
        "notes": bctx.notes,
        "llm_disabled": bctx.llm_disabled,
        "limit_message": bctx.limit_message,
    }


def package(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The titles, captions and review of the clips that will render, and two more, on their clip rows,
    ranked again since the review may have found reasons not to post some of them."""
    pid = ctx.job.ref_id
    pp = engine.paths.project(pid)
    picked = job_result(ctx.conn, pid, "brain.select")
    message = picked.get("message")
    engine.update_project(pid, message=message)
    bctx, _ = _context(
        engine,
        ctx,
        pp,
        llm_disabled=bool(picked.get("llm_disabled")),
        limit_message=picked.get("limit_message"),
    )
    if not pp.candidates_json.is_file():
        raise missing(pp.candidates_json)
    cands = [Candidate.model_validate(c) for c in json.loads(pp.candidates_json.read_text(encoding="utf-8"))]
    ids = picked.get("clips")
    if not isinstance(ids, list) or len(ids) != len(cands):
        raise missing(pp.candidates_json)  # the selection that wrote it never finished
    k = package_count(ctx.settings.auto_render, cands)
    packaged = list(zip(ids[:k], run_packaging(bctx, cands[:k]), strict=True))
    packaged.sort(key=lambda pair: rank_key(pair[1]), reverse=True)
    with transaction(ctx.conn):
        for rank, (cid, c) in enumerate(packaged, 1):
            clip = get_clip(ctx.conn, cid)
            if clip is None:
                continue  # deleted while its titles were written
            p = c.packaging
            fields = _clip_fields(c, bctx.transcript)
            if p:
                fields["hook_titles"] = [p.hook_title, *p.hook_alts]
            idea = clip.status in ("idea", "packaged")
            if clip.edits or not idea:  # rendered or moved meanwhile: its range stays where you left it
                fields = {k: v for k, v in fields.items() if k not in RANGE}
            if idea:
                fields["status"] = "packaged"
            update_clip(
                ctx.conn,
                cid,
                rank=rank,
                packaging=p.model_dump(mode="json") if p else None,
                review=c.review.model_dump(mode="json") if c.review else None,
                **fields,
            )
    if bctx.llm_disabled and not picked.get("llm_disabled"):
        note = f"{bctx.limit_message} Basic mode wrote the rest of the titles and captions."
        engine.update_project(pid, message=" ".join(p for p in (message, note) if p))
    return {"packaged": len(packaged), "notes": bctx.notes}


def package_count(auto_render: str, cands: list[Candidate]) -> int:
    """How many of the best candidates to package: the clips that will render (5, 10 or every one that
    passes the gates, or none), plus 2, at least 5, at most all of them."""
    renders = RENDER_COUNTS.get(auto_render)
    if renders is None:
        renders = sum(1 for c in cands if c.passes_gates)
    return min(len(cands), max(PACKAGE_MIN, renders + PACKAGE_EXTRA))


def settings_for(project: Project, settings: Settings) -> Settings:
    """Settings with the project's own answer to a usage limit and its Economy choice, which a clipping
    channel's projects carry (SPEC 10.9), over the ones in Settings."""
    changes: dict[str, Any] = {}
    if project.options.on_limit is not None:
        changes["on_llm_limit"] = project.options.on_limit
    if project.options.economy is not None:
        changes["llm_economy"] = project.options.economy
    return settings.model_copy(update=changes) if changes else settings


def _context(
    engine: Engine,
    ctx: JobContext,
    pp: ProjectPaths,
    *,
    llm_disabled: bool = False,
    limit_message: str | None = None,
) -> tuple[BrainContext, str | None]:
    """The brain's view of the project, and why there is no LLM when one was set but can't be used (a
    missing API key, say), in which case the run is in Basic mode."""
    project = engine.project(ctx.job.ref_id)
    settings = settings_for(project, ctx.settings)
    name = project.options.provider or settings.llm_provider
    try:
        provider, provider_error = engine.provider_factory(settings, name), None
    except LLMError as err:
        provider, provider_error = None, err.message
    bctx = BrainContext(
        project_id=project.id,
        meta=read_meta(pp),
        transcript=read_transcript(pp),
        signals=read_signals(pp),
        options=project.options,
        settings=settings,
        provider=provider,
        provider_name=name if provider is not None else "heuristic",
        cache=LLMCache(pp.llm_dir / "cache"),
        log_dir=pp.llm_dir,
        rights_basis=project.rights_basis,
        focus_s=project.focus_s,
        progress=ctx.progress,
        llm_disabled=llm_disabled,
        limit_message=limit_message,
        lengths=lengths_for(campaign_of(engine, project), LENGTH_RANGES[project.options.length]),
    )
    return bctx, provider_error


def _no_clips(tr: Transcript) -> str:
    """Why no clip was found, and how to post the video whole instead."""
    talk = sum(s.end - s.start for s in tr.sentences)
    if talk < LITTLE_TALK_S:
        heard = f"ClipForge heard only {round(talk)} seconds of talking, and it finds clips in what's said."
    else:
        heard = "ClipForge found no moment worth clipping in what's said."
    return f"{heard} {AS_IS_POINTER}"


def _basic_note(ctx: BrainContext, cands: list[Candidate]) -> str | None:
    """Why some or all of the clips come from Basic mode though an LLM was set; None when none do."""
    basic = sum(1 for c in cands if c.mode == "basic")
    if ctx.provider is None or not basic:
        return None
    why = ctx.limit_message or next((LLM_MESSAGES[n] for n in ctx.notes if n in LLM_MESSAGES), None)
    if basic == len(cands):
        return f"{why} These clips come from Basic mode." if why else "These clips come from Basic mode."
    return f"{why} Basic mode did the rest." if why else "Some of these clips come from Basic mode."


def _clip_fields(c: Candidate, tr: Transcript) -> dict[str, Any]:
    """A candidate as the fields of its clip row."""
    i0, i1 = tr.index(c.start_sid), tr.index(c.end_sid)
    return {
        "start_s": c.start_s,
        "end_s": c.end_s,
        "sentence_ids": [s.id for s in tr.sentences[i0 : i1 + 1]],
        "scores": c.scores,
        "llm_score": c.llm_score,
        "rerank_score": c.rerank_score,
        "signal_score": c.signal_score,
        "final_score": c.final_score,
        "tier": c.tier,
        "passes_gates": c.passes_gates,
        "archetype": c.archetype,
        "reason": c.reason,
        "hook_titles": c.hook_titles,
        "topic_tags": c.topic_tags,
        "evidence": [e.model_dump() for e in c.evidence],
        "flags": {"red": c.red_flags, "soft": c.soft_flags, "notes": c.notes},
        "mode": c.mode,
    }
