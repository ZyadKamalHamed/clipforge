"""The TikTok browser route (SPEC 10.3, research 02 section 3a) against the look-alike TikTok Studio on this
Mac, in headless Chrome: a post goes through the upload page as a person would post it, anything the page
does that ClipForge doesn't expect goes to the person with a screenshot, and login and the self-test read
the pages without uploading anything. No request leaves 127.0.0.1."""

import json
import random
import stat
import time
from datetime import datetime, timedelta

import pytest

from clipforge.errors import ClipForgeError
from clipforge.post import queue, store
from clipforge.post.browser import tiktok
from clipforge.post.browser.login import login_account
from clipforge.post.browser.pacing import Pacer
from clipforge.post.browser.run import why
from clipforge.post.browser.selftest import self_test
from clipforge.post.browser.session import BrowserSessions, Timing
from clipforge.post.browser.tiktok import TikTokBrowserPublisher
from clipforge.post.models import PostOptions
from clipforge.post.queue import NO_ROW, PAUSED_NOTE
from clipforge.trends.models import PostSound
from tests.fakes import Clock, fake_engine
from tests.mock_sites.server import FIRST_ID, OTHER_ID, MockSites
from tests.posting import SYD, add_account, add_clip, at, custom, interrupt_first_visit, run

NOW = at(7, 10)
# TikTok's waits, cut down so the look-alike runs them in seconds. The look-alike's slow page shows its
# editor after about 1 s: past ``processing_s`` and well inside ``editor_s``.
FAST = Timing(
    page_s=10,
    action_s=3,
    find_s=2,
    processing_s=0.5,
    editor_s=4,
    settle_s=0.1,
    popover_s=1.5,
    copyright_s=3,
    enable_s=1.0,
    enable_every_s=0.1,
    success_s=5,
    minute_s=0.15,
    key_delay_ms=5,
    poll_s=0.05,
)
AWAY_S = 10 * FAST.minute_s  # away_min is 10


@pytest.fixture
def sites():
    with MockSites() as s:
        yield s


@pytest.fixture
def engine(sites):
    eng = fake_engine(now=Clock(NOW))
    eng.site_base = sites.origin
    eng.browser = BrowserSessions(
        eng.paths, headless=True, timing=FAST, pacer=Pacer(random.Random(1), 0.0, 0.0)
    )
    yield eng
    eng.close()


def due(engine, caption="Watch this", hashtags=("money", "fyp"), **options):
    """A TikTok post to @mine that is due now, with ``caption``, ``hashtags`` and ``options``."""
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), NOW)
    return store.update_post(
        engine.db(), p.id, caption=caption, hashtags=list(hashtags), options=PostOptions(**options)
    )


def steps(engine, post_id):
    path = engine.paths.post_logs / post_id / "steps.jsonl"
    return [json.loads(line)["step"] for line in path.read_text().splitlines()]


def handed_over(engine, after, sentence):
    """The post is with the person with ``sentence``, and its screenshot and page are kept, private."""
    assert (after.status, after.error) == ("needs_you", sentence)
    shot = after.state["screenshot"]
    assert shot and shot.endswith(".png")
    png = engine.paths.post_logs / after.id / shot.rsplit("/", 1)[-1]
    assert png.read_bytes().startswith(b"\x89PNG")
    page = png.with_suffix(".html")
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    return page.read_text(encoding="utf-8")


def late(sentence):
    """What a hand over whose time ran out says once ClipForge closed the window: why it stopped, then
    to check first, as you may have posted it there."""
    return f"{why(sentence)} {tiktok.LATE}"


def waits_seen(engine, sites, post_id, when="post_clicked"):
    """What the post says it waits for, and its status, read from the database as the look-alike sends
    the event ``when``: the person pressing Post in the window, unless another is named."""
    seen = []

    def look(event):
        if event["name"] == when:
            now = store.get_post(engine.db(), post_id)
            seen.append((now.status, now.state.get("waiting")))

    sites.on_event = look
    return seen


