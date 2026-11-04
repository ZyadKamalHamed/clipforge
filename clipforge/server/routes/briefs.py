"""The briefs API (SPEC 10.11 and 16). The brief sources as Settings lists them; "Add a site", which takes a
public list page whose terms you checked; each source on or off; and Check now, at most once an hour per
source, one read at a time, never for a paste-only source. Then the briefs, the richest first: filtered by
niche, marketplace and status, each hidden or back, and the new ones marked seen once you've looked. And
Use this campaign, which fills the new campaign form from a brief. And the accounts worth adding, each
suggestion or its whole niche hidden for 30 days."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from clipforge import repo
from clipforge.briefs import advice, run, store
from clipforge.briefs.models import FIXED, Brief, BriefSource, Status
from clipforge.briefs.use import use
from clipforge.briefs.worth import SCOUT_HOSTS, brief_json, host_of
from clipforge.errors import ClipForgeError
from clipforge.server.errors import ApiError, Refusal
from clipforge.today.day import today_of
from clipforge.today.value import comparable
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

NAME_MAX = 60
MARKETPLACE_MAX = 40
NAME_NEEDED = "Give the site a name."
NAME_LONG = f"Keep the name to {NAME_MAX} characters."
LINK = "Give the site's public list page, a link that starts with https:// or http://."
TERMS = "Tick the box once you've checked that this site's terms allow reading its list."
TAKEN = "{name} already reads that site."
PASTE_ONLY = "ClipForge doesn't read this marketplace's pages, so paste its briefs instead."
TERMS_NOTE = "You checked this site's terms."
IDS_MAX = 1000
_LOCAL = ("localhost", "localhost.localdomain")
_LIVE: list[Status] = ["new", "seen"]

router = APIRouter(prefix="/api")


def _public(host: str) -> bool:
    """Whether ``host`` may be a marketplace: not this Mac or an address on a private network."""
    if host in _LOCAL or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        return ipaddress.ip_address(host.strip("[]")).is_global
    except ValueError:
        return True  # a name, not an address


class SiteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=200)]
    list_url: Annotated[str, Field(max_length=2000)]
    terms_read: Annotated[bool, Field(validate_default=True)] = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = (clean_text(value) or "").strip()
        if not name:
            raise Refusal(NAME_NEEDED)
        if len(name) > NAME_MAX:
            raise Refusal(NAME_LONG)
        return name

    @field_validator("list_url")
    @classmethod
    def _link(cls, value: str) -> str:
        link = value.strip()
        try:
            parts = urlsplit(link)
            host = parts.hostname
            parts.port  # noqa: B018 - a bad port raises here
        except ValueError:
            raise Refusal(LINK) from None
        if parts.scheme not in ("https", "http") or not host or parts.username or parts.password:
            raise Refusal(LINK)
        if not _public(host):
            raise Refusal(LINK)
        return link

    @field_validator("terms_read")
    @classmethod
    def _terms(cls, value: bool) -> bool:
        if value is not True:
            raise Refusal(TERMS)
        return value


class SourceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class BriefChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["seen", "hidden"]


class Hide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["suggestion", "niche"]
    niche: Annotated[str, Field(min_length=1, max_length=40)]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,8}$")]


class Looked(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=IDS_MAX)]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def source_json(engine: Engine, source: BriefSource) -> dict[str, Any]:
    """A source as Settings shows it: its row, when Check now opens again (None when open now), whether it is
    being read and whether its briefs are to be pasted today."""
    opens = run.check_now_after(engine, source)
    return source.model_dump() | {
        "check_now_after": opens.timestamp() if opens else None,
        "reading": run.reading() == source.id,
        "paste": run.paste_today(engine, source),
    }


def _source(engine: Engine, source_id: str) -> BriefSource:
    source = store.get_source(engine.db(), source_id)
    if source is None:
        raise HTTPException(404)
    return source


@router.get("/brief-sources")
def list_sources(request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return {"sources": [source_json(engine, s) for s in store.sources(engine.db())]}


@router.post("/brief-sources", status_code=201)
def add_site(body: SiteBody, request: Request) -> dict[str, Any]:
    """Add a site: 201 with the new source, read by the general reader from the next morning or a Check now;
    422 with the source that already reads its host, or when the host is (or is under) a paste-only
    marketplace or one Campaign Scout's file covers, whose terms forbid automated reading."""
    engine = _engine(request)
    host = host_of(body.list_url)
    with engine.lock:
        conn = engine.db()
        sources = store.sources(conn)
        paste = {h for s in sources if s.reader == "paste" and (h := host_of(s.list_url))} | set(SCOUT_HOSTS)
        if host is not None and any(host == h or host.endswith(f".{h}") for h in paste):
            raise ApiError(422, "paste_only", PASTE_ONLY, "list_url")
        for other in sources:
            if host is not None and host_of(other.list_url) == host:
                raise ApiError(422, "taken", TAKEN.format(name=other.name), "list_url")
        now = time.time()
        source = store.add_source(
            conn,
            BriefSource(
                id=repo.new_id("bs"),
                name=body.name,
                marketplace=body.name[:MARKETPLACE_MAX].strip(),
                list_url=body.list_url,
                reader="general",
                terms_checked_on=today_of(engine).isoformat(),
                terms_note=TERMS_NOTE,
                created_at=now,
                updated_at=now,
            ),
        )
    return {"source": source_json(engine, source)}


