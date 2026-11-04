"""SPEC 14.5, 14.6 and 16: the posting API. A plan is only ever a plan; Confirm and post checks it again on
the server and makes one batch however often it is pressed; the Queue lists, edits, retries, exports and
marks posts as its buttons say; and pausing, the automation window and the logs do what they promise."""

import json
from pathlib import Path

import anyio
import pytest

from clipforge.post import store
from clipforge.post.distribute import clean_caption
from clipforge.post.models import PostOptions
from clipforge.server.sse import event_stream
from clipforge.trends.models import PostSound
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_clip, at, custom, jobs, sydney
from tests.server import api_client

NOW = at(6)  # Tue 3 Nov 2026, 6:00 am, before the first window
NOT_FOUND = {"error": "not_found", "message": "That isn't here.", "field": None}
PLAN = {"plan_id", "posts", "warnings", "blocked", "renders"}
POST = {
    "id", "batch_id", "clip_id", "clip_title", "account_id", "account_name", "platform", "method", "caption",
    "hashtags", "options", "scheduled_at", "status", "attempts", "url", "error", "error_code", "details",
    "note", "waiting", "actions", "retry_helps", "on_tiktok", "screenshot", "posted_at", "created_at",
    "updated_at",
}  # fmt: skip
TIKTOK_URL = "https://www.tiktok.com/@mine/video/1"


@pytest.fixture
def clock():
    return Clock(NOW)


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
    return api_client(engine)  # no lifespan, so the runner never starts and jobs only queue


class FakeRequest:
    """All event_stream asks of a request: whether the window has gone."""

    gone = False

    async def is_disconnected(self) -> bool:
        return self.gone


def asked(clips, accounts, **req) -> dict:
    return {"clip_ids": [c.id for c in clips], "account_ids": [a.id for a in accounts], **req}


def count_jobs(engine) -> int:
    return engine.db().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_plan_route_has_no_side_effects(client, engine):
    clip, account = add_clip(engine), add_account(engine)
    planned = client.post("/api/posts/plan", json=asked([clip], [account]))
    assert planned.status_code == 200
    plan = planned.json()
    assert set(plan) == PLAN and plan["blocked"] is False
    (p,) = plan["posts"]
    assert (p["clip_id"], p["account_id"], p["platform"], p["caption"]) == (
        clip.id,
        account.id,
        "tiktok",
        "TikTok words",
    )
    assert at(7) <= sydney(p["scheduled_at"]) <= at(9)
    assert store.list_posts(engine.db()) == [] and count_jobs(engine) == 0
    assert client.post("/api/posts/plan", json=asked([clip], [account])).json()["plan_id"] != plan["plan_id"]

    gone = client.post("/api/posts/plan", json={"clip_ids": ["nope"], "account_ids": [account.id]})
    assert (gone.status_code, gone.json()["error"]) == (409, "gone")
    none = client.post("/api/posts/plan", json={"clip_ids": [], "account_ids": [account.id]})
    assert (none.status_code, none.json()["field"]) == (422, "clip_ids")
    many = client.post("/api/posts/plan", json={"clip_ids": [clip.id], "account_ids": [account.id] * 25})
    assert (many.status_code, many.json()["field"]) == (422, "account_ids")
    assert store.list_posts(engine.db()) == [] and count_jobs(engine) == 0


def test_confirm_twice_makes_one_batch(client, engine):
    clip, account = add_clip(engine), add_account(engine)
    body = asked([clip], [account])
    plan = client.post("/api/posts/plan", json=body).json()
    first = client.post("/api/posts/confirm", json={"plan_id": plan["plan_id"], "request": body})
    assert first.status_code == 201
    made = first.json()
    assert set(made) == {"batch_id", "posts"}
    (p,) = made["posts"]
    assert set(p) == POST
    assert (p["status"], p["clip_id"], p["account_id"], p["scheduled_at"]) == (
        "scheduled",
        clip.id,
        account.id,
        plan["posts"][0]["scheduled_at"],
    )
    again = client.post("/api/posts/confirm", json={"plan_id": plan["plan_id"], "request": body})
    assert again.status_code == 200
    assert again.json()["batch_id"] == made["batch_id"]
    assert [q["id"] for q in again.json()["posts"]] == [p["id"]]
    assert len(store.list_posts(engine.db())) == 1 and len(jobs(engine, p["id"])) == 1
    bare = client.post("/api/posts/confirm", json={"request": body})
    assert (bare.status_code, bare.json()["field"]) == (422, "plan_id")


