import json

import pytest

from clipforge import db, repo
from clipforge.db import connect, migrate
from clipforge.today import store
from clipforge.today.models import Day, Timing, TodoItem


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def item(**kw) -> TodoItem:
    fields = {
        "id": repo.new_id("td"),
        "day": "2026-11-03",
        "kind": "drafts",
        "ref": "cc1",
        "title": "Approve 6 drafts (Podcasts)",
        "estimate_s": 120,
        "created_at": 1000.0,
    }
    return TodoItem(**(fields | kw))


def test_item_round_trips(conn):
    full = item(
        ids=["dr1", "dr2"],
        detail="2 with rules to check by hand",
        value=4.5,
        currency="AUD",
        as_if_usd=True,
        guess=True,
        optional=True,
        due=True,
        campaigns={"cp1": 2},
        carried_from="2026-11-02",
        started_at=1100.0,
        units_at_start=2,
        done_at=1200.0,
        done_how="hand",
        done_ids=["dr1"],
    )
    stored = store.upsert_item(conn, full)
    assert stored == full
    assert store.get_item(conn, full.id) == full
    assert store.get_item(conn, "td-none") is None
    row = conn.execute("SELECT * FROM todo_items WHERE id = ?", (full.id,)).fetchone()
    assert json.loads(row["ref_json"]) == {
        "ref": "cc1",
        "ids": ["dr1", "dr2"],
        "detail": "2 with rules to check by hand",
        "currency": "AUD",
        "as_if_usd": True,
        "guess": True,
        "due": True,
        "campaigns": {"cp1": 2},
        "started_at": 1100.0,
        "units_at_start": 2,
        "done_ids": ["dr1"],
    }
    assert (row["ref"], row["optional"], row["done_how"]) == ("cc1", 1, "hand")
    plain = store.upsert_item(conn, item(kind="payout", ref="Whop:USD", title="Payout ready on Whop"))
    assert (plain.ids, plain.detail, plain.currency, plain.done_at, plain.done) == (
        [],
        None,
        "USD",
        None,
        False,
    )
    assert full.done is True
    assert [i.id for i in store.items_of(conn, "2026-11-03")] == [full.id, plain.id]
    assert store.items_of(conn, "2026-11-04") == []


def test_one_item_per_day_kind_ref(conn):
    first = store.upsert_item(conn, item(ids=["dr1"]))
    again = store.upsert_item(
        conn, item(ids=["dr1", "dr2"], title="Approve 7 drafts (Podcasts)", created_at=2000.0)
    )
    assert (again.id, again.created_at) == (first.id, 1000.0)  # the row keeps who it is
    assert (again.ids, again.title) == (["dr1", "dr2"], "Approve 7 drafts (Podcasts)")
    assert [i.id for i in store.items_of(conn, "2026-11-03")] == [first.id]
    other_day = store.upsert_item(conn, item(day="2026-11-04"))
    assert other_day.id != first.id
    store.update_item(conn, first.id, done_at=3000.0, done_how="app", done_ids=["dr1"], optional=True)
    done = store.get_item(conn, first.id)
    assert (done.done_at, done.done_how, done.done_ids, done.optional, done.ids) == (
        3000.0,
        "app",
        ["dr1"],
        True,
        ["dr1", "dr2"],
    )
    with pytest.raises(ValueError):
        store.update_item(conn, first.id, nonsense=1)


def test_timings_newest_first_limited(conn):
    for n in range(25):
        kind = "drafts" if n % 5 else "submit"
        store.add_timing(
            conn,
            Timing(
                id=repo.new_id("tm"),
                kind=kind,
                started_at=n * 100.0,
                finished_at=n * 100.0 + 40,
                seconds=40.0,
                units=2,
            ),
        )
    drafts = store.timings_of(conn, "drafts")
    assert len(drafts) == 20
    assert [t.finished_at for t in drafts] == sorted((t.finished_at for t in drafts), reverse=True)
    assert drafts[0].finished_at == 2440.0 and drafts[0].units == 2
    assert [t.finished_at for t in store.timings_of(conn, "submit", limit=2)] == [2040.0, 1540.0]
    assert store.timings_of(conn, "payout") == []


def test_days_are_kept_and_read_back(conn):
    assert store.get_day(conn, "2026-11-03") is None
    store.put_day(conn, Day(day="2026-11-01", drafts_cap=20, planned_s=700, used_s=300, notified_at=10.0))
    store.put_day(conn, Day(day="2026-11-02", planned_s=950))
    store.put_day(conn, Day(day="2026-11-02", drafts_cap=18, planned_s=950, used_s=60))
    store.put_day(conn, Day(day="2026-11-03"))
    assert store.get_day(conn, "2026-11-02") == Day(day="2026-11-02", drafts_cap=18, planned_s=950, used_s=60)
    assert [d.day for d in store.last_days(conn, "2026-11-03", 3)] == ["2026-11-02", "2026-11-01"]
    assert [d.day for d in store.last_days(conn, "2026-11-03", 1)] == ["2026-11-02"]


def test_stored_notify_events_is_dropped(tmp_path, monkeypatch):
    """A Mac that stored phase 4's ``notify_events: true`` gets SPEC 18's default after the upgrade: events
    off, every other posting value kept."""
    c = connect(tmp_path / "old.db")
    everything = db._migrations
    monkeypatch.setattr(db, "_migrations", lambda: [m for m in everything() if m[0] <= 8])
    assert migrate(c) == 8
    old = {"tiktok_daily_cap": 3, "notify_events": True, "telegram_alerts": True, "telegram_target": "@me"}
    c.execute("INSERT INTO settings (key, value_json) VALUES ('posting', ?)", (json.dumps(old),))
    c.commit()
    monkeypatch.setattr(db, "_migrations", everything)
    assert migrate(c) == 13
    stored = json.loads(c.execute("SELECT value_json FROM settings WHERE key = 'posting'").fetchone()[0])
    assert "notify_events" not in stored
    s = repo.load_settings(c)
    assert s.today.event_notifications is False
    assert (s.posting.tiktok_daily_cap, s.posting.telegram_alerts, s.posting.telegram_target) == (
        3,
        True,
        "@me",
    )
    c.close()
