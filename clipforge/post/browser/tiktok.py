"""TikTok through the installed Chrome (SPEC 10.3, research 02 section 3a): TikTok Studio's upload page,
filled in and posted as a person would post it, one step at a time."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError

from clipforge.post import queue, slots, store
from clipforge.post.browser.run import BrowserPublisher, LoggedOut, Run, Unexpected, norm
from clipforge.post.browser.selectors import Missing, Selectors
from clipforge.post.distribute import full_caption
from clipforge.post.errors import NeedsUser
from clipforge.post.models import Post, PublishResult
from clipforge.post.plan import CAPS, at_text
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from playwright.sync_api import Locator

    from clipforge.post.publisher import Report

ASSISTED = "ClipForge has filled this in. Check it and press Post."
ASSISTED_SOUND = (
    'ClipForge has filled this in. Add the sound "{title}" in TikTok\'s editor, check it and press Post.'
)
LOGGED_OUT = "TikTok logged @{handle} out. Log in again on the Accounts page, then press Retry."
LOGGED_OUT_POST = "TikTok logged @{handle} out. Press Log in, then Retry once you're in."  # on the post's row
UNKNOWN = (
    "TikTok Studio showed something ClipForge doesn't know. Finish the post in its window, or press Retry."
)
NEVER_ENABLED = "TikTok Studio's Post button never turned on. Finish the post in its window, or press Retry."
FAILED = "TikTok Studio said something went wrong. Finish the post in its window, or press Retry."
NOT_PROCESSED = (
    "TikTok Studio didn't finish processing the video. Finish the post in its window, or press Retry."
)
NO_SUCCESS = (
    "TikTok Studio didn't say the post went up. Check TikTok Studio, then press Retry or Mark as posted."
)
COPYRIGHT_FOUND = (
    "TikTok's copyright check found a match. Check the window, then post it yourself or cancel it."
)
ASSISTED_LATE = (
    "ClipForge filled this in but didn't see it go up within {minutes} minutes. "
    "Check TikTok Studio, then press Retry to fill it in again or Mark as posted."
)
MISSING = (
    "TikTok Studio has changed and ClipForge couldn't find {thing}. "
    "Finish the post in its window, or press Retry."
)
CLOSED = "The TikTok Studio window was closed before the post went up. Press Retry to start again."
UNSEEN = (
    "The TikTok Studio window was closed before ClipForge could see whether the post went up. "
    "Check TikTok Studio, then press Retry or Mark as posted."
)
LATE = (
    "ClipForge didn't see the post go up before it closed the window. Check TikTok Studio, then press "
    "Retry or Mark as posted."
)
SOMETHING = queue.SOMETHING.format(site=queue.SITE["tiktok"])
NO_LINK = "Posted, but ClipForge couldn't find its link in TikTok Studio."
READY = "TikTok Studio's upload page has everything ClipForge needs."
SCHEDULE_FILLED = (
    "ClipForge has filled this in and set TikTok's schedule to {when}. Check it and press Schedule."
)
SCHEDULE_SOUND = (
    "ClipForge has filled this in and set TikTok's schedule to {when}. "
    'Add the sound "{title}" in TikTok\'s editor, check it and press Schedule.'
)
SCHEDULE_LATE = (
    "ClipForge filled this in and set TikTok's schedule but didn't see it scheduled within {minutes} "
    "minutes. Check TikTok Studio, then press Retry to fill it in again or Mark as posted."
)
SCHEDULE_MISMATCH = (
    "TikTok's schedule shows {shown}, not {wanted}, so nothing was scheduled. Press Retry to try again, "
    "or turn off TikTok's own schedule in Settings, under Posting, and press Retry to post it at its time."
)
SCHEDULE_MISSING = (
    "TikTok Studio has changed and ClipForge couldn't find {thing}, so nothing was scheduled. Schedule it "
    "in its window, or turn off TikTok's own schedule in Settings, under Posting, and press Retry."
)
SCHEDULE_GONE = (
    "TikTok Studio has changed and ClipForge couldn't find {thing}, and it didn't see the post scheduled "
    "before it closed the window. Check TikTok Studio, then press Retry or Mark as posted. To post it at "
    "its time instead, turn off TikTok's own schedule in Settings, under Posting, first."
)
SCHEDULE_TOO_CLOSE = (
    "TikTok Studio schedules 20 minutes to 10 days ahead, so it can't take {when} now. Nothing was "
    "uploaded: post it yourself in TikTok Studio, then mark it as posted."
)
CHANGED = "TikTok Studio has changed, so ClipForge can't post there until it is updated."

VISIBILITY = {"everyone": "Everyone", "friends": "Friends", "only_me": "Only you"}
THINGS = {
    "file_input": "the file picker",
    "editor": "the caption box",
    "visibility": "who can watch the video",
    "visibility_option": "who can watch the video",
    "schedule_radio": "the Schedule choice",
    "schedule_done": "the Schedule button",
    "schedule_date": "the schedule's date",
    "calendar_week": "the schedule's calendar",
    "calendar_header": "the slot's month in the schedule's calendar",
    "calendar_next": "the schedule's next month arrow",
    "calendar_day": "the slot's day in the schedule's calendar",
    "schedule_time": "the schedule's time",
    "time_hours": "the slot's hour in the schedule's time picker",
    "time_minutes": "the slot's minutes in the schedule's time picker",
}
ZONE = "() => Intl.DateTimeFormat().resolvedOptions().timeZone"
# What sits over the page once the video is in, front first: the tour's overlay covers everything, and the
# content-check modal sits over the split-screen prompt.
DISMISS = ("tour", "content_check", "split_prompt", "cookie_banner")
CAPTION_MATCH = 30  # the caption's first characters, enough to tell its row in the post list
# The link of the post whose row holds ``needle``, passing over the links ``taken`` by ClipForge's other
# posts: each link's row is the biggest box around it that holds no other post's link.
FIND_LINK = """({ needle, taken }) => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
  for (const a of document.querySelectorAll("a[href*='/video/']")) {
    const id = (/\\/video\\/(\\d+)/.exec(a.href) || [])[1];
    if (taken.includes(id)) continue;
    let row = a;
    while (row.parentElement) {
      const up = row.parentElement;
      const links = new Set([...up.querySelectorAll("a[href*='/video/']")].map((x) => x.href));
      if (links.size > 1) break;
      row = up;
    }
    if (norm(row.textContent).includes(needle)) return a.href;
  }
  return null;
}"""
VIDEO_ID = re.compile(r"/video/(\d+)")


def shown_text(day: str, clock: str, zone: ZoneInfo) -> str:
    """What TikTok's schedule shows, worded as ClipForge words a time when it reads as one."""
    try:
        return at_text(datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=zone), zone)
    except ValueError:
        return f"{day} {clock}".strip()[:40] or "nothing"


