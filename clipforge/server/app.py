"""The web app (SPEC 6, 16, 17) and ``serve``, which runs it with the engine on 127.0.0.1."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import html
import logging
import secrets
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import anyio.to_thread
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException

from clipforge import service
from clipforge.app import Engine
from clipforge.campaigns import insights
from clipforge.errors import INTERNAL, ClipForgeError
from clipforge.launcher import DEFAULT_PORT
from clipforge.paths import Paths
from clipforge.post import public_media, queue
from clipforge.server import media, sse
from clipforge.server.errors import INVALID, ApiError, Refusal, api_error
from clipforge.server.routes import (
    accounts,
    briefs,
    campaigns,
    channels,
    clips,
    drafts,
    library,
    links,
    posts,
    projects,
    submissions,
    system,
    today,
    trends,
)
from clipforge.server.security import NONCE, SESSION_COOKIE, SecurityMiddleware, csp
from clipforge.ytdlp_update import update_ytdlp

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
THEME = "__THEME__"
IMMUTABLE = "public, max-age=31536000, immutable"
NOT_BUILT = "ClipForge's window isn't built yet."
BUILD = "npm --prefix web ci && npm --prefix web run build"
PORT_TAKEN = "Port {port} is in use by another app. Close it, or start ClipForge with --port."
PORT_FAILED = "ClipForge couldn't use port {port}: {reason}."
HTTP_ERRORS = {
    404: ("not_found", "That isn't here."),
    405: ("method", "That can't be done here."),
}


class _App(FastAPI):
    def build_middleware_stack(self):
        # Outside Starlette's error middleware, so its 500 answers get the security headers too.
        engine = self.state.engine
        return SecurityMiddleware(
            super().build_middleware_stack(),
            port=self.state.port,
            session=self.state.session,
            public_host=lambda: public_media.public_host(engine.settings.posting),
            public_file=lambda path: public_media.file_for(engine, path),
        )


CLEAN_FIRST_S = 60.0  # SPEC 16: the clean-up runs a minute after the start, then every 6 hours
CLEAN_EVERY_S = 6 * 3600.0
POST_TICK_S = 60.0  # how often the posts are matched to their jobs and the Mac kept awake for the next
INSIGHTS_EVERY_S = 3600.0  # how often the campaign posts' Instagram insights are looked at
WATCH_TICK_S = 60.0  # how often the clipping channels look at the clock for their daily check
TODAY_TICK_S = 60.0  # how often the morning looks at the clock for the day's list and notification
BRIEF_TICK_S = 60.0  # how often the brief sources look at the clock for the morning's read
CATCH_UP_S = 1.0  # how soon Today's list catches up with a change while one of its timers runs
TREND_TICK_S = 300.0  # how often the trending lists look at the clock: Instagram's daily, the weekly ping


async def _cleaning(engine: Engine) -> None:
    await asyncio.sleep(CLEAN_FIRST_S)
    while True:
        try:
            await anyio.to_thread.run_sync(engine.cleanup)
        except Exception:
            log.exception("the storage clean-up failed")
        await asyncio.sleep(CLEAN_EVERY_S)


def _post_round(engine: Engine, last_read: float | None, now: float) -> float | None:
    """The posting check, then the insights once an hour has passed since they were last looked at (a
    failed look waits the hour too): when they were last looked at."""
    try:
        queue.tick(engine)
    except Exception:
        log.exception("the posting check failed")
    if last_read is None or now - last_read >= INSIGHTS_EVERY_S:
        last_read = now
        try:
            insights.read_due(engine)
        except Exception:
            log.exception("reading the Instagram insights failed")
    return last_read


async def _posting(engine: Engine) -> None:
    last_read: float | None = None
    while True:
        await asyncio.sleep(POST_TICK_S)
        last_read = await anyio.to_thread.run_sync(_post_round, engine, last_read, time.monotonic())


async def _watching(engine: Engine) -> None:
    from clipforge.watch import schedule  # here, so a test can swap its tick

    while True:  # at the start first, for a check whose time passed while ClipForge was closed
        try:
            await anyio.to_thread.run_sync(schedule.tick, engine)
        except Exception:
            log.exception("the clipping channels' check failed to start")
        await asyncio.sleep(WATCH_TICK_S)


async def _today(engine: Engine) -> None:
    from clipforge.today import morning  # here, so a test can swap its tick

    while True:  # at the start first: a start after the notify time builds the day at once
        try:
            await anyio.to_thread.run_sync(morning.tick, engine)
        except Exception:
            log.exception("the morning's list failed to build")
        await asyncio.sleep(TODAY_TICK_S)


async def _briefing(engine: Engine) -> None:
    from clipforge.briefs import run  # here, so a test can swap its tick

    while True:  # at the start first: a start after the read time reads at once
        try:
            await anyio.to_thread.run_sync(run.tick, engine)
        except Exception:
            log.exception("the morning's brief read failed")
        await asyncio.sleep(BRIEF_TICK_S)


async def _catching_up(engine: Engine) -> None:
    from clipforge.today import day  # here, so a test can swap its catch-up

    while True:
        await asyncio.sleep(CATCH_UP_S)
        if not engine.changes.pending():
            continue
        try:
            await anyio.to_thread.run_sync(day.catch_up, engine)
        except Exception:
            log.exception("Today's list failed to catch up")


async def _trending(engine: Engine) -> None:
    from clipforge.trends import feed, instagram  # here, so a test can swap their ticks

    while True:
        await asyncio.sleep(TREND_TICK_S)  # first after a wait, so a test's server never reads
        try:
            await anyio.to_thread.run_sync(instagram.tick, engine)
        except Exception:
            log.exception("reading Instagram's trending sounds failed")
        try:
            await anyio.to_thread.run_sync(feed.tick, engine)
        except Exception:
            log.exception("reading the weekly list feed failed")


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Starts the jobs, posting and the timers. The caller closes the engine, which kills the children."""
    engine = app.state.engine
    engine.runner.start()
    engine.start_posting()
    timers = [
        asyncio.create_task(_cleaning(engine)),
        asyncio.create_task(_posting(engine)),
        asyncio.create_task(_watching(engine)),
        asyncio.create_task(_briefing(engine)),
        asyncio.create_task(_today(engine)),
        asyncio.create_task(_catching_up(engine)),
        asyncio.create_task(_trending(engine)),
    ]
    try:
        yield
    finally:
        for timer in timers:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer


