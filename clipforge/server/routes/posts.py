"""The posting API (SPEC 10, 14.5, 14.6 and 16): a plan to show, which changes nothing; Confirm and post,
which checks the plan again and makes one batch however often it is pressed; the Queue's posts and their
buttons (edit, move, cancel, Mark as posted, Try again, Export, Publish anyway, the log); opening an exported
post's upload page; and pausing posting and bringing the automation window to the front."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.errors import ClipForgeError
from clipforge.post import store
from clipforge.post.errors import LifecycleError, PlanChanged
from clipforge.post.export import open_upload as put_up
from clipforge.post.lifecycle import WAITING
from clipforge.post.models import POST_STATUSES, PlanRequest, Post
from clipforge.post.plan import NAMES
from clipforge.server.errors import INVALID, ApiError
from clipforge.server.views import (
    cleaned,
    media_url,
    plan_json,
    post_json,
    submission_json,
    waits_for_you,
    web_link,
)

if TYPE_CHECKING:
    from clipforge.app import Engine

LOG_LINES = 500  # the last steps of a post's log, which is plenty to see where it stopped
SHOT = re.compile(r"[0-9]{2}-[A-Za-z0-9_-]+\.png")  # the screenshots a post's log keeps beside its steps
STATUS_WORDS = {
    "scheduled": "scheduled",
    "rendering": "rendering",
    "uploading": "uploading now",
    "processing": "being processed by the site",
    "scheduled_on_tiktok": "scheduled on TikTok",
    "needs_you": "waiting for you",
    "failed": "failed",
    "posted": "already posted",
    "cancelled": "cancelled",
    "exported": "exported",
    "draft": "a draft waiting for review",
    "discarded": "a discarded draft",
}
NOTHING = "There was nothing to change."
STATUS_ALONE = "Change a post's status on its own."
URL_WITH_POSTED = "A link only goes with marking a post as posted."
NOT_A_LINK = "That isn't a web link. Paste the post's address from the site."
NO_STATUS = "That isn't a post status."
NO_PLATFORM = "That isn't a platform ClipForge posts to."
NOT_HELD = "Only a post Instagram held for someone else's material can be published anyway."
CHANGED = "These drafts changed. Close this and try again."
QUEUE = [s for s in POST_STATUSES if s not in ("draft", "discarded")]  # drafts wait on Today instead

router = APIRouter(prefix="/api")


class PlanBody(PlanRequest):
    model_config = ConfigDict(extra="forbid")

    clip_ids: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=100)] = []
    account_ids: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=24)] = []
    draft_ids: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=100)] = []


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Annotated[str, Field(max_length=64)]
    request: PlanBody


class PostChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: Annotated[str, Field(max_length=10000)] | None = None  # the platform's limit is checked after
    hashtags: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=60)] | None = None
    scheduled_at: Annotated[float, Field(allow_inf_nan=False)] | None = None
    status: Literal["cancelled", "posted"] | None = None
    url: Annotated[str, Field(max_length=2048)] | None = None
    rule_ticks: Annotated[list[Annotated[str, Field(max_length=20)]], Field(max_length=20)] | None = None
    off_campaign: bool | None = None  # a draft unlinked from its campaign, or linked again
    drop_sound: bool | None = None  # the sound picked for it taken off


class RetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drop_sound: bool = False  # Post without the sound


class Views(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: Any  # checked as a whole number from 0 to 10 billion, with its own sentence


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _post(engine: Engine, post_id: str) -> Post:
    post = store.get_post(engine.db(), post_id)
    if post is None:
        raise HTTPException(404)
    return post


def _wrong(engine: Engine, post_id: str, doing: str) -> ApiError:
    """The 409 for a button the post's status has gone past, in the post's own words. One TikTok Studio was
    given, waiting for you, says that instead: uploading it again would put it up twice."""
    post = _post(engine, post_id)
    words = STATUS_WORDS[post.status]
    if post.status == "needs_you" and post.state.get("on_tiktok"):
        words = "already on TikTok Studio"
    return ApiError(409, "wrong_status", f"This post is {words}, so it can't be {doing}.")


def _checked(engine: Engine, body: PlanBody) -> None:
    """Fresh posts need clips and accounts; a review of drafts may name only the drafts' own, so a dialog
    left open while they changed says so instead of planning something else."""
    if not body.draft_ids:
        for name in ("clip_ids", "account_ids"):
            if not getattr(body, name):
                raise ApiError(422, "invalid", INVALID, name)
        return
    conn = engine.db()
    posts = [p for p in (store.get_post(conn, i) for i in body.draft_ids) if p is not None]
    clips, accounts = {p.clip_id for p in posts}, {p.account_id for p in posts}
    if not set(body.clip_ids) <= clips or not set(body.account_ids) <= accounts:
        raise ApiError(422, "invalid", CHANGED, "draft_ids")


def _gone(err: LookupError) -> ApiError:
    return ApiError(
        409, "gone", str(err.args[0]) if err.args else "A clip or an account isn't there any more."
    )


@router.post("/posts/plan")
def plan(body: PlanBody, request: Request) -> dict[str, Any]:
    """The posts the Post dialog would make, with their times and warnings. Nothing is written."""
    engine = _engine(request)
    _checked(engine, body)
    try:
        return plan_json(engine, engine.posting.plan(body))
    except LookupError as err:
        raise _gone(err) from None


@router.post("/posts/confirm", status_code=201)
def confirm(body: ConfirmBody, request: Request, response: Response) -> Any:
    """The plan shown made into a batch of scheduled posts (201), or the batch it already made (200). A
    plan that changed since it was shown comes back to be checked again (409)."""
    engine = _engine(request)
    _checked(engine, body.request)
    if store.batch_for_plan(engine.db(), body.plan_id) is not None:
        response.status_code = 200
    try:
        batch_id, posts = engine.posting.confirm(body.request, body.plan_id)
    except PlanChanged as err:
        out = {
            "error": "plan_changed",
            "message": str(err),
            "field": None,
            "plan": plan_json(engine, err.plan),
        }
        return JSONResponse(out, status_code=409)
    except LookupError as err:
        raise _gone(err) from None
    return {"batch_id": batch_id, "posts": [post_json(engine, p) for p in posts]}


@router.get("/posts")
def list_posts(
    request: Request,
    status: str | None = None,
    account_id: str | None = None,
    platform: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """The posts in time order, narrowed by status (a comma list; without one, every post but the drafts),
    account, platform or batch, and how many of all the posts wait for the person: Needs you, failed and not
    yet dealt with, or waiting in its Chrome window for the person's click."""
    engine = _engine(request)
    statuses = QUEUE
    if status is not None:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if not statuses or any(s not in POST_STATUSES for s in statuses):
            raise ApiError(422, "invalid", NO_STATUS, "status")
    if platform is not None and platform not in NAMES:
        raise ApiError(422, "invalid", NO_PLATFORM, "platform")
    conn = engine.db()
    posts = store.list_posts(
        conn, status=statuses, account_id=account_id, platform=platform, batch_id=batch_id
    )
    busy = store.list_posts(conn, status=("uploading", "processing"))
    waiting = len(store.list_posts(conn, status=WAITING)) + sum(waits_for_you(p) is not None for p in busy)
    return {"posts": [post_json(engine, p) for p in posts], "needs_you": waiting}


