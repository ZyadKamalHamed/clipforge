"""SPEC 11 and 16: the accounts API. Accounts list with what their cards show and never a token, a Keychain
name or a profile folder; adding one starts its login or links it over Meta's API; Check runs the account's
check; edits are checked as they are saved; and removing one leaves nothing of it behind."""

import dataclasses
import time

import pytest

from clipforge.config import WINDOW_WRONG
from clipforge.errors import ClipForgeError
from clipforge.post import keychain, queue, store
from clipforge.post.browser import login
from clipforge.post.browser import run as browser_run
from clipforge.post.browser.selftest import SelfTestResult
from clipforge.post.browser.tiktok import CHANGED
from clipforge.server import views
from clipforge.server.errors import INVALID
from clipforge.server.routes.accounts import TOO_MANY_WINDOWS
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import IG_TOKEN, PAGE_TOKEN, TOKENS, USER_TOKEN, FakeGraph, error
from tests.posting import add_account, add_clip, at, custom, jobs
from tests.server import api_client

LOGIN_QUEUED = "Waiting for Chrome. The login window opens when the job before it is done."

NOW = at(6)  # Tue 3 Nov 2026, 6:00 am, before the first window
NOT_FOUND = {"error": "not_found", "message": "That isn't here.", "field": None}
ACCOUNT = {
    "id", "platform", "method", "label", "handle", "display_name", "avatar", "status", "status_note",
    "paused", "ig_mode", "experimental", "token_expires_at", "last_checked_at", "created_at", "settings",
    "today", "last_post",
}  # fmt: skip


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def engine(clock, graph):
    eng = fake_engine(now=clock)  # the real publishers, the Graph look-alike behind them
    eng.graph_transport = graph.transport()
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)  # no lifespan, so the runner never starts and jobs only queue


def ig_account(engine, mode: str = "facebook", token: str | None = None, **fields):
    """A linked Instagram account, its token in the Keychain."""
    account = add_account(engine, "instagram")
    ref = keychain.ref_for(account.id)
    keychain.put_token(ref, token or (f"{PAGE_TOKEN}-1" if mode == "facebook" else IG_TOKEN))
    ids = {"ig_user_id": "ig1", "fb_page_id": "pg1" if mode == "facebook" else None}
    return store.update_account(engine.db(), account.id, ig_mode=mode, token_ref=ref, **ids | fields)


def run_job(engine, job_id: int):
    """The job run as a worker runs it: the job after."""
    engine.db().execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = ? WHERE id = ?",
        (time.time(), job_id),
    )
    engine.runner._run(engine.db(), engine.runner.get(job_id))
    return engine.runner.get(job_id)


def shown(client, account_id: str) -> dict:
    (found,) = [a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == account_id]
    return found