def create_app(engine: Engine, *, port: int, static_dir: Path | None = None) -> FastAPI:
    """The app for ``engine``, answering on ``port``, with a new session token each time."""
    static = static_dir or STATIC
    app = _App(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
    app.state.engine = engine
    app.state.port = port
    engine.notifier.port = port  # a notification opens this window's pages
    app.state.session = secrets.token_urlsafe(32)
    app.state.static = static
    app.state.update_ytdlp = update_ytdlp  # a stand-in in tests, which never update for real

    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(ClipForgeError, _clipforge_error)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _internal)
    app.include_router(system.router)
    app.include_router(projects.router)
    app.include_router(clips.router)
    app.include_router(library.router)
    app.include_router(accounts.router)
    app.include_router(posts.router)
    app.include_router(channels.router)
    app.include_router(drafts.router)
    app.include_router(campaigns.router)
    app.include_router(briefs.router)
    app.include_router(submissions.router)
    app.include_router(links.router)
    app.include_router(today.router)
    app.include_router(trends.router)
    app.include_router(sse.router)
    app.include_router(media.router)

    @app.get("/assets/{name:path}", include_in_schema=False)
    def asset(name: str) -> Response:
        path = static_file(static / "assets", name)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": IMMUTABLE})

    @app.get("/{rest:path}", include_in_schema=False)
    def page(rest: str) -> Response:
        if rest.split("/", 1)[0] in ("api", "media"):
            raise HTTPException(404)
        if rest and rest != "index.html" and "/" not in rest:
            path = static_file(static, rest)
            if path is not None:
                return FileResponse(path, headers={"Cache-Control": "no-cache"})
        return index_page(static, session=app.state.session, theme=engine.settings.theme)

    return app


