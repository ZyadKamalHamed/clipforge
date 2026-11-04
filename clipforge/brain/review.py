"""Review (SPEC 9.2 step 5): a second, independent look at each packaged clip as viewers will hear it, next
to what is said just before and after it. The LLM never sees the scores. It says whether the story is
complete, whether the cut changes what the speaker meant, which title and which captions are true to the
clip and which fact cards the clip supports, and its answers become red flags, a new hook title, new
captions and fewer cards. A clip the user edited is checked again in the same way (SPEC 14.4), as it is
now: then only its review and red flags change, since the words on it are the user's.

Adapted from the editorial review in Cutawan (github.com/JeremySNR/cutawan, editorialReview.ts, MIT): the
kept speech is judged against the context left out, with the scores withheld and every text from the video
treated as data."""

from __future__ import annotations

import logging

from clipforge.brain.context import LIMIT_KINDS, BrainContext, ask_all, load_prompt
from clipforge.brain.llm.base import LLMRequest
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.schemas import ReviewItem, ReviewOut
from clipforge.brain.text import shortened, title_word
from clipforge.brain.transcript_view import wrap_untrusted
from clipforge.errors import LLMError
from clipforge.models import Candidate, Packaging, ReviewResult, Transcript, Word

log = logging.getLogger(__name__)

BATCH = 5
CONTEXT_S = 20.0
NOTE_CHARS = 300
CAPTIONS = {"tiktok_caption": "TikTok", "instagram_caption": "Instagram"}  # as the reviewer reads them
REVIEW_FLAGS = ("meaning_distortion", "incomplete_story", "no_faithful_hook")  # the red flags it raises
EPS = 1e-6  # as render.edl.clip_words picks a clip's words, without the render's imports
SAID = "The same stretch as it was said, before words were cut from it (viewers never hear the cut words):"


def kept_text(
    tr: Transcript, c: Candidate, *, drop_fillers: bool = True, struck: frozenset[int] = frozenset()
) -> str:
    """What viewers hear of the clip: the words whose middle falls inside it, as the render picks them,
    less the words ``struck`` from it and, with ``drop_fillers``, the fillers, which are cut by default
    (SPEC 7.9)."""
    words = [
        w for w in tr.words if c.start_s - EPS <= (w.start + w.end) / 2 <= c.end_s + EPS and w.i not in struck
    ]
    return _spoken(words, drop_fillers=drop_fillers)


def context_text(tr: Transcript, t0: float, t1: float) -> str:
    """The words starting from ``t0`` to before ``t1``, without the fillers."""
    return _spoken([w for w in tr.words if t0 <= w.start < t1])


def review(ctx: BrainContext, cands: list[Candidate]) -> list[Candidate]:
    """Copies of the candidates, in the order given, each packaged one with its review and what follows
    from it: red flags, the first true title as the hook title and only the fact cards the clip supports.
    The LLM checks them five at a time, in parallel. A clip it doesn't answer for keeps its packaging, with
    no review and the note ``review_skipped``. In Basic mode nothing is reviewed, and a candidate without
    packaging is passed over. A usage limit or a login problem stops the batches that haven't started, then
    waits or goes Basic."""
    out = list(cands)
    todo = [k for k, c in enumerate(cands) if c.packaging is not None]
    if ctx.provider is None:
        return out
    if ctx.llm_disabled:  # a limit turned the LLM off earlier in the run
        for k in todo:
            out[k] = _skipped(cands[k])
        return out
    batches = [todo[j : j + BATCH] for j in range(0, len(todo), BATCH)]
    requests = [_request(ctx, [cands[k] for k in batch]) for batch in batches]
    message = "Checking clips ({done} of {total} clips done)"
    answers = ask_all(ctx, requests, ReviewOut, message, sizes=[len(batch) for batch in batches])
    for batch, answer in zip(batches, answers, strict=True):
        if isinstance(answer, LLMError) and answer.kind not in LIMIT_KINDS:
            log.warning("review: %d clips failed (%s), so they go unchecked", len(batch), answer.kind)
            ctx.on_llm_error(answer)
        items = _items(answer) if isinstance(answer, ReviewOut) else {}
        for k in batch:
            item = items.get(cands[k].id)
            if item is None and isinstance(answer, ReviewOut):
                log.info("review: no answer for %s, so it goes unchecked", cands[k].id)
            out[k] = _reviewed(cands[k], item) if item is not None else _skipped(cands[k])
    return out


