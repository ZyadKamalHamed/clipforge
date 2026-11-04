"""Rerank (SPEC 9.2 step 3): every candidate side by side in one call. Discover scored each window on its
own, so its scores don't compare across windows; this pass puts them on one 0 to 100 scale for the whole
video. When the user asked for a topic, it also says which candidates are about it."""

from __future__ import annotations

import logging

from clipforge.brain.context import BrainContext, load_prompt
from clipforge.brain.llm.base import LLMRequest, call_structured
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.schemas import RerankItem, RerankOut
from clipforge.brain.transcript_view import fmt_time, wrap_untrusted
from clipforge.errors import LLMError
from clipforge.models import Candidate, Transcript

log = logging.getLogger(__name__)

HEAD_WORDS = 150
TAIL_WORDS = 60
SLACK_WORDS = 10  # a clip only this far over head and tail is shown whole


def clip_text_for_rerank(
    tr: Transcript, c: Candidate, *, head_words: int = HEAD_WORDS, tail_words: int = TAIL_WORDS
) -> str:
    """The clip's text, with its middle left out (``" ... "``) when it has more than ``head_words +
    tail_words + 10`` words."""
    words = tr.text(tr.index(c.start_sid), tr.index(c.end_sid)).split()
    if len(words) <= head_words + tail_words + SLACK_WORDS:
        return " ".join(words)
    return f"{' '.join(words[:head_words])} ... {' '.join(words[-tail_words:])}"


def rerank(ctx: BrainContext, cands: list[Candidate]) -> list[Candidate]:
    """The candidates in the order given, each with ``rerank_score`` set, and with a topic asked for,
    ``on_topic`` too (note ``off_topic`` when it isn't). With one candidate, or without the LLM, the
    Discover score stands. So it does when the call fails (after ``ctx.on_llm_error``, which may wait
    instead), and for any candidate the answer leaves out (note ``rerank_missing``)."""
    if len(cands) < 2 or ctx.provider is None or ctx.llm_disabled:
        return [_scored(c, c.llm_score) for c in cands]
    try:
        req = _request(ctx, cands)
        answer = call_structured(ctx.provider, req, RerankOut, cache=ctx.cache, log_dir=ctx.log_dir)
    except LLMError as err:
        log.warning("rerank: failed (%s), so the Discover scores stand", err.kind)
        ctx.on_llm_error(err)
        return [_scored(c, c.llm_score) for c in cands]
    found: dict[str, RerankItem] = {}
    for item in answer.ranking:
        found.setdefault(item.id.strip().lower(), item)  # a repeat keeps the first
    topic = bool(ctx.options.prompt)
    out = []
    for c in cands:
        item = found.get(c.id)
        if item is None:
            out.append(_scored(c, c.llm_score, "rerank_missing"))
        elif topic and not item.on_topic:
            out.append(_scored(c, min(100, max(0, item.score)), "off_topic", on_topic=False))
        else:
            out.append(_scored(c, min(100, max(0, item.score)), on_topic=True if topic else None))
    return out


def _scored(c: Candidate, score: float, *notes: str, on_topic: bool | None = None) -> Candidate:
    return c.model_copy(
        update={
            "rerank_score": float(score),
            "on_topic": on_topic,
            "notes": list(dict.fromkeys([*c.notes, *notes])),
        }
    )


def _request(ctx: BrainContext, cands: list[Candidate]) -> LLMRequest:
    return LLMRequest(
        pass_name="rerank",
        system=load_prompt("rerank"),
        prompt=_prompt(ctx.transcript, cands, ctx.options.prompt),
        schema=schema_for(RerankOut),
        model=ctx.model("rerank"),
        timeout_s=ctx.settings.llm_timeout_s,
    )


def _prompt(tr: Transcript, cands: list[Candidate], topic: str | None = None) -> str:
    blocks = []
    if topic:
        blocks.append(f"{wrap_untrusted('user_topic', topic)}\nThe user asked for clips about this topic.")
    blocks.append(f"{len(cands)} candidates from one video:")
    for c in cands:
        scores = " ".join(f"{name} {value}" for name, value in c.scores.items()) or "not scored"
        lines = [
            f"{c.id} [{fmt_time(c.start_s)}-{fmt_time(c.end_s)}] {c.duration:.1f} s, {c.archetype}, {scores}"
        ]
        if c.hook_titles:
            lines.append(wrap_untrusted("title", c.hook_titles[0]))
        lines.append(wrap_untrusted("transcript", clip_text_for_rerank(tr, c)))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
