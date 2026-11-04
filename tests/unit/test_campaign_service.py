"""Saving campaign profiles and linking clipping channels and projects to them (SPEC 10.10): footage
channels looked up once, the budget dated, deleting refused once there are submissions, and a linked
channel or project kept to the campaign's footage with the rights basis Clipping programme."""

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from clipforge import repo
from clipforge.campaigns import parse, service, store
from clipforge.campaigns.models import Submission
from clipforge.errors import ClipForgeError
from clipforge.models import ProjectOptions
from clipforge.watch import check, sources
from clipforge.watch import store as watch_store
from clipforge.watch.check import run_check, start_check
from clipforge.watch.models import ChannelListing, ClipChannel
from tests.fakes import Clock, FakeYouTube, fake_engine
from tests.posting import bare_post
from tests.unit.test_watch_check import CHANNEL_XML, EP2, LIVE, SHORT

UC, UC2 = "UCabcdefghijklmnopqrstuv", "UCbbbbbbbbbbbbbbbbbbbbbb"
BEAST = "https://www.youtube.com/@MrBeast/videos"
OTHER_TAB = f"https://www.youtube.com/channel/{UC2}/videos"
VID1, VID2, VID3 = "dQw4w9WgXcQ", "aaaaaaaaaaa", "ccccccccccc"
MORNING = datetime(2026, 10, 27, 19, tzinfo=UTC)  # 06:00 on Wednesday 28 October in Sydney
NOT_ALLOWED = "The campaign Beast clips doesn't allow footage from this channel."


class Channels(FakeYouTube):
    """list_channel answers by channel link; a link not here fails as a missing channel does."""

    def __init__(self, listings: dict[str, ChannelListing]):
        super().__init__(seconds=1800.0)
        self.listings = listings

    def list_channel(self, url: str, limit: int) -> ChannelListing:
        self._record("list_channel", url, limit)
        if url not in self.listings:
            raise ClipForgeError("unavailable", "That channel isn't on YouTube.")
        return self.listings[url]


def listing(uc: str, title: str) -> ChannelListing:
    return ChannelListing(channel_id=uc, title=title, url=f"https://www.youtube.com/channel/{uc}/videos")


@pytest.fixture
def clock():
    return Clock(MORNING)


@pytest.fixture
def youtube():
    yt = Channels({BEAST: listing(UC, "MrBeast"), OTHER_TAB: listing(UC2, "Other")})
    yt.videos = {v: {} for v in (VID1, VID2, VID3, SHORT, LIVE, EP2)}
    return yt


@pytest.fixture
def engine(youtube, clock, monkeypatch):
    monkeypatch.setattr(check, "FEED_GAP_S", 0.0)
    eng = fake_engine(youtube=youtube, now=clock)
    yield eng
    eng.close()


def form(**given):
    return (
        parse.blank("Clip MrBeast. $1 per 1K views.") | {"name": "Beast clips", "marketplace": "Whop"} | given
    )


def channel(engine, name: str = "Podcasts") -> ClipChannel:
    now = engine.now().timestamp()
    return watch_store.add_channel(
        engine.db(), ClipChannel(id=repo.new_id("cc"), name=name, created_at=now, updated_at=now)
    )


def videos(engine, ch: ClipChannel) -> dict[str, tuple[str, str | None]]:
    return {i.video_id: (i.status, i.reason) for i in watch_store.items_for(engine.db(), ch.id)}


def project(engine, *, kind: str = "youtube", video_id: str | None = None, uc: str | None = None):
    conn = engine.db()
    made = repo.create_project(
        conn,
        source_kind=kind,
        source_url=None,
        video_id=video_id,
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
    )
    if uc:
        repo.update_project(conn, made.id, channel_url=f"https://www.youtube.com/channel/{uc}")
    return engine.project(made.id)


def test_save_looks_up_footage_once_each(engine, youtube):
    links = ["@MrBeast", "https://www.youtube.com/@MrBeast", f"https://www.youtube.com/channel/{UC2}"]
    made = service.create(engine, form(footage_links=links, footage={"videos": [VID1], "files": True}))
    assert [(c.yt_channel_id, c.title) for c in made.footage.channels] == [(UC, "MrBeast"), (UC2, "Other")]
    assert (made.footage.videos, made.footage.files) == ([VID1], True)
    assert len(youtube.called("list_channel")) == 2  # the two spellings of @MrBeast are one channel
    again = service.update(
        engine, made.id, {"footage_links": [c.url for c in made.footage.channels], "notes": "x"}
    )
    assert again.footage.channels == made.footage.channels
    assert len(youtube.called("list_channel")) == 2  # channels it already has aren't looked up again
    assert store.get_campaign(engine.db(), made.id) == again


