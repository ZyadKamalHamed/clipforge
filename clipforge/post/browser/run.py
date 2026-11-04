"""What the routes through the installed Chrome share (SPEC 10.3, 10.4): a publisher whose check is the
self-test, and one post through a site's pages, one step at a time. Anything the page does that ClipForge
doesn't expect stops the run with a screenshot and the page kept, and the window stays with the person for
``away_min`` first, so a post they finish there still counts. While a window waits for the person, the
post says so in the Queue."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TypeVar

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from clipforge.post import queue
from clipforge.post.browser.selectors import Missing, Selectors
from clipforge.post.browser.selftest import self_test
from clipforge.post.errors import Fatal, NeedsUser
from clipforge.post.export import NO_VIDEO
from clipforge.post.models import Account, Capabilities, Health, Post, PublishResult

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

    from clipforge.app import Engine
    from clipforge.post.publisher import Report

T = TypeVar("T")

STOPPED_HERE = "ClipForge stopped here. Finish the post yourself in this window, or close it."
WAITS = "It waits for you in its Chrome window for {minutes}."
BANNER = """(text) => {
  document.getElementById("clipforge-banner")?.remove();
  const b = document.createElement("div");
  b.id = "clipforge-banner";
  b.textContent = text;
  b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;" +
    "background:#111;color:#fff;font:600 15px -apple-system,sans-serif;text-align:center;pointer-events:none";
  (document.body || document.documentElement).append(b);
}"""


class LoggedOut(Exception):
    """The site sent the window to its login page."""


class Unexpected(Exception):
    """The page did something ClipForge doesn't expect: the person takes it from here. ``sentence`` says
    why, then what to do in the window; ``late``, what the post says once the window is closed, when not
    the reason followed by the site's ``LATE``."""

    def __init__(self, sentence: str, details: str | None = None, late: str | None = None):
        super().__init__(sentence)
        self.sentence = sentence
        self.details = details
        self.late = late


def minutes_text(n: int) -> str:
    return "1 minute" if n == 1 else f"{n} minutes"


def norm(text: str) -> str:
    return " ".join(text.split()).lower()


def short(err: Exception) -> str:
    return str(err).splitlines()[0][:300] if str(err) else type(err).__name__


def why(sentence: str) -> str:
    """A hand over's first sentence: why ClipForge stopped, without what to do in the window."""
    return sentence.split(". ", 1)[0].rstrip(".") + "."


def who(account: Account) -> str:
    return account.handle or account.label