@router.patch("/posts/{post_id}")
def change(post_id: str, body: PostChanges, request: Request) -> dict[str, Any]:
    """A post's caption, hashtags or time changed, or the post cancelled or marked as posted (with the
    link to it, when there is one)."""
    engine = _engine(request)
    _post(engine, post_id)
    sent = {name for name in body.model_fields_set if getattr(body, name) is not None}
    if not sent:
        raise ApiError(422, "invalid", NOTHING)
    if "status" in sent and sent - {"status", "url"}:
        raise ApiError(422, "invalid", STATUS_ALONE, "status")
    if "url" in sent and body.status != "posted":
        raise ApiError(422, "invalid", URL_WITH_POSTED, "url")
    url = None
    if body.url is not None:
        url = web_link(body.url.strip())
        if url is None:
            raise ApiError(422, "invalid", NOT_A_LINK, "url")
    posting = engine.posting
    try:
        if body.status == "cancelled":
            post = posting.cancel(post_id)
        elif body.status == "posted":
            post = posting.mark_posted(post_id, url)
        else:
            post = _post(engine, post_id)
            if sent & {"caption", "hashtags", "rule_ticks", "off_campaign", "drop_sound"}:
                post = _edit(engine, post_id, body)
            if body.scheduled_at is not None:
                post = _reschedule(engine, post_id, body.scheduled_at)
    except LifecycleError:
        doing = {"cancelled": "cancelled", "posted": "marked as posted"}.get(body.status or "", "changed now")
        raise _wrong(engine, post_id, doing) from None
    return {"post": post_json(engine, post)}


def _reschedule(engine: Engine, post_id: str, at: float) -> Post:
    try:
        return engine.posting.reschedule(post_id, at)
    except LifecycleError:
        raise
    except ClipForgeError as err:  # a time the account's rules don't allow, said on the time field
        raise ApiError(422, err.code, err.message, "scheduled_at") from None