def static_file(root: Path, rel: str) -> Path | None:
    """``root/rel`` when it is a regular file reached without any symlink or ``..``, else None."""
    parts = rel.split("/")
    if not rel or "\\" in rel or "\0" in rel or any(p in ("", ".", "..") for p in parts):
        return None
    try:
        real = (root / rel).resolve(strict=True)
        base = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real == base.joinpath(*parts) and real.is_file() else None


def index_page(static: Path, *, session: str, theme: str) -> Response:
    """The UI's page with a fresh nonce for its tags and the session cookie. Without a built UI, a page
    that says how to build it."""
    try:
        text = (static / "index.html").read_text(encoding="utf-8")
    except OSError:
        body = (
            f"<!doctype html><meta charset=utf-8><title>ClipForge</title>"
            f"<p>{html.escape(NOT_BUILT, quote=False)} Run:</p><pre>{html.escape(BUILD, quote=False)}</pre>"
        )
        return HTMLResponse(body, status_code=503, headers={"Cache-Control": "no-store"})
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(
        text.replace(NONCE, nonce).replace(THEME, theme),
        headers={"Content-Security-Policy": csp(nonce), "Cache-Control": "no-store"},
    )
    response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="strict", path="/")
    return response


async def _api_error(request: Request, exc: ApiError) -> Response:
    return api_error(exc.status, exc.code, exc.message, exc.field)


async def _clipforge_error(request: Request, exc: ClipForgeError) -> Response:
    return api_error(409, exc.code, exc.message)


async def _invalid(request: Request, exc: RequestValidationError) -> Response:
    errors = exc.errors()
    loc = [str(p) for p in (errors[0].get("loc", ()) if errors else ()) if p not in ("body", "query", "path")]
    said = errors[0].get("ctx", {}).get("error") if errors else None
    message = str(said) if isinstance(said, Refusal) else INVALID
    return api_error(422, "invalid", message, ".".join(loc) or None)


async def _http_error(request: Request, exc: HTTPException) -> Response:
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        code, message = HTTP_ERRORS.get(exc.status_code, ("http", "That request didn't work."))
        return api_error(exc.status_code, code, message, headers=exc.headers)
    text = "Not found" if exc.status_code == 404 else "Not allowed"
    return PlainTextResponse(text, status_code=exc.status_code, headers=exc.headers)


async def _internal(request: Request, exc: Exception) -> Response:
    log.error("the request for %s failed", request.url.path, exc_info=exc)
    return api_error(500, "internal", INTERNAL)


def bind(port: int) -> socket.socket:
    """A socket on 127.0.0.1:``port``, bound here so a taken port can be reported plainly."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        raise
    return sock


@contextlib.contextmanager
def _sigterm_returns():
    """uvicorn stops gracefully on SIGTERM, then raises it again for the handler it found. Here that handler
    does nothing, so ``serve`` returns and closes the engine and its children instead of dying first."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, before)


def serve(paths: Paths | None = None, *, port: int = DEFAULT_PORT) -> int:
    """Run ClipForge's engine and web app until stopped. The engine opens first, so a second copy is
    turned away with the busy sentence before it touches the port. Started by the background service, it
    first waits for the home to come free (the window's engine may still hold it)."""
    if service.is_self():
        service.wait_for_home(paths or Paths.from_env())
    with _sigterm_returns():
        return _serve(paths, port)


def _serve(paths: Paths | None, port: int) -> int:
    try:
        engine = Engine.open(paths)
    except ClipForgeError as err:
        print(err.message, file=sys.stderr)
        return 1
    try:
        try:
            sock = bind(port)
        except OSError as err:
            taken = err.errno == errno.EADDRINUSE
            print(
                (PORT_TAKEN if taken else PORT_FAILED).format(port=port, reason=err.strerror), file=sys.stderr
            )
            return 1
        with sock:
            config = uvicorn.Config(
                create_app(engine, port=port),
                log_level="warning",
                access_log=False,
                lifespan="on",
                timeout_graceful_shutdown=3,
                server_header=False,
            )
            uvicorn.Server(config).run(sockets=[sock])
    finally:
        engine.close()
    return 0