class _Upload(Run):
    """One post through the upload page, in the order research 02 section 3a gives."""

    LOGGED_OUT = LOGGED_OUT
    LOGGED_OUT_POST = LOGGED_OUT_POST
    UNKNOWN = UNKNOWN
    MISSING = MISSING
    CLOSED = CLOSED
    UNSEEN = UNSEEN
    LATE = LATE
    PRESSED = frozenset({"post", "assisted", "handed_over"})
    UP = "posted"
    NO_LINK = NO_LINK
    SOMETHING = SOMETHING
    ASSISTED = ASSISTED
    ASSISTED_LATE = ASSISTED_LATE
    THINGS = THINGS
    looked = False  # the post list was searched since the page last arrived there
    studio: datetime | None = None  # the time TikTok Studio's own schedule is set to, for a post it holds

    def steps(self) -> PublishResult:
        self.studio = self.studio_time()
        self.open_upload()
        self.set_file()
        self.wait_processed()
        self.settle()
        self.fill_caption()
        self.set_visibility()
        self.set_switches()
        self.set_disclosure()
        self.copyright()
        sound = self.sound_title()
        if self.studio is not None:  # ClipForge never presses Schedule: you do, as with an assisted post
            when = self.set_schedule(self.studio)
            if sound:
                return self.assisted(SCHEDULE_SOUND.format(when=when, title=sound), SCHEDULE_LATE)
            return self.assisted(SCHEDULE_FILLED.format(when=when), SCHEDULE_LATE)
        if sound:  # never Post for a post whose sound you still have to add
            return self.assisted(ASSISTED_SOUND.format(title=sound))
        if self.left_to_you():
            return self.assisted()
        self.click_post()
        self.wait_success()
        return self.result()

    # The steps

    def sound_title(self) -> str | None:
        """The title of the shortlisted sound you add in TikTok's editor, cleaned for the banner, or None."""
        sound = self.options.sound
        if sound is None or sound.source != "shortlist":
            return None
        return " ".join((clean_text(sound.title) or "").split()) or None

    def studio_time(self) -> datetime | None:
        """The post's slot as TikTok Studio's own schedule takes it, for a post it holds; None for one that
        goes up now. A slot it won't take any more is yours before anything is uploaded."""
        slot = (self.post.state.get("studio") or {}).get("slot")
        if not slot:
            return None
        at = datetime.fromisoformat(slot)
        found = slots.studio_slot(at, self.engine.now())
        if found is None:
            tz = ZoneInfo(self.engine.settings.posting.timezone)
            raise NeedsUser(SCHEDULE_TOO_CLOSE.format(when=at_text(at, tz)))
        return found

    def open_upload(self) -> None:
        self.report.step("open_upload")
        self.page.goto(self.sel.url("upload"), timeout=self.t.page_s * 1000)
        self.check_login()
        self.pause()

    def set_file(self) -> None:
        """A leftover draft is discarded first, each of its prompts answered Discard."""
        for _ in range(4):
            self.dismiss("cookie_banner")
            discard = self.sel.first(self.page, "discard", visible=True)
            if discard is None:
                break
            self.report.step("discard_draft")
            discard.click()
            self.pause()
        self.check_login()
        chooser = self.sel.find(self.page, "file_input", self.t.find_s * 1000)
        self.report.step("choose_file", size=self.video.stat().st_size)
        chooser.set_input_files(str(self.video))
        self.pause()

    def wait_processed(self) -> None:
        if self.wait_for(self.t.processing_s, lambda: self.sel.first(self.page, "processed", visible=True)):
            self.report.step("processed")
            return
        if not self.wait_for(self.t.editor_s, lambda: self.sel.first(self.page, "editor", visible=True)):
            raise Unexpected(NOT_PROCESSED, "step: processed")
        self.page.wait_for_timeout(self.t.settle_s * 1000)
        self.report.step("editor_shown")

    def settle(self) -> None:
        """Every banner, prompt and tour in front of the form closed, front first. A click something else
        is in the way of is let go: a dialog ClipForge doesn't know is the person's."""
        for _ in range(3):  # closing one can bring up another
            closed = [step for step in DISMISS if self.dismiss(step)]
            if not closed:
                break
        self.check_login()
        self.check_dialog()

    def fill_caption(self) -> None:
        """TikTok puts the file's name in the caption: all of it goes, then the body goes in a line at a time
        and each hashtag is typed so TikTok's list can turn it into a tag."""
        editor = self.need("editor")
        editor.evaluate("e => e.focus()")
        keys = self.page.keyboard
        keys.press("Meta+A")
        keys.press("Backspace")
        lines = (self.post.caption or "").split("\n")
        for n, line in enumerate(lines):
            if n:
                keys.press("Enter")
            if line:
                keys.insert_text(line)
        tags = [t.lstrip("#") for t in self.post.hashtags if t.lstrip("#")]
        if tags and any(lines):
            keys.press("Enter")
            keys.press("Enter")
        picked = sum(self.hashtag(tag) for tag in tags)
        self.report.step("caption", lines=len(lines), hashtags=len(tags), picked=picked)
        self.pause()

    def hashtag(self, tag: str) -> bool:
        """Types ``#tag`` and picks it from TikTok's list when the list has it exactly, else leaves the
        text as it is."""
        keys = self.page.keyboard
        keys.type("#" + tag, delay=self.t.key_delay_ms)
        exact = re.compile(rf"^\s*#{re.escape(tag)}(?!\w)", re.IGNORECASE)

        def match() -> Locator | None:
            popover = self.sel.first(self.page, "popover", visible=True)
            if popover is None:
                return None
            rows = popover.locator("*").filter(has_text=exact)
            return rows.last if rows.count() else None

        row = self.wait_for(self.t.popover_s, match)
        if row is None:
            keys.press("Escape")
            keys.type(" ")
            return False
        row.click()
        return True

    def set_visibility(self) -> None:
        label = VISIBILITY[self.options.visibility]
        self.need("visibility").click()
        self.pause()
        self.need("visibility_option", label=label).click()
        self.report.step("visibility", label=label)
        self.pause()

    def set_switches(self) -> None:
        """A switch that isn't there only matters when it has to be turned off."""
        wanted = (
            ("Comment", self.options.comments),
            ("Duet", self.options.duet),
            ("Stitch", self.options.stitch),
        )
        for label, on in wanted:
            switch = self.sel.first(self.page, "switch", label=label)
            if switch is None:
                if not on:
                    raise Unexpected(MISSING.format(thing=f"the {label} switch"), f"step: switch {label}")
                self.report.step("switch_missing", label=label)
                continue
            switch.set_checked(on, force=True)
            self.report.step("switch", label=label, on=on)
            self.pause()

    def set_disclosure(self) -> None:
        switch = self.sel.first(self.page, "disclosure")
        if not self.options.disclosure:
            if switch is not None and switch.is_checked():
                switch.set_checked(False, force=True)
            return
        if switch is None:
            raise Unexpected(MISSING.format(thing="the content disclosure switch"), "step: disclosure")
        switch.set_checked(True, force=True)
        self.pause()
        branded = self.wait_for(
            self.t.find_s, lambda: self.sel.first(self.page, "branded_content", visible=True)
        )
        if branded is None:
            raise Unexpected(MISSING.format(thing="the Branded content box"), "step: branded_content")
        branded.set_checked(True, force=True)
        self.report.step("disclosure", branded=True)
        self.pause()

    def copyright(self) -> None:
        """Runs TikTok's copyright check and waits for its answer: a match goes to the person, no answer in
        time lets the post go on, as it would for a person who didn't wait."""
        box = self.sel.first(self.page, "copyright")
        if box is None:
            self.report.step("copyright_missing")
            return

        def answer() -> str | None:
            text = norm(box.inner_text())
            if "no issues found" in text:
                return "clear"
            if "issues detected" in text:
                return "match"
            return None

        if answer() is None and "checking" not in norm(box.inner_text()):
            switch = self.sel.first(self.page, "copyright_switch", visible=True)
            if switch is None:
                self.report.step("copyright_missing")
                return
            switch.click()
            self.pause()
        found = self.wait_for(self.t.copyright_s, answer)
        self.report.step("copyright", answer=found or "none")
        if found == "match":
            raise Unexpected(COPYRIGHT_FOUND, "step: copyright")

    def set_schedule(self, at: datetime) -> str:
        """TikTok Studio's own schedule set to ``at`` in the window's time zone, the one its picker shows,
        then read back: any difference is yours, with the window closed, since a Schedule press there would
        hold the post at a time ClipForge didn't set. The time as the window shows it."""
        zone = self.window_zone()
        local = at.astimezone(zone)
        try:
            self.sel.find(self.page, "schedule_radio", self.t.find_s * 1000).check(force=True)
            self.pause()
            self.need("schedule_done")
            self.pick_day(local)
            self.pick_time(local)
            shown = (self.need("schedule_date").input_value(), self.need("schedule_time").input_value())
        except Missing as err:
            thing = THINGS.get(err.step, err.step.replace("_", " "))
            late = SCHEDULE_GONE.format(thing=thing)
            raise Unexpected(SCHEDULE_MISSING.format(thing=thing), f"step: {err.step}", late) from err
        wanted = (f"{local:%Y-%m-%d}", f"{local:%H:%M}")
        when = at_text(local, zone)
        if shown != wanted:
            sentence = SCHEDULE_MISMATCH.format(shown=shown_text(*shown, zone), wanted=when)
            details = f"step: schedule, shown {shown[0][:20]} {shown[1][:20]}"
            raise NeedsUser(sentence, details=details, screenshot=self.keep("schedule"))
        self.report.step("schedule", date=wanted[0], time=wanted[1], zone=zone.key)
        return when

    def window_zone(self) -> ZoneInfo:
        """The window's time zone, which is the Mac's and can differ from the posting time zone while you
        travel; the posting one when the window doesn't say."""
        try:
            return ZoneInfo(self.page.evaluate(ZONE))
        except (PlaywrightError, ValueError, KeyError, TypeError):
            return ZoneInfo(self.engine.settings.posting.timezone)

    def pick_day(self, at: datetime) -> None:
        """The slot's month by the calendar's next arrow, then its day with a real mouse click: the
        calendar ignores a click a script makes."""
        self.need("schedule_date").click()
        self.pause()
        self.need("calendar_week")
        month = norm(f"{at:%B} / {at.year}")  # in English, as the window is; its spaces are non-breaking
        presses = 0
        while norm(self.need("calendar_header").inner_text()) != month:
            if presses == 2:  # 10 days ahead is never more than a month on
                raise Missing("calendar_header")
            self.need("calendar_next").click()
            presses += 1
            self.pause()
        self.need("calendar_day", label=str(at.day)).click()
        self.pause()

    def pick_time(self, at: datetime) -> None:
        """The hour, then the minutes, each scrolled into view in its column first: a row out of sight
        doesn't take a click."""
        self.need("schedule_time").click()
        self.pause()
        for step, value in (("time_hours", at.hour), ("time_minutes", at.minute)):
            item = self.need(step, label=f"{value:02d}")
            item.scroll_into_view_if_needed()
            item.click()
            self.pause()

    def click_post(self) -> None:
        def enabled() -> Locator | None:
            button = self.sel.first(self.page, "post", visible=True)
            if button is None or button.is_disabled():
                return None
            off = any(button.get_attribute(a) == "true" for a in ("data-disabled", "aria-disabled"))
            return None if off else button

        button = self.wait_for(self.t.enable_s, enabled, every=self.t.enable_every_s)
        if button is None:
            raise Unexpected(NEVER_ENABLED, "step: post")
        self.report.final_check()
        self.report.step("post")
        button.click()
        self.report.processing()

    def wait_success(self) -> None:
        """ "Post now" is pressed when TikTok asks, once; then the success text or the post list counts."""
        asked = False

        def done() -> bool:
            nonlocal asked
            if self.sel.first(self.page, "failure", visible=True) is not None:
                raise Unexpected(FAILED, "step: success")
            if not asked:
                now = self.sel.first(self.page, "post_now", visible=True)
                if now is not None:
                    asked = True
                    self.report.step("post_now")
                    now.click()
                    return False
            return self.succeeded()

        if not self.wait_for(self.t.success_s, done):
            raise Unexpected(NO_SUCCESS, "step: success", late=NO_SUCCESS)

    def result(self) -> PublishResult:
        """The post's link, from the row in TikTok Studio's post list that holds its caption."""
        if self.studio is not None:
            return self.scheduled()
        self.report.step("posted")
        if found := self.find_row():
            return found
        self.report.step("no_link")
        return PublishResult(external_id=None, url=None, warnings=[NO_LINK])

    def find_row(self, tries: int = 3) -> PublishResult | None:
        needle = norm(full_caption(self.post.caption or "", self.post.hashtags))[:CAPTION_MATCH]
        taken = store.site_ids(self.engine.db(), "tiktok", besides=self.post.id)
        for _ in range(tries if needle else 0):  # an empty caption could be anyone's row
            try:
                self.page.goto(self.sel.url("content"), timeout=self.t.page_s * 1000)
                href = self.page.evaluate(FIND_LINK, {"needle": needle, "taken": taken})
            except PlaywrightError:  # the page's own move to the list cut ours short
                href = None
            found = VIDEO_ID.search(href or "")
            if found:
                self.report.step("link", external_id=found.group(1))
                return PublishResult(external_id=found.group(1), url=href)
            self.page.wait_for_timeout(self.t.settle_s * 1000)
        return None

    def succeeded(self) -> bool:
        return self.on_list() or self.sel.first(self.page, "success", visible=True) is not None

    def on_list(self) -> bool:
        return urlsplit(self.page.url).path.rstrip("/") == urlsplit(self.sel.url("content")).path.rstrip("/")

    def done_by_hand(self) -> PublishResult | None:
        """What the person finished in the window. Opening the post list by hand looks the same as being
        sent there after posting, so on the list only a row holding the caption counts, looked for once
        each time the page arrives there."""
        if self.sel.first(self.page, "success", visible=True) is not None:
            return self.result()
        if not self.on_list():
            self.looked = False
            return None
        if self.looked:
            return None
        self.looked = True
        if found := self.find_row():
            if self.studio is not None:
                return self.scheduled(found)
            self.report.step("posted")
        return found

    def look(self) -> PublishResult | None:
        """One read of the post list for a post TikTok Studio put up at its slot, nothing uploaded or
        pressed: its link when a row holds its caption. A window TikTok logged out marks the account."""
        found = self.find_row(tries=1)
        if found is None:
            try:
                self.check_login()
            except LoggedOut:
                raise self.logged_out_error() from None
        return found

    def scheduled(self, found: PublishResult | None = None) -> PublishResult:
        """A post TikTok Studio holds for its slot: not up yet, with its link when the post list showed it."""
        self.report.step("scheduled", at=self.studio.isoformat() if self.studio else None)
        return (found or PublishResult(external_id=None, url=None)).model_copy(update={"scheduled": True})


class TikTokBrowserPublisher(BrowserPublisher):
    platform = "tiktok"
    caps = CAPS["tiktok:browser"]
    run = _Upload
    READY = READY
    CHANGED = CHANGED

    def find_link(self, post: Post, report: Report) -> PublishResult | None:
        """The link of a post TikTok Studio held to its slot, from one read of its post list in the
        account's window. None when no row holds its caption."""
        selectors = Selectors.load(self.platform, base_url=self.engine.site_base)
        with self.engine.browser.open(report.account) as context:
            page = context.pages[0] if context.pages else context.new_page()
            return _Upload(self.engine, selectors, page, post, report).look()
