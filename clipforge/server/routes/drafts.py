"""The drafts API (SPEC 10.9, 14.9 and 16): what the clipping channels drafted, grouped for review, and
Discard with its Undo. Approving goes through the Post dialog's plan and Confirm with the draft ids."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from clipforge.server.errors import ApiError
from clipforge.server.views import drafts_json, post_json
from clipforge.watch import drafts

if TYPE_CHECKING:
    from clipforge.app import Engine

router = APIRouter(prefix="/api")


class DraftIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_ids: Annotated[list[Annotated[str, Field(max_length=64)]], Field(min_length=1, max_length=100)]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


@router.get("/drafts")
def list_drafts(request: Request) -> dict[str, Any]:
    """The drafts by clipping channel, then by source video, and when the next check runs."""
    return drafts_json(_engine(request))


@router.post("/drafts/discard")
def discard(body: DraftIds, request: Request) -> dict[str, Any]:
    """The drafts put aside, all of them or, when one was approved or removed already, none (409)."""
    engine = _engine(request)
    try:
        posts = drafts.discard(engine, body.post_ids)
    except LookupError as err:
        raise ApiError(409, "gone", str(err.args[0]) if err.args else drafts.GONE) from None
    return {"posts": [post_json(engine, p) for p in posts]}


@router.post("/drafts/restore")
def restore(body: DraftIds, request: Request) -> dict[str, Any]:
    """Undo: the discarded drafts back for review, all of them or none."""
    engine = _engine(request)
    try:
        posts = drafts.restore(engine, body.post_ids)
    except LookupError as err:
        raise ApiError(409, "gone", str(err.args[0]) if err.args else drafts.GONE) from None
    return {"posts": [post_json(engine, p) for p in posts]}