@pytest.mark.parametrize("link", ["@Nobody", "https://vimeo.com/123"])
def test_failed_footage_lookup_is_refused(engine, link):
    with pytest.raises(ClipForgeError) as err:
        service.create(engine, form(footage_links=[link]))
    assert link in str(err.value)
    assert store.list_campaigns(engine.db()) == []


def test_bad_fields_are_refused_naming_the_field(engine, youtube):
    with pytest.raises(service.Refused) as err:
        service.create(engine, form(rate_per_1000=-1.0, footage_links=["@MrBeast"]))
    assert err.value.field == "rate_per_1000"
    assert youtube.called("list_channel") == []  # checked before anything is looked up
    with pytest.raises(service.Refused) as err:
        service.create(engine, form(min_s=60.0, max_s=30.0))
    assert str(err.value) == "The shortest clip can't be longer than the longest."
    with pytest.raises(service.Refused) as err:
        service.create(engine, form(submit_url="javascript:alert(1)"))
    assert (err.value.field, str(err.value)) == ("submit_url", "A link must start with https:// or http://.")
    assert store.list_campaigns(engine.db()) == []


def test_budget_edit_dates_it(engine, clock):
    made = service.create(engine, form(budget_left=500.0))
    assert made.budget_entered_on == "2026-10-28"  # the day in Sydney
    clock.set(MORNING + timedelta(days=2))
    assert service.update(engine, made.id, {"notes": "Paid weekly."}).budget_entered_on == "2026-10-28"
    assert (
        service.update(engine, made.id, {"budget_left": 500.0}).budget_entered_on == "2026-10-28"
    )  # unchanged
    cut = service.update(engine, made.id, {"budget_left": 300.0})
    assert (cut.budget_left, cut.budget_entered_on) == (300.0, "2026-10-30")
    assert service.create(engine, form(name="No budget")).budget_entered_on is None


def test_delete_with_submissions_is_refused(engine):
    conn = engine.db()
    made = service.create(engine, form())
    now = time.time()
    store.add_submission(
        conn,
        Submission(
            id=repo.new_id("sb"), post_id=bare_post(conn), campaign_id=made.id, created_at=now, updated_at=now
        ),
    )
    with pytest.raises(ClipForgeError) as err:
        service.delete(engine, made.id)
    assert str(err.value) == "This campaign has 1 submission, so mark it ended instead."
    other = service.create(engine, form(name="Other"))
    ch = channel(engine)
    service.link_channel(engine, ch.id, other.id)
    service.delete(engine, other.id)
    assert store.get_campaign(conn, other.id) is None
    unlinked = watch_store.get_channel(conn, ch.id)
    assert (unlinked.campaign_id, unlinked.rights_basis) == (None, "not_cleared")


def test_linking_a_channel_adds_allowed_sources_and_videos(engine):
    conn = engine.db()
    made = service.create(
        engine, form(footage_links=["@MrBeast"], footage={"videos": [VID1, VID2], "files": False})
    )
    ch = channel(engine)
    linked = service.link_channel(engine, ch.id, made.id)
    assert (linked.campaign_id, linked.rights_basis) == (made.id, "clipping_programme")
    watched = watch_store.list_sources(conn, ch.id)
    assert [s.yt_channel_id for s in watched if not s.is_footage] == [UC]
    (footage,) = [s for s in watched if s.is_footage]
    assert footage.yt_channel_id == f"campaign:{made.id}"
    assert {i.video_id: (i.status, i.source_id) for i in watch_store.items_for(conn, ch.id)} == {
        VID1: ("new", footage.id),
        VID2: ("new", footage.id),
    }
    service.link_channel(engine, ch.id, made.id)  # again: nothing twice
    assert len(watch_store.list_sources(conn, ch.id)) == 2 and len(videos(engine, ch)) == 2
    service.update(engine, made.id, {"footage": {"videos": [VID2, VID3], "files": False}})
    assert videos(engine, ch) == {VID1: ("skipped", "not_allowed"), VID2: ("new", None), VID3: ("new", None)}


