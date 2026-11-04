"""The rehearsal (SPEC 10.11 and 14.10, phase 5d): a brief morning on fakes. Campaign Scout's file and a
look-alike site served on 127.0.0.1 are read once at 6:00, Claude (a fake) fills them in, and the morning's
list puts "Look at N new briefs" first, ahead of the drafts, which earn nothing yet. The Briefs tab and
Today's block 10 show them; Use this campaign then Save makes a campaign whose join item is on the list and
whose line is in the sheet's triangle. Nothing is asked of any host but 127.0.0.1 (the autouse guard fails a
test that leaves this Mac), and nothing opens but the recorded ``open`` calls."""

from __future__ import annotations

import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect

from clipforge.briefs import run, store
from clipforge.briefs.models import BriefSource
from clipforge.campaigns import parse
from clipforge.campaigns import store as campaign_store
from clipforge.today import day, morning
from clipforge.today import store as today_store
from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.fakes import Clock, FakeProvider, fake_engine
from tests.posting import add_account, at
from tests.ui.conftest import live_server
from tests.ui.test_campaigns import triangle
from tests.ui.test_shell import opened, toasts
from tests.ui.test_today import still
from tests.watching import add_channel, add_check, watched

FIXTURES = Path(__file__).parents[1] / "fixtures"
SCOUT = FIXTURES / "campaign_scout.csv"
PAGES = {
    "/campaigns": "listing.html",
    "/c/alpha": "alpha.html",
    "/c/beta": "beta.html",
    "/c/gamma": "gamma.html",
}
READ_AT = at(6, 0)
NOW = at(7, 35)  # the list was built at 7:30
TODAY = "2026-11-03"
LOOK = re.compile(r"^Look at 2 new briefs \(about US\$[\d.,]+ a day")
PIXEL = "Pixel Arena: Pixel Arena highlights"


