"""The re-check of an edited clip (SPEC 9.2 step 5 and 14.4), job ``clip.review`` on the llm lane beside the
clip's render, its ref the clip id. The review reads the clip as the user left it: its range, without the
words struck from it and with its fillers only when its style keeps them, under the title, cards and
captions it goes out with. Its red flags take the place of the earlier review's, and the user's text and
edits never change. A check that goes unanswered leaves the note ``review_skipped``, which the card shows
as "Not re-checked after your edits."."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from clipforge import repo
from clipforge.brain import review, score
from clipforge.brain.context import BrainContext
from clipforge.brain.snap import length_flags
from clipforge.edits import apply_packaging_edits, effective_edits, review_key
from clipforge.errors import LLMError
from clipforge.jobs.runner import JobContext
from clipforge.jobs.stages import read_meta, read_transcript
from clipforge.jobs.stages.brain import settings_for
from clipforge.models import Candidate, ClipRow, Packaging, Project, Signals
from clipforge.render.styles import style_for

if TYPE_CHECKING:
    from clipforge.app import Engine

log = logging.getLogger(__name__)

SKIPPED = "review_skipped"
CARRY_ON = "ClipForge will check the edited clip again at {when}."
NO_SIGNALS = Signals(  # the review reads none
    has_heatmap=False,
    has_comments=False,
    has_chat=False,
    has_events=False,
    rms=[],
    heat_p80=None,
    max_bucket_weight=0.0,
    sentences={},
)
LENGTH_RED = ("too_short", "too_long")  # worked out again from where the clip is now


def clip_review(engine: Engine, ctx: JobContext) -> dict[str, Any] | None:
    """Checks the clip again as it is now and keeps the result with the ``key`` of what it read; with
    ``stale`` when the clip changed meanwhile (on_finished queues another check), ``skipped`` when it
    went unchecked. None for a clip gone, never packaged or set aside meanwhile."""
    clip = repo.get_clip(engine.db(), ctx.job.ref_id)
    project = engine.project(clip.project_id) if clip else None
    if clip is None or project is None or not clip.packaging:
        return None
    edits = clip.edits or {}
    key = review_key(clip, edits)
    drop_fillers = style_for(ctx.settings, effective_edits(project.options, edits)).filler_removal
    struck = frozenset(edits.get("struck") or ())
    checked = review.recheck(
        _context(engine, ctx, project), _candidate(clip, edits), drop_fillers=drop_fillers, struck=struck
    )
    with engine.lock:  # as saving edits and discarding the clip do
        now = repo.get_clip(engine.db(), clip.id)
        if ctx.cancelled() or now is None:
            return None
        if review_key(now, now.edits or {}) != key:
            return {"key": key, "stale": True}
        if checked is None:
            skipped(engine, now.id)
            return {"key": key, "skipped": True}
        engine.update_clip(now.id, **_checked_fields(now, checked, key))
    return {"key": key}


def skipped(engine: Engine, clip_id: str) -> None:
    """Notes on the clip, once, that its edits went unchecked."""
    with engine.lock:
        clip = repo.get_clip(engine.db(), clip_id)
        flags = (clip.flags or {}) if clip else {}
        notes = flags.get("notes", [])
        if clip is not None and SKIPPED not in notes:
            engine.update_clip(clip_id, flags={**flags, "notes": [*notes, SKIPPED]})


def _context(engine: Engine, ctx: JobContext, project: Project) -> BrainContext:
    """The brain's view of the clip's project for one review: no cache, so a clip left unanswered is asked
    again next time, and a wait that says it is this clip that waits."""
    pp = engine.paths.project(project.id)
    settings = settings_for(project, ctx.settings)
    name = project.options.provider or settings.llm_provider
    try:
        provider = engine.provider_factory(settings, name)
    except LLMError as err:  # a missing API key, say: the clip goes unchecked
        log.warning("recheck: no %s provider (%s), so the clip goes unchecked", name, err.kind)
        provider = None
    return BrainContext(
        project_id=project.id,
        meta=read_meta(pp),
        transcript=read_transcript(pp),
        signals=NO_SIGNALS,
        options=project.options,
        settings=settings,
        provider=provider,
        provider_name=name if provider is not None else "heuristic",
        log_dir=pp.llm_dir,
        rights_basis=project.rights_basis,
        focus_s=project.focus_s,
        progress=ctx.progress,
        carry_on=CARRY_ON,
    )


def _candidate(clip: ClipRow, edits: dict[str, Any]) -> Candidate:
    """The clip as the review reads it, with its own title, cards and post copy over its packaging."""
    flags = clip.flags or {}
    sids = clip.sentence_ids or [""]
    return Candidate(
        id="k1",
        start_sid=sids[0],
        end_sid=sids[-1],
        start_s=clip.start_s,
        end_s=clip.end_s,
        scores={},
        red_flags=flags.get("red", []),
        soft_flags=flags.get("soft", []),
        notes=flags.get("notes", []),
        packaging=apply_packaging_edits(Packaging.model_validate(clip.packaging), edits),
    )


def _checked_fields(clip: ClipRow, checked: Candidate, key: str) -> dict[str, Any]:
    """The clip's fields once checked: the red flags the review didn't raise, as they are now, with the
    check's own, no ``review_skipped``, the review with its key and time, and the tier those flags give
    at the clip's length now."""
    flags = clip.flags or {}
    kept = [flag for flag in flags.get("red", []) if flag not in review.REVIEW_FLAGS]
    raised = [flag for flag in checked.red_flags if flag in review.REVIEW_FLAGS]
    red = list(dict.fromkeys([*kept, *raised]))
    notes = [note for note in flags.get("notes", []) if note != SKIPPED]
    assert checked.review is not None  # a check that was answered
    fields: dict[str, Any] = {
        "flags": {**flags, "red": red, "notes": notes},
        "review": checked.review.model_dump(mode="json") | {"key": key, "rechecked_at": time.time()},
    }
    if clip.final_score is not None:
        hard = [*(flag for flag in red if flag not in LENGTH_RED), *length_flags(clip.start_s, clip.end_s)[0]]
        fields["tier"] = score.tier(clip.final_score, bool(clip.passes_gates), hard)
    return fields
