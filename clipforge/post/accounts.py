"""What the Accounts page does to accounts (SPEC 11, 16): the jobs that open an account's Chrome window (its
login and its self-test), the check that records how an account is, and removing an account so nothing of it
is left behind but the history of what it posted."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge.errors import ClipForgeError
from clipforge.jobs.pipeline import ACCOUNT_KINDS, USER_PRIORITY
from clipforge.post import keychain, queue, store
from clipforge.post.browser import login
from clipforge.post.browser.session import ACCOUNT_ID
from clipforge.post.models import ACTIVE, Account, Health
from clipforge.post.publisher import publisher_for
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.jobs.runner import Job, JobContext

GONE = "That account was removed."
POSTING_NOW = "This account is posting right now. Wait for the post to finish, then remove the account."
WINDOW_OPEN = "This account's Chrome window is open. Close it, then remove the account."


def start(engine: Engine, kind: str, account_id: str) -> int:
    """The account's ``kind`` job queued ahead of the clips, or the one already queued or running."""
    job_id = engine.runner.enqueue(kind, account_id, priority=USER_PRIORITY, rerun=True)
    if job_id is not None:
        return job_id
    live = [
        j for j in engine.runner.jobs_for(account_id) if j.kind == kind and j.status in ("queued", "running")
    ]
    return live[-1].id


def stop_login(engine: Engine, account_id: str) -> None:
    """The account's login stopped, queued or waiting in its window: the window closes and the account
    stays as it was."""
    for job in engine.runner.jobs_for(account_id):
        if job.kind == "account.login" and job.status in ("queued", "running"):
            engine.runner.cancel(job.id)


def record(engine: Engine, account: Account, health: Health) -> Account:
    """The account as its check found it. A key Instagram still takes keeps its expiry warning."""
    status, note = health.status, health.sentence
    if status == "connected":
        status, note = (
            ("token_expiring", account.status_note) if account.status == "token_expiring" else (status, None)
        )
    return queue.change_account(
        engine, account.id, status=status, status_note=note, last_checked_at=engine.now().timestamp()
    )


def check(engine: Engine, account: Account) -> Health:
    """The account's route checked and the answer recorded. Raises ``Fatal`` when there is no route."""
    health = publisher_for(engine, account).check(account)
    record(engine, account, health)
    return health


def run_job(engine: Engine, ctx: JobContext) -> dict[str, Any] | None:
    """An ``account.login`` or ``account.selftest`` job."""
    if ctx.cancelled():
        return None
    if ctx.job.kind == "account.login":
        login.login_account(engine, ctx.job.ref_id, ctx)
        return None
    account = store.get_account(engine.db(), ctx.job.ref_id)
    if account is None:
        raise ClipForgeError("no_account", GONE)
    return check(engine, account).model_dump()


def finished(engine: Engine, job: Job) -> None:
    """The runner's hook for an account job: one that failed says why on the account's card."""
    if job.status != "failed":
        return
    with engine.lock:
        if store.get_account(engine.db(), job.ref_id) is not None:
            queue.change_account(engine, job.ref_id, status_note=clean_text(job.error))


def remove(engine: Engine, account_id: str) -> None:
    """The account removed: its posts still to go cancelled, its key, Chrome profile and picture deleted,
    its pair let go and its row kept as deleted, so the posts it made keep their history. Raises
    ClipForgeError while it is posting or its window is open, and KeyError when it isn't there."""
    with engine.lock:
        conn = engine.db()
        account = store.get_account(conn, account_id)
        if account is None:
            raise KeyError(account_id)
        posts = store.list_posts(conn, status=ACTIVE, account_id=account_id)
        if any(p.status in ("uploading", "processing") for p in posts):
            raise ClipForgeError("posting_now", POSTING_NOW)
        jobs = [j for j in engine.runner.jobs_for(account_id) if j.kind in ACCOUNT_KINDS]
        if any(j.status == "running" for j in jobs):
            raise ClipForgeError("window_open", WINDOW_OPEN)
        for job in jobs:
            if job.status == "queued":
                engine.runner.cancel(job.id)
        for post in posts:
            queue.move_post(engine, post.id, "cancelled")
        if account.token_ref:
            keychain.delete_token(account.token_ref)
        if ACCOUNT_ID.fullmatch(account.id):
            for folder in (engine.paths.browser / account.id, engine.paths.accounts / account.id):
                _remove_folder(folder)
        mate = account.settings.pair_id and store.get_account(conn, account.settings.pair_id)
        if mate and mate.settings.pair_id == account.id:
            settings = mate.settings.model_copy(update={"pair_id": None})
            queue.change_account(engine, mate.id, settings=settings)
        store.delete_account(conn, account.id)
        queue.account_event(engine, store.get_account(conn, account.id, deleted=True))
    queue.sync_jobs(engine)


def _remove_folder(folder: Path) -> None:
    if folder.is_symlink():  # only ever the link, never what it leads to
        folder.unlink()
    elif folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
