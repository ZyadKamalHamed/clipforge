"""The TikTok sound shortlist (SPEC 13): a sound's link pasted from TikTok, read as text and never fetched,
kept with your title, moods, energy and region. Instagram's trending rows are read, never edited here."""

import time
from urllib.parse import quote

import pytest

from clipforge.edits import EditError
from clipforge.trends import shortlist, store
from tests.fakes import Clock, fake_engine
from tests.posting import at
from tests.unit.test_trends_store import ig_account, trending

ID = "7234567890123456789"
RLO = chr(0x202E)
# "new song" in Arabic, two words joined by a hyphen as a TikTok slug joins them
ARABIC = "".join(map(chr, (0x0623, 0x063A, 0x0646, 0x064A, 0x0629)))
NEW = "".join(map(chr, (0x062C, 0x062F, 0x064A, 0x062F, 0x0629)))
NOT_A_SOUND = "That isn't a TikTok sound link. Copy it from a sound's page in TikTok."
SHORT_LINK = "Open that short link in your browser and paste the full tiktok.com/music link it shows."


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


def refused(link) -> EditError:
    with pytest.raises(EditError) as err:
        shortlist.parse_tiktok_music(link)
    assert err.value.field == "link"
    return err.value


def test_parse_tiktok_music_links(engine):
    # Reading a link is text work: the engine's look-alike-less transport and tests/conftest.py's guard fail
    # any request, so none of these reaches a site.
    canonical = f"https://www.tiktok.com/music/Original-Sound-{ID}"
    for link in (
        f"https://www.tiktok.com/music/Original-Sound-{ID}?lang=en",
        f"tiktok.com/music/Original-Sound-{ID}",
        f"  https://m.tiktok.com/music/Original-Sound-{ID}/#top  ",
        f"http://TikTok.com/music/Original-Sound-{ID}",
        f"www.tiktok.com/music/Original--Sound-{ID}",
    ):
        assert shortlist.parse_tiktok_music(link) == (ID, "Original Sound", canonical), link
    slug = f"{ARABIC}-{NEW}"
    found = shortlist.parse_tiktok_music(f"https://www.tiktok.com/music/{slug}-{ID}")
    assert found == (ID, f"{ARABIC} {NEW}", f"https://www.tiktok.com/music/{quote(slug)}-{ID}")
    assert found[2].isascii()
    assert shortlist.parse_tiktok_music(f"https://www.tiktok.com/music/{quote(slug)}-{ID}") == found
    # an id with no name gets a plain title; a hidden control in an encoded name is dropped
    assert shortlist.parse_tiktok_music(f"https://www.tiktok.com/music/{ID}") == (
        ID,
        shortlist.UNTITLED,
        f"https://www.tiktok.com/music/{ID}",
    )
    assert shortlist.UNTITLED == "Untitled sound"
    sneaky = quote(f"Night{RLO}-Drive")
    assert shortlist.parse_tiktok_music(f"tiktok.com/music/{sneaky}-{ID}")[1:] == (
        "Night Drive",
        f"https://www.tiktok.com/music/Night-Drive-{ID}",
    )
    for link in ("https://vm.tiktok.com/ZMabc123/", "vt.tiktok.com/ZSxyz", "http://vm.tiktok.com/x"):
        assert refused(link).message == SHORT_LINK, link
    for link in (
        f"https://www.tiktok.com/@user/video/{ID}",
        f"https://tiktok.com.evil.example/music/x-{ID}",
        f"https://evil.example/music/x-{ID}",
        f"https://www.tiktok.com:8443/music/x-{ID}",
        f"https://me@www.tiktok.com/music/x-{ID}",
        "https://www.tiktok.com/music/x-123",  # too short for a sound's id
        f"https://www.tiktok.com/music/x-{ID}12",  # too long
        f"https://www.tiktok.com/music/x{ID}",
        f"ftp://www.tiktok.com/music/x-{ID}",
        f"https://www.tiktok.com/music/x-{ID}{RLO}",
        f"https://www.tiktok.com/music/x y-{ID}",
        f"https://www.tiktok.com/music/x-{ID}/more",
        "https://www.tiktok.com/music/",
        "",
        "   ",
        "not a link",
        f"https://www.tiktok.com/music/{'x' * 3000}-{ID}",
    ):
        assert refused(link).message == NOT_A_SOUND, link


