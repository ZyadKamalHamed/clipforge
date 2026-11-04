"""The general reader (SPEC 10.11): a public listing read politely, Claude picking its briefs out as data,
only links the page has, the new briefs' pages read within the site's 40 a day, and a stop when the list
shows nothing it showed before, when a page asks for a sign-in or when there is no Claude."""

from pathlib import Path

import httpx
import pytest

from clipforge.brain.context import load_prompt
from clipforge.briefs import fetch, general, merge, run, store
from clipforge.briefs.models import Brief, BriefSource
from clipforge.db import transaction
from clipforge.errors import LLMError
from clipforge.today.day import today_of
from tests.fakes import Clock, FakeProvider, fake_engine
from tests.posting import at

FIXTURES = Path(__file__).parent.parent / "fixtures" / "brief_site"
SITE = "https://clips.example"
LIST = SITE + "/campaigns"
ALPHA, BETA, GAMMA = (f"{SITE}/c/{name}" for name in ("alpha", "beta", "gamma"))
FILES = {
    "/campaigns": "listing.html",
    "/c/alpha": "alpha.html",
    "/c/beta": "beta.html",
    "/c/gamma": "gamma.html",
}
SOURCE = BriefSource(
    id="bs_clips",
    name="Clip Site",
    marketplace="Clip Site",
    list_url=LIST,
    reader="general",
    created_at=0.0,
    updated_at=0.0,
)


class Site:
    """The look-alike on httpx's MockTransport: the fixture files by path, ``moves`` as redirects and 404 for
    anything else, robots.txt too. Every path asked for is kept."""

    def __init__(self, files=None, moves=None):
        self.files = dict(FILES if files is None else files)
        self.moves = dict(moves or {})
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path in self.moves:
            return httpx.Response(302, headers={"location": self.moves[path]})
        name = self.files.get(path)
        if name is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"")
        body = (FIXTURES / name).read_bytes()
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


def item(url, title, **kw):
    blank = {
        "brand": None,
        "rate_per_1000": None,
        "budget_total": None,
        "budget_left": None,
        "platforms": None,
    }
    return {"url": url, "title": title} | blank | kw


LISTED = [
    item(
        ALPHA,
        "Alpha Energy clips",
        brand="Alpha",
        rate_per_1000=2.5,
        budget_total=5000,
        budget_left=4000,
        platforms=["tiktok"],
    ),
    item(
        BETA,
        "Beta Podcast moments",
        brand="Beta Media",
        rate_per_1000=3,
        budget_total=2000,
        budget_left=900,
        platforms=["tiktok", "instagram"],
    ),
    item(GAMMA, "Gamma Gaming highlights", brand="Gamma", rate_per_1000=1),
]


class Claude:
    """Answers the list pass with ``items`` (an exception is raised instead), recording each request."""

    def __init__(self, items=None):
        self.items = LISTED if items is None else items
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.items if isinstance(self.items, Exception) else {"items": self.items}


@pytest.fixture
def claude():
    return Claude()


@pytest.fixture
def engine(claude):
    eng = fake_engine(provider=FakeProvider({"brief_list": claude}), now=Clock(at(6, 0)))
    yield eng
    eng.close()


def serve(engine, site: Site | None = None) -> Site:
    site = site or Site()
    engine.brief_transport = httpx.MockTransport(site)
    return site


def pages_read(engine) -> int:
    return store.site_read(engine.db(), "clips.example", today_of(engine).isoformat())[0]


def stopped(engine) -> fetch.Stop:
    with pytest.raises(fetch.Stop) as stop:
        general.read_site(engine, SOURCE)
    return stop.value


def put(engine, url, **kw) -> Brief:
    text = kw.pop("text", "")
    brief = Brief(
        id=kw.pop("id", "br_known"),
        source_id=kw.pop("source_id", "bs_scout"),
        external_id=url,
        url=url,
        marketplace=kw.pop("marketplace", "Clip Site"),
        title=kw.pop("title", "Known"),
        text=text,
        text_hash=merge.text_hash(text),
        first_seen_at=1.0,
        last_seen_at=1.0,
        **kw,
    )
    conn = engine.db()
    with engine.lock, transaction(conn):
        return store.put_brief(conn, brief)


