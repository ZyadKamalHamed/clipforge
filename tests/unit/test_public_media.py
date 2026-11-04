"""Instagram Login's public media (SPEC 10.2 Mode B, 17): a post's video at a random public address, live
only while the post is with Instagram, which ClipForge fetches itself before Meta is told about it; and the
public host answering nothing else."""

import base64
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from clipforge.config import PostingSettings
from clipforge.post import keychain, store
from clipforge.post.instagram_api import SOUND_LOST
from clipforge.post.public_media import PublicMedia, public_url, self_test
from clipforge.trends.models import PostSound
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import IG_TOKEN, FakeGraph, error
from tests.posting import add_account, add_clip, custom, post, run
from tests.server import api_client
from tests.unit.test_instagram_api import NOW

BASE = "https://mac.tail.ts.net"
HOST = "mac.tail.ts.net"
UNREACHABLE = (
    "Instagram can't reach this Mac. Check that Tailscale Funnel is on for mac.tail.ts.net, then press Retry."
)


def with_base(engine, base: str | None) -> None:
    posting = engine.settings.posting.model_copy(update={"public_base_url": base})
    engine.settings = engine.settings.model_copy(update={"posting": posting})


def through_app(engine, fetched: list[httpx.Request]) -> httpx.MockTransport:
    """The public internet as far as the self-test goes: each request reaches ClipForge's own app, with
    no session cookie and no Origin, as Meta's fetch would."""
    client = api_client(engine, session=False, origin=False)

    def handle(request: httpx.Request) -> httpx.Response:
        fetched.append(request)
        headers = {"host": request.url.netloc.decode()}
        if "range" in request.headers:
            headers["range"] = request.headers["range"]
        r = client.request(request.method, request.url.raw_path.decode(), headers=headers)
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    return httpx.MockTransport(handle)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def fetched():
    return []


@pytest.fixture
def engine(clock, graph, fetched):
    eng = fake_engine(now=clock)
    eng.graph_transport = graph.transport()
    eng.public_transport = through_app(eng, fetched)
    with_base(eng, BASE)
    yield eng
    eng.close()


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(bytes(range(256)) * 20)  # 5120 bytes
    return path


def mode_b(engine):
    a = add_account(engine, "instagram")
    ref = keychain.ref_for(a.id)
    keychain.put_token(ref, IG_TOKEN)
    return store.update_account(engine.db(), a.id, ig_mode="instagram", ig_user_id="17841400", token_ref=ref)


def due(engine, clock):
    return custom(engine, add_clip(engine), mode_b(engine), clock())


def token_of(url: str) -> str:
    return url.rsplit("/", 2)[-2]


# The address


def test_token_is_32_random_bytes(video):
    media = PublicMedia()
    tokens = {media.issue(f"p{n}", video) for n in range(100)}
    assert len(tokens) == 100
    for t in tokens:
        assert len(base64.urlsafe_b64decode(t + "=")) == 32
    settings = PostingSettings(public_base_url=BASE + "/clipforge/")
    t = next(iter(tokens))
    assert public_url(settings, t) == f"{BASE}/clipforge/m/{t}/clip.mp4"


def test_a_new_address_for_a_post_ends_its_old_one(video):
    media = PublicMedia()
    first = media.issue("p1", video)
    second = media.issue("p1", video)
    assert media.lookup(first) is None and media.lookup(second) == video
    media.revoke("p1")
    assert media.lookup(second) is None
    assert media.lookup("not-a-token") is None


def test_restart_drops_every_token(engine, video):
    token = engine.public_media.issue("p1", video)
    assert engine.public_media.lookup(token) == video
    assert PublicMedia().lookup(token) is None  # what a restart starts with


def test_mode_b_needs_an_https_base_url(engine, clock):
    with pytest.raises(ValidationError):
        PostingSettings(public_base_url="http://mac.tail.ts.net")
    with_base(engine, None)
    _, after = run(engine, due(engine, clock).id)
    assert after.status == "needs_you" and "public address" in after.error


# The public host


