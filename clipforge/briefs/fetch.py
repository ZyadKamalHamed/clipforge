"""Reading public campaign pages politely (SPEC 10.11 and 17): GET only, http or https only, no cookies or
stored logins, redirects followed only within the same site, pages capped at 5 MB, robots.txt obeyed, an
honest User-Agent, a request at most every 5 s per site and 40 brief pages per site a day, both kept in
site_reads across runs. A page whose list its scripts build is read headless in a new, empty profile that
Playwright deletes, with every request to another site, and every picture, sound, font and stylesheet,
aborted. What comes back is untrusted text: it is shown and sent to Claude only as data."""

from __future__ import annotations

import codecs
import contextlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from clipforge import __version__
from clipforge.briefs import store
from clipforge.campaigns.tracker import day_of
from clipforge.db import transaction
from clipforge.errors import ClipForgeError
from clipforge.untrusted import clean_lines, clean_text

log = logging.getLogger(__name__)

USER_AGENT = f"ClipForge/{__version__} (campaign brief reader)"
PACE_S = 5.0
PACE_MAX_S = 60.0  # a robots.txt Crawl-delay ClipForge keeps to, up to this
PAGES_PER_DAY = 40
MAX_BYTES = 5 * 1024 * 1024
DEAD_END = "http://127.0.0.1:9"  # the discard port, where nothing answers: the headless page's wall
MAX_REDIRECTS = 5
LINKS_MAX = 500
TIMEOUT = 20.0
IDLE_MS = 20_000

Result = Literal["blocked", "sign_in", "changed", "error"]

FULL = f"ClipForge read its {PAGES_PER_DAY} pages from this site today."
ROBOTS = "This site's robots.txt asks programs not to read this page."
SLOWER = "This site's robots.txt asks programs to wait more than a minute between pages."
ELSEWHERE = "This page sends ClipForge to another site, so it stopped there."
LOOPS = "This page redirects too many times."
BROKEN = "This page sends ClipForge to an address that doesn't work."
TOO_BIG = "This page is over 5 MB, so ClipForge stopped reading it."
NOT_A_PAGE = "This address isn't a web page."
SIGN_IN = "This page asks you to sign in, and ClipForge never signs in to read briefs."
NO_ANSWER = "The site didn't answer in time."
UNREACHABLE = "The site couldn't be reached."

_MOVED = {301, 302, 303, 307, 308}
PAGE_TYPES = ("text/html", "text/plain")
_SKIPPED = {"image", "media", "font", "stylesheet"}
_PASSWORD = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']?password\b", re.IGNORECASE)
# login, log-in, log_in, signin, sign-in or sign_in as a word of a path in lower case, so /designing is no
# sign-in page
_SIGN_IN = re.compile(r"(?<![a-z0-9])(?:log|sign)[-_]?in(?![a-z0-9])")
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_BLOCKS = {
    "address", "article", "aside", "blockquote", "body", "br", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "table", "td", "th", "title", "tr", "ul",
}  # fmt: skip
_HIDDEN = {"script", "style", "template", "noscript"}


class Stop(ClipForgeError):
    """Why reading a site stopped: "blocked" (the site or its robots.txt said no, or the day's pages are
    read), "sign_in", "changed" (the page isn't what it was) or "error" (the site failed or didn't answer).
    ``message`` is the sentence Settings shows."""

    def __init__(self, result: Result, message: str, *, details: str | None = None):
        super().__init__(f"brief_{result}", message, details=details)
        self.result: Result = result


@dataclass(frozen=True)
class Fetched:
    """A page as read: its final address, its visible text, its links on the same site and its HTML ("" for
    plain text)."""

    url: str
    text: str
    links: list[str]
    html: str


def _site(host: str) -> str:
    return host.lower().removeprefix("www.")


def _on(site: str, url: str) -> bool:
    """Whether ``url`` is an http or https address on ``site``, with no user name in it."""
    try:
        parts = urlsplit(url)
        _ = parts.port  # a port that isn't a number raises
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and _site(parts.hostname or "") == site
    )


class _Text(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.site = _site(urlsplit(base).hostname or "")
        self.parts: list[str] = []
        self.links: dict[str, None] = {}
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN:
            self.hidden += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")
        if tag == "a" and not self.hidden and len(self.links) < LINKS_MAX:
            href = dict(attrs).get("href")
            try:
                link = urljoin(self.base, (href or "").strip()).split("#", 1)[0] if href else ""
            except ValueError:
                link = ""
            if link and _on(self.site, link):
                self.links.setdefault(link, None)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN:
            self.hidden = max(self.hidden - 1, 0)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(_SPACE.sub(" ", clean_text(data) or ""))


def page_text(html: str, base: str) -> tuple[str, list[str]]:
    """The page's visible text, a line to each block, with scripts and styles dropped, and its links on the
    same site as ``base`` (http or https, absolute, without the #part, each once, in page order, at most
    500)."""
    parser = _Text(base)
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line), list(parser.links)