class Site(BaseHTTPRequestHandler):
    """The look-alike marketplace: its list and three brief pages from the fixture files, 404 for anything
    else (robots.txt too, which allows everything). Each path asked for is kept on the server."""

    def do_GET(self):  # noqa: N802 - the name http.server calls
        self.server.asked.append(self.path)
        name = PAGES.get(self.path)
        body = (FIXTURES / "brief_site" / name).read_bytes() if name else b""
        self.send_response(200 if name else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    server.asked = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def no_brief_reads():
    """Here the morning reads the sources itself, as the server's timer would, so the UI tests' stand-in
    for ``run.tick`` is set aside and the real one runs."""


def listed(base: str):
    """Claude's pick of the look-alike's list: the three briefs it links to, as the unit tests have them."""

    def answer(req):
        assert "<untrusted" in req.prompt  # the page's text reaches Claude only as data
        items = [
            ("alpha", "Alpha Energy clips", "Alpha", 2.5, ["tiktok"]),
            ("beta", "Beta Podcast moments", "Beta Media", 3.0, ["tiktok", "instagram"]),
            ("gamma", "Gamma Gaming highlights", "Gamma", 1.0, None),
        ]
        blank = {"budget_total": None, "budget_left": None}
        return {
            "items": [
                {"url": f"{base}/c/{slug}", "title": title, "brand": brand, "rate_per_1000": rate}
                | blank
                | {"platforms": platforms}
                for slug, title, brand, rate, platforms in items
            ]
        }

    return answer


def filled(req):
    """Claude's fill: Gaming for each brief whose block names gaming or Pixel Arena, nothing for the rest."""
    items = []
    for block in re.findall(r'<untrusted kind="brief">\n(.*?)\n</untrusted>', req.prompt, re.S):
        key, _, words = block.partition("\n")
        if re.search(r"gaming|pixel arena", words, re.I):
            items.append({"key": int(key), "niche": "Gaming"})
    return {"items": items}


def rules(req):
    """Claude's reading of a brief's rules in the New campaign form: nothing it is sure of."""
    return {name: None for name in parse.RulesAnswer.model_fields}


def test_a_brief_morning_on_fakes(page, site, no_real_scout, monkeypatch):
    base = f"http://127.0.0.1:{site.server_port}"
    clock = Clock(at(5, 30))
    engine = fake_engine(
        now=clock, provider=FakeProvider({"brief_list": listed(base), "brief": filled, "campaign": rules})
    )
    engine.brief_transport = None  # the real transport, to the look-alike on 127.0.0.1
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    no_real_scout.parent.mkdir(parents=True)
    shutil.copy(SCOUT, no_real_scout)
    try:
        gamer = add_account(engine, "tiktok", "pixelplays", niche_tags=["gaming"], daily_cap=2)
        channel = add_channel(engine, [gamer], "Gaming")
        check = add_check(engine, channel)  # this morning's check, done by 5:30
        pid, _ = watched(engine, channel, check, [88.0, 81.0])
        drafts.settle(engine, pid)
        watch_store.update_channel(engine.db(), channel.id, last_check_on=TODAY)
        store.add_source(
            engine.db(),
            BriefSource(
                id="bs_clips",
                name="Clip Site",
                marketplace="Clip Site",
                list_url=f"{base}/campaigns",
                reader="general",
                terms_note="You checked this site's terms.",
                created_at=clock().timestamp(),
                updated_at=clock().timestamp(),
            ),
        )

        assert run.tick(engine) == 0  # 5:30, before the read time
        clock.set(READ_AT)
        assert run.tick(engine) == 2  # Campaign Scout's file and the site; Whop and Vyro are paste only
        for source_id in ("bs_scout", "bs_clips"):
            read = store.get_source(engine.db(), source_id)
            assert (read.last_result, read.error) == ("ok", None), source_id
        assert run.tick(engine) == 0  # once a day
        assert site.asked == ["/robots.txt", "/campaigns", "/c/alpha", "/c/beta", "/c/gamma"]
        assert all(wait <= 5.0 for wait in engine.pause.seconds)  # one request every 5 s at most
        gaming = {b.title for b in store.briefs(engine.db(), status=["new"]) if b.niche == "Gaming"}
        assert gaming == {"Pixel Arena highlights", "Gamma Gaming highlights"}

        clock.set(at(7, 29))
        assert morning.tick(engine) is False  # not yet the notify time
        clock.set(at(7, 30))
        assert morning.tick(engine) is True
        items = [i for i in day.ordered(engine, today_store.items_of(engine.db(), TODAY)) if not i.done]
        assert LOOK.match(items[0].title), [i.title for i in items]
        assert [i.kind for i in items] == ["briefs", "drafts"]

        clock.set(NOW)
        page.clock.set_fixed_time(NOW)
        with live_server(engine) as window:
            opened(page, window.url)
            look = still(page).nth(0)
            expect(look).to_contain_text(LOOK)
            expect(still(page).nth(1)).to_contain_text("Approve 2 drafts")
            block = page.get_by_role("region", name="New briefs", exact=True)
            expect(block.get_by_role("article")).to_have_count(2)
            for name in ("Pixel Arena highlights", "Gamma Gaming highlights"):
                expect(block.get_by_role("article", name=name, exact=True)).to_be_visible()

            block.get_by_role("link", name="See all briefs").click()
            expect(page.get_by_role("tab", name="Briefs")).to_have_attribute("aria-selected", "true")
            card = page.get_by_role("article", name="Pixel Arena highlights", exact=True)
            expect(card).to_be_visible()
            expect(page.get_by_role("article", name="Gamma Gaming highlights", exact=True)).to_be_visible()
            card.get_by_role("button", name="Use this campaign").click()
            dialog = page.get_by_role("dialog", name="New campaign")
            expect(dialog.get_by_label("Name", exact=True)).to_have_value(PIXEL)
            expect(dialog.get_by_label("Marketplace")).to_have_value("Whop")
            dialog.get_by_role("checkbox", name="I've checked the filled fields").check()
            dialog.get_by_role("button", name="Save").click()
            expect(dialog).to_have_count(0)
            expect(toasts(page)).to_contain_text(f"{PIXEL} saved")
            (campaign,) = campaign_store.list_campaigns(engine.db())
            brief = store.get_brief(engine.db(), campaign.brief_id)
            assert (brief.title, brief.status) == ("Pixel Arena highlights", "used")

            page.get_by_role("navigation", name="Pages").get_by_role(
                "link", name=re.compile("^Today")
            ).click()
            join = still(page).filter(has_text=f"Join {PIXEL} on Whop")
            expect(join).to_have_count(1)
            expect(join.get_by_role("button", name="I've joined")).to_be_visible()
            expect(still(page).filter(has_text=re.compile(r"^Look at 1 new brief\b"))).to_have_count(1)

            page.get_by_role("navigation", name="Pages").get_by_role(
                "link", name=re.compile("^Campaigns")
            ).click()
            triangle(page, PIXEL).hover()
            shown = page.get_by_role("dialog", name=f"{PIXEL} needs you")
            expect(shown.get_by_role("listitem").filter(has_text="Join on Whop")).to_have_count(1)
    finally:
        engine.close()

    assert site.asked == ["/robots.txt", "/campaigns", "/c/alpha", "/c/beta", "/c/gamma"]  # nothing more
    assert [c for c in engine.run_cmd.calls if c[:1] == ["open"]] == [["open", brief.url]]