def test_confirm_rechecks_caps_server_side(client, engine):
    a, b = add_clip(engine, 1), add_clip(engine, 2)
    account = add_account(engine, daily_cap=1)
    body = asked(
        [a], [account], timing="custom", custom_times={f"{a.id}:{account.id}": at(7, 30).timestamp()}
    )
    shown = client.post("/api/posts/plan", json=body).json()
    assert shown["blocked"] is False
    custom(engine, b, account, at(19))  # another post takes today's only slot
    changed = client.post("/api/posts/confirm", json={"plan_id": shown["plan_id"], "request": body})
    assert changed.status_code == 409
    out = changed.json()
    assert (out["error"], out["field"]) == ("plan_changed", None)
    assert out["message"] == "The plan changed. Check it again before posting."
    assert set(out["plan"]) == PLAN and out["plan"]["blocked"] is True
    assert [p.clip_id for p in store.list_posts(engine.db())] == [b.id]


def test_confirm_needs_the_every_acknowledgement(client, engine):
    clip = add_clip(engine)
    one, two = add_account(engine), add_account(engine)
    body = asked([clip], [one, two], mode="every")
    plan = client.post("/api/posts/plan", json=body).json()
    assert plan["blocked"] is True
    assert "every_ack" in [w["code"] for w in plan["warnings"] if w["blocking"]]
    refused = client.post("/api/posts/confirm", json={"plan_id": plan["plan_id"], "request": body})
    assert refused.status_code == 409 and refused.json()["plan"]["blocked"] is True
    assert store.list_posts(engine.db()) == []

    body["acknowledged_every"] = True
    plan = client.post("/api/posts/plan", json=body).json()
    assert plan["blocked"] is False
    made = client.post("/api/posts/confirm", json={"plan_id": plan["plan_id"], "request": body})
    assert made.status_code == 201 and len(made.json()["posts"]) == 2


def test_a_post_waiting_on_a_login_offers_it(client, engine):
    """A post that needs you because its Chrome account was logged out offers Log in until it is back."""
    tt = add_account(engine, "tiktok", "mine")
    p = custom(engine, add_clip(engine), tt, at(7, 10))
    store.update_post(engine.db(), p.id, status="needs_you", error="TikTok logged @mine out.")
    store.update_account(engine.db(), tt.id, status="needs_login")

    def actions() -> list[str]:
        return client.get("/api/posts").json()["posts"][0]["actions"]

    assert "login" in actions()
    store.update_account(engine.db(), tt.id, status="connected")
    assert "login" not in actions()


def test_posts_list_filters_and_counts_needs_you(client, engine):
    tt, ig = add_account(engine, "tiktok", "mine"), add_account(engine, "instagram", "gram")
    a = custom(engine, add_clip(engine, 1), tt, at(7, 10))
    b = custom(engine, add_clip(engine, 2), ig, at(7, 30))
    c = custom(engine, add_clip(engine, 3), tt, at(19))
    store.update_post(engine.db(), b.id, status="needs_you", error="Log in to Instagram again.")
    listed = client.get("/api/posts")
    assert listed.status_code == 200
    body = listed.json()
    assert set(body) == {"posts", "needs_you"}
    assert [p["id"] for p in body["posts"]] == [a.id, b.id, c.id]  # in time order
    assert all(set(p) == POST for p in body["posts"])
    assert body["needs_you"] == 1
    assert (body["posts"][0]["account_name"], body["posts"][0]["clip_title"]) == ("@mine", "The deadline")
    assert body["posts"][1]["error"] == "Log in to Instagram again."

    def ids(query: str) -> list[str]:
        return [p["id"] for p in client.get(f"/api/posts?{query}").json()["posts"]]

    assert ids("status=scheduled") == [a.id, c.id]
    assert ids("status=needs_you,failed") == [b.id]
    assert ids(f"account_id={tt.id}") == [a.id, c.id]
    assert ids("platform=instagram") == [b.id]
    assert ids(f"batch_id={a.batch_id}") == [a.id]
    assert client.get("/api/posts?status=scheduled").json()["needs_you"] == 1  # always every post's count
    bad = client.get("/api/posts?status=lost")
    assert (bad.status_code, bad.json()["field"]) == (422, "status")
    assert client.get("/api/posts?platform=x").json()["field"] == "platform"


