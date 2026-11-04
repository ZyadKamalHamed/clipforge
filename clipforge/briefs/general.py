"""The general reader (SPEC 10.11): a marketplace's public list page, added in Settings with "Add a site". The
list is read politely (fetch.py), headless when its scripts build it, and Claude picks the campaigns out of
its text as untrusted data (fenced, no tools, the answer schema-checked, 17), each one a link the page has.
The new briefs' own pages are then read in the list's order, within the site's 40 a day, and their text
becomes the brief's. A brief ClipForge already knows, from any source, takes the list's figures and keeps
its own text, so its page is never read again. A brief page that is gone, barred by robots.txt or sends
ClipForge elsewhere leaves out that brief only; a site that refuses, slows ClipForge down, asks for a
sign-in or fails stops the read there, and what was read before is kept (Cut). A campaign whose budget left
is 0 is gone."""

from __future__ import annotations

import logging
import math
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from clipforge.brain.context import load_prompt
from clipforge.brain.llm.base import LLMRequest, call_structured
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.transcript_view import wrap_untrusted
from clipforge.briefs import store
from clipforge.briefs.fetch import ELSEWHERE, FULL, LINKS_MAX, LOOPS, ROBOTS, Fetched, Polite, Stop, scripted
from clipforge.briefs.fetch import (
    page_text as page_text,
)  # the list's text, kept here for readers of this one
from clipforge.briefs.fill import RATE_MAX
from clipforge.briefs.models import TEXT_MAX, Brief, BriefIn, BriefSource
from clipforge.config import model_for
from clipforge.errors import LLMError
from clipforge.untrusted import clean_text

log = logging.getLogger(__name__)

PASS = "brief_list"
MODEL_PASS = "package"  # the list pass uses the package pass's model, so Settings needs no new row
LIST_CHARS = 60_000
ITEMS_MAX = 50
LINKS_MIN = 3  # a list page with fewer links on its own site is built by its scripts
BUDGET_MAX = 10_000_000.0
NO_CLAUDE = "Claude is needed to read this site's list. Paste its briefs instead."
FAILED = "Claude couldn't read this site's list today. Paste its briefs instead."
CHANGED = (
    "This site's list showed none of its briefs today, so its page may have changed. "
    "Paste its briefs instead."
)
_HELD = ["new", "seen", "hidden", "used"]  # a source with any of these has shown briefs before
_PLATFORMS = (("tiktok", ("tiktok", "tik tok")), ("instagram", ("instagram", "reels")))
_PASSED = (ROBOTS, ELSEWHERE, LOOPS, FULL)  # stops on one brief page that leave out that brief only


class Cut(Stop):
    """A stop on a brief page partway down the list, with the briefs ``found`` before it."""

    def __init__(self, stop: Stop, found: list[BriefIn]) -> None:
        super().__init__(stop.result, stop.message, details=stop.details)
        self.found = found


class ListItem(BaseModel):
    """One campaign on the list as Claude read it: its link on the page and what the list shows of it. The
    bounds (a rate of 0 to 100 per 1,000 views, budgets up to 10,000,000) are checked when the values are
    placed, so one value out of bounds drops only itself."""

    url: str
    brand: str | None = None
    title: str
    rate_per_1000: float | None = None
    budget_total: float | None = None
    budget_left: float | None = None
    platforms: list[str] | None = None


class ListAnswer(BaseModel):
    items: list[ListItem]


def _provider(engine: Any) -> Any:
    settings = engine.settings
    try:
        return engine.provider_factory(settings, settings.llm_provider)
    except LLMError as err:  # a missing API key, say
        log.warning("briefs: no %s provider (%s)", settings.llm_provider, err.kind)
        return None


def _ask(engine: Any, provider: Any, page: Fetched) -> ListAnswer:
    """Claude's reading of the list page. Raises Stop("error") when the call fails."""
    settings = engine.settings
    links = "\n".join(f"{n}. {link}" for n, link in enumerate(page.links, 1))
    req = LLMRequest(
        pass_name=PASS,
        system=load_prompt("brief_list"),
        prompt=(
            f"The links on the page, numbered:\n\n{wrap_untrusted('links', links)}\n\n"
            f"The page's text:\n\n{wrap_untrusted('listing', page.text[:LIST_CHARS])}"
        ),
        schema=schema_for(ListAnswer),
        model=model_for(settings, settings.llm_provider, MODEL_PASS),
        timeout_s=settings.llm_timeout_s,
    )
    try:
        return call_structured(provider, req, ListAnswer)
    except LLMError as err:
        log.warning("briefs: Claude couldn't read the list at %s (%s)", clean_text(page.url[:200]), err.kind)
        raise Stop("error", FAILED, details=err.kind) from None


def _link(url: str, page: Fetched) -> str | None:
    """``url`` as one of the page's own links (made absolute, without a fragment), or None."""
    try:
        parts = urlsplit(urljoin(page.url, url.strip()))
    except ValueError:
        return None
    link = urlunsplit(parts._replace(fragment=""))
    return link if link in page.links else None