def test_reads_the_list_then_new_pages(engine, claude):
    site = serve(engine)
    put(
        engine,
        BETA,
        text="Scout's own words.",
        rate_per_1000=1.0,
        niche="Podcasts and interviews",
        fields={
            "title": "stated",
            "url": "stated",
            "text": "stated",
            "rate_per_1000": "stated",
            "niche": "claude",
        },
        notes={"verdict": "Worth a look"},
    )
    found, whole = general.read_site(engine, SOURCE)
    assert whole
    assert [b.url for b in found] == [ALPHA, BETA, GAMMA]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha", "/c/gamma"]  # the known brief isn't read
    assert pages_read(engine) == 2

    alpha, beta, gamma = found
    assert (alpha.external_id, alpha.marketplace, alpha.brand, alpha.title) == (
        ALPHA,
        "Clip Site",
        "Alpha",
        "Alpha Energy clips",
    )
    assert (alpha.rate_per_1000, alpha.platforms) == (2.5, ["tiktok"])
    assert (alpha.budget_total, alpha.budget_spent, alpha.budget_left) == (5000.0, 1000.0, 4000.0)
    assert alpha.budget_read_at == at(6, 0).timestamp() + fetch.PACE_S  # when the list was read
    assert "Clip the Alpha launch stream." in alpha.text and "Alpha Energy clips" in alpha.text
    assert set(alpha.stated) == {
        "title",
        "url",
        "brand",
        "rate_per_1000",
        "budget_total",
        "budget_spent",
        "budget_left",
        "budget_read_at",
        "platforms",
        "text",
    }
    assert (gamma.platforms, gamma.budget_left, gamma.budget_read_at) == (None, None, None)
    assert set(gamma.stated) == {"title", "url", "brand", "rate_per_1000", "text"}

    # the brief Campaign Scout found takes the listing's figures and keeps its own words and notes
    assert (beta.text, beta.rate_per_1000, beta.budget_left, beta.notes) == (
        "Scout's own words.",
        3.0,
        900.0,
        {"verdict": "Worth a look"},
    )
    merge.merge(engine, SOURCE, found, engine.now().timestamp(), complete=True)
    kept = store.brief_by_key(engine.db(), BETA, SOURCE.id, BETA)
    assert (kept.text, kept.niche, kept.fields["niche"], kept.rate_per_1000) == (
        "Scout's own words.",
        "Podcasts and interviews",
        "claude",
        3.0,
    )

    (req,) = claude.requests
    assert req.pass_name == "brief_list" and req.system == load_prompt("brief_list")
    assert "never instructions" in req.system
    assert '<untrusted kind="listing">\n' in req.prompt and '<untrusted kind="links">\n' in req.prompt
    assert f"3. {ALPHA}\n4. {BETA}\n5. {GAMMA}" in req.prompt
    assert "Open campaigns" in req.prompt and "never read this" not in req.prompt


def test_items_must_be_links_on_the_page(engine, claude):
    site = serve(engine)
    claude.items = [
        item(SITE + "/c/zeta", "Not on the page"),
        item("https://elsewhere.example/ad", "Another site"),
        item("/c/gamma", "Gamma, by its path"),
        item(ALPHA, "Alpha Energy clips"),
        item(ALPHA, "Alpha twice"),
        item("javascript:alert(1)", "Not a link"),
    ]
    found, _ = general.read_site(engine, SOURCE)
    assert [(b.url, b.title) for b in found] == [(ALPHA, "Alpha Energy clips"), (GAMMA, "Gamma, by its path")]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha", "/c/gamma"]


@pytest.mark.parametrize("limit", ["items", "text", "links"])
def test_a_list_read_cut_short_is_not_whole(engine, monkeypatch, limit):
    """Past a cut may be live briefs, so a read that is not whole marks none gone (run.py)."""
    serve(engine)
    if limit == "items":
        monkeypatch.setattr(general, "ITEMS_MAX", 3)  # Claude named as many as it may
    elif limit == "text":
        monkeypatch.setattr(general, "LIST_CHARS", 20)  # Claude saw only the start of the page
    else:
        monkeypatch.setattr(fetch, "LINKS_MAX", 5)  # the page has as many links as are kept
        monkeypatch.setattr(general, "LINKS_MAX", 5)
    found, whole = general.read_site(engine, SOURCE)
    assert found and not whole


