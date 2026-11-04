"""The Instagram publisher and linking over Meta's Graph API (SPEC 10.2), against the look-alike in
tests/fakes_graph.py: the calls in order, the checkpoints a resumed post carries on from, what each of
Meta's errors does to the post and the account, and the tokens kept out of everything but the Keychain."""

import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from clipforge.errors import ClipForgeError
from clipforge.post import keychain, queue, store
from clipforge.post.distribute import full_caption
from clipforge.post.errors import LifecycleError
from clipforge.post.graph import GRAPH_FB, GraphClient, GraphError
from clipforge.post.instagram_api import (
    OPEN_APP,
    InstagramGraphPublisher,
    caps_for,
    finish_facebook,
    link_facebook,
    link_instagram,
    refresh_tokens,
)
from clipforge.post.models import Account, PostEdit, PostOptions
from clipforge.trends import instagram
from clipforge.trends import store as trends
from clipforge.trends.models import PostSound
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import (
    APP_SECRET,
    IG_TOKEN,
    PAGE_TOKEN,
    TOKENS,
    USER_TOKEN,
    FakeGraph,
    audio_row,
    error,
    graph_time,
    lost,
)
from tests.posting import add_account, add_clip, at, confirm, custom, post, run, sydney

NOW = at(7, 10)
DAY = 86400.0


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


def ig_account(engine, mode: str = "facebook", token: str | None = None, **fields) -> Account:
    """A linked Instagram account, its token in the Keychain."""
    account = add_account(engine, "instagram")  # a handle of its own
    ref = keychain.ref_for(account.id)
    keychain.put_token(ref, token or (f"{PAGE_TOKEN}-1" if mode == "facebook" else IG_TOKEN))
    ids = {"ig_user_id": "ig1", "fb_page_id": "pg1" if mode == "facebook" else None}
    return store.update_account(engine.db(), account.id, ig_mode=mode, token_ref=ref, **ids | fields)


def due(engine, clock, account=None, *, cover_s: float | None = 2.5):
    """A post of a fresh clip to ``account`` (a Mode A account when none), due now."""
    clip = add_clip(engine)
    if cover_s is not None:
        engine.db().execute("UPDATE renders SET cover_s = ? WHERE id = ?", (cover_s, clip.current_render_id))
    return custom(engine, clip, account or ig_account(engine), clock())


def video_bytes(engine, p) -> bytes:
    from clipforge import repo

    return Path(repo.get_render(engine.db(), p.render_id).path).read_bytes()


def account(engine, account_id: str) -> Account:
    return store.get_account(engine.db(), account_id)


# Linking


def test_link_facebook_keeps_page_tokens_and_drops_the_user_token(engine, graph, memory_keyring):
    ticket, found = link_facebook(engine, USER_TOKEN)
    assert [c.ig_user_id for c in found] == ["ig1", "ig3"]
    (made,) = finish_facebook(engine, ticket, ["ig3"])
    assert (made.platform, made.method, made.ig_mode, made.status) == (
        "instagram",
        "api",
        "facebook",
        "connected",
    )
    assert (made.ig_user_id, made.fb_page_id, made.handle, made.display_name) == (
        "ig3",
        "pg3",
        "clips_3",
        "Page 3",
    )
    assert made.token_ref == keychain.ref_for(made.id)
    assert keychain.get_token(made.token_ref) == f"{PAGE_TOKEN}-3"
    assert USER_TOKEN not in memory_keyring.items.values()
    dump = "\n".join(engine.db().iterdump())
    assert all(t not in dump for t in TOKENS) and f"{PAGE_TOKEN}-3" not in dump
    assert ticket not in engine.link_tickets
    with pytest.raises(ClipForgeError) as err:
        finish_facebook(engine, ticket, ["ig1"])  # a ticket is used once
    assert err.value.code == "link_expired"

    again, _ = link_facebook(engine, "short-token-for-tests-1", app_id="1234", app_secret=APP_SECRET)
    assert graph.calls()[-3:] == ["exchange", "pages", "pages"]
    (same,) = finish_facebook(engine, again, ["ig3"])
    assert same.id == made.id  # linking an account again updates it
    assert len(store.list_accounts(engine.db())) == 1
    assert APP_SECRET not in memory_keyring.items.values()
    assert APP_SECRET not in "\n".join(engine.db().iterdump())


