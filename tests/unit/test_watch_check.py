from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from clipforge import repo
from clipforge.campaigns import service
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign, Footage, FootageChannel
from clipforge.config import Settings
from clipforge.errors import ClipForgeError, SourceRefused, YouTubeRateLimited
from clipforge.ingest.urls import canonical_url
from clipforge.ingest.ytdlp import FAILED, RATE_LIMITED
from clipforge.jobs.pipeline import WATCHED_PRIORITY
from clipforge.jobs.runner import JobContext
from clipforge.jobs.stages import ingest
from clipforge.jobs.stages.brain import settings_for
from clipforge.models import ProjectOptions
from clipforge.watch import check, feed, store
from clipforge.watch.check import classify, run_check, start_check
from clipforge.watch.models import ChannelListing, ChannelOptions, ClipChannel, WatchedSource, WatchItem
from tests.fakes import Clock, FakeYouTube, fake_engine

FEEDS = Path(__file__).parent.parent / "fixtures" / "feeds"
CHANNEL_XML = (FEEDS / "channel.xml").read_bytes()
UC = "UCabcdefghijklmnopqrstuv"
UC2 = "UCbbbbbbbbbbbbbbbbbbbbbb"
START = datetime(2026, 10, 25, 12, tzinfo=UTC)  # after Episode 1, before Episodes 2 and 3 and the Short
MORNING = datetime(2026, 10, 27, 19, tzinfo=UTC)  # 06:00 on Wednesday 28 October in Sydney
SHORT, LIVE, EP2, EP1 = "ddddddddd-4", "ccccccccc3_", "bbbbbbbbbb2", "aaaaaaaaaa1"
EP5 = "eeeeeeeeee5"


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(check, "FEED_GAP_S", 0.0)


@pytest.fixture
def clock():
    return Clock(MORNING)


@pytest.fixture
def youtube():
    yt = FakeYouTube(seconds=1800.0)
    yt.videos = {video_id: {} for video_id in (SHORT, LIVE, EP2, EP1, EP5)}  # each its own info
    return yt


@pytest.fixture
def engine(youtube, clock):
    eng = fake_engine(youtube=youtube, now=clock)
    yield eng
    eng.close()