def _status(status: int) -> None:
    if status in (401, 403):
        raise Stop("blocked", f"This site refused ClipForge (HTTP {status}).")
    if status == 429:
        raise Stop("blocked", "This site asked ClipForge to slow down (HTTP 429), so it stopped for today.")
    if status >= 500:
        raise Stop("error", f"The site had a problem (HTTP {status}).")
    if status != 200:
        raise Stop("changed", f"This page answered HTTP {status}.")


def _kind(headers: httpx.Headers) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _decode(body: bytes, headers: httpx.Headers) -> str:
    found = _CHARSET.search(headers.get("content-type", ""))
    charset = "utf-8"
    if found:
        with contextlib.suppress(LookupError):  # a charset Python doesn't know reads as UTF-8
            charset = codecs.lookup(found.group(1)).name
    return body.decode(charset, errors="replace")


def _page(url: str, html: str) -> Fetched:
    """A page's HTML as a Fetched, or Stop("sign_in") when it is a sign-in page."""
    path = urlsplit(url).path.casefold()
    if _SIGN_IN.search(path) or _PASSWORD.search(html):
        raise Stop("sign_in", SIGN_IN)
    text, links = page_text(html, url)
    return Fetched(url=url, text=text, links=links, html=html)


class Polite:
    """Reads one site's pages as SPEC 10.11 asks. ``origin`` is any address on the site, which is its host
    without a leading www.; robots.txt is read from its scheme and host on first use, once."""

    def __init__(self, engine: Any, origin: str):
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"not a site's address: {origin[:80]!r}")
        self.engine = engine
        self.site = _site(parts.hostname)
        self.root = urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], "", "", ""))
        self.pace = PACE_S
        self._robots: RobotFileParser | None = None

    def allowed(self, url: str) -> bool:
        """Whether ``url`` is on the site and its robots.txt lets ClipForge read it. Raises Stop when
        robots.txt can't be read (a 5xx, a timeout, a 429)."""
        return _on(self.site, url) and self._rules().can_fetch(USER_AGENT, url)

    def get(self, url: str, *, page: bool, accept: tuple[str, ...] = PAGE_TYPES) -> Fetched:
        """The page at ``url``, read as one of the ``accept`` types (text/html or text/plain unless told
        otherwise), its redirects followed within the site. A brief page (``page``) counts against the site's
        40 a day, once however it redirects. Another type, like a feed, comes back as its decoded text with
        no links. Raises Stop."""
        if page:
            self._room()
        counted = page
        for _ in range(MAX_REDIRECTS + 1):
            if not _on(self.site, url):
                raise Stop("blocked", ELSEWHERE)
            if not self._rules().can_fetch(USER_AGENT, url):
                raise Stop("blocked", ROBOTS)
            status, headers, body = self._send(url, page=counted, types=accept)
            counted = False
            if status in _MOVED and headers.get("location"):
                try:
                    url = urljoin(url, headers["location"].strip())
                except ValueError:
                    raise Stop("changed", BROKEN) from None
                continue
            _status(status)
            if _kind(headers) == "text/plain":
                path = urlsplit(url).path.casefold()
                if _SIGN_IN.search(path):
                    raise Stop("sign_in", SIGN_IN)
                text = (clean_lines(_decode(body, headers)) or "").strip()
                return Fetched(url=url, text=text, links=[], html="")
            if _kind(headers) != "text/html":
                return Fetched(url=url, text=_decode(body, headers), links=[], html="")
            return _page(url, _decode(body, headers))
        raise Stop("blocked", LOOPS)

    def full(self) -> bool:
        """Whether the site's 40 brief pages for today are read."""
        day = day_of(self.engine, self.engine.now()).isoformat()
        return store.site_read(self.engine.db(), self.site, day)[0] >= PAGES_PER_DAY

    def _room(self) -> None:
        if self.full():
            raise Stop("blocked", FULL)

    def _wait(self, *, page: bool) -> None:
        """Waits until the site's pace has passed since its last request (from any run today), then counts
        this one, as a brief page when ``page``."""
        engine = self.engine
        conn = engine.db()
        day = day_of(engine, engine.now()).isoformat()
        pages, last = store.site_read(conn, self.site, day)
        if page and pages >= PAGES_PER_DAY:
            raise Stop("blocked", FULL)
        if last is not None:
            wait = last + self.pace - engine.now().timestamp()
            if wait > 0:
                engine.pause(wait)
        with engine.lock, transaction(conn):
            store.count_read(conn, self.site, day, page=page, at=engine.now().timestamp())

    def _send(
        self, url: str, *, page: bool, types: tuple[str, ...] | None
    ) -> tuple[int, httpx.Headers, bytes]:
        """One GET through a new client, so no cookie is ever sent back: its status, headers and body, the
        body read only for a 200 of one of ``types`` (any type when None), which it asks for."""
        self._wait(page=page)
        accept = "text/html, text/plain;q=0.9" if types in (None, PAGE_TYPES) else ", ".join(types or ())
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        try:
            with (
                httpx.Client(
                    transport=self.engine.brief_transport,
                    timeout=TIMEOUT,
                    follow_redirects=False,
                    headers=headers,
                ) as client,
                client.stream("GET", url) as answer,
            ):
                if answer.status_code != 200:
                    return answer.status_code, answer.headers, b""
                if types is not None and _kind(answer.headers) not in types:
                    raise Stop("changed", NOT_A_PAGE, details=clean_text(_kind(answer.headers))[:80])
                size = answer.headers.get("content-length", "")
                if size.isdigit() and int(size) > MAX_BYTES:
                    raise Stop("changed", TOO_BIG)
                body = bytearray()
                for chunk in answer.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise Stop("changed", TOO_BIG)
                return 200, answer.headers, bytes(body)
        except httpx.TimeoutException:
            raise Stop("error", NO_ANSWER) from None
        except httpx.HTTPError as err:
            raise Stop("error", UNREACHABLE, details=f"{type(err).__name__}: {err}"[:200]) from None

    def _rules(self) -> RobotFileParser:
        """The site's robots.txt, read once: a 404 or another 4xx allows everything, a 401 or 403 allows
        nothing, and a 429, a 5xx or no answer stops the read."""
        if self._robots is not None:
            return self._robots
        rules = RobotFileParser()
        url = self.root + "/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            status, headers, body = self._send(url, page=False, types=None)
            if status in _MOVED and headers.get("location"):
                try:
                    url = urljoin(url, headers["location"].strip())
                except ValueError:
                    raise Stop("changed", BROKEN) from None
                if not _on(self.site, url):
                    raise Stop("blocked", ELSEWHERE)
                continue
            break
        else:
            raise Stop("blocked", LOOPS)
        if status == 200:
            rules.parse(_decode(body, headers).splitlines())
        elif status in (401, 403):
            rules.disallow_all = True
        elif status == 429 or status >= 500:
            _status(status)
        else:
            rules.allow_all = True
        delay = rules.crawl_delay(USER_AGENT)
        if delay:
            if float(delay) > PACE_MAX_S:
                raise Stop("blocked", SLOWER)
            self.pace = max(PACE_S, float(delay))
        self._robots = rules
        return rules