def test_link_ticket_expires_after_ten_minutes(engine, clock):
    ticket, _ = link_facebook(engine, USER_TOKEN)
    clock.advance(minutes=10, seconds=1)
    with pytest.raises(ClipForgeError) as err:
        finish_facebook(engine, ticket, ["ig1"])
    assert err.value.code == "link_expired"
    assert store.list_accounts(engine.db()) == []
    with pytest.raises(ClipForgeError) as err:
        finish_facebook(engine, "no-such-ticket", ["ig1"])
    assert err.value.code == "link_expired"


def test_link_instagram_validates_account_type(engine, graph, clock, memory_keyring):
    graph.me["account_type"] = "PERSONAL"
    with pytest.raises(ClipForgeError) as err:
        link_instagram(engine, IG_TOKEN)
    assert err.value.code == "not_professional"
    assert store.list_accounts(engine.db()) == [] and memory_keyring.items == {}

    graph.script["me"] = [error(190, None, "Invalid OAuth access token")]
    with pytest.raises(ClipForgeError) as err:
        link_instagram(engine, IG_TOKEN)
    assert err.value.code == "token_rejected"

    graph.me["account_type"] = "MEDIA_CREATOR"
    made = link_instagram(engine, IG_TOKEN)
    assert (made.ig_mode, made.ig_user_id, made.handle, made.status) == (
        "instagram",
        "17841400",
        "clips_b",
        "connected",
    )
    assert made.token_expires_at == NOW.timestamp() + 60 * DAY
    assert made.token_refreshed_at == NOW.timestamp()
    assert keychain.get_token(made.token_ref) == IG_TOKEN

    store.update_account(engine.db(), made.id, status="needs_login")
    again = link_instagram(engine, f"{IG_TOKEN}-new", made.id)  # Reconnect
    assert (again.id, again.status) == (made.id, "connected")
    assert keychain.get_token(made.token_ref) == f"{IG_TOKEN}-new"

    graph.me |= {"user_id": "17841499", "username": "someone_else"}
    with pytest.raises(ClipForgeError) as err:
        link_instagram(engine, f"{IG_TOKEN}-other", made.id)  # another account's key
    assert err.value.code == "other_account" and "@clips_b" in err.value.message
    assert keychain.get_token(made.token_ref) == f"{IG_TOKEN}-new"


def test_caps_by_mode(engine):
    a = caps_for(ig_account(engine))
    b = caps_for(ig_account(engine, "instagram"))
    assert (a.local_file, a.needs_public_url) == (True, False)
    assert (b.local_file, b.needs_public_url) == (False, True)
    assert (a.max_duration_s, a.max_size_mb, a.daily_cap) == (900, 300, 50)


# Posting


def test_publish_happy_path_mode_a(engine, graph, clock):
    p = due(engine, clock)
    job, after = run(engine, p.id)
    assert graph.calls() == ["limit", "container", "upload", "status", "publish", "media"]
    page = f"OAuth {PAGE_TOKEN}-1"
    assert all(r.headers["authorization"] == page for r in graph.requests)
    assert all(PAGE_TOKEN not in str(r.url) for r in graph.requests)
    limit, made, upload, status, publish, media = graph.requests
    assert str(limit.url).startswith("https://graph.facebook.com/v25.0/ig1/content_publishing_limit?")
    assert limit.url.params["fields"] == "config,quota_usage"
    assert (made.method, made.url.path) == ("POST", "/v25.0/ig1/media")
    assert graph.form(made) == {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": full_caption(p.caption, p.hashtags),
        "share_to_feed": "true",
        "thumb_offset": "2500",
    }
    body = video_bytes(engine, p)
    assert upload.url.host == "rupload.facebook.com"
    assert (upload.headers["offset"], upload.headers["file_size"], upload.content) == (
        "0",
        str(len(body)),
        body,
    )
    assert status.url.params["fields"] == "status_code,status,copyright_check_status"
    assert (publish.method, publish.url.path, publish.url.params["creation_id"]) == (
        "POST",
        "/v25.0/ig1/media_publish",
        "c1",
    )
    assert media.url.params["fields"] == "permalink,timestamp,media_product_type"
    assert job.status == "done"
    assert (after.status, after.external_id, after.url) == (
        "posted",
        "m1",
        "https://www.instagram.com/reel/m1/",
    )
    assert after.note is None
    assert after.state["container"] == "c1" and after.state["media_id"] == "m1"
    steps = [json.loads(line)["step"] for line in Path(after.log_path).read_text().splitlines()]
    assert steps[:2] == ["limit", "container"] and "publish" in steps