@router.patch("/brief-sources/{source_id}")
def change_source(source_id: str, body: SourceChange, request: Request) -> dict[str, Any]:
    """On or off: 200 with the source, 404 when there is no such source."""
    engine = _engine(request)
    _source(engine, source_id)
    with engine.lock:
        source = store.update_source(engine.db(), source_id, enabled=body.enabled)
    return {"source": source_json(engine, source)}


@router.post("/brief-sources/{source_id}/check", status_code=202)
def check_now(source_id: str, request: Request) -> dict[str, Any]:
    """Check now: 202 with the source while its read runs in the background; 422 for a paste-only source,
    429 with when it can run again and 409 while another read runs."""
    engine = _engine(request)
    try:
        run.check_now(engine, source_id)
    except LookupError:
        raise HTTPException(404) from None
    except run.PasteOnly as err:
        raise ApiError(422, err.code, err.message) from None
    except run.TooSoon as err:
        raise ApiError(429, err.code, err.message) from None
    except run.Busy as err:
        raise ApiError(409, err.code, err.message) from None
    return {"source": source_json(engine, _source(engine, source_id))}


def _richest_first(engine: Engine, shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reposting briefs, then the rest, each by what they are worth in US$ (through your exchange
    rates), highest first, those whose worth isn't known last; ties keep the order they were first seen in."""

    def key(item: dict[str, Any]) -> tuple[bool, int, float]:
        value = item["worth"]
        if value["amount"] is None:
            return (not item["reposting"], 1, 0.0)
        return (not item["reposting"], 0, -comparable(engine.settings, value["amount"], value["currency"])[0])

    return sorted(shown, key=key)


def _brief(engine: Engine, brief_id: str) -> Brief:
    brief = store.get_brief(engine.db(), brief_id)
    if brief is None:
        raise HTTPException(404)
    return brief


@router.get("/briefs")
def list_briefs(
    request: Request,
    niche: str | None = None,
    marketplace: str | None = None,
    status: Annotated[list[Status] | None, Query()] = None,
) -> dict[str, Any]:
    """The briefs, new and seen unless ``status`` names others, the reposting ones then the richest first."""
    engine = _engine(request)
    found = store.briefs(engine.db(), status=status or _LIVE, niche=niche, marketplace=marketplace)
    return {"briefs": _richest_first(engine, [brief_json(engine, b) for b in found])}


@router.patch("/briefs/{brief_id}")
def change_brief(brief_id: str, body: BriefChange, request: Request) -> dict[str, Any]:
    """Hidden, or seen (which also brings a hidden brief back): 200 with the brief, 404 when there is no
    such brief and 409 for one that became a campaign or has gone."""
    engine = _engine(request)
    with engine.lock:
        brief = _brief(engine, brief_id)
        if brief.status in FIXED:
            raise ApiError(409, brief.status, FIXED[brief.status])
        brief = store.update_brief(engine.db(), brief_id, status=body.status)
    engine.events.publish("brief", {"ids": [brief_id], "status": body.status})
    return {"brief": brief_json(engine, brief)}


@router.post("/briefs/seen")
def mark_seen(body: Looked, request: Request) -> dict[str, Any]:
    """I've looked: each of the new briefs named becomes seen. 200 with the ids that changed."""
    engine = _engine(request)
    seen: list[str] = []
    with engine.lock:
        conn = engine.db()
        for brief_id in dict.fromkeys(body.ids):
            brief = store.get_brief(conn, brief_id)
            if brief is not None and brief.status == "new":
                store.update_brief(conn, brief_id, status="seen")
                seen.append(brief_id)
    if seen:
        engine.events.publish("brief", {"ids": seen, "status": "seen"})
    return {"seen": seen}


@router.post("/briefs/{brief_id}/use")
def use_brief(brief_id: str, request: Request) -> dict[str, Any]:
    """Use this campaign: 200 with the new campaign form as the brief fills it, saving nothing, and the
    brief's page opened in your default browser when it is on a marketplace ClipForge reads; 404 when
    there is no such brief and 409 for one that became a campaign or has gone."""
    try:
        result = use(_engine(request), brief_id)
    except LookupError:
        raise HTTPException(404) from None
    except ClipForgeError as err:
        raise ApiError(409, err.code, err.message) from None
    return asdict(result)


def _advice(engine: Engine) -> dict[str, Any]:
    return asdict(advice.advice(engine, today_of(engine)))


@router.get("/advice")
def accounts_worth_adding(request: Request) -> dict[str, Any]:
    """The accounts worth adding: up to 3 suggestions and the accounts that could take a brief today."""
    return {"advice": _advice(_engine(request))}


@router.post("/advice/hide")
def hide_advice(body: Hide, request: Request) -> dict[str, Any]:
    """Not now (a suggestion) or Not this niche: hidden for 30 days. 200 with the advice as it is now."""
    engine = _engine(request)
    until = today_of(engine) + timedelta(days=advice.HIDE_DAYS)
    with engine.lock:
        store.hide_advice(engine.db(), body.kind, body.niche, body.language, until.isoformat())
    engine.events.publish("brief", {"advice": "hidden"})
    return {"advice": _advice(engine)}