def test_accounts_list_never_includes_tokens(client, engine):
    linked = ig_account(engine, token_expires_at=NOW.timestamp() + 86400)
    tiktok = add_account(engine, "tiktok", "mine", daily_cap=2)
    profile = engine.browser.profile_dir(tiktok)
    store.update_account(engine.db(), tiktok.id, profile_dir=str(profile))
    browser_gram = add_account(engine, "instagram", "typed", method="browser", daily_cap=3)
    went = custom(engine, add_clip(engine, 1), tiktok, at(7, 10))
    store.update_post(
        engine.db(), went.id, status="posted", posted_at=at(7, 12).timestamp(), url="https://x.y/1"
    )
    custom(engine, add_clip(engine, 2), tiktok, at(19))

    listed = client.get("/api/accounts")
    assert listed.status_code == 200
    accounts = {a["id"]: a for a in listed.json()["accounts"]}
    assert set(accounts) == {linked.id, tiktok.id, browser_gram.id}
    assert all(set(a) == ACCOUNT for a in accounts.values())
    tt = accounts[tiktok.id]
    assert (tt["platform"], tt["method"], tt["handle"], tt["status"], tt["paused"]) == (
        "tiktok",
        "browser",
        "mine",
        "connected",
        False,
    )
    assert tt["today"] == {"posts": 2, "cap": 2, "ceiling": 10}
    assert tt["last_post"] == {
        "id": went.id,
        "clip_title": "The deadline",
        "url": "https://x.y/1",
        "posted_at": at(7, 12).timestamp(),
    }
    assert tt["experimental"] is False and accounts[browser_gram.id]["experimental"] is True
    assert tt["settings"]["daily_cap"] == 2 and tt["avatar"] is None
    ig = accounts[linked.id]
    assert (ig["ig_mode"], ig["token_expires_at"], ig["last_post"]) == (
        "facebook",
        NOW.timestamp() + 86400,
        None,
    )
    assert ig["today"] == {"posts": 0, "cap": engine.settings.posting.instagram_daily_cap, "ceiling": 50}
    assert accounts[browser_gram.id]["today"] == {"posts": 0, "cap": 1, "ceiling": 1}
    text = listed.text
    assert all(t not in text for t in TOKENS)
    assert "account:" not in text and str(engine.paths.browser) not in text
    assert "pg1" not in text and "ig1" not in text  # Meta's ids stay in the database

    avatar = engine.paths.accounts / tiktok.id / "avatar.png"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"png")
    store.update_account(engine.db(), tiktok.id, avatar_path=str(avatar))
    assert shown(client, tiktok.id)["avatar"] == f"/media/accounts/{tiktok.id}/avatar.png"


def test_add_tiktok_account_starts_login(client, engine, monkeypatch):
    made = client.post(
        "/api/accounts",
        json={
            "platform": "tiktok",
            "method": "browser",
            "label": "Gaming",
            "niche_tags": ["Gaming", "streamers"],
        },
    )
    assert made.status_code == 201
    body = made.json()
    account = body["account"]
    assert set(account) == ACCOUNT
    assert (
        account["platform"],
        account["method"],
        account["label"],
        account["handle"],
        account["status"],
    ) == (
        "tiktok",
        "browser",
        "Gaming",
        None,
        "needs_login",
    )
    assert account["settings"]["niche_tags"] == ["gaming", "streamers"]
    job = engine.runner.get(body["job_id"])
    assert (job.kind, job.ref_id, job.status) == ("account.login", account["id"], "queued")
    assert views.job_json(engine, job)["label"] == LOGIN_QUEUED  # Chrome's lane may be busy with a post
    running = dataclasses.replace(job, status="running")
    assert views.job_json(engine, running)["label"] == "Log in in the Chrome window."
    again = client.post(f"/api/accounts/{account['id']}/login")
    assert again.status_code == 202
    assert again.json()["job_id"] == body["job_id"]  # one login at a time

    logged = []

    def logs_in(eng, account_id, ctx=None):
        logged.append(account_id)
        return queue.change_account(eng, account_id, handle="mine", status="connected", status_note=None)

    monkeypatch.setattr(login, "login_account", logs_in)
    assert run_job(engine, body["job_id"]).status == "done"
    assert logged == [account["id"]]
    assert (shown(client, account["id"])["status"], shown(client, account["id"])["handle"]) == (
        "connected",
        "mine",
    )

    def closes(eng, account_id, ctx=None):
        raise ClipForgeError("login_closed", login.CLOSED)

    monkeypatch.setattr(login, "login_account", closes)
    second = client.post(f"/api/accounts/{account['id']}/login").json()["job_id"]
    assert second != body["job_id"]
    assert run_job(engine, second).status == "failed"
    after = shown(client, account["id"])
    assert (after["status"], after["status_note"]) == ("connected", login.CLOSED)

    api = client.post("/api/accounts", json={"platform": "tiktok", "method": "api"})
    assert (api.status_code, api.json()["field"]) == (422, "method")
    assert client.post("/api/accounts", json={"platform": "x", "method": "browser"}).status_code == 422
    extra = client.post("/api/accounts", json={"platform": "tiktok", "method": "browser", "password": "x"})
    assert (extra.status_code, extra.json()["field"]) == (422, "password")
    assert client.post("/api/accounts/nope/login").json() == NOT_FOUND