def test_failed_posts_count_as_needing_you(client, engine):
    """A failed post waits for you until you retry, reschedule, export or cancel it, so the rail counts it."""
    tt = add_account(engine, "tiktok", "mine")
    a = custom(engine, add_clip(engine, 1), tt, at(7, 10))
    b = custom(engine, add_clip(engine, 2), tt, at(19))
    custom(engine, add_clip(engine, 3), tt, at(7, 10, day=4))
    store.update_post(engine.db(), a.id, status="needs_you", error="Log in to TikTok again.")
    store.update_post(
        engine.db(), b.id, status="failed", error="TikTok turned it down.", error_code="rejected"
    )
    assert client.get("/api/posts").json()["needs_you"] == 2
    store.update_post(engine.db(), b.id, status="cancelled")
    assert client.get("/api/posts").json()["needs_you"] == 1


def test_a_post_whose_window_waits_for_you_says_so_and_counts(client, engine):
    """An assisted or handed-over post waits in its Chrome window for your click: the rail counts it and the
    Queue says what it waits for, only while the site has it. One a stopped run left is never shown."""
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), at(7, 10))
    said = (
        "ClipForge has filled this in. Check it and press Post. "
        "It waits for you in its Chrome window for 10 minutes."
    )

    def shown():
        body = client.get("/api/posts").json()
        (row,) = [q for q in body["posts"] if q["id"] == p.id]
        return body["needs_you"], row.get("waiting")

    assert shown() == (0, None)
    for status in ("uploading", "processing"):
        store.update_post(engine.db(), p.id, status=status, state={"waiting": said})
        assert shown() == (1, said), status
    store.update_post(engine.db(), p.id, status="uploading", state={})
    assert shown() == (0, None)
    store.update_post(
        engine.db(), p.id, status="needs_you", error="Post wasn't pressed.", state={"waiting": said}
    )
    assert shown() == (1, None)  # the post itself needs you now, and its sentence says why
    again = client.post(f"/api/posts/{p.id}/retry").json()["post"]
    assert again["status"] == "scheduled" and "waiting" not in store.get_post(engine.db(), p.id).state


def test_a_failed_post_says_whether_a_retry_can_help(client, engine):
    """Retry can't change a post the site turned down, or one whose clip, account or route is gone."""
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), at(19))

    def helps(status: str, code: str | None) -> bool:
        store.update_post(engine.db(), p.id, status=status, error="It went wrong.", error_code=code)
        (shown,) = [q for q in client.get("/api/posts").json()["posts"] if q["id"] == p.id]
        return shown["retry_helps"]

    for code in ("rejected", "clip_deleted", "account_deleted", "no_route"):
        assert helps("failed", code) is False, code
    for code in ("retries_used", "upload_failed", "processing_timeout", "no_render", None):
        assert helps("failed", code) is True, code
    assert helps("needs_you", "rejected") is True  # with you, Retry is always yours to press
    assert helps("scheduled", None) is True


