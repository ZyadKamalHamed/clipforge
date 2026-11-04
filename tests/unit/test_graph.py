"""The Graph client (SPEC 10.2): the calls it makes, where the token goes, and what each of Meta's errors
means for a post."""

import httpx
import pytest

from clipforge.post.graph import GRAPH_FB, GRAPH_IG, GraphClient, GraphError, IgAudio, action_for
from tests.fakes_graph import IG_TOKEN, PAGE_TOKEN, USER_TOKEN, FakeGraph, audio_row, error

PAGE_FIELDS = "id,name,access_token,instagram_business_account{id,username,profile_picture_url}"


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def client(graph):
    return GraphClient("v25.0", transport=graph.transport())


def test_pages_lists_linked_instagram_accounts_with_paging(graph, client):
    found = client.pages(USER_TOKEN)
    assert [(p.page_id, p.ig_user_id, p.username) for p in found] == [
        ("pg1", "ig1", "clips_1"),
        ("pg3", "ig3", "clips_3"),
    ]
    assert found[0].page_token == f"{PAGE_TOKEN}-1"
    assert "page_token" not in found[0].model_dump() and PAGE_TOKEN not in repr(found[0])
    first, second = graph.of("pages")
    assert str(first.url).startswith("https://graph.facebook.com/v25.0/me/accounts?")
    assert first.url.params["fields"] == PAGE_FIELDS
    for r in (first, second):
        assert r.headers["authorization"] == f"OAuth {USER_TOKEN}"
        assert "access_token" not in r.url.params  # the token Meta puts in its next link is left out


def test_pages_refuses_a_next_link_off_graph(graph, client):
    graph.next_host = "graph.facebook.com.evil.example"
    assert [p.page_id for p in client.pages(USER_TOKEN)] == ["pg1"]
    assert [r.url.host for r in graph.requests] == ["graph.facebook.com"]


def test_each_host_takes_its_own_kind_of_header(graph, client):
    client.publishing_limit(GRAPH_FB, "ig1", PAGE_TOKEN)
    client.publishing_limit(GRAPH_IG, "ig1", IG_TOKEN)
    me = client.ig_me(IG_TOKEN)
    fb, ig, mine = graph.requests
    assert fb.headers["authorization"] == f"OAuth {PAGE_TOKEN}"
    assert (ig.headers["authorization"], mine.headers["authorization"]) == (f"Bearer {IG_TOKEN}",) * 2
    assert (
        str(mine.url)
        == "https://graph.instagram.com/v25.0/me?fields=user_id%2Cusername%2Caccount_type%2Cprofile_picture_url"
    )
    assert (me.user_id, me.username, me.account_type) == ("17841400", "clips_b", "BUSINESS")
    assert all(t not in str(r.url) for r in graph.requests for t in (PAGE_TOKEN, IG_TOKEN))


def test_ig_audio_reads_only_its_fields(graph, client):
    graph.audio["music"] = [
        audio_row("a1"),
        audio_row(
            "a2", title=None, display_artist=7, duration_in_ms="long", on_platform_audio_preview_link=None
        ),
        audio_row(None),
        {"title": "No id at all"},
        "not a row",
    ]
    found = client.ig_audio(GRAPH_FB, "ig1", PAGE_TOKEN, "music")
    (r,) = graph.of("audio")
    assert str(r.url).startswith("https://graph.facebook.com/v25.0/ig_audio?")
    assert dict(r.url.params) == {
        "audio_type": "music",
        "user_id": "ig1",
        "fields": "audio_id,title,display_artist,ig_username,audio_type,duration_in_ms,"
        "on_platform_audio_preview_link",
    }
    assert r.headers["authorization"] == f"OAuth {PAGE_TOKEN}"
    assert found == [
        IgAudio(
            audio_id="a1",
            title="Song a1",
            display_artist="The Artist",
            ig_username="the_artist",
            audio_type="music",
            duration_in_ms=30500,
            on_platform_audio_preview_link="https://www.instagram.com/reels/audio/a1/",
        ),
        IgAudio(
            audio_id="a2",
            title="",
            display_artist=None,
            ig_username="the_artist",
            audio_type="music",
            duration_in_ms=None,
            on_platform_audio_preview_link=None,
        ),
    ]
    assert "cdn.example" not in repr(found)  # the file, artwork and picture links are dropped unread
    assert client.ig_audio(GRAPH_FB, "ig1", PAGE_TOKEN, "original_sound") == []  # none trending is no error
    assert graph.of("audio")[-1].url.params["audio_type"] == "original_sound"


def test_exchange_sends_the_secret_in_the_body(graph, client):
    assert client.exchange("short-token-for-tests-1", "1234", "app-secret-for-tests-0001") == USER_TOKEN
    (r,) = graph.requests
    assert r.method == "POST" and "secret" not in str(r.url) and "short-token" not in str(r.url)
    assert graph.form(r) == {
        "grant_type": "fb_exchange_token",
        "client_id": "1234",
        "client_secret": "app-secret-for-tests-0001",
        "fb_exchange_token": "short-token-for-tests-1",
    }