def test_stopping_a_login_cancels_its_job(client, engine):
    a = store.update_account(engine.db(), add_account(engine, "tiktok", "mine").id, status="needs_login")
    job_id = client.post(f"/api/accounts/{a.id}/login").json()["job_id"]
    stopped = client.post(f"/api/accounts/{a.id}/login/stop")
    assert stopped.status_code == 204
    assert engine.runner.get(job_id).status == "cancelled"
    assert client.post(f"/api/accounts/{a.id}/login/stop").status_code == 204  # nothing left to stop
    assert client.post("/api/accounts/a_nobody/login/stop").status_code == 404


def test_add_instagram_browser_account_needs_its_handle(client, engine):
    bare = client.post("/api/accounts", json={"platform": "instagram", "method": "browser"})
    assert (bare.status_code, bare.json()["field"]) == (422, "handle")
    assert bare.json()["message"] == login.NO_TYPED_HANDLE
    wrong = client.post(
        "/api/accounts", json={"platform": "instagram", "method": "browser", "handle": "no way!"}
    )
    assert (wrong.status_code, wrong.json()["field"]) == (422, "handle")
    made = client.post(
        "/api/accounts", json={"platform": "instagram", "method": "browser", "handle": "@Clips.B"}
    )
    assert made.status_code == 201
    assert made.json()["account"]["handle"] == "clips.b"
    assert engine.runner.get(made.json()["job_id"]).kind == "account.login"
    twice = client.post(
        "/api/accounts", json={"platform": "instagram", "method": "browser", "handle": "clips.b"}
    )
    assert (twice.status_code, twice.json()["error"], twice.json()["field"]) == (
        409,
        "handle_taken",
        "handle",
    )
    assert len(store.list_accounts(engine.db())) == 1


def test_facebook_pages_returns_candidates_without_tokens(client, engine, graph, memory_keyring):
    linked = ig_account(engine)  # already linked to ig1
    found = client.post("/api/accounts/facebook/pages", json={"token": USER_TOKEN})
    assert found.status_code == 200
    body = found.json()
    assert set(body) == {"ticket", "candidates"}
    assert body["candidates"] == [
        {
            "ig_user_id": "ig1",
            "page_id": "pg1",
            "page_name": "Page 1",
            "username": "clips_1",
            "account_id": linked.id,
        },
        {
            "ig_user_id": "ig3",
            "page_id": "pg3",
            "page_name": "Page 3",
            "username": "clips_3",
            "account_id": None,
        },
    ]
    assert all(t not in found.text for t in TOKENS) and "scontent" not in found.text
    assert len(memory_keyring.items) == 1  # nothing new is kept until Link

    graph.script["pages"] = [error(190, None, "Invalid OAuth access token")]
    bad = client.post("/api/accounts/facebook/pages", json={"token": "a-wrong-token"})
    assert (bad.status_code, bad.json()["error"], bad.json()["field"]) == (400, "token_rejected", "token")
    assert "a-wrong-token" not in bad.text
    half = client.post("/api/accounts/facebook/pages", json={"token": USER_TOKEN, "app_id": "1234"})
    assert (half.status_code, half.json()["field"]) == (422, "app_secret")
    blank = client.post("/api/accounts/facebook/pages", json={"token": "   "})
    assert (blank.status_code, blank.json()["field"]) == (422, "token")


