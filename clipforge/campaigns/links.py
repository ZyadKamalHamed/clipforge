"""Opening a campaign's links (SPEC 16, 17): only a link ClipForge stored, named by its id, never a URL from
the request, checked again as a web address before macOS ``open`` shows it in your default browser. The ids
are ``<campaign id>:page``, ``<campaign id>:submit``, ``withdraw:<marketplace>``, ``brief:<brief id>``, a
brief's page, which opens only on a marketplace ClipForge reads (10.11), ``sound:<sound id>``, a sound's page,
which opens only on its own platform's site (SPEC 13), ``list:<name>``, one of the weekly trending lists
ClipForge knows, and ``starter:<entry id>``, a starter gameplay video's YouTube page (SPEC 12)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from clipforge.briefs import store as brief_store
from clipforge.briefs import worth
from clipforge.campaigns import store
from clipforge.campaigns.models import web_url
from clipforge.errors import ClipForgeError
from clipforge.library import starter
from clipforge.trends import feed, instagram
from clipforge.trends import store as trends_store
from clipforge.trends.models import Sound

if TYPE_CHECKING:
    from clipforge.app import Engine

KINDS = {"page": "campaign_url", "submit": "submit_url"}
WITHDRAW = "withdraw:"
BRIEF = "brief:"
BAD_LINK = "That link isn't a web address ClipForge can open, so fix it on the campaign first."
BAD_BRIEF = "That brief's link isn't on a marketplace ClipForge reads, so it shows as text only."
SOUND = "sound:"
LIST = "list:"
STARTER = "starter:"
BAD_SOUND = "That sound's link isn't on TikTok or Instagram, so it shows as text only."
SOUND_HOSTS = {"shortlist": ("www.tiktok.com",), "ig_trending": instagram.PREVIEW_HOSTS}


def _sound_url(sound: Sound) -> str:
    """The sound's stored link when it is https on its own platform's site, with no port or user name."""
    try:
        url = web_url(sound.url or "")
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ClipForgeError("bad_link", BAD_SOUND) from None
    if parts.scheme != "https" or parts.hostname not in SOUND_HOSTS[sound.source] or port is not None:
        raise ClipForgeError("bad_link", BAD_SOUND)
    return url


def link_url(engine: Engine, link_id: str) -> str:
    """The stored link ``link_id`` names, checked again. LookupError when nothing is stored under it;
    ClipForgeError with the sentence when what is stored isn't a web address ClipForge may open."""
    if link_id.startswith(BRIEF):
        brief = brief_store.get_brief(engine.db(), link_id.removeprefix(BRIEF))
        if brief is None or not brief.url:
            raise LookupError(link_id)
        if not worth.openable(engine, brief):
            raise ClipForgeError("bad_link", BAD_BRIEF)
        return web_url(brief.url)
    if link_id.startswith(SOUND):
        sound = trends_store.get_sound(engine.db(), link_id.removeprefix(SOUND))
        if sound is None or not sound.url:
            raise LookupError(link_id)
        return _sound_url(sound)
    if link_id.startswith(LIST):
        listed = feed.LISTS.get(link_id.removeprefix(LIST))
        if listed is None:
            raise LookupError(link_id)
        return listed
    if link_id.startswith(STARTER):
        entry = starter.entry_of(link_id.removeprefix(STARTER))
        if entry is None:
            raise LookupError(link_id)
        return starter.url_of(entry)
    if link_id.startswith(WITHDRAW):
        name = link_id.removeprefix(WITHDRAW).casefold()
        places = {n.casefold(): p for n, p in engine.settings.marketplaces.items()}
        place = places.get(name)
        stored = place.withdraw_url if place is not None else None
    else:
        campaign_id, _, kind = link_id.rpartition(":")
        column = KINDS.get(kind)
        stored = store.stored_link(engine.db(), campaign_id, column) if campaign_id and column else None
    if not stored:
        raise LookupError(link_id)
    try:
        return web_url(stored)
    except ValueError:
        raise ClipForgeError("bad_link", BAD_LINK) from None


def open_link(engine: Engine, link_id: str) -> None:
    """The stored link opened in your default browser."""
    engine.run_cmd(["open", link_url(engine, link_id)])