def _within(value: float | None, top: float) -> bool:
    return value is not None and math.isfinite(value) and 0 <= value <= top


def _platforms(named: list[str] | None) -> list[str] | None:
    """The platforms the list names, in ClipForge's words: None when it names none, [] when it names only
    others."""
    if not named:
        return None
    words = " ".join(named).casefold()
    return [platform for platform, marks in _PLATFORMS if any(mark in words for mark in marks)]


def _values(item: ListItem, at: float) -> dict[str, Any] | None:
    """What the list shows of one campaign, each value within its bounds, or None when it is left out: no
    name, or paid only on other platforms."""
    title = (clean_text(item.title) or "").strip()[:200]
    allowed = _platforms(item.platforms)
    if not title or allowed == []:
        return None
    values: dict[str, Any] = {"title": title}
    brand = (clean_text(item.brand) or "").strip()[:200]
    if brand:
        values["brand"] = brand
    if _within(item.rate_per_1000, RATE_MAX):
        values["rate_per_1000"] = item.rate_per_1000
    total = item.budget_total if _within(item.budget_total, BUDGET_MAX) else None
    left = item.budget_left if _within(item.budget_left, BUDGET_MAX) else None
    if total is not None:
        values["budget_total"] = total
    if left is not None:
        values["budget_left"] = left
    if total is not None and left is not None:
        values["budget_spent"] = max(total - left, 0.0)
    if total is not None or left is not None:
        values["budget_read_at"] = at
    if left == 0:
        values["gone"] = True  # its budget is spent
    if allowed:
        values["platforms"] = allowed
    return values


def _listed(answer: ListAnswer, page: Fetched, at: float) -> list[tuple[str, dict[str, Any]]]:
    """The campaigns Claude found, each a link on the page once, in the page's order, with their values."""
    found: dict[str, dict[str, Any]] = {}
    for item in answer.items[:ITEMS_MAX]:
        link = _link(item.url, page)
        if link is None or link in found:
            continue
        values = _values(item, at)
        if values is not None:
            found[link] = values
    return sorted(found.items(), key=lambda pair: page.links.index(pair[0]))


def _known(known: Brief, values: dict[str, Any]) -> BriefIn:
    """A brief ClipForge knows, with the list's figures over its own: its text, notes and what Claude filled
    are kept, and what it stated still counts as stated."""
    data = known.model_dump(include=set(BriefIn.model_fields))
    stated = [name for name, by in known.fields.items() if by == "stated"]
    return BriefIn(**(data | values), stated=list(dict.fromkeys([*stated, "title", "url", *values])))


def read_site(engine: Any, source: BriefSource) -> tuple[list[BriefIn], bool]:
    """The briefs on ``source``'s list page, in the page's order: each new one with its own page's text,
    each known one from the list alone. New briefs past the site's 40 pages for today are left for
    tomorrow, and a brief page that is gone, barred or sends ClipForge elsewhere leaves out its brief. With
    them, whether the list was read whole: not when its text or links were cut or Claude named as many
    briefs as it may, as more may be past the cut. Raises
    Stop: "error" without Claude (before anything is read) or when Claude fails, "changed" when the list
    shows no campaigns although this source has shown briefs before, and whatever reading the list raises
    (fetch.py); Cut, with the briefs found so far, for any other stop on a brief page."""
    provider = _provider(engine)
    if provider is None:
        raise Stop("error", NO_CLAUDE)
    url = source.list_url or ""
    polite = Polite(engine, url)
    page = polite.get(url, page=False)
    if len(page.links) < LINKS_MIN:
        page = scripted(engine, page.url, polite=polite)
    at = engine.now().timestamp()
    answer = _ask(engine, provider, page)
    listed = _listed(answer, page, at)
    whole = len(page.text) <= LIST_CHARS and len(page.links) < LINKS_MAX and len(answer.items) < ITEMS_MAX
    conn = engine.db()
    if not listed and store.briefs(conn, status=_HELD, source_id=source.id):
        raise Stop("changed", CHANGED)
    found: list[BriefIn] = []
    for link, values in listed:
        known = store.brief_by_key(conn, link, source.id, link)
        try:
            if known is not None:
                found.append(_known(known, values))
                continue
            if values.get("gone"):
                continue  # spent before ClipForge saw it, so its page isn't read
            if polite.full():
                continue  # read tomorrow, when the site has pages to spare
            text = polite.get(link, page=True).text[:TEXT_MAX]
            stated = ["title", "url", *values, *(["text"] if text else [])]
            found.append(
                BriefIn(
                    external_id=link,
                    url=link,
                    marketplace=source.marketplace,
                    text=text,
                    stated=stated,
                    **values,
                )
            )
        except ValidationError:
            log.warning("briefs: left out a brief at %s that doesn't fit", clean_text(link[:200]))
        except Stop as stop:
            if stop.result != "changed" and stop.message not in _PASSED:
                raise Cut(stop, found) from None
            log.warning("briefs: left out the brief at %s: %s", clean_text(link[:200]), stop.message)
    return found, whole
