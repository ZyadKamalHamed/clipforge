"""The submissions API (SPEC 10.10, 14.11 and 16): a campaign's submissions, by status if you like, and
each one moved only along the ways a marketplace can answer, with the reason for a rejection and the
amount of a payment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from clipforge.campaigns import store, tracker
from clipforge.campaigns.models import SubmissionStatus
from clipforge.errors import ClipForgeError
from clipforge.server.errors import ApiError
from clipforge.server.views import submission_json

if TYPE_CHECKING:
    from clipforge.app import Engine

FIELDS = ("reason", "paid_amount")  # refusals said beside their field, as 422s

router = APIRouter(prefix="/api")


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SubmissionStatus
    reason: Annotated[str, Field(max_length=1000)] | None = None  # its own limit is checked after
    paid_amount: Annotated[float, Field(allow_inf_nan=False)] | None = None


def _engine(request: Request) -> Engine:
    return request.app.state.engine


@router.get("/submissions")
def list_submissions(
    request: Request,
    campaign_id: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[SubmissionStatus | None, Query()] = None,
) -> dict[str, Any]:
    """The submissions, newest first, of one campaign or all, in one status or any."""
    engine = _engine(request)
    rows = store.list_submissions(engine.db(), campaign_id, [status] if status else None)
    return {"submissions": [submission_json(engine, s) for s in rows]}


@router.patch("/submissions/{submission_id}")
def move_submission(submission_id: str, body: Move, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    try:
        moved = tracker.move_submission(
            engine, submission_id, body.status, reason=body.reason, paid_amount=body.paid_amount
        )
    except ClipForgeError as err:
        if err.code == "not_found":
            raise HTTPException(404) from None
        if err.code in FIELDS:
            raise ApiError(422, err.code, err.message, err.code) from None
        raise ApiError(409, err.code, err.message) from None
    return {"submission": submission_json(engine, moved)}
