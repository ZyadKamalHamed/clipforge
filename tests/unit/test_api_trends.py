"""SPEC 13 and 16: the Trends API. One view of every list (each account linked through Facebook with its
trending sounds and last read, the TikTok shortlist, the four weekly lists and their badge, the hashtag sets
and the accounts Instagram gives no list), Check now reading one account and refused while a read runs,
the shortlist and the sets made, changed and deleted with each refusal a sentence for its field, and a
clip's suggested sounds. Every title comes back clean, and every write sends the trends event."""

from urllib.parse import quote

import pytest

from clipforge import repo
from clipforge.post import store as post_store
from clipforge.server.errors import INVALID
from clipforge.trends import feed, hashtags, instagram, recommend, shortlist, store
from clipforge.trends.models import HashtagSet, TrendRead
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import FakeGraph, audio_row
from tests.posting import add_account, add_clip, at
from tests.server import api_client
from tests.unit.test_instagram_api import ig_account
from tests.unit.test_trends_recommend import track
from tests.unit.test_trends_store import ig_account as bare_ig_account
from tests.unit.test_trends_store import shortlist as shortlist_row
from tests.unit.test_trends_store import trending

DAY = 86400.0
ID = "7234567890123456789"
LINK = f"https://www.tiktok.com/music/Night-Drive-{ID}"
RLO = chr(0x202E)
# "new song" in Arabic
ARABIC = "".join(map(chr, (0x0623, 0x063A, 0x0646, 0x064A, 0x0629)))
NEW = "".join(map(chr, (0x062C, 0x062F, 0x064A, 0x062F, 0x0629)))


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def engine(clock, graph):
    eng = fake_engine(now=clock)
    eng.graph_transport = graph.transport()
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


def refusal(r, field, message, error="invalid"):
    assert r.status_code == 422, r.text
    assert r.json() == {"error": error, "message": message, "field": field}


def trending_rows(engine, account, *ids, **fields):
    """Instagram's music list for ``account``, read now, each row added now with ``fields``."""
    now = engine.now().timestamp()
    rows = [
        trending(account.id, x, rank).model_copy(update={"created_at": now} | fields)
        for rank, x in enumerate(ids)
    ]
    store.replace_trending(engine.db(), account.id, "music", rows, now)
    return rows


def clip_feeling(engine, mood="hype", energy=4):
    clip = add_clip(engine)
    repo.update_clip(engine.db(), clip.id, packaging={"mood": mood, "energy": energy})
    return clip


def test_trends_view_lists_everything(engine, client, clock, heard):
    conn = engine.db()
    now = clock().timestamp()
    a = ig_account(engine)
    unread = bare_ig_account(conn, "no_token")  # linked through Facebook, its token not in the Keychain
    login = ig_account(engine, mode="instagram")
    gone = ig_account(engine)
    post_store.delete_account(conn, gone.id)
    add_account(engine, "tiktok")
    m0, _ = trending_rows(engine, a, "m0", "m1")
    store.update_sound(conn, m0.id, url="https://www.instagram.com/reels/audio/m0/")
    trending_rows(engine, gone, "g0")
    store.save_read(conn, TrendRead(id=instagram.read_id(a.id), read_at=now - 3600, result="ok"))
    added = shortlist.add(engine, LINK, region="AU", moods=["hype"], energy=4)
    old = store.put_shortlist(
        conn, shortlist_row("7234567890123456780", title="Old One", created_at=now - 20 * DAY)
    )
    store.put_set(
        conn,
        HashtagSet(
            id="hs1",
            niche="Podcasts",
            name="Big pods",
            tags=["podcast", "clips"],
            created_at=now,
            updated_at=now,
        ),
    )

    r = client.get("/api/trends")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"instagram", "shortlist", "lists", "badge", "sets", "mode_b"}
    first, second = body["instagram"]
    assert first["account"] == {"id": a.id, "handle": a.handle}
    assert first["read"] == {
        "id": f"ig:{a.id}",
        "read_at": now - 3600,
        "newest_at": None,
        "seen_at": None,
        "result": "ok",
        "error": None,
    }
    assert first["can_check"] is True
    assert [(s["external_id"], s["rank"], s["stale"], s["link_id"]) for s in first["sounds"]] == [
        ("m0", 0, False, f"sound:{m0.id}"),
        ("m1", 1, False, None),  # no preview link, so nothing to open
    ]
    assert first["sounds"][0] == {
        "id": m0.id,
        "platform": "instagram",
        "source": "ig_trending",
        "external_id": "m0",
        "title": "Song m0",
        "artist": None,
        "url": "https://www.instagram.com/reels/audio/m0/",
        "region": None,
        "mood_tags": [],
        "energy": None,
        "audio_type": "music",
        "duration_s": None,
        "rank": 0,
        "account_id": a.id,
        "fetched_at": now,
        "created_at": now,
        "stale": False,
        "link_id": f"sound:{m0.id}",
    }
    assert second == {
        "account": {"id": unread.id, "handle": "no_token"},
        "read": None,
        "sounds": [],
        "can_check": False,
    }
    assert [(s["title"], s["stale"], s["link_id"]) for s in body["shortlist"]] == [
        ("Night Drive", False, f"sound:{added.id}"),
        ("Old One", True, f"sound:{old.id}"),  # added 20 days ago
    ]
    assert body["lists"] == [
        {"name": name, "label": feed.LABELS[name], "link_id": f"list:{name}"} for name in feed.LISTS
    ]
    assert feed.LABELS == {
        "later": "Later: Instagram Reels trends",
        "buffer": "Buffer: trending audio on Instagram",
        "socialpilot_tiktok": "SocialPilot: TikTok trends",
        "socialpilot_instagram": "SocialPilot: Instagram Reels trends",
    }
    assert body["badge"] is True  # no list opened yet
    assert body["sets"] == [
        {
            "id": "hs1",
            "niche": "Podcasts",
            "name": "Big pods",
            "tags": ["podcast", "clips"],
            "created_at": now,
            "updated_at": now,
        }
    ]
    assert body["mode_b"] == [login.handle]

    # the ids the view gives are the ones Open takes, and only what ClipForge stored is opened
    assert client.post(f"/api/links/{quote(first['sounds'][0]['link_id'], safe=':')}/open").status_code == 204
    assert client.post(f"/api/links/{quote(body['lists'][1]['link_id'], safe=':')}/open").status_code == 204
    assert [c for c in engine.run_cmd.calls if c[:1] == ["open"]] == [
        ["open", "https://www.instagram.com/reels/audio/m0/"],
        ["open", feed.LISTS["buffer"]],
    ]

    # opening a list puts the badge out
    r = client.post("/api/trends/lists/seen")
    assert r.status_code == 204
    assert client.get("/api/trends").json()["badge"] is False
    assert ("trends", {"lists": "seen"}) in heard


