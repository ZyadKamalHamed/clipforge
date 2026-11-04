"""The event stream (SPEC 6.2): the engine's events as Server-Sent Events, so the window follows the jobs,
the projects and the clips as they change. A window that falls too far behind is told to load again."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

import anyio.to_thread
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from clipforge.events import Event

if TYPE_CHECKING:
    from clipforge.app import Engine

RETRY = "retry: 2000\n\n"  # how long the browser waits before it connects again
PING = ": ping\n\n"  # a comment, so a quiet stream isn't taken for a dead one
RESYNC = "event: resync\ndata: {}\n\n"

router = APIRouter(prefix="/api")


class _Request(Protocol):
    async def is_disconnected(self) -> bool: ...


def format_event(ev: Event) -> str:
    """One event: its topic as the name and its data as one line of JSON (escaped, so nothing in it
    breaks the line)."""
    return f"event: {ev.topic}\ndata: {json.dumps(ev.data, separators=(',', ':'), default=str)}\n\n"


async def event_stream(
    engine: Engine, request: _Request, *, heartbeat_s: float = 15.0, poll_s: float = 1.0
) -> AsyncIterator[str]:
    """Every event from now on, a ping after ``heartbeat_s`` of quiet, and a resync when events were
    lost because the window fell behind. It ends when the window goes, and always lets go of its
    subscription."""
    sub = engine.events.subscribe()
    try:
        yield RETRY
        quiet_since = time.monotonic()
        while not await request.is_disconnected():
            event = await anyio.to_thread.run_sync(sub.get, poll_s)
            if sub.overflowed:
                sub.overflowed = False
                yield RESYNC
            if event is not None:
                yield format_event(event)
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= heartbeat_s:
                yield PING
                quiet_since = time.monotonic()
    finally:
        sub.close()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    return StreamingResponse(event_stream(request.app.state.engine, request), media_type="text/event-stream")