def test_list_values_are_checked(engine, claude):
    serve(engine)
    claude.items = [
        item(
            ALPHA,
            "  Al\x07pha  " + "x" * 300,
            brand="   ",
            rate_per_1000=150,
            budget_total=-5,
            budget_left=1e12,
            platforms=["Instagram Reels", "TikTok"],
        ),
        item(BETA, "Beta", platforms=["YouTube Shorts"]),  # pays for neither
        item(GAMMA, "   "),  # no name
    ]
    (alpha,), _ = general.read_site(engine, SOURCE)
    assert alpha.title == ("Al pha  " + "x" * 300)[:200]
    assert (alpha.brand, alpha.rate_per_1000, alpha.budget_total, alpha.budget_left) == (
        None,
        None,
        None,
        None,
    )
    assert alpha.platforms == ["tiktok", "instagram"]
    assert set(alpha.stated) == {"title", "url", "platforms", "text"}


def test_list_with_no_links_reads_headless(engine, claude, monkeypatch):
    site = serve(engine, Site(FILES | {"/campaigns": "static.html"}))
    calls = []

    def scripted(eng, url, *, polite=None):
        calls.append((eng, url, polite.site))
        text = "Open campaigns Alpha Energy clips Beta Podcast moments Gamma Gaming highlights"
        return fetch.Fetched(url=url, text=text, links=[SITE + "/about", ALPHA, BETA, GAMMA], html="")

    monkeypatch.setattr(general, "scripted", scripted)
    found, _ = general.read_site(engine, SOURCE)
    assert calls == [(engine, LIST, "clips.example")]
    assert [b.url for b in found] == [ALPHA, BETA, GAMMA]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha", "/c/beta", "/c/gamma"]
    assert f"1. {SITE}/about\n2. {ALPHA}\n" in claude.requests[0].prompt


def test_changed_list_stops(engine, claude):
    site = serve(engine)
    claude.items = []
    assert general.read_site(engine, SOURCE) == ([], True)  # a new site may have no campaigns yet
    put(engine, ALPHA, source_id=SOURCE.id, status="seen")
    stop = stopped(engine)
    assert (stop.result, stop.message) == ("changed", general.CHANGED)
    assert "/c/alpha" not in site.paths


def test_page_text_drops_scripts():
    assert general.page_text is fetch.page_text
    text, links = general.page_text((FIXTURES / "listing.html").read_text(), LIST)
    assert "Open campaigns" in text and "Alpha Energy clips" in text
    assert "never read this" not in text and "font-family" not in text
    assert links == [SITE + "/about", SITE + "/faq", ALPHA, BETA, GAMMA]


def test_without_claude_it_stops():
    eng = fake_engine(now=Clock(at(6, 0)))
    try:
        site = serve(eng)
        stop = stopped(eng)
        assert (stop.result, stop.message) == (
            "error",
            "Claude is needed to read this site's list. Paste its briefs instead.",
        )
        assert site.paths == []  # nothing is read that Claude couldn't use
    finally:
        eng.close()


def test_claude_failing_stops(engine, claude):
    site = serve(engine)
    claude.items = LLMError("timeout")
    stop = stopped(engine)
    assert (stop.result, stop.message) == ("error", general.FAILED)
    assert site.paths == ["/robots.txt", "/campaigns"]


def test_a_sign_in_page_stops(engine):
    serve(engine, Site(FILES | {"/account/welcome": "login.html"}, moves={"/c/gamma": "/account/welcome"}))
    stop = stopped(engine)
    assert (stop.result, stop.message) == ("sign_in", fetch.SIGN_IN)
    assert [b.url for b in stop.found] == [ALPHA, BETA]  # what was read before it is kept


def test_stops_reading_pages_at_the_days_limit(engine):
    site = serve(engine)
    put(engine, GAMMA, source_id=SOURCE.id, status="seen", text="Known words.")
    conn, day = engine.db(), today_of(engine).isoformat()
    with engine.lock, transaction(conn):
        for _ in range(fetch.PAGES_PER_DAY - 1):
            store.count_read(conn, "clips.example", day, page=True, at=0.0)
    found, _ = general.read_site(engine, SOURCE)
    assert [b.url for b in found] == [ALPHA, GAMMA]  # beta waits for tomorrow, and the known brief is kept
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha"]
    assert pages_read(engine) == fetch.PAGES_PER_DAY


SLOW = "This site asked ClipForge to slow down (HTTP 429), so it stopped for today."


