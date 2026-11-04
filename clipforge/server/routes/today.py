"""The Today API (SPEC 14.10 and 16): the day's list with its minutes and the board around it, and Start
and the tick for an item of today's list. An item of another day, or one that has gone, is a 404."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException

from clipforge.server.views import today_view, todo_json
from clipforge.today import day
from clipforge.today import store as today_store

if TYPE_CHECKING:
    from clipforge.app import Engine

router = APIRouter(prefix="/api")


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _todays(engine: Engine, item_id: str) -> str:
    item = today_store.get_item(engine.db(), item_id)
    if item is None or item.day != day.today_of(engine).isoformat():
        raise HTTPException(404)
    return item.id


@router.get("/today")
def get_today(request: Request) -> dict[str, Any]:
    return today_view(_engine(request))


@router.post("/today/items/{item_id}/start")
def start_item(item_id: str, request: Request) -> dict[str, Any]:
    """The item with its timer running from the first click on Start."""
    engine = _engine(request)
    return todo_json(day.start(engine, _todays(engine, item_id)))


@router.post("/today/items/{item_id}/tick")
def tick_item(item_id: str, request: Request) -> dict[str, Any]:
    """The item done by your tick."""
    engine = _engine(request)
    return todo_json(day.tick(engine, _todays(engine, item_id)))