def recheck(
    ctx: BrainContext,
    cand: Candidate,
    *,
    drop_fillers: bool = True,
    struck: frozenset[int] = frozenset(),
) -> Candidate | None:
    """The candidate, a clip the user edited, checked again as it is now (SPEC 14.4): its words less the
    ones ``struck`` and, with ``drop_fillers``, the fillers, and only the title it goes out with. The copy
    has the new review, and red flags that are its own in place of the earlier review's; its packaging is
    untouched, as the words on the clip are the user's. None when it went unchecked: in Basic mode, with
    no packaging, or when the LLM failed or left it out. A usage limit or a login problem waits or goes
    Basic, as in the brain's passes."""
    if ctx.provider is None or ctx.llm_disabled or cand.packaging is None:
        return None
    p = cand.packaging.model_copy(update={"hook_alts": []})
    req = _request(
        ctx, [cand.model_copy(update={"packaging": p})], again=True, drop_fillers=drop_fillers, struck=struck
    )
    (answer,) = ask_all(ctx, [req], ReviewOut, "Re-checking the edited clip")
    if isinstance(answer, LLMError) and answer.kind not in LIMIT_KINDS:
        log.warning("recheck: %s failed (%s), so it goes unchecked", cand.id, answer.kind)
        ctx.on_llm_error(answer)
    item = _items(answer).get(cand.id) if isinstance(answer, ReviewOut) else None
    if item is None:
        if isinstance(answer, ReviewOut):
            log.info("recheck: no answer for %s, so it goes unchecked", cand.id)
        return None
    result, raised = _verdict(p, item)
    flags = [*(flag for flag in cand.red_flags if flag not in REVIEW_FLAGS), *raised]
    return cand.model_copy(update={"review": result, "red_flags": list(dict.fromkeys(flags))})


# The question


def _request(
    ctx: BrainContext,
    batch: list[Candidate],
    *,
    again: bool = False,
    drop_fillers: bool = True,
    struck: frozenset[int] = frozenset(),
) -> LLMRequest:
    """The question about a batch; ``again`` for a clip the user edited, which is told how it was."""
    system = load_prompt("review")
    if again:
        system += "\n\n" + load_prompt("recheck")
    return LLMRequest(
        pass_name="review",
        system=system,
        prompt=_prompt(ctx, batch, drop_fillers=drop_fillers, struck=struck),
        schema=schema_for(ReviewOut),
        model=ctx.model("review"),
        timeout_s=ctx.settings.llm_timeout_s,
    )


def _prompt(
    ctx: BrainContext,
    batch: list[Candidate],
    *,
    drop_fillers: bool = True,
    struck: frozenset[int] = frozenset(),
) -> str:
    """The video's title and channel, which may say who is speaking, then each clip of the batch."""
    meta = f"Title: {ctx.meta.title}\nChannel: {ctx.meta.channel or 'unknown'}"
    clips = (_clip(ctx.transcript, c, drop_fillers=drop_fillers, struck=struck) for c in batch)
    return "\n\n".join([wrap_untrusted("metadata", meta), *clips])


def _clip(
    tr: Transcript, c: Candidate, *, drop_fillers: bool = True, struck: frozenset[int] = frozenset()
) -> str:
    """One clip as the reviewer reads it: what is said around it, the clip as cut (and as it was said, when
    words were struck from it), its titles, its captions and its cards. Nothing about its scores."""
    p = c.packaging
    heard = kept_text(tr, c, drop_fillers=drop_fillers, struck=struck)
    lines = [f"Clip {c.id} ({c.duration:.1f} s)"]
    lines += _around("before", context_text(tr, c.start_s - CONTEXT_S, c.start_s))
    hear = (
        "The clip, as viewers hear it:" if drop_fillers else "The clip, as viewers hear it, fillers and all:"
    )
    lines += [hear, wrap_untrusted("clip", heard)]
    if struck and (spoken := kept_text(tr, c, drop_fillers=drop_fillers)) != heard:
        lines += [SAID, wrap_untrusted("said", spoken)]
    lines += _around("after", context_text(tr, c.end_s, c.end_s + CONTEXT_S))
    lines += ["Titles:", wrap_untrusted("titles", _numbered([p.hook_title, *p.hook_alts]))]
    captions = [f"{label}: {getattr(p, name)}" for name, label in CAPTIONS.items() if getattr(p, name)]
    if captions:
        lines += ["Captions:", wrap_untrusted("captions", "\n".join(captions))]
    else:
        lines.append("Captions: none.")
    if p.fact_cards:
        lines += ["Fact cards:", wrap_untrusted("cards", _numbered([card.text for card in p.fact_cards]))]
    else:
        lines.append("Fact cards: none.")
    return "\n".join(lines)


