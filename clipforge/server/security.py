"""The local server's guard (SPEC 17). Only ClipForge's own window gets an answer: the Host must be
127.0.0.1 or localhost on ClipForge's port (which stops DNS rebinding) with no tunnel's forwarding
headers, anything that changes state must
come from the same origin, and the API, the event stream and the media need the session cookie the page
set. Every response also carries headers that keep it out of frames and away from other sites."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "clipforge_session"
NONCE = "__CSP_NONCE__"  # what Vite's html.cspNonce leaves in index.html, replaced on every request
BARE_CSP = "default-src 'none'; frame-ancestors 'none'"
ORIGIN_REFUSED = "This request came from outside ClipForge, so it was refused."
SESSION_REFUSED = "ClipForge restarted. Reload the window."
SAFE_METHODS = frozenset({"GET", "HEAD"})
# What a tunnel or proxy adds on its way in. ClipForge's own window never sends any of them, so a request
# carrying one came from outside this Mac even when the tunnel rewrote its Host to ClipForge's own.
FORWARDING = frozenset({"forwarded", "x-real-ip", "via", "cf-connecting-ip", "tailscale-funnel-request"})
_COMMON = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}


def allowed_host(host: str | None, port: int) -> bool:
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def same_origin(origin: str | None, host: str) -> bool:
    return origin is not None and origin == f"http://{host}"


def csp(nonce: str) -> str:
    """The UI page's Content Security Policy: its own files, plus the inline tags carrying ``nonce``."""
    return (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def needs_session(path: str) -> bool:
    if path in ("/api/health", "/api/health/"):
        return False
    return path.startswith(("/api/", "/media/")) or path in ("/api", "/media")


class SecurityMiddleware:
    """Pure ASGI, outermost, so even the answers it gives itself and the server's own errors carry
    the headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        port: int,
        session: str,
        public_host: Callable[[], str | None] = lambda: None,
        public_file: Callable[[str], Path | None] = lambda path: None,
    ):
        self.app = app
        self.port = port
        self._session = session.encode()
        self.public_host = public_host  # the public base URL's host, which only ever gets public media
        self.public_file = public_file  # the live video a public path names

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # no websockets: the events are Server-Sent Events
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        path = scope["path"]
        send = _with_headers(send, api=path == "/api" or path.startswith("/api/"))
        headers = Headers(scope=scope)
        host = headers.get("host")
        public = self.public_host()
        if public is not None and host is not None and _bare(host) == _bare(public):
            await self._public(scope, receive, send)
            return
        if not allowed_host(host, self.port) or _forwarded(headers):
            await _answer(send, 404, b"Not found", b"text/plain; charset=utf-8")
            return
        if scope["method"] not in SAFE_METHODS and not same_origin(headers.get("origin"), host or ""):
            await _answer_json(send, 403, "origin", ORIGIN_REFUSED)
            return
        if needs_session(path) and not self._has_session(headers):
            await _answer_json(send, 401, "session", SESSION_REFUSED)
            return
        await self.app(scope, receive, send)

    async def _public(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Instagram fetching a Mode B video: a GET or HEAD of a live address, with ranges, and a bare 404
        for anything else, so nothing of the app is reachable from the public host."""
        file = self.public_file(scope["path"]) if scope["method"] in SAFE_METHODS else None
        if file is None:
            await _answer(send, 404, b"Not found", b"text/plain; charset=utf-8")
            return
        response = FileResponse(file, media_type="video/mp4", headers={"cache-control": "no-store"})
        await response(scope, receive, send)

    def _has_session(self, headers: Headers) -> bool:
        value = cookie_parser(headers.get("cookie", "")).get(SESSION_COOKIE)
        return value is not None and hmac.compare_digest(value.encode(), self._session)


def _forwarded(headers: Headers) -> bool:
    return any(name in FORWARDING or name.startswith("x-forwarded-") for name in headers)


def _bare(host: str) -> str:
    return host.lower().removesuffix(":443")


def _with_headers(send: Send, *, api: bool) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message.setdefault("headers", [])
            headers = MutableHeaders(scope=message)
            for name, value in _COMMON.items():
                headers[name] = value
            if "content-security-policy" not in headers:
                headers["content-security-policy"] = BARE_CSP
            if api and "cache-control" not in headers:
                headers["cache-control"] = "no-store"
        await send(message)

    return wrapped


async def _answer(send: Send, status: int, body: bytes, content_type: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _answer_json(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": code, "message": message, "field": None}).encode()
    await _answer(send, status, body, b"application/json")