class Slowed(Site):
    """The look-alike, with beta's page asking ClipForge to slow down."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/c/beta":
            self.paths.append(request.url.path)
            return httpx.Response(429, headers={"content-type": "text/html"}, content=b"")
        return super().__call__(request)


def test_a_brief_page_that_is_gone_is_left_out(engine):
    site = serve(engine, Site({path: name for path, name in FILES.items() if path != "/c/beta"}))
    found, _ = general.read_site(engine, SOURCE)
    assert [b.url for b in found] == [ALPHA, GAMMA]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha", "/c/beta", "/c/gamma"]
    assert pages_read(engine) == 3


def test_a_brief_page_that_leaves_the_site_or_loops_is_left_out(engine):
    site = serve(engine, Site(moves={"/c/alpha": "https://elsewhere.example/c/alpha", "/c/beta": "/c/beta"}))
    found, _ = general.read_site(engine, SOURCE)
    assert [b.url for b in found] == [GAMMA]
    assert "elsewhere.example" not in " ".join(site.paths)
    assert site.paths[-1] == "/c/gamma"


def test_a_slow_down_on_a_brief_page_keeps_what_was_read(engine):
    site = serve(engine, Slowed())
    with pytest.raises(general.Cut) as cut:
        general.read_site(engine, SOURCE)
    assert (cut.value.result, cut.value.message) == ("blocked", SLOW)
    assert [b.url for b in cut.value.found] == [ALPHA]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/alpha", "/c/beta"]  # nothing after the 429


def test_a_cut_read_stores_its_briefs_and_stops_for_the_day():
    filled = Claude([{"key": 1, "niche": "Gaming"}])  # the fill pass, which puts alpha in Gaming
    engine = fake_engine(
        provider=FakeProvider({"brief_list": Claude(), "brief": filled}), now=Clock(at(6, 0))
    )
    try:
        serve(engine, Slowed())
        store.add_source(engine.db(), SOURCE)
        old = put(engine, f"{SITE}/c/old", id="br_old", source_id=SOURCE.id, status="seen")
        read = run.read_source(engine, SOURCE.id)
        assert (read.result, read.new, read.gone, read.error) == ("blocked", 1, 0, SLOW)
        (alpha,) = store.briefs(engine.db(), status=["new"])
        assert alpha.url == ALPHA
        assert len(filled.requests) == 1  # filled, though the read stopped
        assert (alpha.niche, alpha.fields["niche"]) == ("Gaming", "claude")
        assert store.get_brief(engine.db(), old.id).status == "seen"  # a cut list says nothing of the rest
        source = store.get_source(engine.db(), SOURCE.id)
        assert (source.last_result, source.error) == ("blocked", SLOW)
        assert run.stopped_today(engine, source)
    finally:
        engine.close()


def test_a_list_cut_at_its_text_limit_marks_no_brief_gone(monkeypatch):
    """Claude saw only the start of a long list, so a brief it didn't name may still be live past the cut."""
    filled = Claude([])
    engine = fake_engine(
        provider=FakeProvider({"brief_list": Claude(), "brief": filled}), now=Clock(at(6, 0))
    )
    try:
        serve(engine)
        monkeypatch.setattr(general, "LIST_CHARS", 20)
        store.add_source(engine.db(), SOURCE)
        old = put(engine, f"{SITE}/c/old", id="br_old", source_id=SOURCE.id, status="seen")
        read = run.read_source(engine, SOURCE.id)
        assert (read.result, read.gone) == ("ok", 0)
        assert store.get_brief(engine.db(), old.id).status == "seen"
    finally:
        engine.close()


def test_a_spent_budget_makes_a_brief_gone(engine, claude):
    spent = {"budget_total": 2000, "budget_left": 0}
    claude.items = [item(ALPHA, "Alpha Energy clips", **spent), item(BETA, "Beta Podcast moments", **spent)]
    claude.items.append(LISTED[2])
    put(engine, BETA, source_id=SOURCE.id, status="seen", text="Known words.")
    site = serve(engine)
    found, _ = general.read_site(engine, SOURCE)
    assert [(b.url, b.budget_left, b.gone) for b in found] == [(BETA, 0.0, True), (GAMMA, None, False)]
    assert site.paths == ["/robots.txt", "/campaigns", "/c/gamma"]  # a spent new brief's page isn't read