def test_shortlist_add_update_remove(engine, clock):
    conn = engine.db()
    s = shortlist.add(
        engine,
        f"tiktok.com/music/Night-Drive-{ID}?lang=en",
        region="AU",
        moods=["hype", "funny", "hype"],
        energy=4,
    )
    assert (s.platform, s.source, s.external_id, s.title, s.url) == (
        "tiktok",
        "shortlist",
        ID,
        "Night Drive",
        f"https://www.tiktok.com/music/Night-Drive-{ID}",
    )
    assert (s.region, s.mood_tags, s.energy, s.account_id) == ("AU", ["hype", "funny"], 4, None)
    assert s.created_at == clock().timestamp()
    assert store.sounds(conn, source="shortlist") == [s]
    # the same sound pasted again writes over it, keeping when it was first added
    clock.advance(days=20)
    again = shortlist.add(
        engine, f"https://www.tiktok.com/music/Night-Drive-{ID}", title=f"  Night {RLO}Drive  2 "
    )
    assert (again.id, again.created_at, again.title, again.mood_tags, again.region, again.energy) == (
        s.id,
        s.created_at,
        "Night Drive 2",
        [],
        None,
        None,
    )
    assert again.stale(clock().timestamp())
    assert shortlist.add(engine, f"tiktok.com/music/Night-Drive-{ID}", title="   ").title == "Night Drive"
    assert len(store.sounds(conn, source="shortlist")) == 1

    changed = shortlist.update(engine, s.id, title="Late Night", region="US", moods=["calm"], energy=2)
    assert (changed.title, changed.region, changed.mood_tags, changed.energy, changed.url) == (
        "Late Night",
        "US",
        ["calm"],
        2,
        again.url,
    )
    assert (
        shortlist.update(engine, s.id, region=None, energy=None).model_dump()
        == changed.model_copy(update={"region": None, "energy": None}).model_dump()
    )
    for fields, field, sentence in (
        ({"moods": ["spooky"]}, "moods", shortlist.NO_SUCH_MOOD),
        ({"title": "  "}, "title", shortlist.NO_TITLE),
        ({"title": "x" * 201}, "title", shortlist.LONG_TITLE),
        ({"energy": 6}, "energy", shortlist.ENERGY),
        ({"energy": True}, "energy", shortlist.ENERGY),
        ({"region": "UK"}, "region", shortlist.REGION),
    ):
        with pytest.raises(EditError) as err:
            shortlist.update(engine, s.id, **fields)
        assert (err.value.field, err.value.message) == (field, sentence), fields
    with pytest.raises(EditError) as err:
        shortlist.add(engine, f"tiktok.com/music/x-{ID}", energy=0)
    assert err.value.field == "energy"
    assert shortlist.NO_SUCH_MOOD == "Pick moods from ClipForge's list."
    assert shortlist.ENERGY == "Energy goes from 1 to 5."

    # Instagram's trending rows aren't the shortlist's to change, and a missing sound is missing
    ig = ig_account(conn)
    row = trending(ig.id, "a1", 0)
    store.replace_trending(conn, ig.id, "music", [row], time.time())
    for call in (lambda i: shortlist.update(engine, i, title="Mine"), lambda i: shortlist.remove(engine, i)):
        for sound_id in (row.id, "snd_missing"):
            with pytest.raises(LookupError):
                call(sound_id)
    assert store.get_sound(conn, row.id).title == "Song a1"
    shortlist.remove(engine, s.id)
    assert store.sounds(conn, source="shortlist") == []
    with pytest.raises(LookupError):
        shortlist.remove(engine, s.id)
