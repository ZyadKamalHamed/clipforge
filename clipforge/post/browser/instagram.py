"""Instagram through the installed Chrome (SPEC 10.4, research 02 section 3b), experimental: a Reel shared
through instagram.com's web composer as a person would share it, one step at a time. Used only when an
account is switched to the browser method."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from clipforge.post import queue
from clipforge.post.browser.run import BrowserPublisher, Run, Unexpected, norm
from clipforge.post.distribute import full_caption
from clipforge.post.models import PublishResult
from clipforge.post.plan import CAPS

if TYPE_CHECKING:
    from playwright.sync_api import Locator

ASSISTED = "ClipForge has filled this in. Check it and press Share."
LOGGED_OUT = "Instagram logged @{handle} out. Log in again on the Accounts page, then press Retry."
LOGGED_OUT_POST = (
    "Instagram logged @{handle} out. Press Log in, then Retry once you're in."  # on the post's row
)
UNKNOWN = "Instagram showed something ClipForge doesn't know. Finish the post in its window, or press Retry."
FAILED = "Instagram said the reel couldn't be shared. Finish the post in its window, or press Retry."
NO_SHARE = "Instagram didn't say the reel was shared. Check Instagram, then press Retry or Mark as posted."
CAPTION_WRONG = (
    "Instagram's caption box didn't take the whole caption. Fix it in its window and press Share, "
    "or press Retry."
)
ASSISTED_LATE = (
    "ClipForge filled this in but didn't see it shared within {minutes} minutes. "
    "Check Instagram, then press Retry to fill it in again or Mark as posted."
)
MISSING = (
    "Instagram has changed and ClipForge couldn't find {thing}. "
    "Finish the post in its window, or press Retry."
)
CLOSED = "The Instagram window was closed before the reel was shared. Press Retry to start again."
UNSEEN = (
    "The Instagram window was closed before ClipForge could see whether the reel was shared. "
    "Check Instagram, then press Retry or Mark as posted."
)
LATE = (
    "ClipForge didn't see the reel shared before it closed the window. Check Instagram, then press "
    "Retry or Mark as posted."
)
SOMETHING = queue.SOMETHING.format(site=queue.SITE["instagram"])
NO_LINK = "Shared, but ClipForge couldn't find the reel's link on Instagram."
READY = "Instagram's page has everything ClipForge needs to start a post."
CHANGED = "Instagram has changed, so ClipForge can't post there until it is updated."

THINGS = {
    "new_post": "the New post button",
    "post_option": "Post in the Create menu",
    "file_input": "the file picker",
    "crop": "the crop button",
    "crop_original": "the Original crop",
    "next": "the Next button",
    "caption": "the caption box",
    "share": "the Share button",
}
# What comes up over the page: the Reels notice once a video is chosen, and the Not Now modals a fresh
# session meets. Never "Turn On" or "Save info".
FRONT = ("reels_ok", "not_now")
CAPTION_MATCH = 30  # the caption's first characters, enough to tell its reel
LOOK = 4  # reels opened from the top of the grid: a pinned reel or two can come before the new one
REEL_CODE = re.compile(r"/reel/([A-Za-z0-9_-]+)")
REEL_LINKS = """(selector) => [...document.querySelectorAll(selector)].map((a) => a.href)"""
REEL_TEXT = """() => [
  document.querySelector("meta[property='og:description']")?.content || "",
  document.body?.innerText || "",
].join(" ")"""


class _Share(Run):
    """One Reel through the web composer, in the order research 02 section 3b gives."""

    LOGGED_OUT = LOGGED_OUT
    LOGGED_OUT_POST = LOGGED_OUT_POST
    UNKNOWN = UNKNOWN
    MISSING = MISSING
    CLOSED = CLOSED
    UNSEEN = UNSEEN
    LATE = LATE
    PRESSED = frozenset({"share", "assisted", "handed_over"})
    UP = "shared"
    NO_LINK = NO_LINK
    SOMETHING = SOMETHING
    ASSISTED = ASSISTED
    ASSISTED_LATE = ASSISTED_LATE
    THINGS = THINGS
    composing = False  # the composer is a dialog itself

    def steps(self) -> PublishResult:
        self.open_home()
        self.open_composer()
        self.set_file()
        self.crop_original()
        self.next_steps()
        self.fill_caption()
        if self.left_to_you():
            return self.assisted()
        self.click_share()
        self.wait_shared()
        return self.result()

    # The steps

    def open_home(self) -> None:
        self.report.step("open_home")
        self.page.goto(self.sel.url("upload"), timeout=self.t.page_s * 1000)
        self.check_login()
        self.pause()
        self.settle()
        self.check_login()
        self.check_dialog()

    def open_composer(self) -> None:
        """New post, then Post from the Create menu. A Create button that opens the composer straight away
        needs no menu."""
        self.press("new_post")

        def menu_or_composer() -> Locator | None:
            return self.sel.first(self.page, "post_option", visible=True) or self.sel.first(
                self.page, "dialog", visible=True
            )

        if self.wait_for(self.t.find_s, menu_or_composer) is None:
            raise Unexpected(MISSING.format(thing=THINGS["post_option"]), "step: post_option")
        if self.sel.first(self.page, "post_option", visible=True) is not None:
            self.press("post_option")
        self.composing = True
        self.report.step("composer")

    def set_file(self) -> None:
        """The video into the composer's file input, then OK on the Reels notice when it shows."""
        chooser = self.wait_for(self.t.find_s, lambda: self.sel.first(self.page, "file_input"))
        if chooser is None:
            raise Unexpected(MISSING.format(thing=THINGS["file_input"]), "step: file_input")
        self.report.step("choose_file", size=self.video.stat().st_size)
        chooser.set_input_files(str(self.video))
        self.pause()
        ok = self.wait_for(self.t.notice_s, lambda: self.sel.first(self.page, "reels_ok", visible=True))
        if ok is not None:
            self.close(ok, "reels_ok")

    def crop_original(self) -> None:
        """Original keeps the 9:16 video whole; Instagram's own pick is a square crop."""
        self.press("crop")
        self.press("crop_original")
        self.report.step("crop", choice="original")

    def next_steps(self) -> None:
        """Next past the crop, then Next past the edit step. The second waits for the first to land, so a
        click can't count twice."""
        self.press("next")
        if not self.wait_for(self.t.find_s, lambda: self.sel.first(self.page, "crop", visible=True) is None):
            raise Unexpected(MISSING.format(thing="the step after the crop"), "step: next")
        self.press("next")
        self.need("caption")
        self.report.step("next")

    def fill_caption(self) -> None:
        """The caption a line at a time, then a space: a hashtag still being typed keeps Instagram's list
        open over Share. What the box holds is read back before anything is shared."""
        text = full_caption(self.post.caption or "", self.post.hashtags)
        editor = self.need("caption")
        editor.evaluate("e => e.focus()")
        keys = self.page.keyboard
        lines = text.split("\n")
        for n, line in enumerate(lines):
            if n:
                keys.press("Enter")
            if line:
                keys.insert_text(line)
        if text:
            keys.insert_text(" ")
        self.pause()
        if norm(editor.inner_text()) != norm(text):
            raise Unexpected(CAPTION_WRONG, "step: caption")
        self.report.step("caption", lines=len(lines), hashtags=len(self.post.hashtags))

    def click_share(self) -> None:
        button = self.need("share")
        self.report.final_check()
        self.report.step("share")
        button.click()
        self.report.processing()

    def wait_shared(self) -> None:
        """ "Sharing" is the real upload: up to ``share_s`` for Instagram to say the reel is up."""

        def done() -> bool:
            if self.sel.first(self.page, "share_failed", visible=True) is not None:
                raise Unexpected(FAILED, "step: shared")
            return self.succeeded()

        if not self.wait_for(self.t.share_s, done):
            raise Unexpected(NO_SHARE, "step: shared", late=NO_SHARE)

    def result(self) -> PublishResult:
        """The reel's link: the top of the account's reels grid, each reel opened until one holds the
        caption."""
        self.report.step("shared")
        handle = self.report.account.handle
        needle = norm(full_caption(self.post.caption or "", self.post.hashtags))[:CAPTION_MATCH]
        for _ in range(3 if handle and needle else 0):  # an empty caption could be any reel
            try:
                self.page.goto(self.sel.url("reels", handle=handle), timeout=self.t.page_s * 1000)
                self.page.wait_for_timeout(self.t.settle_s * 1000)
                selector = self.sel.candidates("reel_link")[0]
                codes = [
                    m.group(1) for h in self.page.evaluate(REEL_LINKS, selector) if (m := REEL_CODE.search(h))
                ]
                for code in list(dict.fromkeys(codes))[:LOOK]:
                    url = self.sel.url("reel", code=code)
                    self.page.goto(url, timeout=self.t.page_s * 1000)
                    if needle in norm(self.page.evaluate(REEL_TEXT)):
                        self.report.step("link", external_id=code)
                        return PublishResult(external_id=code, url=url)
            except PlaywrightError:  # a page cut short by the next one
                pass
            self.page.wait_for_timeout(self.t.settle_s * 1000)
        self.report.step("no_link")
        return PublishResult(external_id=None, url=None, warnings=[NO_LINK])

    def succeeded(self) -> bool:
        return self.sel.first(self.page, "shared", visible=True) is not None

    # Instagram's modals

    def press(self, step: str) -> None:
        """Clicks the step's element. A notice or Not Now modal that came up over it is closed and the
        click tried once more."""
        for again in (True, False):
            found = self.need(step)
            try:
                found.click(timeout=self.t.action_s * 1000)
            except PlaywrightTimeout:
                self.check_login()
                if again and self.settle():
                    continue
                self.check_dialog()
                raise
            self.pause()
            return

    def settle(self) -> bool:
        """Closes what comes up over the page, the newest first. True when anything was closed."""
        closed = False
        for _ in range(4):  # closing one can bring up another
            found = next(((step, f) for step in FRONT if (f := self.newest(step)) is not None), None)
            if found is None or not self.close(found[1], found[0]):
                break
            closed = True
        return closed

    def newest(self, step: str) -> Locator | None:
        """What ``step`` finds that showed last: Instagram's modals stack, so the last one sits on top."""
        for selector in self.sel.candidates(step):
            found = self.page.locator(selector + " >> visible=true")
            if found.count():
                return found.last
        return None

    def check_dialog(self) -> None:
        """Once the composer is open, only a dialog besides it is one ClipForge doesn't know."""
        dialogs = self.page.locator(self.sel.candidates("dialog")[0] + " >> visible=true")
        if dialogs.count() > (1 if self.composing else 0):
            raise Unexpected(UNKNOWN, "dialog: " + norm(dialogs.last.inner_text())[:200])


class InstagramBrowserPublisher(BrowserPublisher):
    platform = "instagram"
    caps = CAPS["instagram:browser"]
    experimental = True
    run = _Share
    READY = READY
    CHANGED = CHANGED
