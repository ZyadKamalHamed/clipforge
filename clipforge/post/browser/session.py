"""Chrome windows for the browser route (SPEC 10.3, 17): the installed Chrome with a persistent profile per
account at ``~/ClipForge/browser/<account id>``, private to this Mac's user, and only one window at a time
so ClipForge never drives two sites at once. Playwright is imported only when a window opens."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge.paths import Paths
from clipforge.post.browser.pacing import Pacer
from clipforge.post.models import Account

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Timing:
    """How long the browser route waits for each thing, in seconds (research 02 sections 3a and 3b)."""

    page_s: float = 60  # a page to load
    action_s: float = 15  # a click or a key to land
    find_s: float = 10  # an element the page should already show
    processing_s: float = 60  # TikTok's resolution label once the file is chosen
    editor_s: float = 120  # then, without the label, the caption box
    settle_s: float = 8  # and a little more after the caption box shows
    popover_s: float = 5  # the hashtag list after a tag is typed
    copyright_s: float = 60  # the copyright check's answer
    enable_s: float = 180  # the Post button to turn on
    enable_every_s: float = 2  # how often to look at it
    success_s: float = 60  # the site to say the post went up
    notice_s: float = 5  # Instagram's Reels notice once the file is chosen
    share_s: float = 360  # Instagram's "Sharing", which is the real upload (research 02 section 3b)
    minute_s: float = 60  # a minute, for away_min and the login wait
    key_delay_ms: float = 60  # between the keys of a hashtag
    poll_s: float = 0.25  # between looks while waiting


def launch(pw: Any, profile_dir: Path, *, headless: bool) -> BrowserContext:
    """The installed Chrome on the account's profile, with nothing added to it."""
    return pw.chromium.launch_persistent_context(
        str(profile_dir), channel="chrome", headless=headless, locale="en-US"
    )


def _sync_playwright() -> AbstractContextManager[Any]:
    from playwright.sync_api import sync_playwright

    return sync_playwright()


class BrowserSessions:
    def __init__(
        self,
        paths: Paths,
        *,
        headless: bool = False,
        playwright_factory: Callable[[], AbstractContextManager[Any]] | None = None,
        timing: Timing | None = None,
        pacer: Pacer | None = None,
    ):
        self.paths = paths
        self.headless = headless
        self.playwright_factory = playwright_factory or _sync_playwright
        self.timing = timing or Timing()
        self.pacer = pacer or Pacer()
        self._lock = threading.Lock()
        self._front = threading.Event()

    def profile_dir(self, account: Account) -> Path:
        """The account's Chrome profile folder, made private to this Mac's user."""
        if not ACCOUNT_ID.fullmatch(account.id):
            raise ValueError(f"not an account id: {account.id!r}")
        folder = self.paths.browser / account.id
        for f in (self.paths.browser, folder):
            f.mkdir(parents=True, exist_ok=True)
            f.chmod(0o700)
        return folder

    @contextmanager
    def open(self, account: Account) -> Iterator[BrowserContext]:
        """A Chrome window on the account's profile, for as long as the block runs. A second caller waits
        until the first window has closed."""
        with self._lock:
            folder = self.profile_dir(account)
            with self.playwright_factory() as pw:
                context = launch(pw, folder, headless=self.headless)
                try:
                    context.set_default_timeout(self.timing.action_s * 1000)
                    yield context
                finally:
                    context.close()

    def request_front(self) -> None:
        """The person asked to see the window: the next wait brings it to the front."""
        self._front.set()

    def front(self, page: Page) -> None:
        if self._front.is_set():
            self._front.clear()
            page.bring_to_front()