def test_refresh_keeps_the_token_out_of_the_url(graph, client):
    token, seconds = client.ig_refresh(IG_TOKEN)
    (r,) = graph.requests
    assert (token, seconds) == (f"{IG_TOKEN}-refreshed-1", 5184000)
    assert str(r.url) == "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token"
    assert r.headers["authorization"] == f"Bearer {IG_TOKEN}"


def test_upload_goes_only_to_metas_upload_host(graph, client, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    client.upload("https://rupload.facebook.com/ig-api-upload/v25.0/c1", PAGE_TOKEN, video, offset=4)
    (r,) = graph.requests
    assert (r.headers["offset"], r.headers["file_size"], r.content) == ("4", "10", b"456789")
    assert r.headers["authorization"] == f"OAuth {PAGE_TOKEN}"
    for uri in (
        "http://rupload.facebook.com/c1",
        "https://example.com/c1",
        "https://rupload.facebook.com.evil.example/c1",
    ):
        with pytest.raises(GraphError):
            client.upload(uri, PAGE_TOKEN, video, offset=0)
    assert len(graph.requests) == 1


def test_graph_errors_read_from_answers(graph, client):
    graph.script["limit"] = [
        error(9007, 2207027, "The media is not ready for publishing, please wait for a moment"),
        httpx.Response(502, text="<html>Bad gateway</html>"),
        httpx.Response(200, text="not json"),
        httpx.ConnectTimeout("no answer"),
        httpx.ConnectError("no route"),
    ]
    got = []
    for _ in range(5):
        with pytest.raises(GraphError) as err:
            client.publishing_limit(GRAPH_FB, "ig1", PAGE_TOKEN)
        got.append((err.value.status, err.value.code, err.value.subcode))
    assert got == [
        (400, 9007, 2207027),
        (502, None, None),
        (200, None, None),
        (0, None, None),
        (0, None, None),
    ]


def test_a_success_without_its_field_is_an_unknown_answer(graph, client):
    """A 200 answer missing what was asked for is garbled, so it is read as no answer, never a crash."""
    empty = httpx.Response(200, json={})
    graph.script.update(publish=[empty], container=[empty], exchange=[empty], refresh=[empty])
    calls = [
        lambda: client.publish(GRAPH_FB, "ig1", "c1", PAGE_TOKEN),
        lambda: client.create_container(GRAPH_FB, "ig1", PAGE_TOKEN, media_type="REELS"),
        lambda: client.exchange(USER_TOKEN, "1234", "secret-for-tests"),
        lambda: client.ig_refresh(IG_TOKEN),
    ]
    for call in calls:
        with pytest.raises(GraphError) as err:
            call()
        assert (err.value.status, err.value.code, action_for(err.value)) == (200, None, "retry")


def test_an_echoed_token_never_reaches_the_error(graph, client):
    graph.script["container"] = [error(100, None, f"Invalid parameter: access_token={PAGE_TOKEN}")]
    with pytest.raises(GraphError) as err:
        client.create_container(GRAPH_FB, "ig1", PAGE_TOKEN, media_type="REELS")
    assert PAGE_TOKEN not in str(err.value) and PAGE_TOKEN not in err.value.message
    assert "Invalid parameter" in err.value.message


@pytest.mark.parametrize(
    ("status", "code", "subcode", "action"),
    [
        (400, 9007, 2207027, "poll"),
        (400, 9007, None, "poll"),
        (400, 9, 2207042, "later_24h"),
        (400, 4, 2207051, "pause_fatal"),
        (400, 25, 2207050, "open_app"),
        (400, 24, 2207008, "new_container"),
        (400, -2, 2207020, "new_container"),
        (400, -1, 2207053, "new_container"),
        (400, 9004, 2207052, "retest_url"),
        (400, -2, 2207003, "retest_url"),
        (400, 190, None, "reconnect"),
        (400, 190, 463, "reconnect"),
        (403, 10, None, "reconnect"),
        (403, 200, None, "reconnect"),
        (400, 4, None, "rate_limited"),
        (400, 17, None, "rate_limited"),
        (400, 32, None, "rate_limited"),
        (400, 613, None, "rate_limited"),
        (500, 1, None, "retry"),
        (503, 2, None, "retry"),
        (502, None, None, "retry"),
        (200, None, None, "retry"),
        (0, None, None, "retry"),
        (400, 100, None, "fatal"),
        (400, 352, 2207026, "fatal"),
        (400, 1, 2207057, "fatal"),
    ],
)
def test_error_codes_map_to_actions(status, code, subcode, action):
    assert action_for(GraphError(status, code, subcode, "x")) == action
