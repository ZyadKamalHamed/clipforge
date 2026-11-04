"""Claude fills what a brief's source didn't state (SPEC 10.11): its niche from your list, the language its
clips are in, and any rate, minimum views, cap, platforms or footage links its text gives. Each brief's text
is untrusted data, fenced and sent with no tools, and the answer is checked: a field the source stated is
never changed, and a value outside its bounds or the list is dropped. Without Claude, after an error or
past the day's limit, a keyword guess picks the niche."""

from __future__ import annotations

import logging
import math
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from clipforge.brain.context import load_prompt
from clipforge.brain.llm.base import LLMRequest, call_structured
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.transcript_view import wrap_untrusted
from clipforge.briefs import store
from clipforge.briefs.models import Brief
from clipforge.campaigns import tracker
from clipforge.campaigns.models import Platform
from clipforge.config import Niche, model_for
from clipforge.db import transaction
from clipforge.errors import LLMError

log = logging.getLogger(__name__)

PASS = "brief"
MODEL_PASS = "package"  # the brief pass uses the package pass's model, so Settings needs no new row
BATCH = 20
READS = "(claude)"  # the briefs Claude read today are counted in site_reads under this name, never a host
RATE_MAX = 100.0
MIN_VIEWS_MAX = 100_000_000
PAYOUT_MAX = 1_000_000.0
LINKS_MAX = 10
_LANGUAGE = re.compile(r"[a-z]{2,8}")
_YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_LIVE = ("new", "seen")


class BriefFill(BaseModel):
    """Claude's reading of one brief, by its key in the request: null for anything the text doesn't give.
    The bounds (a rate of 0 to 100 per 1,000 views, minimum views up to 100,000,000, a cap up to 1,000,000,
    up to 10 footage links) are checked when the values are placed, so one value out of bounds drops only
    itself."""

    key: int
    niche: str | None = None
    language: str | None = None
    rate_per_1000: float | None = None
    min_views: int | None = None
    max_payout: float | None = None
    platforms: list[Platform] | None = None
    footage_links: list[str] = []


class BriefAnswer(BaseModel):
    items: list[BriefFill]


def guess_niche(brief: Brief, niches: list[Niche]) -> str | None:
    """The niche whose tags appear most often, as whole words, in the brief's brand, title and text; ties go
    to the one earlier in the list, and None when no tag appears."""
    words = " ".join(part for part in (brief.brand, brief.title, brief.text) if part).casefold()
    best, most = None, 0
    for niche in niches:
        count = sum(len(re.findall(rf"(?<!\w){re.escape(tag)}(?!\w)", words)) for tag in niche.tags)
        if count > most:
            best, most = niche.name, count
    return best


def _worth(brief: Brief) -> float:
    return (brief.rate_per_1000 or 0.0) * (brief.budget_left or 0.0)


def _within(value: float | int | None, top: float) -> bool:
    return value is not None and math.isfinite(value) and 0 <= value <= top


def _youtube(link: str) -> bool:
    try:
        parts = urlsplit(link.strip())
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "") in _YOUTUBE and parts.username is None


def _changes(brief: Brief, item: BriefFill, niches: list[Niche]) -> dict[str, Any]:
    """The fields Claude's reading fills: only those the source didn't state, each within its bounds."""
    stated = {name for name, by in brief.fields.items() if by == "stated"}
    names = {n.name.casefold(): n.name for n in niches}
    fields = dict(brief.fields)
    changes: dict[str, Any] = {}

    def put(name: str, value: Any) -> None:
        if name not in stated:
            changes[name] = value
            fields[name] = "claude"

    put("niche", names.get((item.niche or "").strip().casefold()))  # marked read even when it fits none
    language = (item.language or "").strip().lower()
    if _LANGUAGE.fullmatch(language):
        put("language", language)
    if _within(item.rate_per_1000, RATE_MAX):
        put("rate_per_1000", item.rate_per_1000)
    if _within(item.min_views, MIN_VIEWS_MAX):
        put("min_views", item.min_views)
    if _within(item.max_payout, PAYOUT_MAX):
        put("max_payout", item.max_payout)
    if item.platforms:
        put("platforms", [p for p in ("tiktok", "instagram") if p in item.platforms])
    links = list(dict.fromkeys(link.strip() for link in item.footage_links if _youtube(link)))[:LINKS_MAX]
    if links:
        put("footage", brief.footage.model_copy(update={"links": links}))
    if changes:
        changes["fields"] = fields
    return changes


