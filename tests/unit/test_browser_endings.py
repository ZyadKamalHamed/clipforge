"""How a post through Chrome ends when ClipForge can't see the end (SPEC 10.3, 10.4). A window closed
after the press that makes the post public, or after the site said it went up, never says nothing went up,
and neither does an assisted post that waited out its time or a window closed while it was handed over
to the person: Retry would then post it twice. Nor does a hand over whose wait ran out, as ClipForge
closes its window. Assisted posting turned on or off for an account reaches the posts already in its
Queue. The real runs go against a page that stands in for the site's, which the person closes."""

import dataclasses

import pytest
from playwright.sync_api import Error as PlaywrightError

from clipforge.post.browser import instagram, tiktok
from clipforge.post.browser.run import Unexpected
from clipforge.post.models import PublishResult
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_clip, at, custom, run
from tests.server import api_client

GONE = PlaywrightError("Target page, context or browser has been closed")


class Page:
    """The site's window, which the person closes as the button is pressed ("press"), as ClipForge opens
    a page ("goto"), while ClipForge waits ("wait") or never ("never")."""

    url = "https://example.test/"

    def __init__(self, closes: str):
        self.closes = closes
        self.closed = False

    def is_closed(self):
        return self.closed

    def alive(self):
        if self.closed:
            raise GONE

    def goto(self, url, **kwargs):
        self.closed = self.closed or self.closes == "goto"
        self.alive()

    def wait_for_timeout(self, ms):
        self.closed = self.closed or self.closes == "wait"
        self.alive()

    def bring_to_front(self):
        self.alive()

    def evaluate(self, *args):
        self.alive()

    def screenshot(self, **kwargs):
        self.alive()
        return b""

    def content(self):
        self.alive()
        return ""


class Button:
    def __init__(self, page: Page):
        self.page = page

    def is_disabled(self):
        return False

    def get_attribute(self, name):
        return None

    def click(self):
        self.page.closed = self.page.closes == "press"


class Sel:
    """The site's elements, of which those in ``shown`` are on the page."""

    def __init__(self, page: Page, shown: set[str]):
        self.page = page
        self.shown = shown

    def first(self, page, step, *, visible=False, **labels):
        self.page.alive()
        return Button(self.page) if step in self.shown else None

    def logged_out(self, page):
        self.page.alive()
        return False

    def url(self, name, **labels):
        return f"https://example.test/{name}"


class TikTokEarly(tiktok._Upload):
    def steps(self):
        self.open_upload()
        raise AssertionError("the window was closed as its page opened")


class TikTokPressed(tiktok._Upload):
    def steps(self):
        self.click_post()
        self.wait_success()
        return self.result()


class TikTokLeft(tiktok._Upload):
    def steps(self):
        return self.assisted()


class InstagramPressed(instagram._Share):
    def steps(self):
        self.click_share()
        self.wait_shared()
        return self.result()


class InstagramLeft(instagram._Share):
    def steps(self):
        return self.assisted()


class TikTokHandedOver(tiktok._Upload):
    def steps(self):
        raise Unexpected(tiktok.MISSING.format(thing="who can watch the video"), "step: visibility")


class InstagramHandedOver(instagram._Share):
    def steps(self):
        raise Unexpected(instagram.MISSING.format(thing="the caption box"), "step: caption")


def ended(route, shown: set[str], closes: str, minute_s: float | None = None):
    """The post as it is after one run of ``route``, and its site's module, a hand over's minute
    ``minute_s`` long when given."""
    site = tiktok if issubclass(route, tiktok._Upload) else instagram

    def publish(post, report):
        page = Page(closes)
        return route(report.engine, Sel(page, shown), page, post, report).go()

    platform = "tiktok" if site is tiktok else "instagram"
    clock = Clock(at(6))
    engine = fake_engine(
        now=clock, publishers={f"{platform}:browser": FakePublisher([publish], platform=platform)}
    )
    if minute_s is not None:
        engine.browser.timing = dataclasses.replace(engine.browser.timing, minute_s=minute_s)
    try:
        p = custom(engine, add_clip(engine), add_account(engine, platform, method="browser"), at(7, 10))
        clock.set(at(7, 10))
        return run(engine, p.id)[1], site
    finally:
        engine.close()


@pytest.mark.parametrize(
    ("route", "shown", "closes"),
    [
        (TikTokPressed, {"post"}, "press"),
        (TikTokLeft, set(), "wait"),
        (InstagramPressed, {"share"}, "press"),
        (InstagramLeft, set(), "wait"),
    ],
    ids=["tiktok-pressed", "tiktok-assisted", "instagram-pressed", "instagram-assisted"],
)
def test_a_window_closed_after_the_press_says_to_check_first(route, shown, closes):
    after, site = ended(route, shown, closes)
    assert after.status == "needs_you"
    assert after.error != site.CLOSED
    assert "Mark as posted" in after.error and "Check" in after.error


