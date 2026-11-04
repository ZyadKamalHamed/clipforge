"""Logging a browser account in (SPEC 10.3, 10.4, 11): the site's login page opened in the account's own
window, where the person logs in themselves. ClipForge only waits for the login's cookie to appear, then
reads the handle and the avatar from the profile page: TikTok's profile address moves to the account's own,
and on Instagram the handle typed when the account was added has to open the account's own profile. It
never types into the login page and never keeps or logs a cookie's value."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from clipforge.errors import ClipForgeError
from clipforge.jobs.runner import JobContext
from clipforge.post import queue, store
from clipforge.post.browser.selectors import Missing, Selectors
from clipforge.post.models import Account
from clipforge.post.plan import NAMES

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

    from clipforge.app import Engine

LOGIN_MINUTES = 10
VISIT_TRIES = 3
INTERRUPTED = "interrupted by another navigation"
SESSION_COOKIE = "sessionid"
HANDLE = re.compile(r"/@([A-Za-z0-9_.]+)")
INSTAGRAM_HANDLE = re.compile(r"[A-Za-z0-9._]{1,30}")
TIMED_OUT = "No {name} login came through in 10 minutes. Press Log in to try again."
CLOSED = "The login window was closed before the login finished. Press Log in to try again."
STOPPED = "You stopped the login."
NO_HANDLE = "Logged in, but ClipForge couldn't read the account's handle. Press Log in to try again."
NO_TYPED_HANDLE = "Type the account's Instagram handle first, then press Log in with Chrome."
WRONG_HANDLE = "That handle isn't the account you logged in with."
NO_ACCOUNT = "That account was removed."


def login_account(engine: Engine, account_id: str, ctx: JobContext | None = None) -> Account:
    """The ``account.login`` job: waits up to 10 minutes for the person to log in, or until they stop it,
    then keeps the handle, the avatar and the profile folder with the account and marks it connected."""
    from playwright.sync_api import Error as PlaywrightError

    account = store.get_account(engine.db(), account_id)
    if account is None:
        raise ClipForgeError("no_account", NO_ACCOUNT)
    typed = (account.handle or "").lstrip("@")
    if account.platform == "instagram" and not INSTAGRAM_HANDLE.fullmatch(typed):
        raise ClipForgeError("no_handle", NO_TYPED_HANDLE)
    sel = Selectors.load(account.platform, base_url=engine.site_base)
    timing = engine.browser.timing
    try:
        with engine.browser.open(account) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(sel.url("login"), timeout=timing.page_s * 1000)
            _wait_for_session(engine, context, page, sel, account, ctx)
            if account.platform == "instagram":
                handle = _own_profile(engine, page, sel, typed)
            else:
                handle = _profile_handle(engine, page, sel)
            avatar = _avatar(engine, page, sel, account)
    except PlaywrightError as err:
        raise ClipForgeError("login_closed", CLOSED) from err
    return queue.change_account(
        engine,
        account.id,
        handle=handle,
        avatar_path=avatar,
        status="connected",
        status_note=None,
        profile_dir=str(engine.browser.profile_dir(account)),
        last_checked_at=engine.now().timestamp(),
    )


def _wait_for_session(
    engine: Engine,
    context: BrowserContext,
    page: Page,
    sel: Selectors,
    account: Account,
    ctx: JobContext | None,
) -> None:
    timing = engine.browser.timing
    until = time.monotonic() + LOGIN_MINUTES * timing.minute_s
    while not any(c["name"] == SESSION_COOKIE and c["value"] for c in context.cookies(sel.url("home"))):
        if ctx is not None and ctx.cancelled():
            raise ClipForgeError("login_stopped", STOPPED)
        if time.monotonic() >= until:
            raise ClipForgeError("login_timeout", TIMED_OUT.format(name=NAMES[account.platform]))
        engine.browser.front(page)
        if ctx is not None:
            ctx.heartbeat()
        page.wait_for_timeout(timing.poll_s * 1000)


def _visit(engine: Engine, page: Page, url: str) -> None:
    """Opens ``url`` once the login has come through. The site goes home by itself right after a login, and
    when that crosses this visit Chrome stops the visit, so the visit waits for the site's page and is tried
    again, up to 3 times."""
    from playwright.sync_api import Error as PlaywrightError

    timing = engine.browser.timing
    for tries_left in reversed(range(VISIT_TRIES)):
        try:
            page.goto(url, timeout=timing.page_s * 1000)
            return
        except PlaywrightError as err:
            if not tries_left or INTERRUPTED not in str(err):
                raise
            page.wait_for_load_state(timeout=timing.page_s * 1000)


def _profile_handle(engine: Engine, page: Page, sel: Selectors) -> str:
    """TikTok's profile address moves to the logged-in account's own, whose path holds its handle."""
    timing = engine.browser.timing
    _visit(engine, page, sel.url("profile"))
    page.wait_for_url(re.compile(r".*/@"), timeout=timing.page_s * 1000)
    found = HANDLE.search(page.url)
    if found is None:
        raise ClipForgeError("no_handle", NO_HANDLE)
    return found.group(1)


def _own_profile(engine: Engine, page: Page, sel: Selectors, handle: str) -> str:
    """The typed handle's profile, which has to be the logged-in account's own: only that one has its
    edit-profile link."""
    timing = engine.browser.timing
    _visit(engine, page, sel.url("profile", handle=handle))
    try:
        sel.find(page, "edit_profile", timing.find_s * 1000, visible=True)
    except Missing:
        raise ClipForgeError("wrong_handle", WRONG_HANDLE) from None
    return handle


def _avatar(engine: Engine, page: Page, sel: Selectors, account: Account) -> str | None:
    """The profile picture as an element screenshot at ``accounts/<id>/avatar.png``, or None."""
    found = sel.first(page, "avatar", visible=True)
    if found is None:
        return None
    path = engine.paths.accounts / account.id / "avatar.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    found.screenshot(path=str(path))
    return str(path)
