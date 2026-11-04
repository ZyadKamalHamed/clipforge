"""SPEC 14.10 and 16: the Today API. ``GET /api/today`` answers the day's list with its minutes and the
board around it, every piece of text made safe to show; Start and the tick answer the item and refuse one
that isn't today's; and the Campaigns sheet's rows carry each campaign's share of the list."""

import pytest

from clipforge import repo
from clipforge.post import store
from clipforge.today import day
from clipforge.today import store as today_store
from clipforge.today.models import Day, Need, TodoItem
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, at
from tests.server import api_client

ITEM_KEYS = {
    "id",
    "kind",
    "ref",
    "title",
    "detail",
    "estimate_s",
    "value",
    "currency",
    "as_if_usd",
    "guess",
    "optional",
    "due",
    "carried_over",
    "done",
    "done_how",
    "campaigns",
}


@pytest.fixture
def clock():
    return Clock(at(8, 0))


@pytest.fixture
def engine(clock):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers)
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)


@pytest.fixture
def found(monkeypatch):
    """What needs you, as the test sets it: a list each refresh reads."""
    current: list[Need] = []
    monkeypatch.setattr(day, "needs", lambda engine, today: list(current))
    return current


def need(kind: str, ref: str, n: int, title: str, **fields) -> Need:
    return Need(kind, ref, [f"{kind}{ref}{k}" for k in range(n)], title, **fields)


def test_today_answer_shape(client, found):
    found.append(need("drafts", "ch1", 2, "Approve 2 drafts \u202e(Podcasts)", detail="1 with\u0007 rules"))
    body = client.get("/api/today").json()
    assert set(body) == {
        "day",
        "budget_s",
        "used_s",
        "left_s",
        "built",
        "items",
        "post",
        "submit",
        "guidelines",
        "accounts",
        "money",
        "best",
        "path",
    }
    assert (body["day"], body["budget_s"], body["used_s"], body["left_s"], body["built"]) == (
        "2026-11-03",
        900,
        0,
        40,
        False,
    )
    (item,) = body["items"]
    assert set(item) == ITEM_KEYS
    assert (item["title"], item["detail"]) == ("Approve 2 drafts (Podcasts)", "1 with  rules")
    assert (item["carried_over"], item["done"], item["done_how"]) == (False, False, None)
    assert [body[k] for k in ("post", "submit", "guidelines", "accounts", "money", "best")] == [[]] * 6
    assert body["path"] == {"on": False}  # the guide is off on a fake engine


def test_today_carries_the_path_while_the_guide_is_on(clock):
    eng = fake_engine(now=clock, today={"guide": True})
    try:
        body = api_client(eng).get("/api/today").json()
    finally:
        eng.close()
    assert body["path"]["on"] and body["path"]["level"] == 1
    assert {i["kind"] for i in body["items"]} == {"path"}
    first = body["path"]["levels"][0]["steps"][0]
    assert (first["id"], first["state"], first["to"]) == ("notify", "todo", None)
    assert body["path"]["account"]["names"][0] == "Late Night Clips"


def test_today_is_built_once_the_morning_has_run(client, engine, found):
    today_store.put_day(engine.db(), Day(day="2026-11-03", notified_at=at(7, 30).timestamp()))
    assert client.get("/api/today").json()["built"] is True


def test_tick_and_start_answer_the_item(client, engine, found):
    found.append(need("submit", "c1", 3, "Submit 3 live posts to Whop (Beast clips)"))
    (item,) = client.get("/api/today").json()["items"]
    started = client.post(f"/api/today/items/{item['id']}/start")
    assert started.status_code == 200 and started.json()["id"] == item["id"]
    ticked = client.post(f"/api/today/items/{item['id']}/tick").json()
    assert (ticked["done"], ticked["done_how"]) == (True, "hand")
    assert client.get("/api/today").json()["left_s"] == 0


def test_tick_and_start_refuse_other_days(client, engine, found):
    old = today_store.upsert_item(
        engine.db(),
        TodoItem(
            id=repo.new_id("td"),
            day="2026-11-02",
            kind="drafts",
            ref="ch1",
            ids=["p1"],
            title="Approve 1 draft",
            estimate_s=20,
            created_at=at(8, day=2).timestamp(),
        ),
    )
    for action in ("start", "tick"):
        for item_id in (old.id, "td_gone"):
            answer = client.post(f"/api/today/items/{item_id}/{action}")
            assert answer.status_code == 404, (action, item_id)
    assert today_store.get_item(engine.db(), old.id).done_at is None


def test_campaign_rows_carry_their_todo(client, engine, found):
    beast = add_campaign(engine, ends_on="2026-11-04")
    drama = add_campaign(engine, name="Drama", marketplace="Vyro")
    late = add_account(engine, "tiktok", "late")
    store.update_account(engine.db(), late.id, status="needs_login")
    reels = add_account(engine, "instagram", "reels")
    found.extend(
        [
            need("drafts", "ch1", 3, "Approve 3 drafts (Podcasts)", campaigns={beast.id: 2, drama.id: 1}),
            need("submit", beast.id, 3, "Submit 3 live posts to Whop (Beast clips)", campaigns={beast.id: 3}),
            need("views", "tiktok", 1, "Enter TikTok views for 1 post", campaigns={beast.id: 1}),
            need("decide", "whop", 1, "Record 1 Whop decision", campaigns={beast.id: 1}),
            need("needs_you", "all", 1, "1 post needs you", campaigns={beast.id: 1}),
            need("login", late.id, 1, "Log in again: @late on TikTok", campaigns={beast.id: 4}),
            need(
                "insights",
                reels.id,
                1,
                "Give Instagram the insights permission: @reels",
                campaigns={beast.id: 2},
            ),
            need("payout", "Whop:USD", 1, "Payout ready on Whop", campaigns={beast.id: 2}),
            need("ends", beast.id, 1, "Beast clips ends tomorrow", campaigns={beast.id: 1}),
            need("exported", "all", 2, "Put up 2 exported posts", campaigns={drama.id: 2}),
        ]
    )
    rows = {c["id"]: c for c in client.get("/api/campaigns").json()["campaigns"]}
    assert {t["kind"]: (t["title"], t["count"]) for t in rows[beast.id]["todo"]} == {
        "drafts": ("Approve 2 drafts", 2),
        "submit": ("Submit 3 live posts", 3),
        "views": ("Enter views for 1 post", 1),
        "decide": ("Record 1 decision", 1),
        "needs_you": ("Finish 1 post that needs you", 1),
        "login": ("Log in again: @late", 4),
        "insights": ("Give Instagram the insights permission: @reels", 2),
        "payout": ("Payout ready", 2),
        "ends": ("Ends tomorrow", 1),
    }
    assert rows[drama.id]["todo"] == [  # in the list's order: worth nothing, so the quicker first
        {"kind": "drafts", "title": "Approve 1 draft", "count": 1},
        {"kind": "exported", "title": "Put up 2 exported posts", "count": 2},
    ]
    one = client.get(f"/api/campaigns/{drama.id}").json()["campaign"]
    assert one["todo"] == rows[drama.id]["todo"]  # its own page has the same list
    items = {i.kind: i for i in day.refresh(engine)}
    day.tick(engine, items["drafts"].id)
    rows = {c["id"]: c for c in client.get("/api/campaigns").json()["campaigns"]}
    assert [t["kind"] for t in rows[drama.id]["todo"]] == ["exported"]  # a ticked item is off the row