def test_facebook_link_makes_accounts(client, engine, memory_keyring):
    ticket = client.post("/api/accounts/facebook/pages", json={"token": USER_TOKEN}).json()["ticket"]
    made = client.post("/api/accounts/facebook/link", json={"ticket": ticket, "ig_user_ids": ["ig1", "ig3"]})
    assert made.status_code == 201
    accounts = made.json()["accounts"]
    assert all(set(a) == ACCOUNT for a in accounts)
    assert [(a["handle"], a["ig_mode"], a["status"]) for a in accounts] == [
        ("clips_1", "facebook", "connected"),
        ("clips_3", "facebook", "connected"),
    ]
    assert all(t not in made.text for t in TOKENS)
    assert sorted(memory_keyring.items.values()) == [f"{PAGE_TOKEN}-1", f"{PAGE_TOKEN}-3"]

    used = client.post("/api/accounts/facebook/link", json={"ticket": ticket, "ig_user_ids": ["ig1"]})
    assert (used.status_code, used.json()["error"], used.json()["field"]) == (400, "link_expired", "ticket")
    none = client.post("/api/accounts/facebook/link", json={"ticket": ticket, "ig_user_ids": []})
    assert (none.status_code, none.json()["field"]) == (422, "ig_user_ids")


def test_linking_a_handle_already_here_is_refused(client, engine, memory_keyring):
    add_account(engine, "instagram", "clips_3", method="browser")
    ticket = client.post("/api/accounts/facebook/pages", json={"token": USER_TOKEN}).json()["ticket"]
    taken = client.post("/api/accounts/facebook/link", json={"ticket": ticket, "ig_user_ids": ["ig3"]})
    assert (taken.status_code, taken.json()["error"]) == (409, "handle_taken")
    assert len(store.list_accounts(engine.db())) == 1  # no half-made account is left behind
    assert memory_keyring.items == {}


def test_token_route_links_mode_b(client, engine, graph, memory_keyring):
    made = client.post(
        "/api/accounts", json={"platform": "instagram", "method": "api", "token": IG_TOKEN, "label": "Drama"}
    )
    assert made.status_code == 201
    body = made.json()
    assert body["job_id"] is None
    account = body["account"]
    assert (account["handle"], account["ig_mode"], account["status"], account["label"]) == (
        "clips_b",
        "instagram",
        "connected",
        "Drama",
    )
    assert IG_TOKEN not in made.text
    no_token = client.post("/api/accounts", json={"platform": "instagram", "method": "api"})
    assert (no_token.status_code, no_token.json()["field"]) == (422, "token")
    browser_token = client.post(
        "/api/accounts", json={"platform": "tiktok", "method": "browser", "token": "x"}
    )
    assert (browser_token.status_code, browser_token.json()["field"]) == (422, "token")

    store.update_account(engine.db(), account["id"], status="needs_login")
    again = client.post(f"/api/accounts/{account['id']}/token", json={"token": f"{IG_TOKEN}-new"})
    assert again.status_code == 200
    assert again.json()["account"]["status"] == "connected"
    assert keychain.get_token(keychain.ref_for(account["id"])) == f"{IG_TOKEN}-new"
    assert IG_TOKEN not in again.text

    graph.me |= {"user_id": "17841499", "username": "someone_else"}
    other = client.post(f"/api/accounts/{account['id']}/token", json={"token": f"{IG_TOKEN}-other"})
    assert (other.status_code, other.json()["error"], other.json()["field"]) == (
        400,
        "other_account",
        "token",
    )
    mode_a = ig_account(engine)
    refused = client.post(f"/api/accounts/{mode_a.id}/token", json={"token": IG_TOKEN})
    assert (refused.status_code, refused.json()["error"]) == (400, "reconnect_with_facebook")
    tiktok = add_account(engine)
    refused = client.post(f"/api/accounts/{tiktok.id}/token", json={"token": IG_TOKEN})
    assert (refused.status_code, refused.json()["error"]) == (400, "log_in_instead")
    assert client.post("/api/accounts/nope/token", json={"token": IG_TOKEN}).json() == NOT_FOUND