def scripted(engine: Any, url: str, *, polite: Polite | None = None) -> Fetched:
    """The page at ``url`` after its scripts have run, read in the installed Chrome, headless, in a new,
    empty profile that Playwright deletes afterwards. Service workers are blocked, and so is every request
    that isn't a GET to the site or that asks for a picture, sound, font or stylesheet. Anything the routes
    can't see (a redirect's later hops, a WebSocket, WebRTC) is sent to a dead address on this Mac, which
    only the site itself is let past, so nothing reaches another site; it is a wall, not a way around a
    block. A page that strays is ELSEWHERE, and one over 5 MB once its scripts have run is TOO_BIG. It is
    paced and counted as one brief page, and waits for the network to go quiet for up to 20 s. Raises
    Stop."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    polite = polite or Polite(engine, url)
    if not _on(polite.site, url):
        raise Stop("blocked", ELSEWHERE)
    if not polite.allowed(url):
        raise Stop("blocked", ROBOTS)
    polite._room()
    polite._wait(page=True)

    def route(handled: Any) -> None:
        request = handled.request
        elsewhere = not _on(polite.site, request.url)
        if elsewhere:
            log.info("briefs: stopped a request to %s", clean_text(urlsplit(request.url).hostname or "?"))
        if elsewhere or request.method != "GET" or request.resource_type in _SKIPPED:
            handled.abort()
        else:
            handled.continue_()

    strayed: list[str] = []  # pages the tab was sent to on another site

    def went(request: Any) -> None:
        if request.is_navigation_request() and not _on(polite.site, request.url):
            strayed.append(request.url)

    with sync_playwright() as pw:
        chrome = pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-audio-output", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            # Chrome's last matching rule wins, so loopback is walled first and the site let past after
            proxy={"server": DEAD_END, "bypass": f"<-loopback>,{polite.site},www.{polite.site}"},
        )
        try:
            context = chrome.new_context(
                user_agent=USER_AGENT, service_workers="block", accept_downloads=False
            )
            context.route("**/*", route)
            tab = context.new_page()
            tab.on("request", went)
            try:
                answer = tab.goto(url, wait_until="domcontentloaded", timeout=IDLE_MS)
            except PlaywrightTimeout:
                raise Stop("error", NO_ANSWER) from None
            except PlaywrightError as err:
                if strayed:
                    raise Stop("blocked", ELSEWHERE) from None
                raise Stop("error", UNREACHABLE, details=str(err)[:200]) from None
            # Playwright routes only a redirect's first address, so the hops are checked here
            hop = answer.request if answer is not None else None
            while hop is not None:
                if not _on(polite.site, hop.url):
                    raise Stop("blocked", ELSEWHERE)
                hop = hop.redirected_from
            if answer is not None:
                _status(answer.status)
            with contextlib.suppress(PlaywrightTimeout):  # what has loaded by then is read
                tab.wait_for_load_state("networkidle", timeout=IDLE_MS)
            final, html = tab.url, tab.content()
        finally:
            chrome.close()
    if strayed or not _on(polite.site, final):
        raise Stop("blocked", ELSEWHERE)
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise Stop("changed", TOO_BIG)
    return _page(final, html)
