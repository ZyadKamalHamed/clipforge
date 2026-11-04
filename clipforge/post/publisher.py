"""How ClipForge reaches a platform (SPEC 10.1): every route is a ``Publisher``, and a publish gets a
``Report`` to log its steps, keep its screenshots and state, and stop before the step that makes a post
public when posting was paused."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from clipforge.jobs.runner import JobContext
from clipforge.post import store
from clipforge.post.errors import Fatal, Paused
from clipforge.post.lifecycle import StepLog
from clipforge.post.models import Account, Capabilities, Health, Post, PublishResult
from clipforge.post.plan import NAMES

if TYPE_CHECKING:
    from clipforge.app import Engine

NO_ROUTE = "ClipForge can't post to {name} this way yet. Export this post and post it by hand."


class Publisher(Protocol):
    platform: str
    method: str
    caps: Capabilities

    def check(self, account: Account) -> Health: ...

    def publish(self, post: Post, report: Report) -> PublishResult: ...


class Report:
    """What a publisher tells ClipForge as it goes. ``video`` is the file to post, ``account`` the account
    it goes to and ``post`` the post as it was when the publish started, with ``state`` kept up to date."""

    def __init__(self, engine: Engine, ctx: JobContext, post: Post, account: Account, video: Path | None):
        self.engine = engine
        self.ctx = ctx
        self.post = post
        self.account = account
        self.video = video
        self.folder = engine.paths.post_logs / post.id
        self.log = StepLog(Path(post.log_path) if post.log_path else self.folder / "steps.jsonl")
        self.steps: list[str] = []  # this publish's steps, in order

    def step(self, name: str, **data: Any) -> None:
        self.log.write(name, **data)
        self.steps.append(name)
        self.ctx.heartbeat()

    def screenshot(self, png: bytes, name: str) -> str:
        """Keeps a screenshot as ``<nn>-<name>.png`` beside the post's step log and gives back its path."""
        self.folder.mkdir(parents=True, exist_ok=True)
        n = len(list(self.folder.glob("*.png"))) + 1
        path = self.folder / f"{n:02d}-{re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-') or 'page'}.png"
        path.write_bytes(png)
        self.step("screenshot", path=str(path))
        return str(path)

    def final_check(self) -> None:
        """Called just before the step that makes the post public: raises ``Paused`` when posting or the
        account was paused since the publish started."""
        account = store.get_account(self.engine.db(), self.account.id)
        if self.engine.settings.posting.paused or account is None or account.paused:
            raise Paused()

    def progress(self, fraction: float, message: str | None = None) -> None:
        self.ctx.progress(fraction, message)

    def processing(self) -> None:
        """The site has the video: the post moves on to ``processing``, where it can't be moved back."""
        from clipforge.post import queue  # queue imports this module

        with self.engine.lock:
            current = store.get_post(self.engine.db(), self.post.id)
            if current.status == "uploading":
                current = queue.move_post(self.engine, self.post.id, "processing")
            self.post = current

    def waiting(self, sentence: str | None) -> None:
        """Shows ``sentence`` on the post in the Queue while its window waits for the person; None takes
        it off."""
        from clipforge.post import queue  # queue imports this module

        with self.engine.lock:
            current = store.get_post(self.engine.db(), self.post.id)
            state = {k: v for k, v in current.state.items() if k != "waiting"}
            if sentence is not None:
                state["waiting"] = sentence
            self.post = queue.change_post(self.engine, self.post.id, state=state)

    def drop(self, *keys: str) -> None:
        """Forgets ``keys`` from the post's state."""
        with self.engine.lock:
            conn = self.engine.db()
            current = store.get_post(conn, self.post.id)
            state = {k: v for k, v in current.state.items() if k not in keys}
            self.post = store.update_post(conn, self.post.id, state=state)

    def save(self, **state: Any) -> None:
        """Keeps ``state`` with the post, so a publish that stops can carry on from there."""
        with self.engine.lock:
            conn = self.engine.db()
            current = store.get_post(conn, self.post.id)
            self.post = store.update_post(conn, self.post.id, state={**current.state, **state})


def is_experimental(engine: Engine, account: Account) -> bool:
    """Whether the account's route is one SPEC 10.1 marks experimental."""
    found = engine.publishers.get(f"{account.platform}:{account.method}")
    return bool(getattr(found, "experimental", False))


def publisher_for(engine: Engine, account: Account) -> Publisher:
    """The route for ``account``'s platform and method. Raises ``Fatal`` when there is none."""
    found = engine.publishers.get(f"{account.platform}:{account.method}")
    if found is None:
        raise Fatal(NO_ROUTE.format(name=NAMES[account.platform]), "no_route")
    return found