def test_check_now_reads_one_account(engine, client, graph, heard):
    a, other = ig_account(engine), ig_account(engine)
    graph.audio["music"] = [audio_row("a1"), audio_row("a2")]
    graph.audio["original_sound"] = [audio_row("o1", audio_type="original_sound")]
    r = client.post(f"/api/trends/instagram/{a.id}/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account"] == {"id": a.id, "handle": a.handle}
    assert (body["read"]["result"], body["can_check"]) == ("ok", True)
    assert [(s["audio_type"], s["external_id"]) for s in body["sounds"]] == [
        ("music", "a1"),
        ("music", "a2"),
        ("original_sound", "o1"),
    ]
    assert len(graph.of("audio")) == 2  # this account's two lists, and no other account's
    assert store.sounds(engine.db(), source="ig_trending", account_id=other.id) == []
    assert ("trends", {"account_id": a.id, "result": "ok"}) in heard

    # refused while a read runs, and for an account Instagram gives no list
    with instagram._lock:
        r = client.post(f"/api/trends/instagram/{a.id}/check")
    assert r.status_code == 409
    assert r.json() == {"error": "trends_busy", "message": instagram.READING, "field": None}
    login = ig_account(engine, mode="instagram")
    r = client.post(f"/api/trends/instagram/{login.id}/check")
    assert r.status_code == 409
    assert r.json() == {"error": "no_trending", "message": instagram.ONLY_FACEBOOK, "field": None}
    post_store.delete_account(engine.db(), other.id)
    for account_id in (other.id, "a_missing"):
        assert client.post(f"/api/trends/instagram/{account_id}/check").status_code == 404
    assert len(graph.of("audio")) == 2


