"""Refine and package (SPEC 9.2 step 4): the LLM may move each clip's start or end to a sentence boundary
ClipForge offers, and writes what goes on and under the clip (SPEC 8.2 to 8.5). Each answer is held to those
limits here, so what breaks one is cut, dropped or replaced, and a clip the LLM doesn't answer for gets
Basic mode's packaging."""

from __future__ import annotations

import logging
import math
import unicodedata
from collections.abc import Callable

from clipforge.brain.context import LIMIT_KINDS, BrainContext, ask_all, load_prompt
from clipforge.brain.heuristic import heuristic_packaging
from clipforge.brain.llm.base import LLMRequest
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.schemas import CardOut, EmojiOut, PackageItem, PackageOut
from clipforge.brain.snap import length_flags, snap_times
from clipforge.brain.text import cleaned, plain_words, shortened, title_word, unquoted
from clipforge.brain.transcript_view import render_view, wrap_untrusted
from clipforge.brain.wording import (
    INSTAGRAM_TAGS,
    POST_CHARS,
    TIKTOK_TAGS,
    TITLE_CHARS,
    TITLE_WORDS,
    credit_line,
    cut_title,
    hashtags,
    post_text,
    units,
)
from clipforge.errors import LLMError
from clipforge.models import Candidate, EmojiCue, FactCard, Packaging, Transcript

log = logging.getLogger(__name__)

Fallback = Callable[[BrainContext, Candidate], Packaging]

BATCH = 5
MAX_ALTS = 2
MAX_EMPHASIS = 6
EMOJI_EVERY_S = 6.0
EMOJI_CHARS = 8
MAX_CARDS = 3
CARD_CHARS = 70
MAX_KEYWORDS = 6
LENGTH_SET = frozenset({"too_short", "too_long", "short", "over_90s_instagram"})  # what the old length set


def boundary_options(
    tr: Transcript,
    c: Candidate,
    *,
    start_before: float = 30.0,
    start_after: float = 20.0,
    end_before: float = 10.0,
    end_after: float = 30.0,
) -> tuple[list[str], list[str]]:
    """The sentences the clip may start on (those starting from ``start_before`` seconds before its start
    to ``start_after`` after) and end on (those ending from ``end_before`` before its end to ``end_after``
    after), in transcript order, without breaking a thought: no start carries on the sentence before it,
    and the sentence after an end doesn't carry on that end. Its current start and end are always among
    them."""
    sentences = tr.sentences
    lo, hi = c.start_s - start_before, c.start_s + start_after
    starts = [s.id for s in sentences if s.id == c.start_sid or (not s.cont and lo <= s.start <= hi)]
    lo, hi = c.end_s - end_before, c.end_s + end_after
    closes = [*(not s.cont for s in sentences[1:]), True]  # whether a thought may end on each sentence
    ends = [
        s.id
        for s, ok in zip(sentences, closes, strict=True)
        if s.id == c.end_sid or (ok and lo <= s.end <= hi)
    ]
    return starts, ends


def package(
    ctx: BrainContext, cands: list[Candidate], *, fallback: Fallback = heuristic_packaging
) -> list[Candidate]:
    """Copies of the candidates, in the order given, each with its packaging and perhaps new bounds. The
    LLM packages them five at a time, in parallel; a clip it doesn't answer for, and every clip without the
    LLM, gets the fallback's packaging (Basic mode's). A usage limit or a login problem stops the batches
    that haven't started, then waits or goes Basic."""
    if ctx.provider is None or ctx.llm_disabled:
        return [_basic(ctx, c, fallback) for c in cands]
    batches = [cands[k : k + BATCH] for k in range(0, len(cands), BATCH)]
    requests = [_request(ctx, batch) for batch in batches]
    message = "Writing titles and captions ({done} of {total} clips done)"
    answers = ask_all(ctx, requests, PackageOut, message, sizes=[len(batch) for batch in batches])
    out: list[Candidate] = []
    for batch, answer in zip(batches, answers, strict=True):
        if isinstance(answer, PackageOut):
            out.extend(_answered(ctx, batch, answer, fallback))
            continue
        if isinstance(answer, LLMError) and answer.kind not in LIMIT_KINDS:
            log.warning("package: %d clips failed (%s), so Basic mode packages them", len(batch), answer.kind)
            ctx.on_llm_error(answer)
        out.extend(_basic(ctx, c, fallback) for c in batch)
    return out


