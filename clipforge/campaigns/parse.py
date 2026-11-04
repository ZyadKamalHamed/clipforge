"""Reading a campaign's pasted rules into the fields of a campaign profile (SPEC 10.10). Claude reads the
text as untrusted data (fenced, no tools, the answer schema-checked, 17) and fills in what the text states;
each value is then checked against the field it fills, and anything that doesn't fit goes into notes.
Nothing is fetched and nothing is saved: Zyad checks the fields and saves them."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from clipforge.brain.context import load_prompt
from clipforge.brain.llm.base import LLMRequest, call_structured
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.transcript_view import wrap_untrusted
from clipforge.campaigns.models import Banned, Campaign, Platform, Required
from clipforge.config import model_for
from clipforge.errors import ClipForgeError, LLMError
from clipforge.ingest.urls import youtube_id
from clipforge.post.distribute import clean_hashtags
from clipforge.untrusted import clean_lines, clean_text
from clipforge.watch.sources import channel_link

log = logging.getLogger(__name__)

MAX_RULES_CHARS = 20_000
PASS = "campaign"
MODEL_PASS = "package"  # the campaign pass uses the package pass's model, so Settings needs no new row
EMPTY = "Paste the campaign's rules first."
TOO_LONG = "That's more than 20,000 characters, so paste just the campaign's rules."
NO_CLAUDE = (
    "Claude couldn't read the rules just now, so fill in the fields yourself; "
    "the text you pasted is kept with the campaign."
)
NOT_PLACED = "Claude read these but they don't fit a field:"
NEITHER = "The campaign pays for neither TikTok nor Instagram posts."
NOTES_MAX = 4000
VIDEOS_MAX, CHANNELS_MAX = 50, 20  # what Footage holds

# A campaign the checks validate one field against: the required fields filled with stand-ins
_STAND_IN = {"id": "c", "name": "c", "marketplace": "c", "created_at": 0.0, "updated_at": 0.0}
# How each field Claude fills reads in notes, when its value doesn't fit
LABELS = {
    "name": "Name",
    "marketplace": "Marketplace",
    "campaign_url": "Campaign page",
    "submit_url": "Submit page",
    "currency": "Currency",
    "rate_per_1000": "Rate per 1,000 views",
    "min_views": "Minimum views",
    "max_payout": "Most paid per clip",
    "budget_left": "Budget left",
    "min_s": "Shortest clip in seconds",
    "max_s": "Longest clip in seconds",
    "ends_on": "End date",
    "hashtags": "Hashtag",
    "mentions": "Mention",
    "caption_text": "Caption text",
    "hand_rules": "Rule",
    "footage_links": "Footage link",
}
SINGLE = ("name", "marketplace", "campaign_url", "submit_url", "currency", "rate_per_1000", "min_views")
SINGLE += ("max_payout", "budget_left", "min_s", "max_s", "ends_on")


Misfit = Callable[[str, object], None]


class RulesAnswer(BaseModel):
    """Claude's reading of the rules: null for anything the text doesn't state."""

    name: str | None = None
    marketplace: str | None = None
    campaign_url: str | None = None
    submit_url: str | None = None
    currency: str | None = None
    rate_per_1000: float | None = None
    min_views: int | None = None
    max_payout: float | None = None
    budget_left: float | None = None
    platforms: list[Platform] | None = None
    footage_links: list[str] | None = None
    footage_files: bool | None = None
    hashtags: list[str] | None = None
    mentions: list[str] | None = None
    caption_text: list[str] | None = None
    banned: list[Banned] | None = None
    hand_rules: list[str] | None = None
    min_s: float | None = None
    max_s: float | None = None
    ends_on: str | None = None
    notes: str | None = None


class ParseResult(BaseModel):
    """The form's fields (every campaign field but its id, status and times, with the footage channels as
    links not yet looked up under ``footage_links``), the names of those Claude filled, and a sentence when
    Claude couldn't read the rules."""

    fields: dict[str, Any]
    filled: list[str]
    sentence: str | None = None


def blank(text: str) -> dict[str, Any]:
    """The form with nothing filled in but the rules text."""
    return {
        "name": "",
        "marketplace": "",
        "campaign_url": None,
        "submit_url": None,
        "currency": "USD",
        "rate_per_1000": None,
        "min_views": None,
        "max_payout": None,
        "platforms": ["tiktok", "instagram"],
        "footage": {"videos": [], "files": False},
        "footage_links": [],
        "required": {"hashtags": [], "mentions": [], "text": []},
        "banned": [],
        "hand_rules": [],
        "min_s": None,
        "max_s": None,
        "ends_on": None,
        "budget_left": None,
        "notes": "",
        "rules_text": text,
    }


def parse_rules(engine: Any, text: str) -> ParseResult:
    """The form filled in from the pasted ``text`` as far as Claude can read it. ClipForgeError for an
    empty paste or one over MAX_RULES_CHARS, before Claude is asked anything."""
    pasted = clean_lines(text.strip()) or ""
    if not pasted:
        raise ClipForgeError("rules_empty", EMPTY)
    if len(pasted) > MAX_RULES_CHARS:
        raise ClipForgeError("rules_too_long", TOO_LONG)
    answer = _ask(engine, pasted)
    if answer is None:
        return ParseResult(fields=blank(pasted), filled=[], sentence=NO_CLAUDE)
    fields, filled = _place(answer, pasted)
    return ParseResult(fields=fields, filled=filled)