def test_no_cover_frame_leaves_the_offset_out(engine, graph, clock):
    run(engine, due(engine, clock, cover_s=None).id)
    assert "thumb_offset" not in graph.form(graph.of("container")[0])


def test_upload_resumes_from_bytes_transferred(engine, graph, clock):
    p = due(engine, clock)

    def dropped(request):
        graph.transferred = 3
        raise httpx.ReadTimeout("the connection dropped", request=request)

    graph.script["upload"] = [dropped]
    job, after = run(engine, p.id)
    assert (job.status, job.not_before) == ("queued", NOW.timestamp() + 60)
    assert after.status == "uploading" and after.state["container"] == "c1"
    clock.advance(seconds=60)
    job, after = run(engine, p.id)
    assert after.status == "posted"
    assert graph.calls() == ["limit", "container", "upload", "offset", "upload", "status", "publish", "media"]
    resumed = graph.of("upload")[-1]
    body = video_bytes(engine, p)
    assert (resumed.headers["offset"], resumed.content) == ("3", body[3:])
    assert len(graph.of("container")) == 1


def test_polls_every_fifteen_seconds_via_wait_until(engine, graph, clock):
    graph.statuses = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]
    p = due(engine, clock)
    for _ in range(2):
        job, after = run(engine, p.id)
        assert (job.status, job.not_before) == ("queued", clock().timestamp() + 15)
        assert after.status == "processing"
        assert after.state["poll_started"] == NOW.timestamp()
        clock.advance(seconds=15)
    job, after = run(engine, p.id)
    assert after.status == "posted"
    assert [len(graph.of(k)) for k in ("container", "upload", "status", "publish")] == [1, 1, 3, 1]


def test_poll_gives_up_after_ten_minutes(engine, graph, clock):
    graph.statuses = ["IN_PROGRESS"]
    p = due(engine, clock)
    run(engine, p.id)
    clock.advance(seconds=300)
    job, after = run(engine, p.id)
    assert (job.status, after.status) == ("queued", "processing")
    clock.advance(seconds=301)
    job, after = run(engine, p.id)
    assert (job.status, after.status, after.error_code) == ("done", "failed", "processing_timeout")
    assert "10 minutes" in after.error
    assert graph.of("publish") == []


def test_copyright_match_needs_you_then_publish_anyway(engine, graph, clock):
    graph.copyright = {"status": "completed", "matches_found": True}
    p = due(engine, clock)
    job, after = run(engine, p.id)
    assert (job.status, after.status) == ("done", "needs_you")
    assert after.state["actions"] == ["publish_anyway", "cancel"]
    assert "someone else owns" in after.error
    assert graph.of("publish") == []

    engine.posting.publish_anyway(p.id)
    moved = post(engine, p.id)
    assert (moved.status, moved.scheduled_at) == ("scheduled", NOW.timestamp())
    assert moved.state["copyright_ok"] is True and "actions" not in moved.state
    job, after = run(engine, p.id)
    assert after.status == "posted"
    assert len(graph.of("container")) == 1 and len(graph.of("publish")) == 1


def test_publish_anyway_only_for_a_copyright_match(engine, graph, clock):
    graph.script["container"] = [error(25, 2207050)]
    p = due(engine, clock)
    run(engine, p.id)
    with pytest.raises(LifecycleError):
        engine.posting.publish_anyway(p.id)


def test_caption_not_attached_warns(engine, graph, clock):
    graph.config_issue = "CAPTION_NOT_ATTACHED"
    _, after = run(engine, due(engine, clock).id)
    assert after.status == "posted"
    assert after.note == "Instagram posted this without its caption. Add it in the Instagram app."