# The question


def _request(ctx: BrainContext, batch: list[Candidate]) -> LLMRequest:
    lo, hi = ctx.length_range
    return LLMRequest(
        pass_name="package",
        system=load_prompt("package", min_s=f"{lo:g}", max_s=f"{hi:g}"),
        prompt=_prompt(ctx, batch),
        schema=schema_for(PackageOut),
        model=ctx.model("package"),
        timeout_s=ctx.settings.llm_timeout_s,
    )


def _prompt(ctx: BrainContext, batch: list[Candidate]) -> str:
    meta, tr = ctx.meta, ctx.transcript
    facts = [
        f"Title: {meta.title}",
        f"Channel: {meta.channel or 'unknown'}",
        f"Language: {tr.language or meta.language or 'unknown'}",
    ]
    parts = [wrap_untrusted("metadata", "\n".join(facts))]
    for c in batch:
        starts, ends = boundary_options(tr, c)
        markers = dict.fromkeys(starts, "(start option)")
        for sid in ends:
            markers[sid] = f"{markers[sid]} (end option)" if sid in markers else "(end option)"
        view = render_view(tr, tr.index(starts[0]), tr.index(ends[-1]), markers=markers)
        header = (
            f"Clip {c.id} ({c.archetype}) runs from {c.start_sid} to {c.end_sid} now, {c.duration:.1f} s."
        )
        parts.append(f"{header}\n{wrap_untrusted('transcript', view)}")
    return "\n\n".join(parts)


# Checking the answer


def _answered(
    ctx: BrainContext, batch: list[Candidate], answer: PackageOut, fallback: Fallback
) -> list[Candidate]:
    """The batch packaged from the answer: the first answer for an id counts and unknown ids are ignored. A
    clip left out, or answered without a usable hook title, gets the fallback's packaging."""
    items: dict[str, PackageItem] = {}
    for item in answer.clips:
        items.setdefault(item.id.strip().lower(), item)
    out = []
    for c in batch:
        item = items.get(c.id.lower())
        packaged = _packaged(ctx, c, item) if item is not None else None
        if packaged is None:
            log.info("package: no usable answer for %s, so Basic mode packages it", c.id)
            packaged = _basic(ctx, c, fallback, "package_missing")
        out.append(packaged)
    return out


def _basic(ctx: BrainContext, c: Candidate, fallback: Fallback, *notes: str) -> Candidate:
    return c.model_copy(update={"packaging": fallback(ctx, c), "notes": _noted(c.notes, *notes)})


def _packaged(ctx: BrainContext, c: Candidate, item: PackageItem) -> Candidate | None:
    """The clip on the bounds the answer chose (when they hold up) with the answer's packaging held to
    SPEC 8's limits, or None when no hook title is left."""
    title, alts, was_cut = _titles([item.hook_title, *item.hook_alts])
    if title is None:
        return None
    c = _refined(ctx, c, item)
    tr = ctx.transcript
    i0, i1 = tr.index(c.start_sid), tr.index(c.end_sid)
    credit = credit_line(ctx.meta, ctx.rights_basis, ctx.options)
    tiktok, tiktok_tags = _caption(item.tiktok_caption, item.tiktok_hashtags, TIKTOK_TAGS, title, credit)
    instagram, instagram_tags = _caption(
        item.instagram_caption, item.instagram_hashtags, INSTAGRAM_TAGS, title, credit
    )
    packaging = Packaging(
        hook_title=title,
        hook_alts=alts,
        accent_word=title_word(item.accent_word, title),
        emphasis=_emphasis(item.emphasis, f" {plain_words(tr.text(i0, i1))} "),
        emoji_cues=_emoji_cues(tr, item.emoji_cues, i0, i1, c.duration),
        fact_cards=_fact_cards(tr, item.fact_cards, i0, i1),
        cold_open_sid=_cold_open(tr, item.cold_open_sid, i0, i1),
        mood=item.mood,
        energy=min(5, max(1, item.energy)),
        tiktok_caption=tiktok,
        tiktok_hashtags=tiktok_tags,
        instagram_caption=instagram,
        instagram_hashtags=instagram_tags,
        keywords=cleaned(item.keywords, lambda word: word.strip().lstrip("#"))[:MAX_KEYWORDS],
        credit=credit,
        mode="llm",
    )
    notes = ["hook_truncated"] if was_cut else []
    return c.model_copy(update={"packaging": packaging, "notes": _noted(c.notes, *notes)})


