"""A site's pages as ClipForge's browser route meets them, on 127.0.0.1. For TikTok: TikTok Studio's upload
page (with its banners, tour, caption editor and hashtag list), its content list, the login page and a
profile. For Instagram: the home page with its web composer and modals, the login page, profiles, the
account's reels grid and a reel's own page. The publisher opens the site's own paths rebased onto this
server, so a test picks a page's variant here with ``variants`` (``?variant=`` works too). What a page posts
lands in ``submissions``, and what it saw happen (a file chosen, a banner declined) in ``events``."""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).parent
FIRST_ID = 7400000000000000001
OTHER_ID = 7399999999999999999
OTHER_CAPTION = "Some other clip from last week #old"
FIRST_CODE = "DQr00000001"  # Instagram's reels have shortcodes
OTHER_CODE = "DPpin0000001"
REEL = re.compile(r"/(?:[A-Za-z0-9._]+/)?reel/([A-Za-z0-9_-]+)/")
PROFILE = re.compile(r"/([A-Za-z0-9._]+)/(reels/)?")


class MockSites:
    def __init__(self, handle: str = "mine", *, site: str = "tiktok") -> None:
        self.site = site
        self.handle = handle
        self.variants: set[str] = set()
        self.today = "2026-11-03"  # the day TikTok Studio's schedule calendar starts from
        self.login_as: str | None = None  # the person logs in as this handle while the login page is open
        self.submissions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.served: list[str] = []
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self.sessions: set[str] = set()
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> MockSites:
        sites = self

        class Handler(_Handler):
            pass

        Handler.sites = sites
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    @property
    def origin(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def url(self, path: str) -> str:
        return self.origin + path

    def event_names(self) -> list[str]:
        return [e["name"] for e in self.events]


class _Handler(BaseHTTPRequestHandler):
    sites: MockSites

    def log_message(self, format: str, *args: Any) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        self.sites.served.append(parts.path)
        variants = set(self.sites.variants) | set(parse_qs(parts.query).get("variant", []))
        if parts.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif self.sites.site == "instagram":
            self._instagram(parts.path, sorted(variants))
        elif parts.path == "/tiktokstudio/upload":
            self._page("upload.html", variants=sorted(variants), today=self.sites.today)
        elif parts.path == "/tiktokstudio/content":
            self._send(200, _content(self.sites), "text/html; charset=utf-8")
        elif parts.path == "/login":
            self._page("login.html", login_as=self.sites.login_as)
        elif parts.path == "/profile":
            self._redirect(f"/@{self.sites.handle}")
        elif parts.path == f"/@{self.sites.handle}":
            self._page("profile.html", handle=self.sites.handle)
        elif parts.path == "/":
            self._send(200, b"<!doctype html><title>Home</title><p>For You</p>", "text/html; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain")

    def _instagram(self, path: str, variants: list[str]) -> None:
        sites = self.sites
        reel = REEL.fullmatch(path)
        profile = PROFILE.fullmatch(path)
        if path == "/" and "login_home" in variants:  # logged out, instagram.com shows its login form at /
            self._page("login.html", login_as=sites.login_as)
        elif path == "/":
            self._page("home.html", variants=variants, handle=sites.handle)
        elif path == "/accounts/login/":
            self._page("login.html", login_as=sites.login_as)
        elif reel:
            self._send(200, _reel(sites, reel.group(1)), "text/html; charset=utf-8")
        elif profile and profile.group(2):
            self._send(200, _reels(sites), "text/html; charset=utf-8")
        elif profile:
            handle = profile.group(1)
            self._page("profile.html", handle=handle, own=handle == sites.handle)
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        self.sites.served.append(parts.path)
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if parts.path == "/api/submit":
            n = len(self.sites.submissions)
            body["id"] = f"DQr{n + 1:08d}" if self.sites.site == "instagram" else str(FIRST_ID + n)
            self.sites.submissions.append(body)
            self._send(200, json.dumps({"id": body["id"]}).encode(), "application/json")
        elif parts.path == "/api/event":
            self.sites.events.append(body)
            if self.sites.on_event is not None:
                self.sites.on_event(body)
            self._send(204, b"", "text/plain")
        elif parts.path == "/login/submit":
            session = secrets.token_hex(16)
            self.sites.sessions.add(session)
            self.sites.handle = body.get("handle") or self.sites.handle
            self.send_response(200)
            self.send_header("set-cookie", f"sessionid={session}; Path=/; HttpOnly")
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
        else:
            self._send(404, b"Not found", "text/plain")

    def _page(self, name: str, **values: Any) -> None:
        text = (HERE / self.sites.site / name).read_text(encoding="utf-8")
        page = text.replace("__CONFIG__", json.dumps(values).replace("</", "<\\/"))
        self._send(200, page.encode(), "text/html; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("location", location)
        self.send_header("content-length", "0")
        self.end_headers()

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _content(sites: MockSites) -> bytes:
    """TikTok Studio's post list: an older post first, then what was posted here, newest last."""
    rows = [(str(OTHER_ID), OTHER_CAPTION)] + [(s["id"], s.get("caption", "")) for s in sites.submissions]
    items = "".join(
        f'<div class="post-row"><a href="/@{html.escape(sites.handle)}/video/{vid}">'
        f'<img alt=""></a><div class="post-caption">{html.escape(caption)}</div>'
        f'<div class="post-stats">0 views</div></div>'
        for vid, caption in rows
    )
    return f"<!doctype html><title>Posts</title><main>{items}</main>".encode()


def _reels(sites: MockSites) -> bytes:
    """The account's reels grid: its pinned reel first, then what was shared here, newest first."""
    codes = [OTHER_CODE] + [s["id"] for s in reversed(sites.submissions)]
    handle = html.escape(sites.handle)
    items = "".join(f'<a href="/{handle}/reel/{code}/"><img alt=""></a>' for code in codes)
    return f"<!doctype html><title>Reels</title><main>{items}</main>".encode()


def _reel(sites: MockSites, code: str) -> bytes:
    """A reel's own page, its caption in the page and in its description, as Instagram shows them."""
    captions = {OTHER_CODE: OTHER_CAPTION} | {s["id"]: s.get("caption", "") for s in sites.submissions}
    if code not in captions:
        return b"<!doctype html><title>Instagram</title><p>Sorry, this page isn't available.</p>"
    caption = html.escape(captions[code])
    handle = html.escape(sites.handle)
    meta = f'<meta property="og:description" content="0 likes, 0 comments - {handle}: &quot;{caption}&quot;">'
    body = f'<article><h2>{handle}</h2><h1 style="white-space: pre-wrap">{caption}</h1></article>'
    return f"<!doctype html><head>{meta}<title>Instagram</title></head>{body}".encode()
