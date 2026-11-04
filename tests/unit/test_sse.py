"""SPEC 6.2: the event stream the window follows, as Server-Sent Events."""

import json
import time

import anyio
import httpx
import pytest

from clipforge.events import Event
from clipforge.server.security import SESSION_COOKIE
from clipforge.server.sse import event_stream, format_event
from tests.fakes import fake_engine
from tests.server import LiveServer

RESYNC = "event: resync\ndata: {}\n\n"


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


class FakeRequest:
    """All event_stream asks of a request: whether the window has gone."""

    def __init__(self):
        self.gone = False

    async def is_disconnected(self) -> bool:
        return self.gone


def data_of(block: str) -> dict:
    return json.loads(block.split("data: ", 1)[1])


def next_event(lines) -> tuple[str, dict]:
    """The next event's name and data from a stream's lines, past blank lines and pings."""
    name = None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: ") and name is not None:
            return name, json.loads(line[len("data: ") :])
    raise AssertionError("the stream ended")


def test_format_event_is_one_block():
    data = {"id": "c1", "error": "Line one.\nLine two.\r", "title": "caf" + chr(0xE9) + chr(0x2028)}
    block = format_event(Event("clip", data, 1.0))
    assert block.endswith("\n\n") and block.count("\n") == 3 and "\r" not in block
    assert chr(0x2028) not in block  # escaped, so no line splitting of any kind breaks the line
    name, line = block[:-2].split("\n")
    assert name == "event: clip"
    assert line.startswith("data: ") and json.loads(line[len("data: ") :]) == data
    assert format_event(Event("job", {"id": 7, "status": "running"}, 1.0)) == (
        'event: job\ndata: {"id":7,"status":"running"}\n\n'
    )


def test_event_stream_sends_retry_events_and_heartbeats(engine):
    async def main():
        request = FakeRequest()
        stream = event_stream(engine, request, heartbeat_s=0.05, poll_s=0.01)
        assert await anext(stream) == "retry: 2000\n\n"
        engine.events.publish("job", {"id": 7, "status": "running"})
        assert await anext(stream) == 'event: job\ndata: {"id":7,"status":"running"}\n\n'
        quiet = time.monotonic()
        assert await anext(stream) == ": ping\n\n"
        assert time.monotonic() - quiet >= 0.04
        engine.events.publish("project", {"id": "p1"})
        assert data_of(await anext(stream)) == {"id": "p1"}
        request.gone = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    anyio.run(main)


def test_event_stream_closes_its_subscription_on_disconnect(engine):
    async def main():
        request = FakeRequest()
        stream = event_stream(engine, request, poll_s=0.01)
        await anext(stream)
        assert len(engine.events._subs) == 1
        request.gone = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert engine.events._subs == []

        dropped = event_stream(engine, FakeRequest(), poll_s=0.01)  # the server lets go of it
        await anext(dropped)
        await dropped.aclose()
        assert engine.events._subs == []

        async def follow():
            async for _ in event_stream(engine, FakeRequest(), poll_s=0.05):
                pass

        async with anyio.create_task_group() as tg:  # Starlette cancels it when the window closes
            tg.start_soon(follow)
            await anyio.sleep(0.1)
            assert len(engine.events._subs) == 1
            tg.cancel_scope.cancel()
        assert engine.events._subs == []

    anyio.run(main)


def test_event_stream_overflow_sends_resync(engine):
    async def main():
        stream = event_stream(engine, FakeRequest(), poll_s=0.01)
        await anext(stream)
        for n in range(1005):  # the queue holds 1000, so the 5 oldest are lost
            engine.events.publish("job", {"n": n})
        assert await anext(stream) == RESYNC
        got = [await anext(stream) for _ in range(1000)]
        assert [data_of(block)["n"] for block in got] == list(range(5, 1005))

        engine.events.publish("job", {"n": "after"})
        assert data_of(await anext(stream)) == {"n": "after"}  # no second resync for the same overflow
        for n in range(1001):
            engine.events.publish("job", {"n": n})
        assert await anext(stream) == RESYNC  # a new overflow, a new resync
        await stream.aclose()

    anyio.run(main)


def test_events_reach_every_subscriber(engine):
    with LiveServer(engine) as live:
        cookie = {"Cookie": f"{SESSION_COOKIE}={live.app.state.session}"}
        with (
            httpx.Client(base_url=live.url, headers=cookie, timeout=5) as client,
            client.stream("GET", "/api/events") as a,
            client.stream("GET", "/api/events") as b,
        ):
            for r in (a, b):
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                assert r.headers["cache-control"] == "no-store"
            lines_a, lines_b = a.iter_lines(), b.iter_lines()
            assert next(lines_a) == next(lines_b) == "retry: 2000"  # each is subscribed by now
            engine.events.publish("clip", {"id": "c1", "status": "rendered"})
            assert next_event(lines_a) == next_event(lines_b) == ("clip", {"id": "c1", "status": "rendered"})
        assert httpx.get(f"{live.url}/api/events", timeout=5).status_code == 401