def _refined(ctx: BrainContext, c: Candidate, item: PackageItem) -> Candidate:
    """The clip on the bounds the answer chose, snapped to word times with its length judged again, when
    both are on offer and the spoken length is in the range. Otherwise the clip as it was, noted
    ``refine_rejected`` when the answer moved a bound."""
    tr = ctx.transcript
    start_sid, end_sid = _sid(item.start_sid), _sid(item.end_sid)
    if (start_sid, end_sid) == (c.start_sid, c.end_sid):
        return c
    starts, ends = boundary_options(tr, c)
    lo, hi = ctx.length_range
    if start_sid in starts and end_sid in ends:
        i0, i1 = tr.index(start_sid), tr.index(end_sid)
        spoken = round(tr.sentences[i1].end - tr.sentences[i0].start, 3)  # below 0 when they're backwards
        if lo <= spoken <= hi:
            start, end = snap_times(tr, i0, i1, max_s=hi)
            red_flags, notes = length_flags(start, end)
            k = _index(tr, c.cold_open_sid)
            update = {
                "start_sid": start_sid,
                "end_sid": end_sid,
                "start_s": start,
                "end_s": end,
                "red_flags": [flag for flag in c.red_flags if flag not in LENGTH_SET] + red_flags,
                "notes": [note for note in c.notes if note not in LENGTH_SET] + notes,
                "cold_open_sid": c.cold_open_sid if k is not None and i0 <= k <= i1 else None,
            }
            return c.model_copy(update=update)
    log.info(
        "package: %s keeps %s to %s; %s to %s isn't on offer",
        c.id,
        c.start_sid,
        c.end_sid,
        start_sid,
        end_sid,
    )
    return c.model_copy(update={"notes": _noted(c.notes, "refine_rejected")})


def _titles(texts: list[str]) -> tuple[str | None, list[str], bool]:
    """The hook title, its alternatives and whether the title was cut. The title is the first of ``texts``
    with 3 to 8 words in 42 characters, and the next two that fit are the alternatives. When none fits,
    the first is the title, cut down to size if it is too long. A text without a letter or digit isn't one."""
    titles = [title for title in cleaned(texts, unquoted) if plain_words(title)]
    fits = [t for t in titles if TITLE_WORDS[0] <= len(t.split()) <= TITLE_WORDS[1] and len(t) <= TITLE_CHARS]
    if fits:
        return fits[0], fits[1 : 1 + MAX_ALTS], False
    if not titles:
        return None, [], False
    first = titles[0]
    title = cut_title(first) if len(first.split()) > TITLE_WORDS[1] or len(first) > TITLE_CHARS else first
    return title or None, [], title != first


def _emphasis(words: list[str], clip: str) -> list[str]:
    """The words and names the clip says, as the answer wrote them with spaces collapsed, each once and at
    most 6. ``clip`` is the clip's plain words with a space either side."""
    out: list[str] = []
    keys: set[str] = set()
    for text in words:
        key = plain_words(text)
        if key and key not in keys and f" {key} " in clip:
            keys.add(key)
            out.append(" ".join(text.split()))
    return out[:MAX_EMPHASIS]