def public(engine, base: str = BASE):
    with_base(engine, base)
    return api_client(engine, session=False, origin=False)


def test_public_host_serves_only_live_tokens(engine, video):
    client = public(engine)
    token = engine.public_media.issue("p1", video)
    path = f"/m/{token}/clip.mp4"
    r = client.get(path, headers={"host": HOST})
    assert r.status_code == 200 and r.content == video.read_bytes()
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["cache-control"] == "no-store"
    head = client.head(path, headers={"host": HOST})
    assert (head.status_code, head.headers["content-length"], head.content) == (200, "5120", b"")
    part = client.get(path, headers={"host": HOST, "range": "bytes=0-1023"})
    assert (part.status_code, part.content) == (206, video.read_bytes()[:1024])
    assert client.get(path, headers={"host": "MAC.tail.ts.net"}).status_code == 200
    funnel = {"host": HOST, "x-forwarded-for": "157.240.1.1", "tailscale-funnel-request": "?1"}
    assert client.get(path, headers=funnel).status_code == 200  # Funnel's own headers still get the video

    under = public(engine, BASE + "/clipforge")  # a Funnel path that is passed on, or taken off
    for p in (f"/clipforge{path}", path):
        assert under.get(p, headers={"host": HOST}).status_code == 200, p


def test_public_host_404s_everything_else(engine, video, tmp_path):
    client = public(engine)
    token = engine.public_media.issue("p1", video)
    gone = engine.public_media.issue("p2", video)
    engine.public_media.revoke("p2")
    for path in (
        "/",
        "/api/health",
        "/media/projects/p1/clips/c1.mp4",
        "/assets/app.js",
        f"/m/{token}/other.mp4",
        f"/m/{token}/clip.mp4/",
        f"/m/{gone}/clip.mp4",
        "/m/short/clip.mp4",
        f"/clipforge/m/{token}/clip.mp4",
    ):
        r = client.get(path, headers={"host": HOST})
        assert (r.status_code, r.text) == (404, "Not found"), path
    for method in ("POST", "PUT", "DELETE", "OPTIONS"):
        r = client.request(method, f"/m/{token}/clip.mp4", headers={"host": HOST, "origin": BASE})
        assert r.status_code == 404, method
    video.unlink()
    assert client.get(f"/m/{token}/clip.mp4", headers={"host": HOST}).status_code == 404

    with_base(engine, None)  # no public address: the host is anyone's
    other = engine.public_media.issue("p3", tmp_path)
    assert client.get(f"/m/{other}/clip.mp4", headers={"host": HOST}).status_code == 404


def test_the_window_host_doesnt_serve_public_media(engine, video):
    token = engine.public_media.issue("p1", video)
    r = api_client(engine).get(f"/m/{token}/clip.mp4")
    assert r.headers.get("content-type", "").startswith("text/html")  # only the window's page


# Posting through Instagram Login


def test_mode_b_self_tests_the_url_first(engine, graph, clock, fetched):
    p = due(engine, clock)
    live = []
    graph.script["container"] = [
        lambda request: (
            live.append(engine.public_media.lookup(token_of(graph.form(request)["video_url"]))) or None
        )
    ]
    job, after = run(engine, p.id)
    assert after.status == "posted"
    assert [(r.method, r.headers.get("range")) for r in fetched] == [("HEAD", None), ("GET", "bytes=0-1023")]
    made = graph.form(graph.of("container")[0])
    assert made["video_url"] == str(fetched[0].url)
    assert made["video_url"].startswith(f"{BASE}/m/") and made["video_url"].endswith("/clip.mp4")
    assert "upload_type" not in made and made["media_type"] == "REELS"
    assert graph.of("upload") == []
    assert all(r.headers["authorization"] == f"Bearer {IG_TOKEN}" for r in graph.requests)
    assert all(r.url.host == "graph.instagram.com" for r in graph.requests)
    assert live[0] is not None  # live while Instagram fetches it
    assert engine.public_media.lookup(token_of(made["video_url"])) is None  # and gone once it's posted
    assert IG_TOKEN not in after.state.values()