def test_unlinking_restores_not_cleared(engine):
    conn = engine.db()
    made = service.create(
        engine, form(footage_links=["@MrBeast"], footage={"videos": [VID1, VID2], "files": False})
    )
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    started = watch_store.item_for_video(conn, ch.id, VID1)
    watch_store.update_item(conn, started.id, status="clipping")
    unlinked = service.link_channel(engine, ch.id, None)
    assert (unlinked.campaign_id, unlinked.rights_basis) == (None, "not_cleared")
    assert videos(engine, ch) == {VID1: ("clipping", None)}  # the one not started is gone, history kept
    assert [s.yt_channel_id for s in watch_store.list_sources(conn, ch.id) if not s.is_footage] == [UC]
    other = channel(engine, "Other")
    service.link_channel(engine, other.id, made.id)
    service.link_channel(engine, other.id, None)
    assert [s.yt_channel_id for s in watch_store.list_sources(conn, other.id)] == [UC]  # no empty footage


def test_linked_channel_refuses_other_sources(engine):
    made = service.create(engine, form(footage_links=["@MrBeast"]))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, ch.id, f"https://www.youtube.com/channel/{UC2}")
    assert str(err.value) == NOT_ALLOWED
    open_brief = service.create(engine, form(name="Anything goes"))  # no footage rule
    free = channel(engine, "Free")
    service.link_channel(engine, free.id, open_brief.id)
    assert sources.add_source(engine, free.id, f"https://www.youtube.com/channel/{UC2}").yt_channel_id == UC2


def test_project_link_follows_footage(engine):
    made = service.create(
        engine, form(footage_links=["@MrBeast"], footage={"videos": [VID1], "files": False})
    )
    by_video = service.link_project(engine, project(engine, video_id=VID1, uc=UC2).id, made.id)
    assert (by_video.campaign_id, by_video.rights_basis) == (made.id, "clipping_programme")
    assert (
        service.link_project(engine, project(engine, video_id=VID2, uc=UC).id, made.id).campaign_id == made.id
    )
    other = project(engine, video_id=VID3, uc=UC2)
    with pytest.raises(ClipForgeError) as err:
        service.link_project(engine, other.id, made.id)
    assert str(err.value) == NOT_ALLOWED
    assert engine.project(other.id).campaign_id is None
    upload = project(engine, kind="file")
    with pytest.raises(ClipForgeError) as err:
        service.link_project(engine, upload.id, made.id)
    assert (
        str(err.value) == "The campaign Beast clips doesn't allow footage from files or Drive links you add."
    )
    service.update(engine, made.id, {"footage": {"videos": [VID1], "files": True}})
    assert service.link_project(engine, upload.id, made.id).campaign_id == made.id
    anything = service.create(engine, form(name="Anything goes"))
    assert service.link_project(engine, other.id, anything.id).campaign_id == anything.id
    back = service.link_project(engine, other.id, None)
    assert (back.campaign_id, back.rights_basis) == (None, "not_cleared")


def test_a_project_not_read_yet_links_and_its_drafts_check_the_channel(engine):
    made = service.create(
        engine, form(footage_links=["@MrBeast"], footage={"videos": [VID1], "files": False})
    )
    fresh = project(engine, video_id=VID3)  # its channel is known once the video is read
    assert service.link_project(engine, fresh.id, made.id).campaign_id == made.id
    repo.update_project(
        engine.db(), fresh.id, channel_url=f"https://www.youtube.com/channel/{UC2}", duration_s=600.0
    )
    campaign = store.get_campaign(engine.db(), made.id)
    assert service.footage_allows(campaign, engine.project(fresh.id)) == NOT_ALLOWED  # what QA then says
    unnamed = project(engine, video_id=VID3)
    repo.update_project(engine.db(), unnamed.id, duration_s=600.0)  # read, and YouTube named no channel
    with pytest.raises(ClipForgeError):
        service.link_project(engine, unnamed.id, made.id)


