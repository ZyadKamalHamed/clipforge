"""Where things are on a site's pages (research 02 section 3a): each step's selectors, best first, kept in
``selectors/<site>.json`` so a page TikTok changes is one file to fix. Tests load them rebased onto the
look-alike pages on 127.0.0.1."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

FOLDER = Path(__file__).parent / "selectors"
POLL_S = 0.1


class Missing(LookupError):
    """No selector for ``step`` found anything on the page."""

    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


@dataclass(frozen=True)
class Selectors:
    name: str
    urls: dict[str, str]
    steps: dict[str, list[str]]
    required: list[str]

    @classmethod
    def load(cls, name: str, *, base_url: str | None = None) -> Selectors:
        """The site's selectors, its URLs moved onto ``base_url``'s scheme and host when one is given."""
        data = json.loads((FOLDER / f"{name}.json").read_text(encoding="utf-8"))
        urls = dict(data["urls"])
        if base_url:
            base = urlsplit(base_url)
            urls = {
                k: urlunsplit(urlsplit(u)._replace(scheme=base.scheme, netloc=base.netloc))
                for k, u in urls.items()
            }
        return cls(name, urls, {k: list(v) for k, v in data["steps"].items()}, list(data["required"]))

    def url(self, name: str, **labels: str) -> str:
        return self.urls[name].format(**labels) if labels else self.urls[name]

    def logged_out(self, page: Page) -> bool:
        """The site's login page, or its login form shown at the address asked for."""
        if "/login" in urlsplit(page.url).path:
            return True
        return "login_form" in self.steps and self.first(page, "login_form") is not None

    def candidates(self, step: str, **labels: str) -> list[str]:
        found = self.steps[step]
        return [s.format(**labels) for s in found] if labels else list(found)

    def first(self, page: Page, step: str, *, visible: bool = False, **labels: str) -> Locator | None:
        """The first element any of the step's selectors finds right now, or None. No waiting."""
        for selector in self.candidates(step, **labels):
            locator = page.locator(selector + (" >> visible=true" if visible else "")).first
            if locator.count():
                return locator
        return None

    def find(
        self, page: Page, step: str, timeout_ms: float, *, visible: bool = False, **labels: str
    ) -> Locator:
        """The step's element once any of its selectors finds it, waiting up to ``timeout_ms``. Raises
        ``Missing`` when none does in time."""
        until = time.monotonic() + timeout_ms / 1000
        while True:
            found = self.first(page, step, visible=visible, **labels)
            if found is not None:
                return found
            if time.monotonic() >= until:
                raise Missing(step)
            page.wait_for_timeout(POLL_S * 1000)
