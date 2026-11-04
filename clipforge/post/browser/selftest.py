"""The browser route's self-test (SPEC 10.3, 17): the site's upload page opened in the account's window and
every selector a post can't do without looked for. Nothing is chosen, typed or uploaded."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from clipforge.post.browser.selectors import Selectors
from clipforge.post.models import Account

if TYPE_CHECKING:
    from clipforge.app import Engine


@dataclass
class SelfTestResult:
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    logged_in: bool = True


def self_test(engine: Engine, account: Account) -> SelfTestResult:
    """Waits up to ``find_s`` for every required selector on the upload page, or for the site's login page."""
    sel = Selectors.load(account.platform, base_url=engine.site_base)
    timing = engine.browser.timing
    with engine.browser.open(account) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(sel.url("upload"), timeout=timing.page_s * 1000)
        until = time.monotonic() + timing.find_s
        while True:
            if sel.logged_out(page):
                return SelfTestResult([], list(sel.required), logged_in=False)
            found = [step for step in sel.required if sel.first(page, step) is not None]
            if len(found) == len(sel.required) or time.monotonic() >= until:
                return SelfTestResult(found, [step for step in sel.required if step not in found])
            page.wait_for_timeout(timing.poll_s * 1000)
