"""The clipping channels API (SPEC 10.9, 14.9 and 16): the clipping channels as their cards show them, made,
changed and removed with every refusal a sentence; the YouTube channels each one watches, added by a link;
Check now, which says when it can run again; and each check's history, with what became of every video."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from clipforge import repo
from clipforge.campaigns import service
from clipforge.errors import ClipForgeError
from clipforge.models import ProjectOptions
from clipforge.post import store as post_store
from clipforge.post.distribute import clean_hashtags
from clipforge.server.errors import INVALID, ApiError, Refusal
from clipforge.server.routes.projects import parse_options
from clipforge.server.views import channel_json, check_json, drafts_by_channel, item_json, source_json
from clipforge.untrusted import clean_text
from clipforge.watch import drafts, schedule, sources, store
from clipforge.watch.models import NAME_MAX, ChannelOptions, ClipChannel, OnLimit, RightsBasis

if TYPE_CHECKING:
    from clipforge.app import Engine

MAX_ACCOUNTS = 24
MAX_HASHTAGS = 10
NAME_NEEDED = "Give the clipping channel a name."
NAME_LONG = f"Keep the name to {NAME_MAX} characters."
CHECK_TIME = "Give the check time as hours and minutes, like 06:00."
VIDEO_LIMIT = "Clip from 1 to 10 new videos a day."
CLIPS_PER_DAY = "Draft from 1 to 50 clips a day, or leave it to what the accounts can post."
ACCOUNT_GONE = "One of those accounts isn't in ClipForge any more."
TOO_MANY_TAGS = f"Give at most {MAX_HASHTAGS} hashtags."
CAMPAIGN_GONE = "That campaign isn't in ClipForge any more."
LINK_FIELDS = {"not_a_channel": "link", "bad_backfill": "backfill"}  # the 422s; any other refusal is a 409
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")

AccountIds = Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=MAX_ACCOUNTS)]

router = APIRouter(prefix="/api")


class _Fields(BaseModel):
    """What a clipping channel's card sets. Checked here, so each refusal is the sentence for its field."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=1000)] | None = None
    account_ids: AccountIds | None = None
    options: dict[str, Any] | None = None  # {"project": options, "hashtags": [...]}, over the channel's own
    rights_basis: RightsBasis | None = None
    check_time: Annotated[str, Field(max_length=100)] | None = None
    daily_video_limit: int | None = None
    clips_per_day: int | None = None  # null: what the accounts can post
    on_limit: OnLimit | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str:
        name = (clean_text(value) or "").strip()
        if not name:
            raise Refusal(NAME_NEEDED)
        if len(name) > NAME_MAX:
            raise Refusal(NAME_LONG)
        return name

    @field_validator("check_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None and not _CLOCK.fullmatch(value):
            raise Refusal(CHECK_TIME)
        return value

    @field_validator("daily_video_limit")
    @classmethod
    def _video_limit(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 10:
            raise Refusal(VIDEO_LIMIT)
        return value

    @field_validator("clips_per_day")
    @classmethod
    def _clips_per_day(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 50:
            raise Refusal(CLIPS_PER_DAY)
        return value


class ChannelBody(_Fields):
    name: Annotated[str, Field(max_length=1000)]
    paused: bool | None = None  # Set it up's channel starts paused until you check it (10.11)


class ChannelChanges(_Fields):
    paused: bool | None = None
    campaign_id: Annotated[str, Field(max_length=64)] | None = None  # null unlinks; linked by its own step


class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link: Annotated[str, Field(max_length=2048)]
    backfill: int = 0  # how many of its newest uploads the next check clips, 0 to 5


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _channel(engine: Engine, channel_id: str) -> ClipChannel:
    channel = store.get_channel(engine.db(), channel_id)
    if channel is None:
        raise HTTPException(404)
    return channel


def _options(raw: dict[str, Any], base: ChannelOptions) -> ChannelOptions:
    """``raw`` over ``base``, a key at a time: the project's options over its own, the hashtags cleaned."""
    for key in raw:
        if key not in ChannelOptions.model_fields:
            raise ApiError(422, "invalid", INVALID, f"options.{key}")
    project, hashtags = base.project, base.hashtags
    if "project" in raw:
        try:
            project = (
                ProjectOptions() if raw["project"] is None else parse_options(raw["project"], base.project)
            )
        except ApiError as err:
            where = (err.field or "options").removeprefix("options")
            raise ApiError(422, "invalid", err.message, f"options.project{where}") from None
    if "hashtags" in raw:
        sent = raw["hashtags"] or []
        if not isinstance(sent, list) or not all(isinstance(t, str) and len(t) <= 100 for t in sent):
            raise ApiError(422, "invalid", INVALID, "options.hashtags")
        if len(sent) > MAX_HASHTAGS:
            raise ApiError(422, "invalid", TOO_MANY_TAGS, "options.hashtags")
        hashtags = clean_hashtags(clean_text(t) or "" for t in sent)
    return ChannelOptions(project=project, hashtags=hashtags)


def _changes(engine: Engine, body: _Fields, channel: ClipChannel | None) -> dict[str, Any]:
    """The fields ``body`` sends, checked, over the clipping channel's (or the defaults, for a new one). A
    field sent as null goes back to its default."""
    fields: dict[str, Any] = {}
    for name in body.model_fields_set - {"campaign_id"}:
        value = getattr(body, name)
        if value is None:
            value = ClipChannel.model_fields[name].get_default(call_default_factory=True)
        elif name == "account_ids":
            value = list(dict.fromkeys(value))
            if any(post_store.get_account(engine.db(), a) is None for a in value):
                raise ApiError(422, "invalid", ACCOUNT_GONE, "account_ids")
        elif name == "options":
            value = _options(value, channel.options if channel else ChannelOptions())
        fields[name] = value
    return fields


@router.get("/clip-channels")
def list_channels(request: Request) -> dict[str, Any]:
    """Every clipping channel, oldest first, as its card shows it."""
    engine = _engine(request)
    counts = drafts_by_channel(engine)
    return {"channels": [channel_json(engine, c, counts[c.id]) for c in store.list_channels(engine.db())]}


@router.post("/clip-channels", status_code=201)
def add_channel(body: ChannelBody, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    with engine.lock:
        fields = _changes(engine, body, None)
        now = time.time()
        channel = ClipChannel(id=repo.new_id("cc"), created_at=now, updated_at=now, **fields)
        store.add_channel(engine.db(), channel)
    engine.events.publish("clip_channel", {"id": channel.id})
    return {"channel": channel_json(engine, channel)}


@router.patch("/clip-channels/{channel_id}")
def change_channel(channel_id: str, body: ChannelChanges, request: Request) -> dict[str, Any]:
    """The card's edits, each checked as it is saved; a field left out stays as it was."""
    engine = _engine(request)
    with engine.lock:
        channel = _channel(engine, channel_id)
        fields = _changes(engine, body, channel)
        if fields:
            channel = store.update_channel(engine.db(), channel_id, **fields)
        if "campaign_id" in body.model_fields_set:
            try:
                channel = service.link_channel(engine, channel_id, body.campaign_id)
            except LookupError:
                raise ApiError(422, "invalid", CAMPAIGN_GONE, "campaign_id") from None
    if fields:
        engine.events.publish("clip_channel", {"id": channel_id})
    return {"channel": channel_json(engine, channel, drafts_by_channel(engine)[channel_id])}


@router.delete("/clip-channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, request: Request) -> Response:
    """The clipping channel removed with its sources and history. Its drafts are discarded, those hidden
    with a clip put aside too; its projects stay, as ordinary ones."""
    engine = _engine(request)
    with engine.lock:
        _channel(engine, channel_id)
        if mine := [p.id for p in drafts.of_channel(engine, channel_id)]:
            drafts.discard(engine, mine)
        store.delete_channel(engine.db(), channel_id)
    engine.events.publish("clip_channel", {"id": channel_id})
    return Response(status_code=204)


@router.post("/clip-channels/{channel_id}/sources", status_code=201)
def add_source(channel_id: str, body: SourceBody, request: Request) -> dict[str, Any]:
    """A YouTube channel to watch, by a link to it or to one of its videos."""
    engine = _engine(request)
    try:
        source = sources.add_source(engine, channel_id, body.link, body.backfill)
    except LookupError:
        raise HTTPException(404) from None
    except ClipForgeError as err:
        field = LINK_FIELDS.get(err.code)
        raise ApiError(422 if field else 409, err.code, err.message, field) from None
    return {"source": source_json(source)}


@router.delete("/clip-channels/{channel_id}/sources/{source_id}", status_code=204)
def delete_source(channel_id: str, source_id: str, request: Request) -> Response:
    engine = _engine(request)
    with engine.lock:
        source = store.get_source(engine.db(), source_id)
        if source is None or source.clip_channel_id != channel_id or source.is_footage:
            raise HTTPException(404)
        store.delete_source(engine.db(), source_id)
    engine.events.publish("clip_channel", {"id": channel_id})
    return Response(status_code=204)


@router.post("/clip-channels/{channel_id}/check", status_code=202)
def check_now(channel_id: str, request: Request) -> dict[str, Any]:
    """Check now: a check started (or the one still running), 429 with when it can run again, or 409 with
    why its campaign ended."""
    engine = _engine(request)
    try:
        check = schedule.check_now(engine, channel_id)
    except LookupError:
        raise HTTPException(404) from None
    except schedule.TooSoon as err:
        raise ApiError(429, err.code, err.message) from None
    except schedule.CampaignEnded as err:
        raise ApiError(409, err.code, err.message) from None
    return {"check": check_json(engine, check, store.items_of_check(engine.db(), check.id))}


@router.get("/clip-channels/{channel_id}/history")
def history(channel_id: str, request: Request) -> dict[str, Any]:
    """The clipping channel's last 30 checks, newest first, each with the videos it saw and why any was
    skipped."""
    engine = _engine(request)
    channel = _channel(engine, channel_id)
    conn = engine.db()
    checks = []
    for check in store.checks_for(conn, channel_id):
        items = store.items_of_check(conn, check.id)
        shown = [item_json(engine, i, channel) for i in items]
        checks.append(check_json(engine, check, items) | {"items": shown})
    return {"checks": checks}