# Posting


def test_tiktok_posts_on_the_lookalike(engine, sites):
    assert isinstance(engine.publishers["tiktok:browser"], TikTokBrowserPublisher)
    p = due(engine, visibility="friends", comments=False, duet=True, stitch=False)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert sub["caption"].rstrip() == "Watch this\n\n#money #fyp"
    assert sub["tags"] == ["#money", "#fyp"]
    assert (sub["visibility"], sub["comment"], sub["duet"], sub["stitch"]) == ("Friends", False, True, False)
    assert (sub["disclosure"], sub["branded"]) == (False, False)
    assert after.external_id == str(FIRST_ID)
    assert after.url.endswith(f"/@mine/video/{FIRST_ID}") and str(OTHER_ID) not in after.url
    (chosen,) = [e for e in sites.events if e["name"] == "file_chosen"]
    assert chosen["file"].endswith(".mp4") and chosen["size"] == len(b"video 1")
    assert after.note is None


def test_disclosure_ticks_branded_content(engine, sites):
    _, after = run(engine, due(engine, disclosure=True).id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert (sub["disclosure"], sub["branded"], sub["your_brand"]) == (True, True, False)


def test_discards_a_leftover_draft(engine, sites):
    sites.variants = {"leftover_draft"}
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    names = sites.event_names()
    assert names.index("draft_discarded") < names.index("file_chosen")


def test_dismisses_banners_tours_and_content_check(engine, sites):
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    assert sites.submissions[0]["cookies"] == "declined"
    names = sites.event_names()
    for dismissed in ("split_dismissed", "tour_dismissed", "content_check_dismissed"):
        assert dismissed in names
    assert [e["choice"] for e in sites.events if e["name"] == "cookies"] == ["declined"]


def test_hashtag_without_a_match_stays_text(engine, sites):
    sites.variants = {"no_popover_match"}
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert sub["tags"] == []
    assert sub["caption"].rstrip() == "Watch this\n\n#money #fyp"
    assert "popover_pick" not in sites.event_names()


def test_caption_keeps_line_breaks_and_emoji(engine, sites):
    body = "Line one\nLine two \U0001f525\n\nLast line"
    _, after = run(engine, due(engine, caption=body, hashtags=["money"]).id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert sub["caption"].rstrip() == body + "\n\n#money"  # and the file name TikTok put there is gone
    assert sub["tags"] == ["#money"]


def test_a_link_another_post_has_is_passed_over(engine, sites):
    """Captions that open alike: the older row matches too, but its link is an earlier post's, so the new
    post takes its own."""
    older = due(engine, caption="Some other clip from last week")
    store.update_post(
        engine.db(),
        older.id,
        status="posted",
        external_id=str(OTHER_ID),
        scheduled_at=at(19, day=2).timestamp(),
    )
    account = store.get_account(engine.db(), older.account_id)
    p = custom(engine, add_clip(engine), account, NOW)
    p = store.update_post(engine.db(), p.id, caption="Some other clip from last week, part two")
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    assert after.external_id == str(FIRST_ID)


def test_waits_for_slow_processing(engine, sites):
    sites.variants = {"slow_processing"}
    p = due(engine)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    assert "editor_shown" in steps(engine, p.id)


# Handing over to the person


def test_post_button_never_enables_needs_you(engine, sites):
    sites.variants = {"never_enables"}
    _, after = run(engine, due(engine).id)
    handed_over(engine, after, late(tiktok.NEVER_ENABLED))
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_failed_post_needs_you_with_a_screenshot(engine, sites):
    sites.variants = {"failed"}
    started = time.monotonic()
    _, after = run(engine, due(engine).id)
    page = handed_over(engine, after, late(tiktok.FAILED))
    assert "Something went wrong" in page
    assert time.monotonic() - started >= AWAY_S  # the window stayed open for the person first
    assert sites.submissions == []


def test_unknown_modal_needs_you_with_a_screenshot(engine, sites):
    sites.variants = {"unknown_modal"}
    _, after = run(engine, due(engine).id)
    page = handed_over(engine, after, late(tiktok.UNKNOWN))
    assert "Verify it" in page
    assert "post_clicked" not in sites.event_names()


def test_copyright_match_needs_you(engine, sites):
    sites.variants = {"copyright_match"}
    _, after = run(engine, due(engine).id)
    handed_over(engine, after, late(tiktok.COPYRIGHT_FOUND))
    assert "post_clicked" not in sites.event_names()


def test_logged_out_mid_post_needs_you(engine, sites):
    sites.variants = {"login_redirect"}
    p = due(engine)
    _, after = run(engine, p.id)
    sentence = tiktok.LOGGED_OUT.format(handle="mine")
    assert (after.status, after.error) == ("needs_you", tiktok.LOGGED_OUT_POST.format(handle="mine"))
    assert after.state["screenshot"]
    account = store.get_account(engine.db(), p.account_id)
    assert (account.status, account.status_note) == ("needs_login", sentence)
    assert sites.submissions == []


def test_assisted_fills_and_waits_for_zyads_post(engine, sites):
    sites.variants = {"person_posts"}
    p = due(engine, assisted=True)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    assert after.external_id == str(FIRST_ID)
    assert "assisted" in steps(engine, p.id)
    (sub,) = sites.submissions
    assert sub["caption"].rstrip() == "Watch this\n\n#money #fyp"


def test_an_assisted_post_says_in_the_queue_that_it_waits_for_you(engine, sites):
    sites.variants = {"person_posts"}
    p = due(engine, assisted=True)
    seen = waits_seen(engine, sites, p.id)
    _, after = run(engine, p.id)
    assert seen == [("uploading", f"{tiktok.ASSISTED} It waits for you in its Chrome window for 10 minutes.")]
    assert after.status == "posted" and "waiting" not in after.state


def test_a_handed_over_post_says_in_the_queue_that_it_waits_for_you(engine, sites):
    sites.variants = {"unknown_modal", "person_finishes"}
    p = due(engine)
    seen = waits_seen(engine, sites, p.id)
    _, after = run(engine, p.id)
    assert seen == [("uploading", f"{tiktok.UNKNOWN} It waits for you in its Chrome window for 10 minutes.")]
    assert after.status == "posted" and "waiting" not in after.state


def test_a_wait_a_stopped_run_left_is_gone_when_the_post_runs_again(engine, sites):
    p = due(engine)
    store.update_post(
        engine.db(),
        p.id,
        state={
            "waiting": "ClipForge has filled this in. It waits for you in its Chrome window for 10 minutes."
        },
    )
    seen = waits_seen(engine, sites, p.id, when="file_chosen")
    _, after = run(engine, p.id)
    assert seen == [("uploading", None)] and after.status == "posted"


def test_assisted_gives_back_after_the_away_time(engine, sites):
    started = time.monotonic()
    _, after = run(engine, due(engine, assisted=True).id)
    assert (after.status, after.error) == ("needs_you", tiktok.ASSISTED_LATE.format(minutes=10))
    assert after.state["screenshot"]
    assert time.monotonic() - started >= AWAY_S
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()
    assert "waiting" not in after.state


def test_opening_the_post_list_during_a_hand_over_is_not_posted(engine, sites):
    sites.variants = {"never_enables", "person_opens_list"}
    _, after = run(engine, due(engine).id)
    assert (after.status, after.error) == ("needs_you", late(tiktok.NEVER_ENABLED))
    assert after.external_id is None and sites.submissions == []


def test_opening_the_post_list_while_assisted_is_not_posted(engine, sites):
    sites.variants = {"person_opens_list"}
    _, after = run(engine, due(engine, assisted=True).id)
    assert (after.status, after.error) == ("needs_you", tiktok.ASSISTED_LATE.format(minutes=10))
    assert sites.submissions == []


SOUND = PostSound(source="shortlist", external_id="7234567890123456789", title="Night Drive")


def test_assisted_note_names_the_sound(engine, sites):
    # not marked assisted: a sound you add yourself is enough for ClipForge to stop before Post
    _, after = run(engine, due(engine, sound=SOUND).id)
    page = handed_over(engine, after, tiktok.ASSISTED_LATE.format(minutes=10))
    assert tiktok.ASSISTED_SOUND.format(title="Night Drive") in page
    assert tiktok.ASSISTED_SOUND == (
        'ClipForge has filled this in. Add the sound "{title}" in TikTok\'s editor, check it and press Post.'
    )
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_pause_before_the_post_click_posts_nothing(engine, sites):
    def pause(event):
        if event["name"] == "file_chosen":
            posting = engine.settings.posting.model_copy(update={"paused": True})
            engine.update_settings(engine.settings.model_copy(update={"posting": posting}))

    sites.on_event = pause
    _, after = run(engine, due(engine).id)
    assert (after.status, after.note) == ("scheduled", PAUSED_NOTE)
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


# TikTok Studio's own schedule

SLOT = datetime(2026, 12, 2, 19, 30, tzinfo=SYD)  # next month, from the 28th


def studio(engine, sites, monkeypatch, slot, zone="Australia/Sydney"):
    """A post due on the 28th of November for TikTok Studio to hold until ``slot``, the window's clock in
    ``zone``."""
    monkeypatch.setenv("TZ", zone)  # the Chrome the run opens reads it
    sites.today = "2026-11-28"
    engine.now.set(at(7, 10, day=28))  # inside a posting window, as NOW is
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine"), engine.now())
    return store.update_post(
        engine.db(), p.id, caption="Watch this", state={"studio": {"slot": slot.isoformat()}}
    )


def schedule_set(sites):
    """The date and time the look-alike's schedule last showed."""
    last = [e for e in sites.events if e["name"] == "schedule_set"][-1]
    return last["date"], last["time"]


def test_schedule_sets_date_and_time(engine, sites, monkeypatch):
    p = studio(engine, sites, monkeypatch, SLOT)
    _, after = run(engine, p.id)
    page = handed_over(engine, after, tiktok.SCHEDULE_LATE.format(minutes=10))
    assert schedule_set(sites) == ("2026-12-02", "19:30")
    assert 'value="2026-12-02"' in page and 'value="19:30"' in page
    assert tiktok.SCHEDULE_FILLED.format(when="Wed 2 Dec, 7:30 pm") in page
    assert "schedule" in steps(engine, p.id)
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_schedule_note_names_the_sound(engine, sites, monkeypatch):
    p = studio(engine, sites, monkeypatch, SLOT)
    store.update_post(engine.db(), p.id, options=PostOptions(sound=SOUND))
    _, after = run(engine, p.id)
    page = handed_over(engine, after, tiktok.SCHEDULE_LATE.format(minutes=10))
    assert tiktok.SCHEDULE_SOUND.format(when="Wed 2 Dec, 7:30 pm", title="Night Drive") in page
    assert tiktok.SCHEDULE_SOUND == (
        "ClipForge has filled this in and set TikTok's schedule to {when}. "
        'Add the sound "{title}" in TikTok\'s editor, check it and press Schedule.'
    )
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_schedule_mismatch_needs_you(engine, sites, monkeypatch):
    sites.variants = {"schedule_shifted"}
    _, after = run(engine, studio(engine, sites, monkeypatch, SLOT).id)
    wrong = tiktok.SCHEDULE_MISMATCH.format(shown="Wed 2 Dec, 7:35 pm", wanted="Wed 2 Dec, 7:30 pm")
    handed_over(engine, after, wrong)
    assert schedule_set(sites) == ("2026-12-02", "19:35")
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_schedule_mismatch_is_not_left_in_the_window(engine, sites, monkeypatch):
    # a Schedule press there would hold the post at the time TikTok shows, recorded at its slot
    sites.variants = {"schedule_shifted", "person_finishes"}
    p = studio(engine, sites, monkeypatch, SLOT)
    _, after = run(engine, p.id)
    wrong = tiktok.SCHEDULE_MISMATCH.format(shown="Wed 2 Dec, 7:35 pm", wanted="Wed 2 Dec, 7:30 pm")
    assert (after.status, after.error) == ("needs_you", wrong)
    assert sites.submissions == [] and "handed_over" not in steps(engine, p.id)
    assert "on_tiktok" not in after.state


def test_schedule_choice_missing_points_to_the_setting(engine, sites, monkeypatch):
    sites.variants = {"no_schedule"}
    _, after = run(engine, studio(engine, sites, monkeypatch, SLOT).id)
    handed_over(engine, after, tiktok.SCHEDULE_GONE.format(thing="the Schedule choice"))
    assert "Settings, under Posting" in tiktok.SCHEDULE_MISSING
    assert "Settings, under Posting" in tiktok.SCHEDULE_GONE
    assert sites.submissions == [] and "post_clicked" not in sites.event_names()


def test_schedule_uses_the_windows_time_zone(engine, sites, monkeypatch):
    _, after = run(engine, studio(engine, sites, monkeypatch, SLOT, zone="Asia/Bangkok").id)
    page = handed_over(engine, after, tiktok.SCHEDULE_LATE.format(minutes=10))
    assert schedule_set(sites) == ("2026-12-02", "15:30")  # 7:30 pm in Sydney is 3:30 pm in Bangkok
    assert tiktok.SCHEDULE_FILLED.format(when="Wed 2 Dec, 3:30 pm") in page


def test_schedule_you_press_is_recorded_as_scheduled(engine, sites, monkeypatch):
    sites.variants = {"person_posts"}
    p = studio(engine, sites, monkeypatch, SLOT)
    _, after = run(engine, p.id)
    (sub,) = sites.submissions
    assert sub["schedule"] == {"date": "2026-12-02", "time": "19:30"}
    log = steps(engine, p.id)
    assert "scheduled" in log and "posted" not in log
    assert (after.status, after.scheduled_at) == ("scheduled_on_tiktok", SLOT.timestamp())
    assert after.state["on_tiktok"] is True


def held(engine, sites, monkeypatch):
    """A post you scheduled in TikTok Studio's window for ``SLOT``, the clock now 10 minutes past it."""
    sites.variants = {"person_posts"}
    p = studio(engine, sites, monkeypatch, SLOT)
    _, after = run(engine, p.id)
    assert after.status == "scheduled_on_tiktok", after.error
    sites.variants = set()
    engine.now.set(SLOT + timedelta(minutes=10))
    return after


def test_link_of_a_held_post_is_read_once_after_its_slot(engine, sites, monkeypatch):
    p = held(engine, sites, monkeypatch)
    before = len(sites.served)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    assert after.external_id == str(FIRST_ID)
    assert after.url.endswith(f"/@mine/video/{FIRST_ID}")
    assert after.posted_at == SLOT.timestamp()  # when TikTok put it up
    read = sites.served[before:]
    assert read.count("/tiktokstudio/content") == 1 and "/tiktokstudio/upload" not in read
    assert len(sites.submissions) == 1  # nothing uploaded again


def test_a_held_post_missing_from_the_list_needs_you_after_one_look(engine, sites, monkeypatch):
    p = held(engine, sites, monkeypatch)
    sites.submissions.clear()  # TikTok's list doesn't show it yet
    before = len(sites.served)
    _, after = run(engine, p.id)
    assert (after.status, after.error) == ("needs_you", NO_ROW)
    read = sites.served[before:]
    assert read.count("/tiktokstudio/content") == 1 and "/tiktokstudio/upload" not in read


def test_schedule_too_close_needs_you_before_uploading(engine, sites, monkeypatch):
    p = studio(engine, sites, monkeypatch, at(7, 25, day=28))
    # the queue saw it in time for TikTok Studio; by the run's first step it isn't any more
    monkeypatch.setattr(queue, "studio_slot", lambda slot, now: slot)
    _, after = run(engine, p.id)
    late = tiktok.SCHEDULE_TOO_CLOSE.format(when="Sat 28 Nov, 7:25 am")
    assert (after.status, after.error) == ("needs_you", late)
    assert "file_chosen" not in sites.event_names()


# Login and the self-test


def logged_out(engine):
    a = add_account(engine, "tiktok", "new")
    return store.update_account(engine.db(), a.id, status="needs_login")


def test_login_reads_the_handle_after_the_cookie(engine, sites, caplog):
    caplog.set_level("DEBUG")
    a = logged_out(engine)
    sites.login_as = "realme"
    account = login_account(engine, a.id)
    assert (account.handle, account.status, account.status_note) == ("realme", "connected", None)
    assert account.profile_dir == str(engine.paths.browser / a.id)
    avatar = engine.paths.home / "accounts" / a.id / "avatar.png"
    assert account.avatar_path == str(avatar)
    assert avatar.read_bytes().startswith(b"\x89PNG")
    assert sites.served.index("/login/submit") < sites.served.index("/profile")
    (session,) = sites.sessions
    log = (engine.paths.home / "logs").glob("**/*")
    assert session not in caplog.text
    assert all(session not in p.read_text(errors="ignore") for p in log if p.is_file())


def test_login_waits_out_the_sites_own_redirect(engine, sites, monkeypatch):
    a = logged_out(engine)
    sites.login_as = "realme"
    visits = interrupt_first_visit(monkeypatch, "/profile")
    account = login_account(engine, a.id)
    assert (account.handle, account.status, account.status_note) == ("realme", "connected", None)
    assert sum(url.endswith("/profile") for url in visits) == 2


def test_login_times_out_after_ten_minutes(engine, sites):
    a = logged_out(engine)
    started = time.monotonic()
    with pytest.raises(ClipForgeError) as err:
        login_account(engine, a.id)
    assert err.value.code == "login_timeout"
    assert "10 minutes" in str(err.value)
    assert time.monotonic() - started >= AWAY_S
    assert store.get_account(engine.db(), a.id).status == "needs_login"


class Stopped:
    """A login job's context once the person pressed Stop."""

    def heartbeat(self) -> None:
        pass

    def cancelled(self) -> bool:
        return True


def test_a_stopped_login_stops_waiting(engine, sites):
    a = logged_out(engine)
    started = time.monotonic()
    with pytest.raises(ClipForgeError) as err:
        login_account(engine, a.id, Stopped())
    assert err.value.code == "login_stopped"
    assert time.monotonic() - started < AWAY_S
    assert store.get_account(engine.db(), a.id).status == "needs_login"


def test_selftest_finds_every_required_selector_and_uploads_nothing(engine, sites):
    a = add_account(engine, "tiktok", "mine")
    result = self_test(engine, a)
    assert (result.found, result.missing, result.logged_in) == (["file_input"], [], True)
    assert "/tiktokstudio/upload" in sites.served
    assert "file_chosen" not in sites.event_names() and sites.submissions == []
    health = engine.publishers["tiktok:browser"].check(a)
    assert health.status == "connected"


def test_selftest_reports_missing_selectors(engine, sites):
    sites.variants = {"no_file_input"}
    a = add_account(engine, "tiktok", "mine")
    result = self_test(engine, a)
    assert (result.found, result.missing) == ([], ["file_input"])
    health = engine.publishers["tiktok:browser"].check(a)
    assert health.status == "error" and "file_input" in (health.details or "")


def test_every_request_stays_on_127(engine, sites, browser_network):
    p = due(engine)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    assert browser_network.hosts and set(browser_network.hosts) == {"127.0.0.1"}
    sites.variants = {"phones_home"}
    self_test(engine, store.get_account(engine.db(), p.account_id))
    assert browser_network.blocked == {"www.tiktok.com"}
    browser_network.blocked.clear()  # seen and stopped, so the guard has nothing left to fail