def test_watched_project_takes_the_channels_campaign(engine):
    made = service.create(engine, form(footage_links=["@MrBeast"]))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    (source,) = watch_store.list_sources(engine.db(), ch.id)
    watch_store.update_source(
        engine.db(), source.id, start_published_at=datetime(2026, 10, 25, 12, tzinfo=UTC).timestamp()
    )
    engine.feed_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=CHANNEL_XML, headers={"content-type": "text/xml"})
    )
    run_check(engine, start_check(engine, ch.id, manual=True).id)
    item = watch_store.item_for_video(engine.db(), ch.id, EP2)
    made_project = engine.project(item.project_id)
    assert (made_project.campaign_id, made_project.rights_basis) == (made.id, "clipping_programme")


def test_footage_source_is_not_listed(engine):
    from tests.server import api_client

    made = service.create(engine, form(footage={"videos": [VID1], "files": False}))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    assert any(s.is_footage for s in watch_store.list_sources(engine.db(), ch.id))
    (shown,) = api_client(engine).get("/api/clip-channels").json()["channels"]
    assert shown["sources"] == []


def test_footage_videos_may_be_links(engine):
    with pytest.raises(service.Refused) as err:
        service.create(engine, form(footage={"videos": [VID1, "https://vimeo.com/1"], "files": False}))
    assert err.value.field == "footage.videos" and "https://vimeo.com/1" in str(err.value)
    made = service.create(
        engine, form(footage={"videos": [f"https://youtu.be/{VID1}", VID1], "files": False})
    )
    assert made.footage.videos == [VID1]


def test_unknown_fields_are_refused(engine):
    with pytest.raises(service.Refused) as err:
        service.create(engine, form(id="mine"))
    assert err.value.field == "id"
    assert store.list_campaigns(engine.db()) == []


def test_relisted_video_waits_again(engine):
    made = service.create(engine, form(footage={"videos": [VID1, VID2], "files": False}))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    service.update(engine, made.id, {"footage": {"videos": [VID2], "files": False}})
    assert videos(engine, ch)[VID1] == ("skipped", "not_allowed")
    service.update(engine, made.id, {"footage": {"videos": [VID1, VID2], "files": False}})
    assert videos(engine, ch) == {VID1: ("new", None), VID2: ("new", None)}


def test_footage_only_channel_is_checked_daily(engine):
    from clipforge.watch import schedule

    made = service.create(engine, form(footage={"videos": [VID1], "files": False}))
    ch = channel(engine)
    assert schedule.next_check_at(engine, ch, engine.now()) is None  # nothing to watch yet
    service.link_channel(engine, ch.id, made.id)
    linked = watch_store.get_channel(engine.db(), ch.id)
    assert schedule.next_check_at(engine, linked, engine.now()) is not None
    started = watch_store.item_for_video(engine.db(), ch.id, VID1)
    watch_store.update_item(engine.db(), started.id, status="clipping")
    unlinked = service.link_channel(engine, ch.id, None)
    assert any(s.is_footage for s in watch_store.list_sources(engine.db(), ch.id))  # its history stays
    assert schedule.next_check_at(engine, unlinked, engine.now()) is None  # but there is nothing to check


def test_history_names_the_campaign(engine):
    from clipforge.server.views import item_json

    made = service.create(engine, form(footage={"videos": [VID1, VID2], "files": False}))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    service.update(engine, made.id, {"footage": {"videos": [VID2], "files": False}})
    skipped = watch_store.item_for_video(engine.db(), ch.id, VID1)
    linked = watch_store.get_channel(engine.db(), ch.id)
    assert item_json(engine, skipped, linked)["reason"] == NOT_ALLOWED
    unlinked = linked.model_copy(update={"campaign_id": None})
    assert item_json(engine, skipped, unlinked)["reason"] == (
        "The campaign it was linked to doesn't allow footage from this channel."
    )


def test_footage_source_cant_be_removed_by_hand(engine):
    from tests.server import api_client

    made = service.create(engine, form(footage={"videos": [VID1], "files": False}))
    ch = channel(engine)
    service.link_channel(engine, ch.id, made.id)
    (footage,) = watch_store.list_sources(engine.db(), ch.id)
    answer = api_client(engine).delete(f"/api/clip-channels/{ch.id}/sources/{footage.id}")
    assert answer.status_code == 404
    assert watch_store.list_sources(engine.db(), ch.id) == [footage]