def _ask(engine: Any, text: str) -> RulesAnswer | None:
    """Claude's reading of ``text``, or None when there is no Claude or it gave no answer that fits."""
    settings = engine.settings
    name = settings.llm_provider
    try:
        provider = engine.provider_factory(settings, name)
    except LLMError as err:  # a missing API key, say: the fields are left to fill by hand
        log.warning("campaign: no %s provider (%s)", name, err.kind)
        return None
    if provider is None:
        return None
    today = engine.now().astimezone(ZoneInfo(settings.posting.timezone)).date().isoformat()
    req = LLMRequest(
        pass_name=PASS,
        system=load_prompt("campaign"),
        prompt=f"Today is {today}.\n\nThe campaign's rules, as pasted:\n\n{wrap_untrusted('rules', text)}",
        schema=schema_for(RulesAnswer),
        model=model_for(settings, name, MODEL_PASS),
        timeout_s=settings.llm_timeout_s,
    )
    try:
        return call_structured(provider, req, RulesAnswer)
    except LLMError as err:
        log.warning("campaign: Claude couldn't read the rules (%s)", err.kind)
        return None


def _place(answer: RulesAnswer, text: str) -> tuple[dict[str, Any], list[str]]:
    """The form with each of Claude's values in its field when it fits there, and the names filled."""
    fields = blank(text)
    filled: list[str] = []
    misfits: list[str] = []

    def misfit(key: str, value: object) -> None:
        shown = f"{value:g}" if isinstance(value, float) else str(value)
        misfits.append(f"{LABELS[key]}: {clean_text(shown)}")

    for key in SINGLE:
        value = getattr(answer, key)
        if isinstance(value, str):
            value = (clean_text(value) or "").strip()
        if value is None or value == "":
            continue
        try:
            fields[key] = getattr(Campaign.model_validate(_STAND_IN | {key: value}), key)
            filled.append(key)
        except ValidationError:
            misfit(key, value)
    if fields["min_s"] is not None and fields["max_s"] is not None and fields["min_s"] > fields["max_s"]:
        for key in ("min_s", "max_s"):  # the shortest can't be longer than the longest: neither is kept
            misfit(key, fields[key])
            fields[key] = None
            filled.remove(key)

    if answer.platforms is not None:
        if answer.platforms:
            fields["platforms"] = list(dict.fromkeys(answer.platforms))
            filled.append("platforms")
        else:
            misfits.append(NEITHER)
    if answer.banned:
        fields["banned"] = list(dict.fromkeys(answer.banned))
        filled.append("banned")

    for key, out, items in (
        ("hashtags", "hashtags", clean_hashtags(answer.hashtags or [])),
        ("mentions", "mentions", mentions(answer.mentions or [])),
        ("caption_text", "text", lines(answer.caption_text or [])),
    ):
        kept = _keep(items, lambda values, out=out: Required.model_validate({out: values}), misfit, key)
        if kept:
            fields["required"][out] = kept
            filled.append(f"required.{out}")
    rules = lines(answer.hand_rules or [])
    hand = _keep(
        rules,
        lambda values: Campaign.model_validate(_STAND_IN | {"hand_rules": values}),
        misfit,
        "hand_rules",
    )
    if hand:
        fields["hand_rules"] = hand
        filled.append("hand_rules")

    _footage(answer, fields, filled, misfit)
    notes = [clean_lines(answer.notes or "").strip()] if (answer.notes or "").strip() else []
    if misfits:
        notes.append("\n".join([NOT_PLACED, *misfits]))
    if notes:
        fields["notes"] = "\n\n".join(notes)[:NOTES_MAX]
        filled.append("notes")
    return fields, filled


def _keep(items: list[str], check: Callable[[list[str]], object], misfit: Misfit, key: str) -> list[str]:
    """The items kept in order while ``check`` accepts the list they make (each item's own rule and the
    field's count); the others go to ``misfit``."""
    kept: list[str] = []
    for item in items:
        try:
            check([*kept, item])
        except ValidationError:
            misfit(key, item)
            continue
        kept.append(item)
    return kept


def mentions(names: list[str]) -> list[str]:
    """Each name as one "@name", once each."""
    out: dict[str, str] = {}
    for name in names:
        word = "".join((clean_text(name) or "").split())
        if word and not word.startswith("@"):
            word = f"@{word}"
        if word:
            out.setdefault(word.casefold(), word)
    return list(out.values())


def lines(items: list[str]) -> list[str]:
    """Each item as one clean line, empty ones left out."""
    return [line for item in items if (line := " ".join((clean_text(item) or "").split()))]


def _footage(answer: RulesAnswer, fields: dict[str, Any], filled: list[str], misfit: Misfit) -> None:
    """The footage links Claude found: videos by their id, channels as their videos tab, anything else to
    notes, within the campaign's limits."""
    videos: list[str] = []
    channels: list[str] = []
    for link in answer.footage_links or []:
        text = (clean_text(link) or "").strip()
        if not text:
            continue
        video, channel = youtube_id(text), channel_link(text)
        if video and (video in videos or len(videos) < VIDEOS_MAX):
            if video not in videos:
                videos.append(video)
        elif channel and (channel in channels or len(channels) < CHANNELS_MAX):
            if channel not in channels:
                channels.append(channel)
        else:
            misfit("footage_links", text)
    if videos:
        fields["footage"]["videos"] = videos
        filled.append("footage.videos")
    if answer.footage_files is not None:
        fields["footage"]["files"] = answer.footage_files
        filled.append("footage.files")
    if channels:
        fields["footage_links"] = channels
        filled.append("footage_links")