def test_patch_a_time_that_breaks_the_rules_is_refused_on_its_field(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    refused = client.patch(f"/api/posts/{p.id}", json={"scheduled_at": at(3, day=4).timestamp()})
    assert (refused.status_code, refused.json()["error"], refused.json()["field"]) == (
        422,
        "outside",
        "scheduled_at",
    )
    assert refused.json()["message"].endswith("The next free time is Wed 4 Nov, 7:00 am.")
    assert store.get_post(engine.db(), p.id).scheduled_at == at(7, 10).timestamp()


def test_patch_caption_only_before_upload(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    url = f"/api/posts/{p.id}"
    edited = client.patch(url, json={"caption": " New words\u0007 ", "hashtags": ["#Fyp", "fyp"]})
    assert edited.status_code == 200
    changed = edited.json()["post"]
    assert (changed["caption"], changed["hashtags"]) == (clean_caption(" New words\u0007 "), ["Fyp"])
    moved = client.patch(url, json={"scheduled_at": at(7, 40).timestamp()})
    assert moved.json()["post"]["scheduled_at"] == at(7, 40).timestamp()
    long = client.patch(url, json={"caption": "x" * 4001})
    assert (long.status_code, long.json()["error"], long.json()["field"]) == (
        422,
        "caption_too_long",
        "caption",
    )

    store.update_post(engine.db(), p.id, status="uploading")
    late = client.patch(url, json={"caption": "Too late"})
    assert (late.status_code, late.json()["error"], late.json()["field"]) == (409, "wrong_status", None)
    assert late.json()["message"]
    assert store.get_post(engine.db(), p.id).caption == changed["caption"]
    assert client.patch(url, json={"scheduled_at": at(8).timestamp()}).status_code == 409
    assert client.patch(url, json={"status": "cancelled"}).status_code == 409

    q = custom(engine, add_clip(engine, 2), add_account(engine), at(7, 10))
    cancelled = client.patch(f"/api/posts/{q.id}", json={"status": "cancelled"})
    assert cancelled.status_code == 200 and cancelled.json()["post"]["status"] == "cancelled"
    assert not jobs(engine, q.id)
    for body, field in [
        ({}, None),
        ({"status": "cancelled", "caption": "x"}, "status"),
        ({"status": "scheduled"}, "status"),
        ({"url": TIKTOK_URL}, "url"),
        ({"caption": "x", "render_id": "r1"}, "render_id"),
    ]:
        refused = client.patch(url, json=body)
        assert (refused.status_code, refused.json()["field"]) == (422, field), body
    assert client.patch("/api/posts/nope", json={"caption": "x"}).json() == NOT_FOUND


def test_a_paused_accounts_posts_say_they_wait(client, engine):
    a = add_account(engine, "tiktok", "clips_1")
    p = custom(engine, add_clip(engine), a, at(18, 40))
    engine.posting.pause_account(a.id, True)
    (row,) = client.get("/api/posts").json()["posts"]
    assert (row["id"], row["status"]) == (p.id, "scheduled")
    assert row["note"] == "@clips_1 is paused, so this waits until you resume it in Accounts."
    engine.posting.pause_account(a.id, False)
    (row,) = client.get("/api/posts").json()["posts"]
    assert row["note"] is None


def test_mark_as_posted_from_needs_you(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    url = f"/api/posts/{p.id}"
    early = client.patch(url, json={"status": "posted"})
    assert (early.status_code, early.json()["error"]) == (409, "wrong_status")
    store.update_post(engine.db(), p.id, status="needs_you", error="Log in to TikTok again.")
    bad = client.patch(url, json={"status": "posted", "url": "file:///etc/hosts"})
    assert (bad.status_code, bad.json()["field"]) == (422, "url")
    done = client.patch(url, json={"status": "posted", "url": TIKTOK_URL})
    assert done.status_code == 200
    went = done.json()["post"]
    assert (went["status"], went["url"], went["posted_at"], went["error"]) == (
        "posted",
        TIKTOK_URL,
        NOW.timestamp(),
        None,
    )
    q = custom(engine, add_clip(engine, 2), add_account(engine), at(19))
    store.update_post(engine.db(), q.id, status="needs_you")
    plain = client.patch(f"/api/posts/{q.id}", json={"status": "posted"})
    assert plain.status_code == 200 and plain.json()["post"]["url"] is None


def test_retry_and_export_routes(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), at(7, 10))
    assert client.post(f"/api/posts/{p.id}/retry").json()["error"] == "wrong_status"
    store.update_post(engine.db(), p.id, status="failed", error="TikTok turned this video down.")
    again = client.post(f"/api/posts/{p.id}/retry")
    assert again.status_code == 200
    assert (again.json()["post"]["status"], again.json()["post"]["error"]) == ("scheduled", None)
    assert len(jobs(engine, p.id)) == 1

    exported = client.post(f"/api/posts/{p.id}/export")
    assert exported.status_code == 200
    body = exported.json()
    assert body["post"]["status"] == "exported" and not jobs(engine, p.id)
    folder = Path(body["folder"])
    assert folder.is_dir() and engine.paths.exports in folder.parents
    assert (folder / "clip.mp4").is_file() and (folder / "caption.txt").is_file()
    assert ["open", str(folder)] in engine.run_cmd.calls
    assert client.post(f"/api/posts/{p.id}/export").status_code == 409

    held = custom(engine, add_clip(engine, 2), add_account(engine), at(7, 10))
    assert client.post(f"/api/posts/{held.id}/publish-anyway").status_code == 409
    store.update_post(engine.db(), held.id, status="needs_you", state={"actions": ["publish_anyway"]})
    sent = client.post(f"/api/posts/{held.id}/publish-anyway")
    assert sent.status_code == 200 and sent.json()["post"]["status"] == "scheduled"
    assert sent.json()["post"]["actions"] == []
    for route in ("retry", "export", "publish-anyway", "log"):
        method = client.get if route == "log" else client.post
        assert method(f"/api/posts/nope/{route}").json() == NOT_FOUND


def test_a_post_can_go_without_its_sound(client, engine):
    """Post without the sound: a scheduled post keeps its time, and one that failed or waits for you is
    scheduled again, each with the clip's own audio only."""
    ig = add_account(engine, "instagram", "mine")
    sound = PostOptions(sound=PostSound(source="ig_trending", external_id="a1", title="Song a1"))
    p = custom(engine, add_clip(engine), ig, at(7, 10))
    store.update_post(engine.db(), p.id, options=sound)
    url = f"/api/posts/{p.id}"
    off = client.patch(url, json={"drop_sound": True})
    assert off.status_code == 200
    assert (off.json()["post"]["options"]["sound"], off.json()["post"]["scheduled_at"]) == (
        None,
        at(7, 10).timestamp(),
    )

    q = custom(engine, add_clip(engine, 2), ig, at(19, day=4))
    store.update_post(
        engine.db(), q.id, options=sound, status="failed", error="Instagram turned this post down: No.",
        error_code="rejected",
    )  # fmt: skip
    assert client.patch(f"/api/posts/{q.id}", json={"drop_sound": True}).status_code == 409
    for body in ({"drop_sound": "maybe"}, {"drop_sound": True, "at": 1}):
        refused = client.post(f"/api/posts/{q.id}/retry", json=body)
        assert refused.status_code == 422, body
    assert store.get_post(engine.db(), q.id).status == "failed"
    again = client.post(f"/api/posts/{q.id}/retry", json={"drop_sound": True})
    assert again.status_code == 200
    shown = again.json()["post"]
    assert (shown["status"], shown["options"]["sound"], shown["error"]) == ("scheduled", None, None)
    assert len(jobs(engine, q.id)) == 1


def test_a_post_on_tiktok_studio_says_so_and_is_never_uploaded_again(client, engine):
    """Once TikTok Studio holds a post, the Queue knows it, and moving, retrying or exporting it says why
    not: each would put it up a second time (SPEC 10.3)."""
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), at(19))
    store.update_post(engine.db(), p.id, status="scheduled_on_tiktok", state={"on_tiktok": True})
    url = f"/api/posts/{p.id}"
    assert client.get("/api/posts").json()["posts"][0]["on_tiktok"] is True
    refused = client.post(f"{url}/retry")
    assert refused.status_code == 409
    assert refused.json()["message"] == "This post is scheduled on TikTok, so it can't be tried again."

    store.update_post(engine.db(), p.id, status="needs_you", error="TikTok's posts list didn't show it.")
    for answer, doing in (
        (client.post(f"{url}/retry"), "tried again"),
        (client.post(f"{url}/export"), "exported"),
        (client.patch(url, json={"scheduled_at": at(20).timestamp()}), "changed now"),
    ):
        assert answer.status_code == 409
        assert answer.json()["message"] == f"This post is already on TikTok Studio, so it can't be {doing}."
    done = client.patch(url, json={"status": "posted", "url": TIKTOK_URL})
    assert done.status_code == 200
    assert (done.json()["post"]["status"], done.json()["post"]["on_tiktok"]) == ("posted", True)

    q = custom(engine, add_clip(engine, 2), add_account(engine), at(20))
    shown = {x["id"]: x["on_tiktok"] for x in client.get("/api/posts").json()["posts"]}
    assert shown[q.id] is False