def test_resume_never_publishes_twice(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lost(graph)]  # Meta publishes it, but the answer never comes back
    job, after = run(engine, p.id)
    assert (job.status, job.not_before, after.status) == ("queued", NOW.timestamp() + 60, "processing")
    assert after.state["publish_requested_at"] == NOW.timestamp()
    clock.advance(seconds=60)
    job, after = run(engine, p.id)
    assert (after.status, after.external_id, after.url) == (
        "posted",
        "m1",
        "https://www.instagram.com/reel/m1/",
    )
    assert len(graph.of("publish")) == 1
    assert graph.calls()[-3:] == ["status", "recent", "media"]


def test_resumed_publish_not_found_posts_without_a_link(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lost(graph)]
    run(engine, p.id)
    graph.recent[0]["caption"] = "Something else"
    clock.advance(seconds=60)
    _, after = run(engine, p.id)
    assert (after.status, after.url, after.external_id) == ("posted", None, None)
    assert after.note == "Instagram shows this as posted, but ClipForge couldn't find its link."
    assert len(graph.of("publish")) == 1


def test_resume_when_instagram_never_got_the_publish_needs_you(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lost()]  # the request never reached Meta
    run(engine, p.id)
    clock.advance(seconds=60)
    job, after = run(engine, p.id)
    assert (job.status, after.status) == ("done", "needs_you")
    assert "may already have posted" in after.error
    assert len(graph.of("publish")) == 1

    engine.posting.retry(p.id)  # Zyad checked: it isn't there
    assert "publish_requested_at" not in post(engine, p.id).state
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert len(graph.of("publish")) == 2


def test_a_publish_answer_without_an_id_is_checked_not_posted_again(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lambda r: (graph._publish(r), httpx.Response(200, json={}))[1]]
    job, after = run(engine, p.id)  # Meta published it and answered without the id
    assert (job.status, job.not_before, after.status) == ("queued", NOW.timestamp() + 60, "processing")
    clock.advance(seconds=60)
    _, after = run(engine, p.id)
    assert (after.status, after.external_id) == ("posted", "m1")
    assert len(graph.of("publish")) == 1


def late_publish(graph, clock, container: str = "c1"):
    """The container goes up on Instagram after ClipForge last read it."""
    graph.published.append(container)
    caption = graph.containers[container]["caption"]
    graph.recent.insert(0, {"id": "m1", "caption": caption, "timestamp": graph_time(clock())})


def test_a_container_found_published_on_retry_is_posted_not_polled(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lost()]
    run(engine, p.id)
    clock.advance(seconds=60)
    _, after = run(engine, p.id)  # the container still read FINISHED
    assert after.status == "needs_you"
    late_publish(graph, clock)  # then it went up
    engine.posting.retry(p.id)  # it wasn't on the account yet when Zyad looked
    clock.set(sydney(post(engine, p.id).scheduled_at))
    job, after = run(engine, p.id)
    assert (job.status, after.status, after.external_id) == ("done", "posted", "m1")
    assert len(graph.of("publish")) == 1


def test_retrying_a_failed_post_reads_its_published_container_first(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lambda r: (graph._publish(r), error(100, None, "Invalid parameter"))[1]]
    _, after = run(engine, p.id)  # Meta said no, and posted it all the same
    assert (after.status, after.error_code) == ("failed", "rejected")
    engine.posting.retry(p.id)
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert (after.status, after.external_id) == ("posted", "m1")
    assert len(graph.of("container")) == 1 and len(graph.of("publish")) == 1


def test_5xx_retries_safe_steps_only(engine, graph, clock):
    p = due(engine, clock)
    graph.script["limit"] = [httpx.Response(502, text="<html>Bad gateway</html>")]
    graph.script["status"] = [
        httpx.Response(503, json={"error": {"code": 2, "message": "Service unavailable"}})
    ]
    graph.script["publish"] = [httpx.Response(500, json={"error": {"code": 1, "message": "Unknown error"}})]
    job, after = run(engine, p.id)  # the publishing limit is a GET
    assert (job.status, job.not_before, after.status) == ("queued", NOW.timestamp() + 60, "uploading")
    clock.advance(seconds=60)
    job, after = run(engine, p.id)  # the status read is a GET
    assert (job.status, job.not_before) == ("queued", clock().timestamp() + 300)
    clock.advance(seconds=300)
    job, after = run(engine, p.id)  # media_publish is not sent again: it is checked
    assert (job.status, after.status) == ("queued", "processing")
    assert after.state["publish_requested_at"] == clock().timestamp()
    clock.advance(seconds=60)
    job, after = run(engine, p.id)
    assert after.status == "needs_you"
    assert len(graph.of("publish")) == 1


