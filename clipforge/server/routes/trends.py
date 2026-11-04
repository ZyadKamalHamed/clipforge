"""The Trends API (SPEC 13 and 16): the Trends page's view of every list, Check now for one account's
trending sounds, the TikTok shortlist and the hashtag sets made, changed and deleted with each refusal a
sentence for its field, and the weekly lists marked seen. Every write sends the ``trends`` event."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from clipforge.campaigns.links import LIST
from clipforge.db import transaction
from clipforge.edits import EditError
from clipforge.post import store as post_store
from clipforge.repo import new_id
from clipforge.server.errors import ApiError
from clipforge.server.views import set_json, sound_json
from clipforge.trends import feed, hashtags, instagram, shortlist, store
from clipforge.trends.models import HashtagSet

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.post.models import Account

# Each past the field's own limit, so a long paste gets its sentence rather than a bare "invalid"
LINK_MAX = 5 * shortlist.LINK_CHARS
TITLE_MAX = 5 * shortlist.TITLE_CHARS
TEXT_MAX = 1000
TAG_MAX = 200
TAGS_MAX = 5 * hashtags.MAX_TAGS

Title = Annotated[str, Field(max_length=TITLE_MAX)]
Text = Annotated[str, Field(max_length=TEXT_MAX)]
Tags = Annotated[list[Annotated[str, Field(max_length=TAG_MAX)]], Field(max_length=TAGS_MAX)]

router = APIRouter(prefix="/api")


class SoundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link: Annotated[str, Field(max_length=LINK_MAX)]
    title: Title | None = None
    region: Any = None  # the shortlist checks these three and names the one that is wrong
    moods: Any = None
    energy: Any = None


class SoundChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    region: Any = None
    moods: Any = None
    energy: Any = None


class SetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niche: Text
    name: Text
    tags: Tags


class SetChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niche: Text | None = None
    name: Text | None = None
    tags: Tags | None = None


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _refused(err: EditError) -> ApiError:
    return ApiError(422, "invalid", err.message, err.field)


def _changed(engine: Engine, **data: Any) -> None:
    engine.events.publish("trends", data)


def _route(account: Account) -> tuple[str, str, str | None]:
    return account.platform, account.method, account.ig_mode


def _block(engine: Engine, account: Account) -> dict[str, Any]:
    """An account's block on the Trends page: its last read, its trending sounds and whether Check now can
    read them (its token is in the Keychain)."""
    conn = engine.db()
    now = engine.now().timestamp()
    read = store.read_of(conn, instagram.read_id(account.id))
    listed = store.sounds(conn, source="ig_trending", account_id=account.id)
    return {
        "account": {"id": account.id, "handle": account.handle},
        "read": read.model_dump(mode="json") if read else None,
        "sounds": [sound_json(s, now) for s in listed],
        "can_check": instagram.can_read(account),
    }


@router.get("/trends")
def trends_view(request: Request) -> dict[str, Any]:
    """The Trends page: a block per Instagram account linked through Facebook, oldest first; the shortlist,
    newest first; the four weekly lists with the id Open takes, and whether one is newer than your last
    look; the hashtag sets; and the handles of the accounts linked through Instagram Login, which Instagram
    gives no list."""
    engine = _engine(request)
    conn = engine.db()
    now = engine.now().timestamp()
    accounts = post_store.list_accounts(conn)
    return {
        "instagram": [_block(engine, a) for a in accounts if _route(a) == ("instagram", "api", "facebook")],
        "shortlist": [sound_json(s, now) for s in store.sounds(conn, source="shortlist")],
        "lists": [
            {"name": name, "label": feed.LABELS[name], "link_id": f"{LIST}{name}"} for name in feed.LISTS
        ],
        "badge": feed.badge(engine),
        "sets": [set_json(s) for s in store.sets(conn)],
        "mode_b": [a.handle for a in accounts if _route(a) == ("instagram", "api", "instagram")],
    }


@router.post("/trends/instagram/{account_id}/check")
def check_now(account_id: str, request: Request) -> dict[str, Any]:
    """Reads the account's trending sounds now, as the morning's read does, and gives its block; 409 with
    the sentence while another read runs or when Instagram gives the account no list."""
    engine = _engine(request)
    account = post_store.get_account(engine.db(), account_id)
    if account is None:
        raise HTTPException(404)
    instagram.read_account(engine, account_id)
    return _block(engine, account)


@router.post("/trends/shortlist", status_code=201)
def add_sound(body: SoundBody, request: Request) -> dict[str, Any]:
    """The sound the pasted link is for, put on the shortlist, or written over when it is there already."""
    engine = _engine(request)
    try:
        sound = shortlist.add(
            engine, body.link, title=body.title, region=body.region, moods=body.moods, energy=body.energy
        )
    except EditError as err:
        raise _refused(err) from None
    _changed(engine, shortlist=sound.id)
    return {"sound": sound_json(sound, engine.now().timestamp())}


@router.patch("/trends/shortlist/{sound_id}")
def change_sound(sound_id: str, body: SoundChanges, request: Request) -> dict[str, Any]:
    """The fields sent changed (null clears a region or an energy); a field left out stays as it was."""
    engine = _engine(request)
    try:
        sound = shortlist.update(engine, sound_id, **{f: getattr(body, f) for f in body.model_fields_set})
    except LookupError:
        raise HTTPException(404) from None
    except EditError as err:
        raise _refused(err) from None
    _changed(engine, shortlist=sound.id)
    return {"sound": sound_json(sound, engine.now().timestamp())}


@router.delete("/trends/shortlist/{sound_id}", status_code=204)
def remove_sound(sound_id: str, request: Request) -> Response:
    engine = _engine(request)
    try:
        shortlist.remove(engine, sound_id)
    except LookupError:
        raise HTTPException(404) from None
    _changed(engine, shortlist=sound_id)
    return Response(status_code=204)


@router.post("/trends/lists/seen", status_code=204)
def lists_seen(request: Request) -> Response:
    """Notes that you opened a weekly list, which puts the badge out until the feed shows a newer post."""
    engine = _engine(request)
    feed.mark_seen(engine)
    _changed(engine, lists="seen")
    return Response(status_code=204)


@router.post("/trends/sets", status_code=201)
def add_set(body: SetBody, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    try:
        niche, name, tags = hashtags.clean_set(body.niche, body.name, body.tags)
    except EditError as err:
        raise _refused(err) from None
    now = engine.now().timestamp()
    made = HashtagSet(id=new_id("hs"), niche=niche, name=name, tags=tags, created_at=now, updated_at=now)
    conn = engine.db()
    with transaction(conn):
        saved = store.put_set(conn, made)
    _changed(engine, set=saved.id)
    return {"set": set_json(saved)}


@router.patch("/trends/sets/{set_id}")
def change_set(set_id: str, body: SetChanges, request: Request) -> dict[str, Any]:
    """The fields sent changed and the set checked again as a whole; a field left out stays as it was."""
    engine = _engine(request)
    conn = engine.db()
    with transaction(conn):
        found = store.get_set(conn, set_id)
        if found is None:
            raise HTTPException(404)
        kept = found.model_dump(include={"niche", "name", "tags"})
        fields = kept | {f: getattr(body, f) for f in body.model_fields_set}
        try:
            niche, name, tags = hashtags.clean_set(
                fields["niche"] or "", fields["name"] or "", fields["tags"] or []
            )
        except EditError as err:
            raise _refused(err) from None
        changed = {"niche": niche, "name": name, "tags": tags, "updated_at": engine.now().timestamp()}
        saved = store.put_set(conn, found.model_copy(update=changed))
    _changed(engine, set=saved.id)
    return {"set": set_json(saved)}


@router.delete("/trends/sets/{set_id}", status_code=204)
def delete_set(set_id: str, request: Request) -> Response:
    engine = _engine(request)
    if not store.delete_set(engine.db(), set_id):
        raise HTTPException(404)
    _changed(engine, set=set_id)
    return Response(status_code=204)