def test_open_upload_refuses_a_post_not_exported(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), at(7, 10))
    refused = client.post(f"/api/posts/{p.id}/open-upload")
    assert refused.status_code == 409
    assert refused.json() == {
        "error": "wrong_status",
        "message": "Only an exported post can be put up by hand.",
        "field": None,
    }
    assert engine.run_cmd.calls == []
    assert client.post("/api/posts/nope/open-upload").json() == NOT_FOUND

    client.post(f"/api/posts/{p.id}/export")
    engine.run_cmd.procs.clear()
    done = client.post(f"/api/posts/{p.id}/open-upload")
    assert (done.status_code, done.content) == (204, b"")
    (page, reveal) = engine.run_cmd.calls
    assert page == ["open", "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en"]
    assert reveal[:2] == ["open", "-R"] and reveal[2].endswith(f"/{p.id}/clip.mp4")


def test_pause_resume_and_front_routes(client, engine, monkeypatch):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    paused = client.post("/api/posting/pause")
    assert (paused.status_code, paused.json()) == (200, {"paused": True})
    assert engine.settings.posting.paused is True and not jobs(engine, p.id)
    resumed = client.post("/api/posting/resume")
    assert (resumed.status_code, resumed.json()) == (200, {"paused": False})
    assert engine.settings.posting.paused is False and len(jobs(engine, p.id)) == 1

    fronted = []
    monkeypatch.setattr(engine.browser, "request_front", lambda: fronted.append(True))
    assert client.post("/api/posting/front").status_code == 204
    assert fronted == [True]