def test_a_definite_publish_error_fails_and_is_not_checked(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [error(100, None, "Invalid parameter")]
    job, after = run(engine, p.id)
    assert (job.status, after.status, after.error_code) == ("done", "failed", "rejected")
    assert "publish_requested_at" not in after.state


def test_rate_limit_waits_an_hour(engine, graph, clock):
    p = due(engine, clock)
    graph.script["container"] = [error(4, None, "Application request limit reached")]
    job, after = run(engine, p.id)
    assert (job.status, job.not_before, after.status) == ("queued", NOW.timestamp() + 3600, "uploading")
    assert "slow down" in after.note
    clock.advance(hours=1)
    _, after = run(engine, p.id)
    assert after.status == "posted"


def test_full_quota_moves_the_post_a_day(engine, graph, clock):
    graph.quota = (50, 50)
    p = due(engine, clock)
    job, after = run(engine, p.id)
    assert (after.status, after.scheduled_at) == ("scheduled", NOW.timestamp() + DAY)
    assert job.status == "queued" and job.not_before == after.scheduled_at
    assert "24 hour" in after.note
    assert graph.calls() == ["limit"]


def errored(engine, graph, clock, answer):
    """A post whose container creation got ``answer``: the job, the post and its account after, and
    whether an account event went out."""
    a = ig_account(engine)
    p = due(engine, clock, a)
    graph.script["container"] = [answer]
    sub = engine.events.subscribe()
    job, after = run(engine, p.id)
    told = []
    while (event := sub.get(timeout=0)) is not None:
        told.append(event.topic == "account" and event.data["id"] == a.id)
    return job, after, account(engine, a.id), any(told)


def test_suspected_spam_pauses_and_fails_for_good(engine, graph, clock):
    job, after, a, told = errored(engine, graph, clock, error(4, 2207051, "Suspected spam"))
    assert (job.status, after.status, after.error_code) == ("done", "failed", "suspected_spam")
    assert (a.paused, told) == (True, True)
    assert len(graph.of("container")) == 1


def test_a_restricted_account_pauses_and_needs_you(engine, graph, clock):
    job, after, a, told = errored(engine, graph, clock, error(25, 2207050, "Restricted"))
    assert (job.status, after.status) == ("done", "needs_you")
    assert (
        after.error
        == OPEN_APP
        == "Open the Instagram app on your phone, clear what it asks, then press Retry."
    )
    assert (a.paused, told) == (True, True)


def test_retry_after_clearing_it_in_the_app_turns_the_account_back_on(engine, graph, clock):
    _, after, a, _ = errored(engine, graph, clock, error(25, 2207050, "Restricted"))
    other = custom(engine, add_clip(engine), a, clock())  # held while the account is paused
    engine.posting.retry(after.id)  # cleared in the app, as the sentence says
    assert (account(engine, a.id).paused, account(engine, a.id).status_note) == (False, None)
    assert post(engine, other.id).status == "scheduled"


def test_retry_leaves_an_account_paused_by_hand_paused(engine, graph, clock):
    _, after, a, _ = errored(engine, graph, clock, error(25, 2207050, "Restricted"))
    engine.posting.pause_account(a.id, False)
    engine.posting.pause_account(a.id, True)  # paused again by hand, for its own reasons
    engine.posting.retry(after.id)
    assert account(engine, a.id).paused


def test_error_190_asks_to_reconnect(engine, graph, clock):
    job, after, a, told = errored(engine, graph, clock, error(190, None, "Session expired"))
    assert (job.status, after.status) == ("done", "needs_you")
    assert "Reconnect Facebook" in after.error
    assert (a.status, a.paused, told) == ("needs_login", False, True)


def test_the_daily_cap_moves_the_post_a_day(engine, graph, clock):
    job, after, a, _ = errored(engine, graph, clock, error(9, 2207042, "Max posts reached"))
    assert (after.status, after.scheduled_at) == ("scheduled", NOW.timestamp() + DAY)
    assert (job.status, job.not_before) == ("queued", after.scheduled_at)
    assert (a.status, a.paused) == ("connected", False)


def test_an_expired_container_is_made_again_once(engine, graph, clock):
    graph.statuses = ["EXPIRED"]
    p = due(engine, clock)
    job, after = run(engine, p.id)
    assert len(graph.of("container")) == 2
    assert (after.status, after.error_code) == ("failed", "upload_failed")

    graph.statuses = ["FINISHED"]
    graph.script["status"] = [
        httpx.Response(200, json={"id": "c3", "status_code": "ERROR", "status": "Error: 2207020"}),
    ]
    q = due(engine, clock)
    _, after = run(engine, q.id)
    assert after.status == "posted"
    assert len(graph.of("container")) == 4


def test_retry_after_a_failed_upload_sends_it_again(engine, graph, clock):
    graph.statuses = ["ERROR"]
    p = due(engine, clock)
    _, after = run(engine, p.id)
    assert (after.status, after.error_code) == ("failed", "upload_failed")
    graph.statuses = ["FINISHED"]
    engine.posting.retry(p.id)
    assert not {"container", "uri", "uploaded", "poll_started"} & set(post(engine, p.id).state)
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert [len(graph.of(k)) for k in ("container", "upload", "publish")] == [2, 2, 1]


def test_retry_after_copyright_keeps_the_container(engine, graph, clock):
    graph.copyright = {"status": "completed", "matches_found": True}
    p = due(engine, clock)
    run(engine, p.id)
    engine.posting.retry(p.id)  # not Publish anyway: Instagram is asked again
    assert post(engine, p.id).state["container"] == "c1"
    graph.copyright = {"status": "completed", "matches_found": False}
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert len(graph.of("container")) == 1


def test_a_paused_account_stops_before_publish(engine, graph, clock):
    a = ig_account(engine)
    p = due(engine, clock, a)

    def pause_meanwhile(request):
        store.update_account(engine.db(), a.id, paused=True)

    graph.script["status"] = [pause_meanwhile]
    _, after = run(engine, p.id)
    assert after.status == "needs_you"  # processing can't go back to scheduled
    assert graph.of("publish") == []


def sound_post(engine, account, when, sound: PostSound):
    """A fresh clip confirmed to ``account`` at ``when`` with ``sound`` picked for it in the Post dialog."""
    clip = add_clip(engine)
    key = f"{clip.id}:{account.id}"
    edit = PostEdit(options=PostOptions(sound=sound))
    _, (p,) = confirm(
        engine, [clip], [account], timing="custom", custom_times={key: when.timestamp()}, edits={key: edit}
    )
    return p


def test_container_carries_the_sound(engine, graph, clock):
    sound = PostSound(source="ig_trending", external_id="a1", title="Song a1", artist="The Artist")
    p = sound_post(engine, ig_account(engine), clock(), sound)
    assert p.options.sound == sound
    _, after = run(engine, p.id)
    assert after.status == "posted"
    (made,) = graph.of("container")
    assert graph.form(made)["audio_configuration"] == '{"audio_id":"a1","video_volume":100,"audio_volume":15}'


def test_post_keeps_its_sound_after_the_list_changes(engine, graph, clock):
    a = ig_account(engine)
    graph.audio["music"] = [audio_row("a1")]
    instagram.read_account(engine, a.id)
    (row,) = trends.sounds(engine.db(), source="ig_trending", account_id=a.id)
    picked = PostSound(
        source="ig_trending", external_id=row.external_id, title=row.title, artist=row.artist, volume=30
    )
    tomorrow = clock() + timedelta(days=1)
    p = sound_post(engine, a, tomorrow, picked)
    clock.set(tomorrow)
    graph.audio["music"] = [audio_row("b1")]
    instagram.read_account(engine, a.id)  # the morning's list no longer has it
    assert [s.external_id for s in trends.sounds(engine.db(), source="ig_trending")] == ["b1"]
    assert post(engine, p.id).options.sound == picked
    graph.script["container"] = [error(100, None, "The audio isn't available")]
    _, after = run(engine, p.id)
    sent = json.loads(graph.form(graph.of("container")[0])["audio_configuration"])
    assert sent == {"audio_id": "a1", "video_volume": 100, "audio_volume": 30}
    assert (after.status, after.error_code) == ("failed", "rejected")
    assert after.error == "Instagram turned this post down: The audio isn't available"
    engine.posting.retry(p.id)
    engine.posting.edit(p.id, drop_sound=True)  # Edit takes the sound off
    assert post(engine, p.id).options.sound is None
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert "audio_configuration" not in graph.form(graph.of("container")[-1])


def test_a_refused_sound_is_left_off_when_tried_again(engine, graph, clock):
    """Post without the sound: a post Instagram turned down goes again at the next free slot with the
    clip's own audio only."""
    sound = PostSound(source="ig_trending", external_id="a1", title="Song a1", artist="The Artist")
    p = sound_post(engine, ig_account(engine), clock(), sound)
    graph.script["container"] = [error(100, None, "The audio isn't available")]
    _, after = run(engine, p.id)
    assert (after.status, after.error_code) == ("failed", "rejected")
    again = engine.posting.retry(p.id, drop_sound=True)
    assert (again.status, again.options.sound, again.error, again.error_code) == (
        "scheduled",
        None,
        None,
        None,
    )
    clock.set(sydney(again.scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert "audio_configuration" not in graph.form(graph.of("container")[-1])


def test_leaving_the_sound_off_makes_a_new_container(engine, graph, clock):
    a = ig_account(engine)
    sound = PostSound(source="ig_trending", external_id="a1", title="Song a1", artist="The Artist")
    p = sound_post(engine, a, clock(), sound)
    graph.script["status"] = [lambda r: (store.update_account(engine.db(), a.id, paused=True), None)[1]]
    _, after = run(engine, p.id)  # stopped with its container made, the sound in it
    assert (after.status, after.state["container"]) == ("needs_you", "c1")
    store.update_account(engine.db(), a.id, paused=False)
    engine.posting.retry(p.id, drop_sound=True)
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert graph.published == ["c2"]
    assert "audio_configuration" not in graph.form(graph.of("container")[-1])


def test_the_sound_stays_on_a_post_instagram_may_have_up(engine, graph, clock):
    sound = PostSound(source="ig_trending", external_id="a1", title="Song a1", artist="The Artist")
    p = sound_post(engine, ig_account(engine), clock(), sound)
    store.update_post(engine.db(), p.id, status="failed", state={"container": "c1", "asked": True})
    with pytest.raises(ClipForgeError) as caught:
        engine.posting.retry(p.id, drop_sound=True)
    assert caught.value.code == "already_asked"
    assert (post(engine, p.id).status, post(engine, p.id).options.sound) == ("failed", sound)


def test_editing_the_caption_makes_a_new_container(engine, graph, clock):
    a = ig_account(engine)
    p = due(engine, clock, a)
    graph.script["status"] = [lambda r: (store.update_account(engine.db(), a.id, paused=True), None)[1]]
    _, after = run(engine, p.id)  # stopped with its container made, the old caption in it
    assert (after.status, after.state["container"]) == ("needs_you", "c1")
    store.update_account(engine.db(), a.id, paused=False)
    engine.posting.retry(p.id)
    engine.posting.edit(p.id, caption="Fixed words")
    clock.set(sydney(post(engine, p.id).scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert graph.published == ["c2"]
    assert graph.containers["c2"]["caption"].startswith("Fixed words")


def test_a_post_instagram_was_asked_to_publish_cant_be_edited(engine, graph, clock):
    p = due(engine, clock)
    graph.script["publish"] = [lost()]
    run(engine, p.id)
    clock.advance(seconds=60)
    run(engine, p.id)  # it may have gone up
    engine.posting.retry(p.id)
    with pytest.raises(ClipForgeError) as err:
        engine.posting.edit(p.id, caption="Fixed words")
    assert err.value.code == "already_asked"
    assert post(engine, p.id).caption != "Fixed words"


def test_missing_token_needs_you_and_reconnect(engine, graph, clock, memory_keyring):
    a = ig_account(engine)
    memory_keyring.items.clear()
    _, after = run(engine, due(engine, clock, a).id)
    assert after.status == "needs_you"
    assert account(engine, a.id).status == "needs_login"
    assert graph.requests == []


def test_mode_b_without_a_public_address_needs_you(engine, graph, clock):
    _, after = run(engine, due(engine, clock, ig_account(engine, "instagram")).id)
    assert after.status == "needs_you"
    assert "public address" in after.error
    assert graph.of("container") == []


def test_check_reads_the_account(engine, graph, memory_keyring):
    publisher = InstagramGraphPublisher(engine)
    a = ig_account(engine)
    assert publisher.check(a).status == "connected"
    graph.script["limit"] = [error(190, None, "Session expired")]
    assert publisher.check(a).status == "needs_login"
    b = ig_account(engine, "instagram")
    assert publisher.check(b).status == "connected"
    assert graph.of("me")[-1].headers["authorization"] == f"Bearer {IG_TOKEN}"
    memory_keyring.items.clear()
    assert publisher.check(a).status == "needs_login"


# Mode B tokens


def mode_b(engine, refreshed_days_ago: float, expires_in_days: float) -> Account:
    t = NOW.timestamp()
    return ig_account(
        engine,
        "instagram",
        token_refreshed_at=t - refreshed_days_ago * DAY,
        token_expires_at=t + expires_in_days * DAY,
    )


def test_refresh_older_than_seven_days_once_at_a_time(engine, graph, clock):
    old, young = mode_b(engine, 8, 52), mode_b(engine, 6, 54)
    ig_account(engine)  # Mode A tokens don't expire
    graph.on_refresh = lambda: refresh_tokens(engine)  # a second refresh while the first is under way
    refresh_tokens(engine)
    assert graph.refreshes == 1
    now = account(engine, old.id)
    assert keychain.get_token(now.token_ref) == f"{IG_TOKEN}-refreshed-1"
    assert (now.token_refreshed_at, now.token_expires_at) == (NOW.timestamp(), NOW.timestamp() + 5184000)
    assert account(engine, young.id).token_refreshed_at == NOW.timestamp() - 6 * DAY
    assert graph.of("refresh")[0].headers["authorization"] == f"Bearer {IG_TOKEN}"

    graph.on_refresh = None
    clock.advance(days=7, seconds=1)
    queue.tick(engine)  # the posting check refreshes them
    assert graph.refreshes == 3


def test_token_expiring_ten_days_out(engine, graph, clock):
    soon, later = mode_b(engine, 50, 9), mode_b(engine, 30, 20)
    graph.script["refresh"] = [error(1, None, "Unknown", status=500), error(1, None, "Unknown", status=500)]
    refresh_tokens(engine)
    a, b = account(engine, soon.id), account(engine, later.id)
    assert a.status == "token_expiring" and "Reconnect" in a.status_note
    assert b.status == "connected" and b.status_note is None
    refresh_tokens(engine)  # works this time
    assert account(engine, soon.id).status == "connected"
    assert account(engine, soon.id).status_note is None

    graph.script["refresh"] = [error(190, None, "Session expired")] * 2
    clock.advance(days=8)
    refresh_tokens(engine)
    assert {account(engine, x.id).status for x in (soon, later)} == {"needs_login"}
    refresh_tokens(engine)  # a token Instagram refused isn't sent again
    assert graph.refreshes == 2


def test_tokens_never_reach_logs_or_errors(engine, graph, clock, caplog):
    caplog.set_level(logging.DEBUG)
    sub = engine.events.subscribe(maxsize=1000)
    token = f"{PAGE_TOKEN}-1"
    graph.script["container"] = [error(100, None, f"Invalid parameter access_token={token}")]
    p = due(engine, clock)
    _, after = run(engine, p.id)
    assert after.status == "failed"
    graph.script["limit"] = [httpx.ConnectError(f"no route for {token}")]
    q = due(engine, clock)
    _, later = run(engine, q.id)
    graph.script["media"] = [error(190, None, f"bad {token}")]
    with pytest.raises(GraphError) as err:
        GraphClient("v25.0", transport=graph.transport()).media(GRAPH_FB, "m1", token)
    seen = []
    while (event := sub.get(timeout=0)) is not None:
        seen.append(json.dumps(event.data))
    texts = [
        caplog.text,
        str(err.value),
        after.error or "",
        after.details or "",
        later.error or "",
        later.details or "",
        Path(after.log_path).read_text(),
        *seen,
    ]
    for t in (*TOKENS, token):
        assert all(t not in text for text in texts), t
    assert "[hidden]" in (after.details or "") or "[hidden]" in (after.error or "")


def test_the_default_publishers_include_instagram(engine):
    assert isinstance(engine.publishers["instagram:api"], InstagramGraphPublisher)
