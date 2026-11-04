"""Polite fetching (SPEC 10.11): GET only, no cookies, an honest agent, robots.txt obeyed, redirects kept on
the site, 5 MB pages, a request every 5 s per site and 40 brief pages per site a day across runs."""

import httpx
import pytest

from clipforge import __version__
from clipforge.briefs import fetch, store
from clipforge.today.day import today_of
from tests.fakes import Clock, fake_engine
from tests.posting import at

SITE = "https://brands.example"
ROBOTS = SITE + "/robots.txt"


def page(body: str | bytes = "<p>A brief</p>", *, status=200, kind="text/html; charset=utf-8", headers=None):
    content = body.encode() if isinstance(body, str) else body
    return httpx.Response(status, headers={"content-type": kind, **(headers or {})}, content=content)


def moved(to: str, **headers):
    return page("", status=301, headers={"location": to, **headers})


class Site:
    """A look-alike on httpx's MockTransport: ``pages`` by address (no query), each a Response or a function
    of the request; anything else answers 404, robots.txt too. Every request is kept."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.pages.get(f"{request.url.scheme}://{request.url.host}{request.url.path}")
        if callable(answer):
            return answer(request)
        return answer if answer is not None else page("", status=404)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@pytest.fixture
def clock():
    return Clock(at(6, 0))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


def serve(engine, site: Site) -> Site:
    engine.brief_transport = httpx.MockTransport(site)
    return site


def pages_read(engine, site="brands.example") -> int:
    return store.site_read(engine.db(), site, today_of(engine).isoformat())[0]


def stopped(call, *args, **kw) -> fetch.Stop:
    with pytest.raises(fetch.Stop) as stop:
        call(*args, **kw)
    return stop.value


def test_tests_never_reach_the_web():
    eng = fake_engine()
    try:
        with pytest.raises(AssertionError, match="without a look-alike site"):
            fetch.Polite(eng, SITE).get(SITE + "/c/1", page=True)
        assert eng.pause.seconds == []
    finally:
        eng.close()


def test_paces_five_seconds_per_site(engine, clock):
    site = serve(
        engine,
        Site(
            {SITE + f"/c/{n}": page(f"<a href='/c/{n + 1}#top'>Next</a>") for n in range(1, 4)}
            | {"https://other.example/c/1": page()}
        ),
    )
    polite = fetch.Polite(engine, SITE)
    got = polite.get(SITE + "/c/1", page=True)  # robots.txt (404) first, then the page 5 s after it
    assert (got.url, got.links, got.text) == (SITE + "/c/1", [SITE + "/c/2"], "Next")
    clock.advance(seconds=2)
    polite.get(SITE + "/c/2", page=True)
    clock.advance(seconds=9)
    polite.get(SITE + "/c/3", page=True)
    assert engine.pause.seconds == pytest.approx([5.0, 3.0])
    assert site.paths() == ["/robots.txt", "/c/1", "/c/2", "/c/3"]

    fetch.Polite(engine, "https://www.brands.example").get(SITE + "/c/1", page=True)  # a new run, same pace
    assert engine.pause.seconds == pytest.approx([5.0, 3.0, 5.0, 5.0])
    fetch.Polite(engine, "https://other.example").get("https://other.example/c/1", page=True)
    assert len(engine.pause.seconds) == 5  # another site's robots.txt went at once
    assert (pages_read(engine), pages_read(engine, "other.example")) == (4, 1)


def test_fetch_limits_hold_across_runs(engine, clock):
    site = serve(engine, Site({SITE + f"/c/{n}": page() for n in range(41)} | {SITE + "/": page()}))
    first = fetch.Polite(engine, SITE)
    for n in range(30):
        first.get(SITE + f"/c/{n}", page=True)
    second = fetch.Polite(engine, SITE)
    for n in range(30, 40):
        second.get(SITE + f"/c/{n}", page=True)
    before = len(site.requests)
    stop = stopped(second.get, SITE + "/c/40", page=True)
    assert (stop.result, stop.message) == ("blocked", "ClipForge read its 40 pages from this site today.")
    stop = stopped(fetch.Polite(engine, SITE).get, SITE + "/c/40", page=True)
    assert stop.result == "blocked" and len(site.requests) == before  # not even robots.txt
    second.get(SITE + "/", page=False)  # the list isn't a brief page
    assert pages_read(engine) == 40

    clock.advance(days=1)
    fetch.Polite(engine, SITE).get(SITE + "/c/40", page=True)
    assert pages_read(engine) == 1


def test_robots_rules(engine):
    robots = page("User-agent: *\nDisallow: /private\n", kind="text/plain")
    site = serve(engine, Site({ROBOTS: robots, SITE + "/c/1": page()}))
    polite = fetch.Polite(engine, SITE)
    assert polite.allowed(SITE + "/c/1") and not polite.allowed(SITE + "/private/x")
    assert not polite.allowed("https://other.example/c/1")
    assert not polite.allowed("ftp://brands.example/c/1")
    assert not polite.allowed("https://me@brands.example/c/1")
    stop = stopped(polite.get, SITE + "/private/x", page=True)
    assert stop.result == "blocked"
    assert site.paths() == ["/robots.txt"]  # read once, and the page never asked for

    own = page("User-agent: Googlebot\nDisallow:\n\nUser-agent: ClipForge\nDisallow: /\n", kind="text/plain")
    serve(engine, Site({ROBOTS: own, SITE + "/c/1": page()}))
    assert not fetch.Polite(engine, SITE).allowed(SITE + "/c/1")

    for status, allowed in ((404, True), (410, True), (401, False), (403, False)):
        serve(engine, Site({ROBOTS: page("", status=status), SITE + "/c/1": page()}))
        assert fetch.Polite(engine, SITE).allowed(SITE + "/c/1") is allowed, status

    for answer in (page("", status=503), page("", status=500)):
        site = serve(engine, Site({ROBOTS: answer, SITE + "/c/1": page()}))
        assert stopped(fetch.Polite(engine, SITE).get, SITE + "/c/1", page=True).result == "error"
        assert site.paths() == ["/robots.txt"]
    serve(engine, Site({ROBOTS: page("", status=429)}))
    assert stopped(fetch.Polite(engine, SITE).allowed, SITE + "/c/1").result == "blocked"

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    engine.brief_transport = httpx.MockTransport(slow)
    assert stopped(fetch.Polite(engine, SITE).allowed, SITE + "/c/1").result == "error"


def test_robots_crawl_delay(engine, clock):
    slow = page("User-agent: *\nCrawl-delay: 12\n", kind="text/plain")
    serve(engine, Site({ROBOTS: slow, SITE + "/c/1": page(), SITE + "/c/2": page()}))
    polite = fetch.Polite(engine, SITE)
    polite.get(SITE + "/c/1", page=True)
    polite.get(SITE + "/c/2", page=True)
    assert engine.pause.seconds == pytest.approx([12.0, 12.0])

    clock.advance(hours=1)
    serve(engine, Site({ROBOTS: page("User-agent: *\nCrawl-delay: 600\n", kind="text/plain")}))
    assert stopped(fetch.Polite(engine, SITE).get, SITE + "/c/1", page=True).result == "blocked"


def test_redirects_stay_on_the_site(engine):
    site = serve(
        engine,
        Site(
            {
                SITE + "/c/1": moved("https://www.brands.example/c/1"),
                "https://www.brands.example/c/1": page("<p>Brief one</p>"),
                SITE + "/c/2": moved("https://elsewhere.example/c/2"),
                SITE + "/c/3": moved("/c/3"),
                SITE + "/c/4": moved("http://[::1"),
            }
        ),
    )
    polite = fetch.Polite(engine, SITE)
    got = polite.get(SITE + "/c/1", page=True)
    assert got.url == "https://www.brands.example/c/1" and "Brief one" in got.text
    assert stopped(polite.get, SITE + "/c/2", page=True).result == "blocked"
    assert "elsewhere.example" not in {r.url.host for r in site.requests}
    assert stopped(polite.get, SITE + "/c/3", page=True).result == "blocked"  # past 5 redirects
    assert site.paths().count("/c/3") == 6
    assert stopped(polite.get, SITE + "/c/4", page=True).result == "error"  # httpx refuses the address
    assert pages_read(engine) == 4  # a page and its redirects count once


def test_big_page_stops_at_five_mb(engine):
    chunk = b"x" * (1024 * 1024)
    big = httpx.Response(200, headers={"content-type": "text/html"}, content=iter([chunk] * 6))
    serve(
        engine,
        Site(
            {
                SITE + "/big": big,
                SITE + "/said-big": page("", headers={"content-length": str(6 * 1024 * 1024)}),
                SITE + "/pdf": page(b"%PDF-1.7", kind="application/pdf"),
                SITE + "/none": httpx.Response(200, content=b"<p>no type</p>"),
                SITE + "/notes": page("Rate: $2 CPM\nTikTok only", kind="text/plain"),
                SITE + "/latin": page(
                    "<p>caf\xe9</p>".encode("latin-1"), kind="text/html; charset=iso-8859-1"
                ),
                SITE + "/odd": page(
                    "<p>caf\xc3\xa9</p>".encode("latin-1"), kind="text/html; charset=nonsense"
                ),
            }
        ),
    )
    polite = fetch.Polite(engine, SITE)
    for path in ("/big", "/said-big", "/pdf", "/none"):
        assert stopped(polite.get, SITE + path, page=True).result == "changed", path
    got = polite.get(SITE + "/notes", page=True)
    assert (got.text, got.links, got.html) == ("Rate: $2 CPM\nTikTok only", [], "")
    assert polite.get(SITE + "/latin", page=True).text == "caf\xe9"
    assert polite.get(SITE + "/odd", page=True).text == "caf\xe9"  # an unknown charset reads as UTF-8


@pytest.mark.parametrize(
    ("status", "result"),
    [(429, "blocked"), (403, "blocked"), (401, "blocked"), (500, "error"), (503, "error"), (404, "changed")],
)
def test_status_codes(engine, status, result):
    serve(engine, Site({SITE + "/c/1": page("", status=status)}))
    stop = stopped(fetch.Polite(engine, SITE).get, SITE + "/c/1", page=True)
    assert stop.result == result and str(status) in stop.message


def test_timeouts_and_lost_connections(engine):
    def answer(request):
        if request.url.path == "/robots.txt":
            return page("", status=404)
        if request.url.path == "/slow":
            raise httpx.ReadTimeout("slow", request=request)
        raise httpx.ConnectError("no route", request=request)

    engine.brief_transport = httpx.MockTransport(answer)
    polite = fetch.Polite(engine, SITE)
    assert stopped(polite.get, SITE + "/slow", page=True).result == "error"
    assert stopped(polite.get, SITE + "/gone", page=True).result == "error"


def test_sign_in_page(engine):
    serve(
        engine,
        Site(
            {
                SITE + "/c/1": page('<form><input name="u"><INPUT Type=Password name="p"></form>'),
                SITE + "/c/2": page("", status=302, headers={"location": "/log-in?next=/c/2"}),
                SITE + "/log-in": page("<p>Welcome</p>"),
                SITE + "/account/signin": page("Sign in", kind="text/plain"),
                SITE + "/c/3": page("<p>A brief about logins</p>"),
            }
        ),
    )
    polite = fetch.Polite(engine, SITE)
    for path in ("/c/1", "/c/2", "/account/signin"):
        stop = stopped(polite.get, SITE + path, page=True)
        assert stop.result == "sign_in", path
    assert stop.message == "This page asks you to sign in, and ClipForge never signs in to read briefs."
    assert "logins" in polite.get(SITE + "/c/3", page=True).text


def test_sign_in_words_are_whole_words(engine):
    serve(
        engine,
        Site(
            {
                SITE + "/c/designing-hooks": page("<p>A brief about designing hooks</p>"),
                SITE + "/c/catalogin": page("Catalog in a list", kind="text/plain"),
                SITE + "/users/sign_in": page("<p>Welcome</p>"),
                SITE + "/Login.php": page("<p>Welcome</p>"),
            }
        ),
    )
    polite = fetch.Polite(engine, SITE)
    assert "designing" in polite.get(SITE + "/c/designing-hooks", page=True).text
    assert "Catalog" in polite.get(SITE + "/c/catalogin", page=True).text
    for path in ("/users/sign_in", "/Login.php"):
        assert stopped(polite.get, SITE + path, page=True).result == "sign_in", path


def test_no_cookies_and_an_honest_agent(engine):
    site = serve(
        engine,
        Site(
            {
                ROBOTS: page(
                    "User-agent: *\nAllow: /\n", kind="text/plain", headers={"set-cookie": "a=1; Path=/"}
                ),
                SITE + "/c/1": page(
                    "", status=302, headers={"location": "/c/2", "set-cookie": "b=2; Path=/"}
                ),
                SITE + "/c/2": page("<p>Two</p>"),
            }
        ),
    )
    fetch.Polite(engine, SITE).get(SITE + "/c/1", page=True)
    assert site.paths() == ["/robots.txt", "/c/1", "/c/2"]
    assert {r.method for r in site.requests} == {"GET"}
    assert all("cookie" not in r.headers for r in site.requests)
    agent = f"ClipForge/{__version__} (campaign brief reader)"
    assert agent == fetch.USER_AGENT
    assert {r.headers["user-agent"] for r in site.requests} == {fetch.USER_AGENT}


def test_page_text_and_links():
    html = """<html><head><title>Briefs</title><style>p { color: red }</style>
    <script>document.write("<a href='/fake'>fake</a>")</script></head>
    <body><h1>Open  campaigns</h1><p>One &amp; two<br>three</p>
    <a href="/c/1">First</a> <a href="https://www.brands.example/c/2#top">Second</a>
    <a href="/c/1">First again</a> <a href="https://other.example/c/3">Elsewhere</a>
    <a href="javascript:void(0)">Script</a> <a href="https://me@brands.example/c/4">Odd</a>
    <a href="mailto:a@b.example">Mail</a> <a href="http://[::1">Broken</a>
    <p>Caf\u00e9 \u202eevil\u202c \u0007bell</p><template><p>hidden</p></template></body></html>"""
    text, links = fetch.page_text(html, SITE + "/list")
    assert text.split("\n") == [
        "Briefs",
        "Open campaigns",
        "One & two",
        "three",
        "First Second First again Elsewhere Script Odd Mail Broken",
        "Caf\u00e9 evil bell",
    ]
    assert links == [SITE + "/c/1", "https://www.brands.example/c/2"]
    many = "".join(f"<a href='/c/{n}'>{n}</a>" for n in range(600))
    assert len(fetch.page_text(many, SITE)[1]) == 500