def _emoji_cues(tr: Transcript, cues: list[EmojiOut], i0: int, i1: int, duration: float) -> list[EmojiCue]:
    """The cues on a sentence of the clip that says their word, each a real emoji and given once: at most
    one for every 6 s of the clip, in the answer's order, then put in the clip's."""
    room = math.floor(round(duration, 3) / EMOJI_EVERY_S)
    kept: list[tuple[int, EmojiCue]] = []
    for cue in cues:
        k = _index(tr, cue.sid)
        word, emoji = " ".join(cue.word.split()), "".join(cue.emoji.split())
        key = plain_words(word)
        if k is None or not i0 <= k <= i1 or not key or not _is_emoji(emoji):
            continue
        if f" {key} " not in f" {plain_words(tr.sentences[k].text)} ":
            continue
        found = EmojiCue(sid=tr.sentences[k].id, word=word, emoji=emoji)
        if all(found != other for _, other in kept):
            kept.append((k, found))
    return [cue for _, cue in sorted(kept[:room], key=lambda pair: pair[0])]


def _is_emoji(text: str) -> bool:
    """Whether ``text`` can be one emoji: at most 8 characters, a symbol among them and no letters, so a
    word or a smiley made of punctuation isn't."""
    return (
        len(text) <= EMOJI_CHARS
        and any(unicodedata.category(ch) == "So" for ch in text)
        and not any(ch.isalpha() for ch in text)
    )


def _fact_cards(tr: Transcript, cards: list[CardOut], i0: int, i1: int) -> list[FactCard]:
    """At most 3 cards, each on a sentence of the clip, cut to 70 characters at a word boundary and given
    once, in the clip's order."""
    kept: list[tuple[int, FactCard]] = []
    for card in cards:
        k = _index(tr, card.sid)
        text = shortened(card.text, CARD_CHARS).rstrip(",;: ")
        if k is None or not i0 <= k <= i1 or not text:
            continue
        if all(text.casefold() != other.text.casefold() for _, other in kept):
            kept.append((k, FactCard(sid=tr.sentences[k].id, text=text)))
    return [card for _, card in sorted(kept[:MAX_CARDS], key=lambda pair: pair[0])]


def _cold_open(tr: Transcript, sid: str | None, i0: int, i1: int) -> str | None:
    """The teaser's sentence id when it is a sentence of the clip after its first, else None: the first line
    would only play twice."""
    k = _index(tr, sid)
    return tr.sentences[k].id if k is not None and i0 < k <= i1 else None


def _caption(text: str, tags: list[str], limit: int, title: str, credit: str | None) -> tuple[str, list[str]]:
    """The caption with its spaces collapsed and its hashtags: a run of hashtags at either end moves to the
    tags, and one inside a sentence keeps its word there and is tagged too, after the answer's own tags.
    The caption is cut at a word boundary so the whole post fits in 2200 UTF-16 units, and an empty one is
    the hook title."""
    words = text.split()
    lead: list[str] = []
    while words and _is_tag(words[0]):
        lead.append(words.pop(0))
    trail: list[str] = []
    while words and _is_tag(words[-1]):
        trail.insert(0, words.pop())
    tags = hashtags([*tags, *lead, *(word for word in words if _is_tag(word)), *trail], limit)
    rest = post_text("", tags, credit)
    room = POST_CHARS - (units(rest) + 2 if rest else 0)  # 2 for the blank line after the caption
    caption = " ".join(word.lstrip("#") if _is_tag(word) else word for word in words)
    return _fit(caption, room) or title, tags


def _is_tag(word: str) -> bool:
    """Whether a word of a caption is a hashtag: ``#`` and at least one letter, so "#1" isn't."""
    return word.startswith("#") and any(ch.isalpha() for ch in word)


# Small helpers


def _sid(text: str) -> str:
    return text.strip().upper()


def _index(tr: Transcript, sid: str | None) -> int | None:
    """Where sentence ``sid`` sits, however the LLM wrote it; None when there is no such sentence."""
    if not sid:
        return None
    try:
        return tr.index(_sid(sid))
    except KeyError:
        return None


def _fit(text: str, room: int) -> str:
    """The text cut at a word boundary to at most ``room`` UTF-16 units."""
    out: list[str] = []
    used = -1  # no space before the first word
    for word in text.split():
        used += 1 + units(word)
        if used > room:
            break
        out.append(word)
    return " ".join(out)


def _noted(notes: list[str], *more: str) -> list[str]:
    return list(dict.fromkeys([*notes, *more]))