def test_post_log_lists_steps_and_screenshots(client, engine):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    folder = engine.paths.post_logs / p.id
    folder.mkdir(parents=True)
    steps = [{"step": "open", "at": 1.0}, {"step": "upload", "at": 2.0, "file": "clip.mp4"}]
    lines = [json.dumps(steps[0]), "not a step", json.dumps(steps[1]), json.dumps([1, 2])]
    (folder / "steps.jsonl").write_text("\n".join(lines) + "\n")
    for name in ("01-open.png", "01-open.html", "02-upload.png", "notes.png"):
        (folder / name).write_bytes(b"png")
    store.update_post(engine.db(), p.id, log_path=str(folder / "steps.jsonl"))
    log = client.get(f"/api/posts/{p.id}/log")
    assert log.status_code == 200
    assert log.json() == {
        "steps": steps,
        "screenshots": [f"/media/logs/posts/{p.id}/01-open.png", f"/media/logs/posts/{p.id}/02-upload.png"],
    }
    fresh = custom(engine, add_clip(engine, 2), add_account(engine), at(19))
    assert client.get(f"/api/posts/{fresh.id}/log").json() == {"steps": [], "screenshots": []}


def test_clip_cards_say_where_they_were_posted(client, engine):
    clip = add_clip(engine)
    tt, ig = add_account(engine, "tiktok", "mine"), add_account(engine, "instagram", "gram")
    went = custom(engine, clip, tt, at(7, 10))
    custom(engine, clip, ig, at(7, 30))  # still waiting, so not on the card
    store.update_post(engine.db(), went.id, status="posted", url=TIKTOK_URL, posted_at=at(7, 11).timestamp())
    (card,) = client.get(f"/api/projects/{clip.project_id}/clips").json()["clips"]
    where = [
        {"post_id": went.id, "account_id": tt.id, "platform": "tiktok", "handle": "mine", "url": TIKTOK_URL}
    ]
    assert card["posted_to"] == where
    assert client.get(f"/api/clips/{clip.id}").json()["posted_to"] == where
    client.delete(f"/api/accounts/{tt.id}")
    (card,) = client.get(f"/api/projects/{clip.project_id}/clips").json()["clips"]
    assert card["posted_to"] == where  # what went up stays said after the account goes


def test_post_and_account_events_reach_the_stream(client, engine):
    account = add_account(engine)
    p = custom(engine, add_clip(engine), account, at(7, 10))

    async def main() -> dict:
        request = FakeRequest()
        stream = event_stream(engine, request, heartbeat_s=5, poll_s=0.01)
        assert await anext(stream) == "retry: 2000\n\n"
        assert client.patch(f"/api/accounts/{account.id}", json={"label": "Gaming"}).status_code == 200
        assert client.patch(f"/api/posts/{p.id}", json={"status": "cancelled"}).status_code == 200
        heard: dict = {}
        while len(heard) < 2:
            name, _, data = (await anext(stream)).partition("\ndata: ")
            if name.removeprefix("event: ") in ("account", "post"):
                heard[name.removeprefix("event: ")] = json.loads(data)
        request.gone = True
        return heard

    heard = anyio.run(main)
    assert (heard["account"]["id"], heard["account"]["status"]) == (account.id, "connected")
    assert (heard["post"]["id"], heard["post"]["status"]) == (p.id, "cancelled")
