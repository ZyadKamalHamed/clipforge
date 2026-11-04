"""Opening a stored link (SPEC 13, 16, 17): a campaign's page, its submit page, a marketplace's Withdraw
page, a brief's page, a sound's page or one of the weekly trend lists, named by its id and never by a URL
from the request, in your default browser."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.exceptions import HTTPException

from clipforge.campaigns import links

router = APIRouter(prefix="/api")


@router.post("/links/{link_id:path}/open", status_code=204)
def open_link(link_id: str, request: Request) -> Response:
    """204 once it is open; 404 when nothing is stored under the id; 409 with the sentence when what is
    stored isn't a web address ClipForge may open."""
    try:
        links.open_link(request.app.state.engine, link_id)
    except LookupError:
        raise HTTPException(404) from None
    return Response(status_code=204)