def test_shortlist_routes(engine, client, clock, heard):
    r = client.post(
        "/api/trends/shortlist",
        json={"link": f"{LINK}?lang=en", "region": "AU", "moods": ["hype"], "energy": 4},
    )
    assert r.status_code == 201, r.text
    sound = r.json()["sound"]
    assert (sound["title"], sound["region"], sound["mood_tags"], sound["energy"], sound["url"]) == (
        "Night Drive",
        "AU",
        ["hype"],
        4,
        LINK,
    )
    assert (sound["stale"], sound["link_id"], sound["created_at"]) == (
        False,
        f"sound:{sound['id']}",
        clock().timestamp(),
    )
    assert heard[-1] == ("trends", {"shortlist": sound["id"]})
    titled = client.post(
        "/api/trends/shortlist", json={"link": f"tiktok.com/music/{ID}", "title": "Mine", "region": "AU"}
    )
    assert titled.json()["sound"]["title"] == "Mine"
    assert titled.json()["sound"]["id"] == sound["id"]  # the same sound, written over

    refusal(
        client.post("/api/trends/shortlist", json={"link": "https://vm.tiktok.com/ZMabc/"}),
        "link",
        shortlist.SHORT_LINK,
    )
    refusal(client.post("/api/trends/shortlist", json={"link": "x" * 5000}), "link", shortlist.NOT_A_SOUND)
    refusal(
        client.post("/api/trends/shortlist", json={"link": LINK, "energy": 9}), "energy", shortlist.ENERGY
    )
    refusal(
        client.post("/api/trends/shortlist", json={"link": LINK, "moods": ["spooky"]}),
        "moods",
        shortlist.NO_SUCH_MOOD,
    )
    refusal(client.post("/api/trends/shortlist", json={"link": LINK, "volume": 3}), "volume", INVALID)
    refusal(client.post("/api/trends/shortlist", json={}), "link", INVALID)

    sid = sound["id"]
    r = client.patch(f"/api/trends/shortlist/{sid}", json={"title": "Late Night", "moods": ["calm"]})
    assert r.status_code == 200, r.text
    changed = r.json()["sound"]
    assert (changed["title"], changed["mood_tags"], changed["region"], changed["energy"]) == (
        "Late Night",
        ["calm"],
        "AU",  # left out, so kept
        None,  # the second paste had no energy
    )
    assert heard[-1] == ("trends", {"shortlist": sid})
    assert (
        client.patch(f"/api/trends/shortlist/{sid}", json={"region": None}).json()["sound"]["region"] is None
    )
    refusal(client.patch(f"/api/trends/shortlist/{sid}", json={"energy": 0}), "energy", shortlist.ENERGY)
    refusal(client.patch(f"/api/trends/shortlist/{sid}", json={"title": "  "}), "title", shortlist.NO_TITLE)
    refusal(client.patch(f"/api/trends/shortlist/{sid}", json={"link": LINK}), "link", INVALID)

    # Instagram's rows aren't the shortlist's, and a missing sound is missing
    (row,) = trending_rows(engine, bare_ig_account(engine.db()), "m0")
    for sound_id in (row.id, "snd_missing"):
        assert client.patch(f"/api/trends/shortlist/{sound_id}", json={"title": "Mine"}).status_code == 404
        assert client.delete(f"/api/trends/shortlist/{sound_id}").status_code == 404
    events = len(heard)
    assert client.delete(f"/api/trends/shortlist/{sid}").status_code == 204
    assert heard[events:] == [("trends", {"shortlist": sid})]
    assert client.delete(f"/api/trends/shortlist/{sid}").status_code == 404
    assert client.get("/api/trends").json()["shortlist"] == []


def test_sets_routes(engine, client, clock, heard):
    r = client.post(
        "/api/trends/sets",
        json={"niche": "Podcasts", "name": "Big pods", "tags": ["#podcast", "Clips", "#clips"]},
    )
    assert r.status_code == 201, r.text
    made = r.json()["set"]
    now = clock().timestamp()
    assert made["id"].startswith("hs")
    assert made == {
        "id": made["id"],
        "niche": "Podcasts",
        "name": "Big pods",
        "tags": ["podcast", "Clips"],  # each once, whatever its case, without its #
        "created_at": now,
        "updated_at": now,
    }
    assert heard[-1] == ("trends", {"set": made["id"]})

    body = {"niche": "Podcasts", "name": "Pods", "tags": ["pods"]}
    for change, field, message in (
        ({"niche": " "}, "niche", hashtags.NO_NICHE),
        ({"name": ""}, "name", hashtags.NO_NAME),
        ({"tags": []}, "tags", hashtags.NO_TAGS),
        ({"tags": ["two words"]}, "tags", hashtags.TAG_ONLY),
        ({"tags": [f"t{n}" for n in range(31)]}, "tags", hashtags.TOO_MANY),
        ({"name": "x" * 61}, "name", hashtags.TOO_LONG.format(what="name", n=60)),
    ):
        refusal(client.post("/api/trends/sets", json=body | change), field, message)
    refusal(client.post("/api/trends/sets", json={"niche": "Podcasts", "tags": ["pods"]}), "name", INVALID)
    refusal(client.post("/api/trends/sets", json=body | {"colour": "red"}), "colour", INVALID)
    assert [s["id"] for s in client.get("/api/trends").json()["sets"]] == [made["id"]]

    clock.advance(minutes=5)
    r = client.patch(f"/api/trends/sets/{made['id']}", json={"name": "Pods"})
    assert r.status_code == 200, r.text
    changed = r.json()["set"]
    assert (changed["name"], changed["niche"], changed["tags"]) == ("Pods", "Podcasts", ["podcast", "Clips"])
    assert (changed["created_at"], changed["updated_at"]) == (now, clock().timestamp())
    assert heard[-1] == ("trends", {"set": made["id"]})
    refusal(client.patch(f"/api/trends/sets/{made['id']}", json={"tags": None}), "tags", hashtags.NO_TAGS)
    refusal(client.patch(f"/api/trends/sets/{made['id']}", json={"tags": ["a b"]}), "tags", hashtags.TAG_ONLY)
    assert client.patch("/api/trends/sets/hs_missing", json={"name": "Pods"}).status_code == 404

    events = len(heard)
    assert client.delete(f"/api/trends/sets/{made['id']}").status_code == 204
    assert heard[events:] == [("trends", {"set": made["id"]})]
    assert client.delete(f"/api/trends/sets/{made['id']}").status_code == 404
    assert client.get("/api/trends").json()["sets"] == []