@pytest.mark.parametrize(
    ("route", "shown"),
    [
        (TikTokPressed, {"post", "success"}),
        (TikTokLeft, {"success"}),
        (InstagramPressed, {"share", "shared"}),
        (InstagramLeft, {"shared"}),
    ],
    ids=["tiktok-pressed", "tiktok-assisted", "instagram-pressed", "instagram-assisted"],
)
def test_a_window_closed_after_the_site_said_it_went_up_is_posted(route, shown):
    after, site = ended(route, shown, "goto")
    assert (after.status, after.url, after.error) == ("posted", None, None)


@pytest.mark.parametrize(
    ("route", "shown"),
    [(TikTokHandedOver, {"success"}), (InstagramHandedOver, {"shared"})],
    ids=["tiktok", "instagram"],
)
def test_a_window_closed_after_a_post_finished_by_hand_is_posted(route, shown):
    after, site = ended(route, shown, "goto")
    assert (after.status, after.url, after.error) == ("posted", None, None)


@pytest.mark.parametrize("route", [TikTokHandedOver, InstagramHandedOver], ids=["tiktok", "instagram"])
def test_a_window_closed_while_handed_over_says_to_check_first(route):
    """You may have finished the post in the window before closing it."""
    after, site = ended(route, set(), "wait")
    assert after.status == "needs_you"
    assert "Mark as posted" in after.error and "Check" in after.error


@pytest.mark.parametrize(
    ("route", "thing"),
    [(TikTokHandedOver, "who can watch the video"), (InstagramHandedOver, "the caption box")],
    ids=["tiktok", "instagram"],
)
def test_a_hand_over_that_ran_out_says_to_check_first(route, thing):
    """ClipForge closes the window once its wait runs out, and you may have finished the post in it."""
    after, site = ended(route, set(), "never", minute_s=0.001)
    assert after.status == "needs_you"
    assert f"couldn't find {thing}." in after.error
    assert "Finish the post" not in after.error
    assert "Mark as posted" in after.error and "Check" in after.error


def test_a_window_closed_before_the_press_starts_again():
    after, site = ended(TikTokEarly, set(), "goto")
    assert (after.status, after.error) == ("needs_you", tiktok.CLOSED)


@pytest.mark.parametrize(
    "sentence",
    [tiktok.ASSISTED_LATE, tiktok.SCHEDULE_LATE, instagram.ASSISTED_LATE],
    ids=["tiktok", "tiktok-schedule", "instagram"],
)
def test_an_assisted_post_that_waited_out_its_time_says_to_check_first(sentence):
    """ClipForge sees the success text or a row with the caption it typed, never the press itself."""
    text = sentence.format(minutes=10)
    assert "wasn't pressed" not in text
    assert "Check" in text and "Mark as posted" in text


class Decides(tiktok._Upload):
    """The real choice between pressing Post and leaving it to you, with the page's work done."""

    chose = None

    def studio_time(self):
        return None

    def open_upload(self):
        pass

    set_file = wait_processed = settle = fill_caption = set_visibility = open_upload
    set_switches = set_disclosure = copyright = open_upload

    def assisted(self, banner=None, late=None):
        self.chose = "left to you"
        return PublishResult(external_id="1", url=None)

    def click_post(self):
        self.chose = "pressed Post"

    def wait_success(self):
        pass

    def result(self):
        return PublishResult(external_id="1", url=None)


def chosen(then: bool, now: bool) -> list[str]:
    """What the run chose for a post confirmed while its account's assisted posting was ``then``,
    turned to ``now`` before the post's time."""
    done = []

    def publish(post, report):
        upload = Decides(report.engine, None, None, post, report)
        result = upload.go()
        done.append(upload.chose)
        return result

    clock = Clock(at(6))
    engine = fake_engine(now=clock, publishers={"tiktok:browser": FakePublisher([publish])})
    try:
        account = add_account(engine, assisted=then)
        p = custom(engine, add_clip(engine), account, at(7, 10))
        answer = api_client(engine).patch(f"/api/accounts/{account.id}", json={"settings": {"assisted": now}})
        assert answer.status_code == 200
        clock.set(at(7, 10))
        run(engine, p.id)
        return done
    finally:
        engine.close()


def test_assisted_posting_turned_on_reaches_posts_already_in_the_queue():
    assert chosen(False, True) == ["left to you"]


def test_assisted_posting_turned_off_reaches_posts_already_in_the_queue():
    """Confirmed while it was on, they go up on their own once you turn it off."""
    assert chosen(True, False) == ["pressed Post"]
