"""What a publisher raises when a post can't go on as it is (SPEC 10.6, 21). Each carries the sentence the
Queue shows; technical detail goes in ``details`` and the post's step log."""

from __future__ import annotations

from typing import Any


class PostError(Exception):
    def __init__(self, sentence: str, *, details: str | None = None):
        super().__init__(sentence)
        self.sentence = sentence
        self.details = details

    def __str__(self) -> str:
        return self.sentence


class NeedsUser(PostError):
    """Only the person can get past this: a login, a check on the site, a press of Post. ``actions`` are
    the buttons the Queue offers, ``screenshot`` the page as ClipForge last saw it."""

    def __init__(
        self,
        sentence: str,
        *,
        details: str | None = None,
        screenshot: str | None = None,
        actions: tuple[str, ...] = (),
    ):
        super().__init__(sentence, details=details)
        self.screenshot = screenshot
        self.actions = tuple(actions)


class Retryable(PostError):
    """Worth trying again in ``after_s`` seconds. ``safe`` is False when the site may already have the post,
    so a retry must check before it uploads again."""

    def __init__(
        self, sentence: str, *, after_s: float = 60.0, safe: bool = True, details: str | None = None
    ):
        super().__init__(sentence, details=details)
        self.after_s = after_s
        self.safe = safe


class Fatal(PostError):
    """Trying again won't help: the post fails with ``code``."""

    def __init__(self, sentence: str, code: str, *, details: str | None = None):
        super().__init__(sentence, details=details)
        self.code = code


class Paused(PostError):
    """Posting was paused before the step that makes a post public."""

    def __init__(self, sentence: str = "Posting is paused."):
        super().__init__(sentence)


class LifecycleError(ValueError):
    """A post was asked to move to a status it can't reach from where it is."""


class PlanChanged(Exception):
    """Confirm found the plan different from the one shown: a new blocking warning, or a time that moved.
    ``plan`` is the new one, to show again."""

    def __init__(self, plan: Any):
        super().__init__("The plan changed. Check it again before posting.")
        self.plan = plan