def test_a_sound_from_before_a_relink_through_instagram_needs_you(engine, graph, clock):
    """Picked while the account was linked through Facebook: Instagram would leave it off now, so the post
    waits for you, and Post without the sound sends it."""
    p = due(engine, clock)
    sound = PostSound(source="ig_trending", external_id="a1", title="Song a1", artist="The Artist")
    store.update_post(engine.db(), p.id, options=p.options.model_copy(update={"sound": sound}))
    _, after = run(engine, p.id)
    assert (after.status, after.error) == ("needs_you", SOUND_LOST)
    assert graph.of("container") == []
    assert SOUND_LOST == (
        "Instagram only adds sounds to accounts linked through Facebook, and this one is linked through "
        "Instagram now. Use Post without the sound, or link it through Facebook again."
    )
    again = engine.posting.retry(p.id, drop_sound=True)
    clock.set(datetime.fromtimestamp(again.scheduled_at, tz=UTC))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert "audio_configuration" not in graph.form(graph.of("container")[0])


def test_an_unreachable_address_needs_you(engine, graph, clock, fetched):
    engine.public_transport = httpx.MockTransport(lambda r: fetched.append(r) or httpx.Response(502))
    _, after = run(engine, due(engine, clock).id)
    assert (after.status, after.error) == ("needs_you", UNREACHABLE)
    assert graph.of("container") == []
    assert [r.method for r in fetched] == ["HEAD"]
    assert engine.public_media.lookup(token_of(str(fetched[0].url))) is None


def test_self_test_reads_the_head_and_the_first_kilobyte(engine, video, fetched):
    token = engine.public_media.issue("p1", video)
    url = f"{BASE}/m/{token}/clip.mp4"
    assert self_test(url, 5120, transport=engine.public_transport) is None
    assert "size" in self_test(url, 999, transport=engine.public_transport)
    engine.public_media.revoke("p1")
    assert "404" in self_test(url, 5120, transport=engine.public_transport)

    def dropped(request):
        raise httpx.ConnectError(f"no route to {request.url}", request=request)

    problem = self_test(url, 5120, transport=httpx.MockTransport(dropped))
    assert problem and token not in problem


def test_retest_on_2207052(engine, graph, clock, fetched):
    p = due(engine, clock)
    graph.script["status"] = [
        httpx.Response(200, json={"id": "c1", "status_code": "ERROR", "status": "Error: 2207052"}),
    ]
    _, after = run(engine, p.id)
    assert after.status == "posted"
    first, second = (graph.form(r)["video_url"] for r in graph.of("container"))
    assert first != second
    assert [r.method for r in fetched] == ["HEAD", "GET", "HEAD", "GET"]


def test_retest_on_create_retries_with_a_new_address(engine, graph, clock, fetched):
    p = due(engine, clock)
    graph.script["container"] = [error(9004, 2207052, "Media download has failed")]
    job, after = run(engine, p.id)
    assert (job.status, job.not_before, after.status) == ("queued", NOW.timestamp() + 60, "uploading")
    clock.advance(seconds=60)
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert len({graph.form(r)["video_url"] for r in graph.of("container")}) == 2


def test_resumed_after_a_restart_gets_a_new_address(engine, graph, clock, fetched):
    p = due(engine, clock)
    graph.statuses = ["IN_PROGRESS", "FINISHED"]
    job, after = run(engine, p.id)
    assert (job.status, after.status) == ("queued", "processing")
    engine.public_media = PublicMedia()  # a restart: every address is gone
    clock.advance(seconds=15)
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert len(graph.of("container")) == 2
    assert len({graph.form(r)["video_url"] for r in graph.of("container")}) == 2


def test_the_address_ends_when_the_post_leaves_instagram(engine, graph, clock):
    graph.copyright = {"status": "completed", "matches_found": True}
    p = due(engine, clock)
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    url = graph.form(graph.of("container")[0])["video_url"]
    assert engine.public_media.lookup(token_of(url)) is None
    assert post(engine, p.id).state["container"] == "c1"
