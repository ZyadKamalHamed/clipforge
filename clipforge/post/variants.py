"""Each account's own version of a clip (Phase 5g, SPEC 11). A post goes up with its clip's current render
unless its account wears a style kit that render doesn't, or that render's stretch of gameplay is one
another account's post has already shown: then it waits while a version made for its account renders
(job ``clip.variant``), and goes up with that. A version never becomes the clip's current render."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from clipforge import repo
from clipforge.edits import render_key
from clipforge.jobs.pipeline import RENDER_TRIES, USER_PRIORITY, VARIANT
from clipforge.jobs.stages.clips import look_of
from clipforge.models import ClipRow, RenderRow
from clipforge.post import store
from clipforge.post.models import Account, Post
from clipforge.render.edl import EDL
from clipforge.render.styles import Style, style_key

if TYPE_CHECKING:
    from clipforge.app import Engine

VARIANT_FAILED = "This account's version of the clip didn't render: {error}"
EPS = 1e-6


class Stretch(NamedTuple):
    item_id: str
    start_s: float
    end_s: float


def variant_ref(clip_id: str, account_id: str) -> str:
    """The ref of the job that renders ``account_id``'s version of ``clip_id``."""
    return f"{clip_id}/{account_id}"


def wanted_style(engine: Engine, clip: ClipRow, account: Account) -> Style:
    """The look the account's posts of ``clip`` wear: its style kit over the clip's own."""
    return look_of(engine, clip, engine.settings, account.settings.style_kit)[2]


def gameplay_stretch(render: RenderRow | None) -> Stretch | None:
    """The stretch of library gameplay ``render`` shows, from the edit list kept beside it; None when it
    shows none or that list isn't there to read."""
    if render is None or not render.edl_path:
        return None
    try:
        edl = EDL.model_validate_json(Path(render.edl_path).read_text())
    except (OSError, ValueError):
        return None
    shown = edl.gameplay or {}
    if not isinstance(shown.get("item_id"), str):
        return None
    start = float(shown.get("start_s") or 0.0)
    return Stretch(shown["item_id"], start, start + edl.duration)


def count_shown(engine: Engine, post: Post, render: RenderRow | None) -> None:
    """Write down the stretch of gameplay ``render`` shows as ``post``'s, so another account's post of the
    clip gets a stretch of its own. Nothing when it shows none."""
    stretch = gameplay_stretch(render)
    if stretch is not None:
        repo.record_gameplay_use(
            engine.db(),
            stretch.item_id,
            stretch.start_s,
            stretch.end_s,
            clip_id=post.clip_id,
            account_id=post.account_id,
            post_id=post.id,
        )


def _current(engine: Engine, clip: ClipRow) -> RenderRow | None:
    return repo.get_render(engine.db(), clip.current_render_id) if clip.current_render_id else None


def needs_variant(engine: Engine, clip: ClipRow, account: Account) -> bool:
    """Whether the account's posts of ``clip`` need a version of their own: its style kit gives a look the
    clip's current render wasn't made in, or that render's gameplay overlaps a stretch another account's
    post has shown. False while the clip has no ready render, which renders first anyway, and for a video
    posted as it is, which every account posts unchanged (SPEC 14.2)."""
    current = _current(engine, clip)
    if current is None or current.status != "ready" or clip.mode == "as_is":
        return False
    kit = account.settings.style_kit
    if not kit.empty and style_key(wanted_style(engine, clip, account)) != current.style_key:
        return True
    stretch = gameplay_stretch(current)
    if stretch is None:
        return False
    return any(
        u.post_id is not None
        and u.account_id != account.id
        and u.start_s < stretch.end_s - EPS
        and u.end_s > stretch.start_s + EPS
        for u in repo.gameplay_uses(engine.db(), stretch.item_id)
    )


def variant_for(engine: Engine, clip: ClipRow, account: Account) -> RenderRow | None:
    """The newest ready version of ``clip`` made for the account from the clip's edits as they are now and
    in the look it wants, with its file there; None when there is none."""
    copy, edits, style, _ = look_of(engine, clip, engine.settings, account.settings.style_kit)
    made, look = render_key(copy, edits), style_key(style)
    for render in reversed(repo.list_renders(engine.db(), clip.id)):
        if (
            render.account_id == account.id
            and render.status == "ready"
            and render.edits_key == made
            and render.style_key == look
            and Path(render.path).is_file()
        ):
            return render
    return None


def post_render(engine: Engine, post: Post) -> RenderRow | None:
    """The render a post that hasn't started would send: its account's own version of the clip when it
    needs one (None until that is made), else the clip's current render."""
    conn = engine.db()
    clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
    if clip is None:
        return None
    account = store.get_account(conn, post.account_id)
    if account is None or not needs_variant(engine, clip, account):
        return _current(engine, clip)
    return variant_for(engine, clip, account)


def start_variant(engine: Engine, post: Post) -> bool:
    """Whether ``post`` waits on its account's version of its clip, which is queued now unless it already
    is."""
    conn = engine.db()
    clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
    account = store.get_account(conn, post.account_id)
    if clip is None or account is None or not needs_variant(engine, clip, account):
        return False
    if variant_for(engine, clip, account) is not None:
        return False
    queue_variant(engine, clip, account)
    return True


def queue_variant(engine: Engine, clip: ClipRow, account: Account) -> int | None:
    """Queue the account's version of ``clip``; None when one is already queued or rendering. One made
    before doesn't stop it, since the look or the edits it was made from may have changed."""
    return engine.runner.enqueue(
        VARIANT.kind,
        variant_ref(clip.id, account.id),
        {"clip_id": clip.id, "account_id": account.id},
        priority=USER_PRIORITY,
        rerun=True,
        max_attempts=RENDER_TRIES[VARIANT.kind],
    )