class Run:
    """One post through a site's pages. A site's run gives its sentences and its ``steps``, ``succeeded``
    and ``result``; this class turns what goes wrong into the person's next step."""

    LOGGED_OUT: ClassVar[str]  # on the account's card, with {handle}
    LOGGED_OUT_POST: ClassVar[str]  # on the post's row in the Queue, beside its Log in, with {handle}
    UNKNOWN: ClassVar[str]
    MISSING: ClassVar[str]  # with {thing}
    CLOSED: ClassVar[str]  # the window closed before the press that makes the post public
    UNSEEN: ClassVar[str]  # the window closed after that press was made or left to you
    PRESSED: ClassVar[frozenset[str]]  # the steps that make that press or leave it to you
    UP: ClassVar[str]  # the step logged once the site says the post went up
    NO_LINK: ClassVar[str]
    SOMETHING: ClassVar[str]
    ASSISTED: ClassVar[str]
    ASSISTED_LATE: ClassVar[str]  # with {minutes}
    LATE: ClassVar[str]  # a hand over's ending once ClipForge closed the window, after why it stopped
    THINGS: ClassVar[dict[str, str]] = {}

    def __init__(self, engine: Engine, sel: Selectors, page: Page, post: Post, report: Report):
        self.engine = engine
        self.sel = sel
        self.page = page
        self.post = post
        self.report = report
        self.t = engine.browser.timing
        self.options = post.options
        self.video: Path = report.video  # type: ignore[assignment]  # publish checked it is there

    def go(self) -> PublishResult:
        if "waiting" in self.report.post.state:  # a run that stopped while its window waited left it
            self.report.waiting(None)
        try:
            try:
                return self.steps()
            except Missing as err:
                thing = self.THINGS.get(err.step, err.step.replace("_", " "))
                raise Unexpected(self.MISSING.format(thing=thing), f"step: {err.step}") from err
            except PlaywrightError as err:
                if self.page.is_closed():
                    return self.closed(err)
                if self.logged_out():
                    raise LoggedOut() from err
                raise Unexpected(self.SOMETHING, short(err), late=self.SOMETHING) from err
        except LoggedOut:
            raise self.logged_out_error() from None
        except Unexpected as err:
            return self.hand_over(err)

    def steps(self) -> PublishResult:
        raise NotImplementedError

    def closed(self, err: PlaywrightError) -> PublishResult:
        """The window closed under the run. Once the site said the post went up it counts, without its
        link; once the press that makes it public was made or left to you, whether it went up is yours to
        check, as Retry could post it twice; before that, nothing went up."""
        done = set(self.report.steps)
        if self.UP in done:
            return PublishResult(external_id=None, url=None, warnings=[self.NO_LINK])
        raise NeedsUser(self.UNSEEN if done & self.PRESSED else self.CLOSED, details=short(err)) from err

    def left_to_you(self) -> bool:
        """Whether you make the press that makes the post public (``queue.left_to_you``), read as the press
        comes."""
        return queue.left_to_you(self.engine, self.post)

    def succeeded(self) -> bool:
        """The site says the post went up."""
        raise NotImplementedError

    def result(self) -> PublishResult:
        """The post's id and link, once it went up."""
        raise NotImplementedError

    def done_by_hand(self) -> PublishResult | None:
        """The post the person finished in the window, once the site says it went up."""
        return self.result() if self.succeeded() else None

    def assisted(self, banner: str | None = None, late: str | None = None) -> PublishResult:
        """Stops before the step that makes the post public: the person checks the page and does it.
        ``banner`` and ``late`` (with {minutes}) stand in for the site's own sentences when given."""
        self.report.final_check()
        text = banner or self.ASSISTED
        self.banner(text)
        self.report.step("assisted")
        minutes = self.engine.settings.posting.away_min
        if found := self.wait_with_you(text, minutes):
            return found
        sentence = (late or self.ASSISTED_LATE).format(minutes=minutes)
        raise NeedsUser(sentence, screenshot=self.keep("assisted"))

    # Handing over

    def hand_over(self, err: Unexpected) -> PublishResult:
        """The page as it is kept, the window left with the person for ``away_min``: a post they finish
        there counts. Otherwise the post is theirs to sort out, and as ClipForge then closes the window,
        in which they may have finished it, they check the site first. A window they close ends as
        ``closed`` says."""
        shot = self.keep("stopped")
        self.report.step("handed_over", details=err.details)
        minutes = self.engine.settings.posting.away_min
        try:
            self.banner(STOPPED_HERE)
            if found := self.wait_with_you(err.sentence, minutes):
                return found
        except PlaywrightError as gone:
            if self.page.is_closed():
                return self.closed(gone)
        except LoggedOut:  # the window went to the login page
            pass
        raise NeedsUser(err.late or f"{why(err.sentence)} {self.LATE}", details=err.details, screenshot=shot)

    def wait_with_you(self, sentence: str, minutes: int) -> PublishResult | None:
        """The window left with the person for ``minutes``, the post saying in the Queue what it waits
        for: the post they finish there, or None."""
        self.report.waiting(f"{sentence} {WAITS.format(minutes=minutes_text(minutes))}")
        try:
            return self.wait_for(minutes * self.t.minute_s, self.done_by_hand)
        finally:
            self.report.waiting(None)

    def logged_out_error(self) -> NeedsUser:
        account = self.report.account
        shot = self.keep("logged-out")
        note = self.LOGGED_OUT.format(handle=who(account))
        queue.change_account(self.engine, account.id, status="needs_login", status_note=note)
        return NeedsUser(self.LOGGED_OUT_POST.format(handle=who(account)), screenshot=shot)

    def keep(self, name: str) -> str | None:
        """A screenshot of the page beside the post's step log, and its HTML beside that, private to this
        Mac's user: the page can hold the account's details."""
        try:
            png = self.page.screenshot(full_page=True)
            html = self.page.content()
        except PlaywrightError:
            return None
        shot = self.report.screenshot(png, name)
        fd = os.open(Path(shot).with_suffix(".html"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        return shot

    # Looking at the page

    def wait_for(
        self, seconds: float, look: Callable[[], T | None], *, every: float | None = None
    ) -> T | None:
        """``look()`` until it finds something, up to ``seconds``. Each round brings the window to the
        front if the person asked, tells the runner the job is alive and notices a move to the login
        page. A look cut short by the page moving on counts as nothing found."""
        until = time.monotonic() + seconds
        while True:
            self.check_login()
            try:
                found = look()
            except PlaywrightError:
                if self.page.is_closed():
                    raise
                found = None
            if found:
                return found
            if time.monotonic() >= until:
                return None
            self.engine.browser.front(self.page)
            self.report.ctx.heartbeat()
            self.page.wait_for_timeout((every or self.t.poll_s) * 1000)

    def need(self, step: str, **labels: str) -> Locator:
        found = self.wait_for(self.t.find_s, lambda: self.sel.first(self.page, step, visible=True, **labels))
        if found is None:
            raise Missing(step)
        return found

    def dismiss(self, step: str) -> bool:
        """Closes what ``step`` finds if it shows. A click something else is in the way of is let go, unless
        that something is a dialog ClipForge doesn't know."""
        found = self.sel.first(self.page, step, visible=True)
        if found is None:
            return False
        return self.close(found, step)

    def close(self, found: Locator, step: str) -> bool:
        try:
            found.click(timeout=self.t.action_s * 1000)
        except PlaywrightTimeout:
            self.check_login()
            self.check_dialog()
            return False
        self.report.step("dismissed", what=step)
        self.pause()
        return True

    def check_dialog(self) -> None:
        dialog = self.sel.first(self.page, "dialog", visible=True)
        if dialog is not None:
            raise Unexpected(self.UNKNOWN, "dialog: " + norm(dialog.inner_text())[:200])

    def logged_out(self) -> bool:
        return self.sel.logged_out(self.page)

    def check_login(self) -> None:
        if self.logged_out():
            raise LoggedOut()

    def banner(self, text: str) -> None:
        self.page.evaluate(BANNER, text)

    def pause(self) -> None:
        self.engine.browser.pacer.pause()


class BrowserPublisher:
    """A route through the installed Chrome: ``check`` is the self-test and ``publish`` is one ``run``."""

    platform: ClassVar[str]
    method = "browser"
    caps: ClassVar[Capabilities]
    experimental: ClassVar[bool] = False
    run: ClassVar[type[Run]]
    READY: ClassVar[str]
    CHANGED: ClassVar[str]

    def __init__(self, engine: Engine):
        self.engine = engine

    def check(self, account: Account) -> Health:
        """The self-test: logged in, and every selector the post needs found on the site's page."""
        result = self_test(self.engine, account)
        if not result.logged_in:
            return Health(status="needs_login", sentence=self.run.LOGGED_OUT.format(handle=who(account)))
        if result.missing:
            details = "missing: " + ", ".join(result.missing)
            return Health(status="error", sentence=self.CHANGED, details=details)
        return Health(status="connected", sentence=self.READY)

    def publish(self, post: Post, report: Report) -> PublishResult:
        if report.video is None:
            raise Fatal(NO_VIDEO, "no_render")
        selectors = Selectors.load(self.platform, base_url=self.engine.site_base)
        with self.engine.browser.open(report.account) as context:
            page = context.pages[0] if context.pages else context.new_page()
            return self.run(self.engine, selectors, page, post, report).go()