def feeds(engine, answers: dict[str, bytes | int]) -> list[str]:
    """Answers each channel's feed with its bytes, or an empty answer with that status."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        channel_id = request.url.params["channel_id"]
        seen.append(channel_id)
        answer = answers[channel_id]
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, content=answer, headers={"content-type": "text/xml"})

    engine.feed_transport = httpx.MockTransport(handle)
    return seen


def channel(engine, name: str = "Podcasts", **fields) -> ClipChannel:
    now = engine.now().timestamp()
    made = ClipChannel(id=repo.new_id("cc"), name=name, created_at=now, updated_at=now, **fields)
    return store.add_channel(engine.db(), made)


def watch(engine, ch: ClipChannel, yt: str = UC, start: datetime = START, **fields) -> WatchedSource:
    source = WatchedSource(
        id=repo.new_id("ws"),
        clip_channel_id=ch.id,
        yt_channel_id=yt,
        title="A Podcast",
        url=f"https://www.youtube.com/channel/{yt}/videos",
        start_published_at=start.timestamp(),
        created_at=start.timestamp(),
        **fields,
    )
    return store.add_source(engine.db(), source)


def checked(engine, ch: ClipChannel, *, manual: bool = False):
    return run_check(engine, start_check(engine, ch.id, manual=manual).id)


def items(engine, ch: ClipChannel) -> dict[str, tuple[str, str | None]]:
    return {i.video_id: (i.status, i.reason) for i in store.items_for(engine.db(), ch.id)}


def project_of(engine, ch: ClipChannel, video_id: str):
    item = store.item_for_video(engine.db(), ch.id, video_id)
    return engine.project(item.project_id)


def test_new_upload_becomes_a_project(engine, youtube):
    options = ChannelOptions(project=ProjectOptions(prompt="the funny bits", layout="stacked"))
    ch = channel(engine, options=options, rights_basis="permission")
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    done = checked(engine, ch)
    assert (done.found, done.skipped, done.clipped) == (3, 1, 2)
    assert done.finished_at is not None and done.drafts is None and done.error is None
    assert items(engine, ch) == {
        SHORT: ("skipped", "short"),
        LIVE: ("clipping", None),
        EP2: ("clipping", None),
    }
    for video_id in (LIVE, EP2):
        project = project_of(engine, ch, video_id)
        assert (project.clip_channel_id, project.rights_basis, project.video_id) == (
            ch.id,
            "permission",
            video_id,
        )
        assert project.options.prompt == "the funny bits" and project.options.layout == "stacked"
        assert project.options.on_limit == "wait" and project.options.economy is None
        assert project.source_url == canonical_url(video_id)
        (job,) = engine.runner.jobs_for(project.id)
        assert (job.kind, job.status, job.priority) == ("ingest.metadata", "queued", WATCHED_PRIORITY)
    assert all(i.check_id == done.id for i in store.items_for(engine.db(), ch.id))
    assert len(youtube.called("fetch_info")) == 2
    (source,) = store.list_sources(engine.db(), ch.id)
    assert (source.newest_video_id, source.error) == (SHORT, None)
    assert source.newest_published_at == datetime(2026, 10, 27, 11, tzinfo=UTC).timestamp()
    assert source.last_feed_at == MORNING.timestamp()


def test_start_check_queues_its_job_and_says_so(engine):
    ch = channel(engine)
    heard = []
    engine.events.listen(lambda event: heard.append((event.topic, event.data)))
    made = start_check(engine, ch.id, manual=False)
    (job,) = engine.runner.jobs_for(made.id)
    assert (job.kind, job.payload, job.max_attempts, job.lane) == ("watch.check", {"check": made.id}, 2, "io")
    assert (made.check_on, made.manual, made.started_at) == ("2026-10-28", False, MORNING.timestamp())
    assert store.get_channel(engine.db(), ch.id).last_check_on == "2026-10-28"
    assert ("clip_channel", {"id": ch.id}) in heard


def test_check_now_is_not_the_days_check(engine):
    ch = channel(engine)
    start_check(engine, ch.id, manual=True)
    assert store.get_channel(engine.db(), ch.id).last_check_on is None


def test_the_job_runs_the_check(engine):
    ch = channel(engine)
    watch(engine, ch, start=datetime(2026, 10, 28, tzinfo=UTC))
    feeds(engine, {UC: CHANNEL_XML})
    made = start_check(engine, ch.id, manual=False)
    assert engine.runner.run_until_idle(timeout_s=20, refs=[made.id])
    done = store.get_check(engine.db(), made.id)
    assert done.finished_at is not None and (done.found, done.drafts) == (0, 0)
    assert done.drafted_at == done.finished_at


def test_a_broken_check_is_closed(engine, monkeypatch):
    ch = channel(engine)

    def broken(engine, check_id):
        raise RuntimeError("no")

    monkeypatch.setattr(check, "run_check", broken)
    made = start_check(engine, ch.id, manual=False)
    assert engine.runner.run_until_idle(timeout_s=20, refs=[made.id])
    done = store.get_check(engine.db(), made.id)
    assert (done.error, done.drafts) == ("internal", 0) and done.finished_at and done.drafted_at


def test_old_entries_are_passed_over(engine, youtube):
    ch = channel(engine)
    watch(engine, ch, start=datetime(2026, 10, 27, 10, tzinfo=UTC))
    feeds(engine, {UC: CHANNEL_XML})
    done = checked(engine, ch)
    assert (done.found, done.skipped, done.clipped, done.drafts) == (1, 1, 0, 0)
    assert done.drafted_at == done.finished_at
    assert items(engine, ch) == {SHORT: ("skipped", "short")}
    assert youtube.called("fetch_info") == []


def test_limit_keeps_the_newest_and_the_rest_wait(engine, youtube, clock):
    ch = channel(engine, daily_video_limit=1)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    done = checked(engine, ch)
    assert done.clipped == 1
    assert items(engine, ch) == {
        SHORT: ("skipped", "short"),
        LIVE: ("clipping", None),
        EP2: ("new", "over_limit"),
    }
    assert len(youtube.called("fetch_info")) == 1
    clock.advance(days=1)
    again = checked(engine, ch)
    assert (again.found, again.clipped) == (0, 1)
    assert items(engine, ch)[EP2] == ("clipping", None)


def test_check_now_keeps_to_the_days_limit(engine, youtube, clock):
    ch = channel(engine, daily_video_limit=1)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    assert checked(engine, ch).clipped == 1  # the day's own check
    clock.advance(minutes=11)
    again = checked(engine, ch, manual=True)
    assert again.clipped == 0
    assert items(engine, ch)[EP2] == ("new", "over_limit")
    assert len(youtube.called("fetch_info")) == 1
    clock.advance(days=1)
    assert checked(engine, ch).clipped == 1  # a new day, a new limit
    assert items(engine, ch)[EP2] == ("clipping", None)


def test_waiting_drops_after_three_days(engine, youtube, clock):
    ch = channel(engine, daily_video_limit=1)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    checked(engine, ch)
    clock.advance(days=3, minutes=1)
    again = checked(engine, ch)
    assert (again.skipped, again.clipped) == (1, 0)
    assert items(engine, ch)[EP2] == ("skipped", "expired")
    assert len(youtube.called("fetch_info")) == 1  # nothing asked about a video given up on


def test_live_waits_then_clips_when_done(engine, youtube, clock):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    youtube.videos[LIVE] = {"live_status": "is_live", "duration": None}
    first = checked(engine, ch)
    assert first.clipped == 1 and items(engine, ch)[LIVE] == ("waiting", "live")
    youtube.videos[LIVE] = {"live_status": "was_live", "duration": 7200}
    clock.advance(days=1)
    second = checked(engine, ch)
    assert (second.found, second.clipped) == (0, 1)
    assert items(engine, ch)[LIVE] == ("clipping", None)


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"availability": "private"}, "private"),
        ({"availability": "subscriber_only"}, "members"),
        ({"age_limit": 18}, "age"),
        ({"duration": 200}, "too_short"),
        ({"duration": 4 * 3600 + 1}, "too_long"),
        ({"live_status": "is_upcoming"}, "upcoming"),
    ],
)
def test_refusals_skip_with_their_reason(engine, youtube, changes, code):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    youtube.videos[LIVE] = changes
    done = checked(engine, ch)
    expected = ("waiting", code) if code == "upcoming" else ("skipped", code)
    assert items(engine, ch)[LIVE] == expected
    assert done.skipped == (1 if code == "upcoming" else 2) and done.clipped == 1


def test_a_refused_lookup_skips_with_its_code(engine, youtube):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    youtube.videos[LIVE] = SourceRefused("private", "This video is private.")
    youtube.videos[EP2] = ClipForgeError("ytdlp_failed", FAILED)
    done = checked(engine, ch)
    assert items(engine, ch)[LIVE] == ("skipped", "private")
    assert items(engine, ch)[EP2] == ("new", None)  # a yt-dlp hiccup: the next check tries again
    assert done.clipped == 0 and done.drafts == 0


def test_classify():
    settings = Settings()
    good = {"id": EP2, "duration": 1800, "live_status": "not_live", "availability": "public"}
    assert classify(good, settings) == ("pass", None)
    assert classify(good | {"live_status": "post_live"}, settings) == ("waiting", "processing")
    assert classify(good | {"duration": None}, settings) == ("waiting", "processing")
    assert classify(good | {"availability": "premium_only"}, settings) == ("skipped", "members")
    assert classify(good | {"availability": "needs_auth"}, settings) == ("skipped", "age")
    assert classify(good | {"duration": 239}, settings) == ("skipped", "too_short")
    assert classify(good | {"duration": 240}, settings) == ("pass", None)
    longer = settings.model_copy(update={"watch": settings.watch.model_copy(update={"max_hours": 6.0})})
    assert classify(good | {"duration": 5 * 3600}, longer) == ("pass", None)


def test_feed_failure_falls_back_to_listing_once(engine, youtube):
    ch = channel(engine)
    watch(engine, ch, newest_video_id=EP2)
    feeds(engine, {UC: 404})
    youtube.listing = ChannelListing(
        channel_id=UC,
        title="A Podcast",
        url=f"https://www.youtube.com/channel/{UC}/videos",
        entries=[(EP5, "Episode 5"), (EP2, "Episode 2"), (EP1, "Episode 1")],
    )
    done = checked(engine, ch)
    assert youtube.called("list_channel") == [
        ("list_channel", f"https://www.youtube.com/channel/{UC}/videos", 15)
    ]
    assert (done.found, done.clipped) == (1, 1)
    assert items(engine, ch) == {EP5: ("clipping", None)}
    (source,) = store.list_sources(engine.db(), ch.id)
    assert (source.error, source.newest_video_id, source.newest_title) == (feed.UNREADABLE, EP5, "Episode 5")


def test_listing_without_a_known_video_starts_from_now(engine, youtube):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: 500})
    youtube.listing = ChannelListing(channel_id=UC, title="A Podcast", url="u", entries=[(EP2, "Episode 2")])
    done = checked(engine, ch)
    assert done.found == 0 and items(engine, ch) == {}
    assert store.list_sources(engine.db(), ch.id)[0].newest_video_id == EP2


@pytest.mark.parametrize("where", ["feed", "listing", "video"])
def test_rate_limit_stops_the_channel_for_the_day(engine, youtube, where):
    ch = channel(engine)
    watch(engine, ch)
    if where == "feed":
        feeds(engine, {UC: 429})
    elif where == "listing":
        feeds(engine, {UC: 404})
        youtube.listing = YouTubeRateLimited("rate_limited", RATE_LIMITED)
    else:
        feeds(engine, {UC: CHANNEL_XML})
        youtube.videos[LIVE] = YouTubeRateLimited("rate_limited", RATE_LIMITED)
    done = checked(engine, ch, manual=True)
    assert done.error == "rate_limited" and done.finished_at is not None
    assert done.clipped == 0 and done.drafts == 0
    assert store.get_channel(engine.db(), ch.id).last_check_on == "2026-10-28"
    assert len(youtube.called("fetch_info")) == (1 if where == "video" else 0)  # nothing after the limit
    if where == "video":
        assert items(engine, ch)[EP2] == ("new", None)


def test_one_source_failing_leaves_the_others(engine, youtube):
    ch = channel(engine)
    watch(engine, ch, yt=UC2)
    watch(engine, ch)
    seen = feeds(engine, {UC2: 404, UC: CHANNEL_XML})
    youtube.listing = ClipForgeError("ytdlp_failed", FAILED)
    done = checked(engine, ch)
    assert seen == [UC2, UC]
    assert done.clipped == 2 and done.error is None
    broken, fine = store.list_sources(engine.db(), ch.id)
    assert (broken.yt_channel_id, broken.error) == (UC2, FAILED)
    assert fine.error is None


def test_second_channel_shares_the_first_projects_files(engine):
    first, second = channel(engine), channel(engine, "Highlights")
    watch(engine, first)
    watch(engine, second)
    feeds(engine, {UC: CHANNEL_XML})
    checked(engine, first)
    earlier = engine.paths.project(project_of(engine, first, EP2).id)
    (earlier.source / "audio.m4a").write_bytes(b"the audio")
    earlier.work.mkdir(parents=True, exist_ok=True)
    for name in ("transcript.json", "signals.json", "audio16k.wav"):
        (earlier.work / name).write_bytes(b"{}")
    checked(engine, second)
    later = engine.paths.project(project_of(engine, second, EP2).id)
    assert later.root != earlier.root
    for path in (
        later.source / "audio.m4a",
        later.source / "info.json",
        later.transcript_json,
        later.signals_json,
    ):
        twin = earlier.root / path.relative_to(later.root)
        assert path.stat().st_ino == twin.stat().st_ino  # a hard link
    assert len(repo.find_projects_by_video(engine.db(), EP2)) == 2


def test_hand_started_video_is_shared_not_refetched(engine, youtube):
    (hand,), _ = engine.add_sources(canonical_url(EP2), ProjectOptions())
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    checked(engine, ch)
    assert len(youtube.called("fetch_info")) == 2  # one for each video, from the check
    watched = project_of(engine, ch, EP2)
    assert watched.id != hand.id and engine.project(hand.id).clip_channel_id is None
    (job,) = engine.runner.jobs_for(watched.id)
    ctx = JobContext(job, engine.db(), engine.paths, engine.settings, engine.events, beat_s=1.0)
    ingest.metadata(engine, ctx)
    assert len(youtube.called("fetch_info")) == 2  # none from the metadata stage
    assert engine.project(watched.id).title == f"Video {EP2}"


def test_run_check_twice_adds_nothing(engine, youtube):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    made = start_check(engine, ch.id, manual=False)
    first = run_check(engine, made.id)
    again = run_check(engine, made.id)
    assert again == first
    assert len(youtube.called("fetch_info")) == 2
    assert len(repo.find_projects_by_video(engine.db(), EP2)) == 1


def test_a_rerun_after_a_stop_keeps_its_counts(engine, youtube):
    ch = channel(engine)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    made = start_check(engine, ch.id, manual=False)
    youtube.videos[EP2] = RuntimeError("ClipForge closed")
    with pytest.raises(RuntimeError):
        run_check(engine, made.id)
    youtube.videos[EP2] = {}
    done = run_check(engine, made.id)
    assert (done.found, done.skipped, done.clipped) == (3, 1, 2)
    assert len(repo.find_projects_by_video(engine.db(), LIVE)) == 1


@pytest.mark.parametrize(
    "on_limit, expected",
    [("wait", ("wait", None)), ("economy", ("wait", True)), ("basic", ("basic", None))],
)
def test_channel_on_limit_reaches_the_project(engine, on_limit, expected):
    ch = channel(engine, on_limit=on_limit)
    watch(engine, ch)
    feeds(engine, {UC: CHANNEL_XML})
    checked(engine, ch)
    options = project_of(engine, ch, EP2).options
    assert (options.on_limit, options.economy) == expected


def test_basic_on_limit_for_watched_project(engine):
    base = Settings(on_llm_limit="wait", llm_economy=False)
    (project,), _ = engine.add_sources(canonical_url(EP2), ProjectOptions(on_limit="basic"))
    assert settings_for(project, base).on_llm_limit == "basic"
    economy = project.model_copy(update={"options": ProjectOptions(economy=True)})
    assert settings_for(economy, base).llm_economy is True
    plain = project.model_copy(update={"options": ProjectOptions()})
    assert settings_for(plain, base) == base


def test_check_skips_a_source_the_campaign_no_longer_allows(engine, youtube):
    conn = engine.db()
    now = engine.now().timestamp()
    allowed = FootageChannel(yt_channel_id=UC2, url=f"https://www.youtube.com/channel/{UC2}/videos")
    made = campaigns.add_campaign(
        conn,
        Campaign(
            id=repo.new_id("cp"),
            name="Beast clips",
            marketplace="Whop",
            footage=Footage(channels=[allowed]),
            created_at=now,
            updated_at=now,
        ),
    )
    ch = channel(engine, campaign_id=made.id, rights_basis="clipping_programme")
    source = watch(engine, ch)  # UC, which the campaign was changed not to allow
    store.add_item(
        conn,
        WatchItem(
            id=repo.new_id("wi"),
            source_id=source.id,
            clip_channel_id=ch.id,
            video_id=EP2,
            status="new",
            first_seen_at=now,
            updated_at=now,
        ),
    )
    seen = feeds(engine, {UC: CHANNEL_XML})
    done = checked(engine, ch)
    assert seen == [] and done.clipped == 0
    assert items(engine, ch) == {EP2: ("skipped", "not_allowed")}
    (kept,) = store.list_sources(conn, ch.id)
    assert kept.error == "The campaign Beast clips doesn't allow footage from this channel."
    assert youtube.called("fetch_info") == []


def test_footage_source_feed_is_never_read(engine, youtube, clock):
    conn = engine.db()
    now = engine.now().timestamp()
    made = campaigns.add_campaign(
        conn,
        Campaign(
            id=repo.new_id("cp"),
            name="Beast clips",
            marketplace="Whop",
            footage=Footage(videos=[EP2, EP1, EP5]),
            created_at=now,
            updated_at=now,
        ),
    )
    ch = channel(engine, daily_video_limit=2)
    service.link_channel(engine, ch.id, made.id)
    seen = feeds(engine, {})
    clock.set(MORNING + timedelta(days=4))  # longer than new uploads wait: a campaign's videos keep waiting
    done = checked(engine, ch)
    assert seen == []  # the campaign's own videos have no feed to read
    assert done.clipped == 2
    assert items(engine, ch) == {
        EP2: ("clipping", None),
        EP1: ("clipping", None),
        EP5: ("new", "over_limit"),
    }
    project = project_of(engine, ch, EP2)
    assert (project.campaign_id, project.rights_basis) == (made.id, "clipping_programme")
    assert store.item_for_video(conn, ch.id, EP2).title == f"Video {EP2}"