def test_check_runs_the_api_check_and_queues_the_browser_one(client, engine, monkeypatch):
    linked = ig_account(engine)
    checked = client.post(f"/api/accounts/{linked.id}/check")
    assert checked.status_code == 200
    body = checked.json()
    assert body["job_id"] is None and body["health"]["status"] == "connected"
    assert body["account"]["last_checked_at"] == NOW.timestamp()
    store.update_account(engine.db(), linked.id, status="token_expiring")
    assert client.post(f"/api/accounts/{linked.id}/check").json()["account"]["status"] == "token_expiring"
    assert client.post(f"/api/accounts/{linked.id}/selftest").status_code == 400

    tiktok = add_account(engine, "tiktok", "mine")
    queued = client.post(f"/api/accounts/{tiktok.id}/check")
    assert queued.status_code == 202
    body = queued.json()
    assert body["health"] is None
    job = engine.runner.get(body["job_id"])
    assert (job.kind, job.ref_id) == ("account.selftest", tiktok.id)
    assert views.job_json(engine, job)["label"] == "Checking the account's page"
    assert client.post(f"/api/accounts/{tiktok.id}/selftest").json()["job_id"] == body["job_id"]

    monkeypatch.setattr(browser_run, "self_test", lambda eng, a: SelfTestResult(["caption"], ["file_input"]))
    done = run_job(engine, body["job_id"])
    assert done.status == "done" and done.result["status"] == "error"
    after = store.get_account(engine.db(), tiktok.id)
    assert (after.status, after.status_note, after.last_checked_at) == ("error", CHANGED, NOW.timestamp())
    assert client.post("/api/accounts/nope/check").json() == NOT_FOUND


def test_window_refusals_say_what_is_wrong(client, engine):
    """The engine's own sentence about the windows reaches the window, not the generic one."""
    url = f"/api/accounts/{add_account(engine).id}"
    for windows, sentence in [
        ([{"start": "11:00", "end": "09:00"}], WINDOW_WRONG),
        ([{"start": "9am", "end": "10:00"}], WINDOW_WRONG),
        ([{"start": "09:00", "end": "10:00"}] * 9, TOO_MANY_WINDOWS),
    ]:
        refused = client.patch(url, json={"settings": {"windows": windows}})
        assert (refused.status_code, refused.json()["field"]) == (422, "settings.windows")
        assert refused.json()["message"] == sentence, windows
    # A field no validator has a sentence for still gets the plain one.
    assert client.patch(url, json={"settings": {"daily_cap": "lots"}}).json()["message"] == INVALID