def _ask(engine: Any, provider: Any, batch: list[Brief]) -> dict[int, BriefFill] | None:
    """Claude's readings of ``batch`` by key (1 on), or None when the call failed."""
    settings = engine.settings
    niches = "\n".join(f"- {n.name}: {', '.join(n.tags)}" for n in settings.briefs.niches)
    blocks = "\n\n".join(
        wrap_untrusted("brief", f"{key}\n{b.brand or ''}\n{b.title}\n{b.text}")
        for key, b in enumerate(batch, 1)
    )
    req = LLMRequest(
        pass_name=PASS,
        system=load_prompt("brief"),
        prompt=f"The niches, each with the words that mark it:\n{niches}\n\nThe briefs:\n\n{blocks}",
        schema=schema_for(BriefAnswer),
        model=model_for(settings, settings.llm_provider, MODEL_PASS),
        timeout_s=settings.llm_timeout_s,
    )
    try:
        answer = call_structured(provider, req, BriefAnswer)
    except LLMError as err:
        log.warning("briefs: Claude couldn't read %d briefs (%s)", len(batch), err.kind)
        return None
    return {item.key: item for item in answer.items if 1 <= item.key <= len(batch)}


def _provider(engine: Any) -> Any:
    settings = engine.settings
    try:
        return engine.provider_factory(settings, settings.llm_provider)
    except LLMError as err:  # a missing API key, say: the keyword guess fills the niche
        log.warning("briefs: no %s provider (%s)", settings.llm_provider, err.kind)
        return None


def _unread(brief: Brief) -> bool:
    """Whether Claude has yet to read the brief: no one has, or only the keyword guess has."""
    if brief.fields.get("niche") == "guess":
        return True
    return brief.niche is None and "niche" not in brief.fields


def fill(engine: Any, briefs: list[Brief]) -> int:
    """Fills the live briefs among ``briefs`` that Claude hasn't read yet, those no one has read first and
    those with only the keyword guess after, each group the most worth first (rate times budget left,
    missing counting as 0), up to the day's ``read_per_morning`` through Claude, 20 to a call; the rest get
    the keyword guess. Returns how many Claude read."""
    settings = engine.settings
    niches = settings.briefs.niches
    todo = [b for b in briefs if b.status in _LIVE and _unread(b)]
    todo.sort(key=lambda b: (b.fields.get("niche") != "guess", _worth(b)), reverse=True)
    conn = engine.db()
    day = tracker.day_of(engine, engine.now()).isoformat()  # today in the posting time zone
    left = max(settings.briefs.read_per_morning - store.site_read(conn, READS, day)[0], 0)
    provider = _provider(engine) if left and todo else None
    read = 0
    guessed: list[Brief] = []
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        if provider is None or read >= left:
            guessed += batch
            continue
        asked = batch[: left - read]
        guessed += batch[len(asked) :]
        found = _ask(engine, provider, asked)
        if found is None:
            guessed += asked
            continue
        with engine.lock, transaction(conn):
            for key, brief in enumerate(asked, 1):
                store.count_read(conn, READS, day, page=True, at=engine.now().timestamp())
                item = found.get(key)
                changes = _changes(brief, item, niches) if item else {}
                if changes:
                    store.update_brief(conn, brief.id, **changes)
        read += len(asked)
    with engine.lock, transaction(conn):
        for brief in guessed:
            niche = guess_niche(brief, niches)
            if niche is not None:
                store.update_brief(conn, brief.id, niche=niche, fields=brief.fields | {"niche": "guess"})
    return read