def _edit(engine: Engine, post_id: str, body: PostChanges) -> Post:
    try:
        return engine.posting.edit(
            post_id,
            caption=body.caption,
            hashtags=body.hashtags,
            rule_ticks=body.rule_ticks,
            off_campaign=body.off_campaign,
            drop_sound=bool(body.drop_sound),
        )
    except ClipForgeError as err:
        raise ApiError(
            422, err.code, err.message, "hashtags" if "hashtag" in err.code else "caption"
        ) from None


@router.post("/posts/{post_id}/views")
def add_views(post_id: str, body: Views, request: Request) -> dict[str, Any]:
    """A count of a posted post's views, entered by hand now, and its submission, if it has one, with its
    new estimate."""
    engine = _engine(request)
    try:
        tracker.add_views(engine, post_id, body.views)
    except ClipForgeError as err:
        if err.code == "not_found":
            raise HTTPException(404) from None
        if err.code == "views":
            raise ApiError(422, err.code, err.message, "views") from None
        raise ApiError(409, err.code, err.message) from None
    sub = campaigns.submission_of_post(engine.db(), post_id)
    return {"submission": submission_json(engine, sub) if sub else None}


@router.post("/posts/{post_id}/retry")
def retry(post_id: str, request: Request, body: RetryBody | None = None) -> dict[str, Any]:
    """Try again: the post scheduled at its next free slot, what went wrong cleared; with ``drop_sound``,
    without the sound picked for it."""
    engine = _engine(request)
    _post(engine, post_id)
    try:
        post = engine.posting.retry(post_id, drop_sound=body is not None and body.drop_sound)
    except LifecycleError:
        raise _wrong(engine, post_id, "tried again") from None
    except ClipForgeError as err:  # Instagram may have it up already
        raise ApiError(409, err.code, err.message) from None
    return {"post": post_json(engine, post)}


@router.post("/posts/{post_id}/export")
def export(post_id: str, request: Request) -> dict[str, Any]:
    """The post's video and caption put in a folder of their own and opened in Finder, for posting by hand."""
    engine = _engine(request)
    _post(engine, post_id)
    try:
        folder = engine.posting.export(post_id)
    except LifecycleError:
        raise _wrong(engine, post_id, "exported") from None
    return {"post": post_json(engine, _post(engine, post_id)), "folder": str(folder)}


@router.post("/posts/{post_id}/open-upload", status_code=204)
def open_upload(post_id: str, request: Request) -> Response:
    """An exported post's own platform upload page opened in your default browser and its clip shown in
    Finder, for putting it up by hand. It takes only the post's id."""
    engine = _engine(request)
    _post(engine, post_id)
    try:
        put_up(engine, post_id)
    except LookupError:
        raise HTTPException(404) from None
    return Response(status_code=204)


@router.post("/posts/{post_id}/publish-anyway")
def publish_anyway(post_id: str, request: Request) -> dict[str, Any]:
    """A post Instagram held for someone else's material sent all the same, as the person chose."""
    engine = _engine(request)
    _post(engine, post_id)
    try:
        return {"post": post_json(engine, engine.posting.publish_anyway(post_id))}
    except LifecycleError:
        raise ApiError(409, "wrong_status", NOT_HELD) from None


@router.get("/posts/{post_id}/log")
def log(post_id: str, request: Request) -> dict[str, Any]:
    """What the post's route did, step by step, and the screenshots it took along the way."""
    engine = _engine(request)
    post = _post(engine, post_id)
    folder = engine.paths.post_logs / post.id
    steps: list[Any] = []
    file = folder / "steps.jsonl"
    if file.is_file() and not file.is_symlink():
        for line in file.read_text(errors="replace").splitlines()[-LOG_LINES:]:
            try:
                step = json.loads(line)
            except ValueError:
                continue
            if isinstance(step, dict):
                steps.append(cleaned(step))
    shots = sorted(p for p in folder.iterdir() if SHOT.fullmatch(p.name)) if folder.is_dir() else []
    return {"steps": steps, "screenshots": [url for p in shots if (url := media_url(engine, str(p)))]}


@router.post("/posting/pause")
def pause(request: Request) -> dict[str, bool]:
    """The kill switch: nothing posts until Resume. A post already uploading finishes."""
    engine = _engine(request)
    engine.posting.pause()
    return {"paused": engine.settings.posting.paused}


@router.post("/posting/resume")
def resume(request: Request) -> dict[str, bool]:
    engine = _engine(request)
    engine.posting.resume()
    return {"paused": engine.settings.posting.paused}


@router.post("/posting/front", status_code=204)
def front(request: Request) -> Response:
    """The automation window brought to the front, for a post that needs the person in it."""
    _engine(request).browser.request_front()
    return Response(status_code=204)