def test_patch_account_validates_fields(client, engine):
    tiktok, gram = add_account(engine, "tiktok", "mine"), add_account(engine, "instagram", "gram")
    other = add_account(engine, "instagram", "other")
    url = f"/api/accounts/{tiktok.id}"
    changed = client.patch(
        url,
        json={
            "label": "  Gaming\u0007 clips ",
            "settings": {
                "niche_tags": ["Gaming", "gaming", "streamers"],
                "hashtags": ["#Fyp", "fyp", "gaming"],
                "daily_cap": 3,
                "windows": [{"start": "09:00", "end": "11:00"}],
            },
        },
    )
    assert changed.status_code == 200
    account = changed.json()["account"]
    assert account["label"] == "Gaming clips"
    assert account["settings"]["niche_tags"] == ["gaming", "streamers"]
    assert account["settings"]["hashtags"] == ["Fyp", "gaming"]
    assert account["settings"]["windows"] == [{"start": "09:00", "end": "11:00"}]
    assert account["today"]["cap"] == 3 and account["settings"]["assisted"] is False
    kept = client.patch(url, json={"settings": {"cta": "Follow for part 2"}}).json()["account"]
    assert kept["settings"]["daily_cap"] == 3 and kept["settings"]["cta"] == "Follow for part 2"
    cleared = client.patch(url, json={"settings": {"daily_cap": None, "windows": None}}).json()["account"]
    assert cleared["settings"]["daily_cap"] is None and cleared["settings"]["windows"] is None

    def pair(account_id):
        return store.get_account(engine.db(), account_id).settings.pair_id

    assert client.patch(url, json={"settings": {"pair_id": gram.id}}).status_code == 200
    assert (pair(tiktok.id), pair(gram.id)) == (gram.id, tiktok.id)  # pairs are kept both ways
    client.patch(url, json={"settings": {"pair_id": other.id}})
    assert (pair(tiktok.id), pair(gram.id), pair(other.id)) == (other.id, None, tiktok.id)
    client.patch(url, json={"settings": {"pair_id": None}})
    assert (pair(tiktok.id), pair(other.id)) == (None, None)
    assert client.patch(url, json={"paused": True}).json()["account"]["paused"] is True
    assert client.patch(url, json={"paused": False}).json()["account"]["paused"] is False

    for body, field in [
        ({"settings": {"daily_cap": 11}}, "settings.daily_cap"),
        ({"settings": {"daily_cap": 0}}, "settings.daily_cap"),
        ({"settings": {"windows": [{"start": "11:00", "end": "09:00"}]}}, "settings.windows"),
        ({"settings": {"windows": [{"start": "09:00", "end": "10:00"}] * 9}}, "settings.windows"),
        ({"settings": {"pair_id": add_account(engine).id}}, "settings.pair_id"),  # the same platform
        ({"settings": {"pair_id": "nope"}}, "settings.pair_id"),
        ({"settings": {"niche_tags": [f"t{n}" for n in range(11)]}}, "settings.niche_tags"),
        ({"settings": {"hashtags": [f"t{n}" for n in range(11)]}}, "settings.hashtags"),
        ({"settings": {"cta": "x" * 151}}, "settings.cta"),
        ({"label": "x" * 61}, "label"),
        ({"handle": "mine2"}, "handle"),  # a TikTok account's handle comes from its login
        ({"settings": {"surprise": 1}}, "settings.surprise"),
        ({"token_ref": "account:x"}, "token_ref"),
    ]:
        refused = client.patch(url, json=body)
        assert (refused.status_code, refused.json()["field"]) == (422, field), body
        assert refused.json()["message"], body
    assert client.patch("/api/accounts/nope", json={"label": "x"}).json() == NOT_FOUND

    typed = add_account(engine, "instagram", "typed", method="browser")
    renamed = client.patch(f"/api/accounts/{typed.id}", json={"handle": "@New.Name"})
    assert renamed.json()["account"]["handle"] == "new.name"
    bad = client.patch(f"/api/accounts/{typed.id}", json={"handle": "bad name!"})
    assert (bad.status_code, bad.json()["field"]) == (422, "handle")


def test_delete_account_removes_token_and_profile_and_cancels_posts(client, engine, memory_keyring):
    tiktok = add_account(engine, "tiktok", "mine")
    profile = engine.browser.profile_dir(tiktok)
    (profile / "Cookies").write_text("a session")
    store.update_account(engine.db(), tiktok.id, profile_dir=str(profile))
    avatar = engine.paths.accounts / tiktok.id / "avatar.png"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"png")
    linked = ig_account(engine)
    waiting = custom(engine, add_clip(engine, 1), tiktok, at(7, 10))
    went = custom(engine, add_clip(engine, 2), tiktok, at(19))
    store.update_post(engine.db(), went.id, status="posted", posted_at=at(19).timestamp())
    theirs = custom(engine, add_clip(engine, 3), linked, at(7, 30))

    gone = client.delete(f"/api/accounts/{tiktok.id}")
    assert gone.status_code == 204
    assert store.get_post(engine.db(), waiting.id).status == "cancelled"
    assert not jobs(engine, waiting.id)
    assert store.get_post(engine.db(), went.id).status == "posted"  # what went up stays in the history
    assert store.get_post(engine.db(), theirs.id).status == "scheduled"
    assert not profile.exists() and not avatar.parent.exists()
    assert store.get_account(engine.db(), tiktok.id) is None
    assert store.get_account(engine.db(), tiktok.id, deleted=True).deleted_at is not None
    assert [a["id"] for a in client.get("/api/accounts").json()["accounts"]] == [linked.id]
    assert client.delete(f"/api/accounts/{tiktok.id}").json() == NOT_FOUND

    assert client.delete(f"/api/accounts/{linked.id}").status_code == 204
    assert memory_keyring.items == {}
    assert store.get_post(engine.db(), theirs.id).status == "cancelled"
