"""Notifications on this Mac (SPEC 18, 21): the morning's one daily notification (14.10), on by default, and,
only once their switch is on, the moments as they happen: clips ready, a clipping channel's drafts ready, a
post that needs the person, a post that failed and a post that went up. Each is a macOS notification from the
helper inside ClipForge.app, which opens the page when clicked, or from osascript when the helper wasn't
built. Telegram alerts, when turned on, carry the same words through OpenClaw. Text from a video or a site
only ever goes in as an argument, cleaned and shortened, and only ClipForge's own pages are ever opened."""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge.post import store
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.config import Settings
    from clipforge.paths import Paths
    from clipforge.post.power import RunCmd

log = logging.getLogger(__name__)

HELPER = "ClipForge Notifier.app"
PAGE = re.compile(r"^http://127\.0\.0\.1:\d{2,5}/[A-Za-z0-9/_-]*$", re.ASCII)  # the helper checks it too
TITLE_CHARS = 80
BODY_CHARS = 200
SAY = [
    "-e",
    "on run argv",
    "-e",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "-e",
    "end run",
]
NEEDS_YOU = "Open the Queue to see what it needs."
FAILED = "Open the Queue to see why."
# a post that goes up from anything else was marked by hand; TikTok Studio puts up the one it holds
SENT_FROM = ("uploading", "processing", "scheduled_on_tiktok")


def cut(text: str, chars: int) -> str:
    """``text`` cleaned, its spaces collapsed, and at most ``chars`` characters, an ellipsis ending it when
    it was cut."""
    text = " ".join((clean_text(text) or "").split())
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "…"


class Notifier:
    """Sends notifications, and hears the engine's events to know when."""

    def __init__(self, paths: Paths, settings: Settings, *, port: int, run: RunCmd, app_dir: Path):
        self.paths = paths
        self.settings = settings
        self.port = port  # the window's, which the server sets
        self.run = run
        self.app_dir = app_dir  # where scripts/make_app.sh put ClipForge.app
        self._seen: dict[tuple[str, str], Any] = {}  # each project's and post's status, as last heard
        self._lock = threading.Lock()

    @property
    def helper(self) -> Path:
        return self.app_dir / "ClipForge.app" / "Contents" / "Helpers" / HELPER

    def send(self, title: str, body: str, path: str) -> None:
        """One notification that opens ``path`` in ClipForge's window when clicked, and the same words on
        Telegram when those alerts are on. Nothing at all while event notifications are off (SPEC 18)."""
        if self.settings.today.event_notifications:
            self._deliver(title, body, path)

    def daily(self, body: str) -> None:
        """The morning's one notification (SPEC 14.10), which opens Today; it goes whenever its own switch is
        on, whatever the switch for the others says."""
        if self.settings.today.daily_notification:
            self._deliver("ClipForge", body, "/")

    def _deliver(self, title: str, body: str, path: str) -> None:
        posting = self.settings.posting
        url = f"http://127.0.0.1:{self.port}{path}"
        if not PAGE.fullmatch(url):
            log.warning("a notification wasn't sent: %r isn't one of ClipForge's pages", path)
            return
        title, body = cut(title, TITLE_CHARS), cut(body, BODY_CHARS)
        if self.helper.is_dir():
            args = ["--title", title, "--body", body, "--open", url]
            self._start(["open", "-g", "-n", "-a", str(self.helper), "--args", *args])
        else:
            self._start(["osascript", *SAY, title, body])
        if posting.telegram_alerts and posting.telegram_target:
            to = ["--channel", "telegram", "--target", posting.telegram_target]
            self._start(["openclaw", "message", "send", *to, "--message", f"{title}: {body}"])

    def _start(self, args: list[str]) -> None:
        try:
            self.run(args)
        except OSError as err:  # not on this Mac: the notification is let go
            log.warning("%s couldn't start: %s", args[0], err)

    def hear(self, engine: Engine, topic: str, data: dict[str, Any]) -> None:
        """Notifies for an event that is one of the moments: a project newly ready, a check's drafts made,
        a post newly needing the person or failed, or a post that just went up."""
        key = data.get("id")
        if topic not in ("project", "post", "watch") or not key:
            return
        status = data.get("status")
        with self._lock:
            before = self._seen.get((topic, key))
            self._seen[(topic, key)] = status
        if status == before or not self.settings.today.event_notifications:
            return
        if topic == "project" and before is None:  # ready before this engine heard it start
            return
        if topic == "post" and status == "posted" and before not in SENT_FROM:
            return
        said = notify_for(topic, data, engine)
        if said is not None:
            self.send(*said)


def notify_for(topic: str, data: dict[str, Any], engine: Engine) -> tuple[str, str, str] | None:
    """The title, body and page of the notification an event's new status calls for, or None."""
    status = data.get("status")
    if topic == "project" and status == "ready":
        project = engine.project(data["id"])
        if project is None or project.clip_channel_id is not None:  # a clipping channel's: its drafts say
            return None
        n, title = len(engine.clips(project.id)), project.title or "your video"
        body = f"No moment in {title} made a clip." if not n else f"{n} clip{'s' * (n != 1)} from {title}."
        return "Clips are ready", body, f"/projects/{project.id}"
    if topic == "watch" and status == "drafted":
        n = data.get("drafts") or 0
        name = data.get("name") or "a clipping channel"
        return ("Drafts are ready", f"{n} draft{'s' * (n != 1)} from {name}.", "/") if n else None
    if topic != "post":
        return None
    if status == "needs_you":
        return "A post needs you", data.get("error") or NEEDS_YOU, "/queue"
    if status == "failed":
        return "A post failed", data.get("error") or FAILED, "/queue"
    if status == "posted":
        post = store.get_post(engine.db(), data["id"])
        account = post and store.get_account(engine.db(), post.account_id, deleted=True)
        if not post or not account:
            return None
        where = f"@{account.handle}" if account.handle else account.label
        return "Posted", f"{post.clip_title} is on {where}.", "/queue"
    return None