def _around(side: str, text: str) -> list[str]:
    """The lines for what is said on one side of the clip, ``before`` or ``after`` it."""
    if not text:
        return [f"Nothing is said in the {CONTEXT_S:g} s {side} the clip."]
    return [f"Said in the {CONTEXT_S:g} s {side} the clip (not part of it):", wrap_untrusted(side, text)]


def _numbered(texts: list[str]) -> str:
    return "\n".join(f"{k}. {text}" for k, text in enumerate(texts))


# Applying the answer


def _items(answer: ReviewOut) -> dict[str, ReviewItem]:
    """The answer by clip id, however its case and spaces were written; the first answer for an id counts."""
    items: dict[str, ReviewItem] = {}
    for item in answer.clips:
        items.setdefault(item.id.strip().lower(), item)
    return items


def _verdict(p: Packaging, item: ReviewItem) -> tuple[ReviewResult, list[str]]:
    """The answer about a clip packaged as ``p``: its review, and a red flag for each failure. An index
    that names no title counts as none, and a card left unanswered isn't supported."""
    titles = [p.hook_title, *p.hook_alts]
    index = item.true_title_index if 0 <= item.true_title_index < len(titles) else -1
    n = len(p.fact_cards)
    result = ReviewResult(
        complete_story=item.complete_story,
        meaning_changed=item.meaning_changed,
        true_title_index=index,
        cards_supported=(item.cards_supported + [False] * n)[:n],
        tiktok_caption_true=item.tiktok_caption_true,
        instagram_caption_true=item.instagram_caption_true,
        notes=shortened(item.notes, NOTE_CHARS),
    )
    failed = {
        "meaning_distortion": item.meaning_changed,
        "incomplete_story": not item.complete_story,
        "no_faithful_hook": index == -1,
    }
    return result, [flag for flag, raised in failed.items() if raised]


def _reviewed(c: Candidate, item: ReviewItem) -> Candidate:
    """The clip with its review: a red flag for each failure, the first true title as its hook title, only
    the cards the answer found supported, and that title (the hook title when none is true) in place of a
    caption that isn't true."""
    p = c.packaging
    titles = [p.hook_title, *p.hook_alts]
    result, raised = _verdict(p, item)
    index, supported = result.true_title_index, result.cards_supported
    flags = [*c.red_flags, *raised]
    update = {"fact_cards": [card for card, ok in zip(p.fact_cards, supported, strict=True) if ok]}
    if index > 0:
        update |= _retitled(p, titles[index], titles[index + 1 :])
    claims = {"tiktok_caption": item.tiktok_caption_true, "instagram_caption": item.instagram_caption_true}
    update |= {name: titles[max(index, 0)] for name, true in claims.items() if not true}
    return c.model_copy(
        update={
            "review": result,
            "red_flags": list(dict.fromkeys(flags)),
            "packaging": p.model_copy(update=update),
        }
    )


def _retitled(p: Packaging, title: str, alts: list[str]) -> dict[str, object]:
    """The fields that change when ``title`` is the first true one: it is the hook title, the titles after
    it are the alternatives and the untrue ones before it go. The accent word stays if the new title has
    it, and a caption that was the old title (as an empty one is) becomes the new one."""
    update: dict[str, object] = {
        "hook_title": title,
        "hook_alts": alts,
        "accent_word": title_word(p.accent_word, title),
    }
    for name in ("tiktok_caption", "instagram_caption"):
        if getattr(p, name) == p.hook_title:
            update[name] = title
    return update


def _skipped(c: Candidate) -> Candidate:
    return c.model_copy(update={"review": None, "notes": list(dict.fromkeys([*c.notes, "review_skipped"]))})


def _spoken(words: list[Word], *, drop_fillers: bool = True) -> str:
    return " ".join(w.text for w in words if not (drop_fillers and w.filler))
