"""Use this campaign (SPEC 10.11, 16): a brief fills the new campaign form through the same
check-the-fields-and-Save step as pasted rules (10.10). Claude reads the brief's text, when it has any, as
pasted rules are read; then the brief's own figures go over what Claude read, each marked to check. With a
rate or cap for each platform, the lower one is used, so an estimate never assumes the better platform,
and both go in the notes. The brief's page opens in your default browser when it is on a marketplace
ClipForge reads, so you can join there yourself. Nothing is saved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from clipforge.briefs import store
from clipforge.briefs.models import FIXED, Brief
from clipforge.briefs.worth import cap_for, openable, openable_link, rate_for
from clipforge.campaigns import links, parse
from clipforge.campaigns.models import web_url
from clipforge.campaigns.money import money_text
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import youtube_id
from clipforge.post.plan import NAMES
from clipforge.untrusted import clean_lines, clean_text
from clipforge.watch.sources import channel_link

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Platform

NAME_MAX = 80


@dataclass(frozen=True)
class UseResult:
    """The new campaign form as the brief fills it, the fields to check, and whether its page opened;
    ``sentence`` says why Claude couldn't read the brief's text, when it couldn't."""

    fields: dict[str, Any]
    filled: list[str]
    brief_id: str
    opened: bool
    sentence: str | None = None


def _name(brief: Brief) -> str:
    title = (clean_text(brief.title) or "").strip()
    brand = (clean_text(brief.brand) or "").strip()
    return (f"{brand}: {title}" if brand else title)[:NAME_MAX].strip()


def _lower(
    brief: Brief, figure: Callable[[Brief, Platform], float | None], label: str, unit: str
) -> tuple[float | None, str | None]:
    """The lowest of the brief's figures across its platforms, and a line for the notes naming each one
    when they differ."""
    found = {p: v for p in brief.platforms if (v := figure(brief, p)) is not None}
    if not found:
        return None, None
    low = min(found.values())
    if len(set(found.values())) == 1:
        return low, None
    each = ", ".join(f"{NAMES[p]} {money_text(v, brief.currency)}" for p, v in found.items())
    return low, f"{label}: {each}{unit}"


def _read(engine: Engine, text: str) -> parse.ParseResult:
    """The form from Claude's reading of the brief's text, or a blank one when it has none once cleaned.
    A brief's text is never longer than pasted rules may be (TEXT_MAX), so Claude is always asked."""
    if not (clean_lines(text) or "").strip():
        return parse.ParseResult(fields=parse.blank(""), filled=[])
    return parse.parse_rules(engine, text)


def use(engine: Engine, brief_id: str) -> UseResult:
    """The form filled from the brief, its page opened when ClipForge may open it. LookupError when there
    is no such brief; ClipForgeError (its status the code) for one that is a campaign already or has
    gone."""
    brief = store.get_brief(engine.db(), brief_id)
    if brief is None:
        raise LookupError(brief_id)
    if brief.status in FIXED:
        raise ClipForgeError(brief.status, FIXED[brief.status])
    read = _read(engine, brief.text)
    fields, filled = read.fields, list(read.filled)
    for key in ("campaign_url", "submit_url"):  # links a stranger's text gave Claude, kept only when openable
        if fields.get(key) is not None and not openable_link(engine, fields[key]):
            fields[key] = None
            filled = [name for name in filled if name != key]

    def put(key: str, value: Any) -> None:
        if value is not None and value != "":
            fields[key] = value
            filled.append(key)

    can_open = openable(engine, brief)
    put("name", _name(brief))
    put("marketplace", brief.marketplace)
    put("campaign_url", web_url(brief.url or "") if can_open else None)
    put("currency", brief.currency)
    rate, rates = _lower(brief, rate_for, "Rates", " per 1,000 views")
    cap, caps = _lower(brief, cap_for, "Most paid per clip", "")
    put("rate_per_1000", rate)
    put("max_payout", cap)
    put("min_views", brief.min_views)
    put("budget_left", brief.budget_left)
    put("ends_on", brief.ends_on)
    put("platforms", list(brief.platforms))
    footage = [(clean_text(link) or "").strip() for link in brief.footage.links]
    videos = list(dict.fromkeys(v for link in footage if (v := youtube_id(link))))
    channels = list(
        dict.fromkeys(c for link in footage if not youtube_id(link) and (c := channel_link(link)))
    )
    if videos:
        fields["footage"]["videos"] = videos[: parse.VIDEOS_MAX]
        filled.append("footage.videos")
    put("footage_links", channels[: parse.CHANNELS_MAX] or None)
    notes = [n for n in (fields["notes"], rates, caps) if n]
    put("notes", "\n\n".join(notes)[: parse.NOTES_MAX])
    if can_open:
        links.open_link(engine, f"{links.BRIEF}{brief.id}")
    return UseResult(fields, list(dict.fromkeys(filled)), brief.id, can_open, read.sentence)