def test_clip_sound_suggestions(engine, client):
    clip = clip_feeling(engine)
    a = ig_account(engine)
    m0, _ = trending_rows(engine, a, "m0", "m1")
    store.update_sound(engine.db(), m0.id, url="https://www.instagram.com/reels/audio/m0/")
    tune = track(engine, "Late Drive", ["hype"], 4)
    sound = shortlist.add(engine, LINK, moods=["hype"], energy=4)

    def asked(**query):
        r = client.get(f"/api/clips/{clip.id}/sounds", params=query)
        assert r.status_code == 200, r.text
        return r.json()["sounds"]

    found = asked(platform="tiktok")
    assert found == [
        {
            "kind": "music",
            "id": tune.id,
            "title": "Late Drive",
            "artist": None,
            "why": "Matches its hype mood",
            "stale": False,
            "score": 3.0,
            "external_id": None,
            "url": None,
            "link_id": None,
        },
        {
            "kind": "shortlist",
            "id": sound.id,
            "title": "Night Drive",
            "artist": None,
            "why": "Matches its hype mood",
            "stale": False,
            "score": 3.0,
            "external_id": ID,
            "url": LINK,
            "link_id": f"sound:{sound.id}",
        },
    ]
    assert asked() == found  # no platform yet: the shortlist with your tracks
    on_instagram = asked(platform="instagram", account_id=a.id)
    assert [(s["kind"], s["title"], s["why"], s["external_id"], s["link_id"]) for s in on_instagram] == [
        ("music", "Late Drive", "Matches its hype mood", None, None),
        ("ig_trending", "Song m0", recommend.TRENDING, "m0", f"sound:{m0.id}"),
        ("ig_trending", "Song m1", recommend.TRENDING, "m1", None),
    ]
    assert [s["kind"] for s in asked(platform="instagram")] == ["music"]  # whose list, unsaid
    r = client.get(f"/api/clips/{clip.id}/sounds", params={"platform": "youtube"})
    refusal(r, "platform", INVALID)
    assert client.get("/api/clips/c_missing/sounds").status_code == 404


def test_trend_text_is_clean(engine, client, clock):
    conn = engine.db()
    now = clock().timestamp()
    dirty, artist = f"{ARABIC}{RLO} {NEW}", f"DJ{RLO}  One{chr(0x85)}"
    store.put_shortlist(
        conn, shortlist_row(ID, title=dirty, artist=artist, mood_tags=["hype"], created_at=now)
    )
    a = ig_account(engine)
    trending_rows(engine, a, "m0", title=dirty, artist=artist)
    store.put_set(
        conn,
        HashtagSet(
            id="hs1",
            niche=f"Pod{RLO}casts",
            name=f"{ARABIC}{RLO}",
            tags=[f"pods{RLO}"],
            created_at=now,
            updated_at=now,
        ),
    )
    clip = clip_feeling(engine)

    r = client.get("/api/trends")
    assert RLO not in r.text
    body = r.json()
    (listed,) = body["shortlist"]
    (trend,) = body["instagram"][0]["sounds"]
    assert (listed["title"], listed["artist"]) == (f"{ARABIC} {NEW}", "DJ One")
    assert (trend["title"], trend["artist"]) == (f"{ARABIC} {NEW}", "DJ One")
    assert body["sets"][0] | {"created_at": 0, "updated_at": 0} == {
        "id": "hs1",
        "niche": "Podcasts",
        "name": ARABIC,
        "tags": ["pods"],
        "created_at": 0,
        "updated_at": 0,
    }
    for query in ({"platform": "tiktok"}, {"platform": "instagram", "account_id": a.id}):
        r = client.get(f"/api/clips/{clip.id}/sounds", params=query)
        assert RLO not in r.text
        assert [(s["title"], s["artist"]) for s in r.json()["sounds"]] == [(f"{ARABIC} {NEW}", "DJ One")]
