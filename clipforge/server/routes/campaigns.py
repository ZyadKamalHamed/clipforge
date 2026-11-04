"""The campaigns API (SPEC 10.10, 14.11, 14.12 and 16): the Campaigns sheet's rows with their money and
counts and the totals per marketplace and currency, a campaign with its submissions, pasted rules read
into the form without saving anything, and campaigns made, changed and deleted with every refusal a
sentence for its field."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from clipforge.campaigns import earnings, parse, service, store, tracker
from clipforge.errors import ClipForgeError
from clipforge.server.errors import ApiError
from clipforge.server.views import campaign_json, money_lines, submission_json
from clipforge.today import board, day

if TYPE_CHECKING:
    from clipforge.app import Engine

PASTE_MAX = 5 * parse.MAX_RULES_CHARS  # past the rules' own limit, so a long paste gets its sentence
JOINED = "Say true once you've joined the campaign on its marketplace, or false."

router = APIRouter(prefix="/api")


class RulesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=PASTE_MAX)]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _refused(err: service.Refused) -> ApiError:
    return ApiError(422, err.code, err.message, err.field)


@router.get("/campaigns")
def list_campaigns(request: Request) -> dict[str, Any]:
    """Every campaign, newest first, after any posted post that lacks its submission gets one, each with its
    share of today's list (brought up to date first), and the totals per marketplace and currency with the
    id of each one's Withdraw link."""
    engine = _engine(request)
    tracker.sweep(engine)
    items = day.refresh(engine)
    today = day.today_of(engine)
    return {
        "campaigns": [
            campaign_json(engine, c) | {"todo": board.campaign_todo(engine, items, c, today)}
            for c in store.list_campaigns(engine.db())
        ],
        "totals": money_lines(engine),
    }


@router.get("/money")
def money_page(request: Request) -> dict[str, Any]:
    """The Money page's posts, accounts and currencies without a rate (SPEC 14.12), after any posted post
    that lacks its submission gets one. It changes nothing else."""
    engine = _engine(request)
    tracker.sweep(engine)
    return earnings.money_view(engine)


@router.post("/campaigns/parse")
def read_rules(body: RulesBody, request: Request) -> dict[str, Any]:
    """The form filled in from the pasted rules as far as Claude can read them. Nothing is saved."""
    try:
        result = parse.parse_rules(_engine(request), body.text)
    except ClipForgeError as err:
        raise ApiError(422, err.code, err.message, "text") from None
    return result.model_dump(mode="json")


@router.post("/campaigns", status_code=201)
def add_campaign(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """A new campaign, from the brief ``brief_id`` names when it has one (Use this)."""
    engine = _engine(request)
    fields = dict(body)
    brief_id = fields.pop("brief_id", None)
    try:
        made = service.create(engine, fields, brief_id=brief_id)
    except service.Refused as err:
        raise _refused(err) from None
    return {"campaign": campaign_json(engine, made)}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, request: Request) -> dict[str, Any]:
    """The campaign's row with its share of today's list, as the sheet has it, and its submissions, newest
    first."""
    engine = _engine(request)
    tracker.sweep(engine)
    campaign = store.get_campaign(engine.db(), campaign_id)
    if campaign is None:
        raise HTTPException(404)
    items = day.refresh(engine)
    todo = board.campaign_todo(engine, items, campaign, day.today_of(engine))
    rows = store.list_submissions(engine.db(), campaign_id)
    return {
        "campaign": campaign_json(engine, campaign) | {"todo": todo},
        "submissions": [submission_json(engine, s, campaign) for s in rows],
    }


@router.patch("/campaigns/{campaign_id}")
def change_campaign(campaign_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """The fields sent changed, each checked as a new campaign's are; a field left out stays as it was.
    ``joined`` (true or false) says whether you've joined it on its marketplace."""
    engine = _engine(request)
    fields = dict(body)
    sent = "joined" in fields
    joined = fields.pop("joined", None)
    if sent and not isinstance(joined, bool):
        raise ApiError(422, "campaign_field", JOINED, "joined")
    try:
        saved = service.update(engine, campaign_id, fields) if fields or not sent else None
        if sent:
            saved = service.joined(engine, campaign_id, joined)
    except LookupError:
        raise HTTPException(404) from None
    except service.Refused as err:
        raise _refused(err) from None
    return {"campaign": campaign_json(engine, saved)}


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str, request: Request) -> Response:
    """The campaign deleted, its clipping channels and projects unlinked; 409 with the sentence once it has
    submissions."""
    try:
        service.delete(_engine(request), campaign_id)
    except LookupError:
        raise HTTPException(404) from None
    return Response(status_code=204)
