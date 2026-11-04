"""Manual export, the way out when a route can't post (SPEC 10.6): the video and its caption in a folder
of their own, opened in Finder, for posting by hand; and, for putting it up (SPEC 14.10 item 4), its
platform's own upload page opened in the default browser with the clip shown in Finder."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns.rules import campaign_of_post
from clipforge.errors import ClipForgeError
from clipforge.models import RenderRow
from clipforge.post import store
from clipforge.post.browser.selectors import Selectors
from clipforge.post.distribute import full_caption, recredit
from clipforge.post.models import Account, Health, Post, PublishResult
from clipforge.post.plan import CAPS, music_credit
from clipforge.post.variants import post_render, start_variant

if TYPE_CHECKING:
    from clipforge.app import Engine

NO_VIDEO = "This clip has no video yet. Render it, then press Retry."
VARIANT_RENDERING = "This account's version of the clip is rendering. Export it again once it's done."
NOT_EXPORTED = "Only an exported post can be put up by hand."


def render_for(engine: Engine, post: Post) -> RenderRow | None:
    """The render a post sends: the one it started with once it has started, else the one it would send
    now (``post_render``: its account's own version of the clip when it needs one, else the clip's
    current render)."""
    conn = engine.db()
    if post.status in ("scheduled", "rendering", "needs_you", "failed") or post.render_id is None:
        clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
        if clip is not None:
            return post_render(engine, post)
    return repo.get_render(conn, post.render_id) if post.render_id else None


def playable(render: RenderRow | None) -> Path | None:
    """``render``'s file, or None when the render failed or its file isn't there."""
    if render is None or render.status != "ready" or not Path(render.path).is_file():
        return None
    return Path(render.path)


def video_for(engine: Engine, post: Post) -> Path | None:
    """The file a post sends (``render_for``), or None when that render or its file isn't there."""
    return playable(render_for(engine, post))


def caption_for(engine: Engine, post: Post, render: RenderRow | None) -> str:
    """The post's caption to go with ``render``: when that isn't the render it was written for, its music
    credit follows the track ``render`` has."""
    if render is None or render.id == post.render_id:
        return post.caption
    was = repo.get_render(engine.db(), post.render_id) if post.render_id else None
    return recredit(post.caption, music_credit(was), music_credit(render), campaign_of_post(engine, post))


def _folder_name(account: Account | None, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "", account.handle or "") if account is not None else ""
    return name.lstrip(".") or fallback


def _account_folder(engine: Engine, post: Post) -> Path:
    account = store.get_account(engine.db(), post.account_id, deleted=True)
    return engine.paths.exports / _folder_name(account, post.account_id)


def export_folder(engine: Engine, post: Post) -> Path:
    """``exports/<handle>/<the post's day in Sydney>/<post id>/``, where the post's export goes."""
    tz = ZoneInfo(engine.settings.posting.timezone)
    day = datetime.fromtimestamp(post.scheduled_at, UTC).astimezone(tz).date().isoformat()
    return _account_folder(engine, post) / day / post.id


def _kept_folder(engine: Engine, post: Post) -> Path | None:
    """The folder the post was exported to, as ``state["export_dir"]`` keeps it under ``exports/``; None
    when it keeps none, or one that leads anywhere else."""
    kept = post.state.get("export_dir")
    if not isinstance(kept, str) or not kept:
        return None
    root = engine.paths.exports.resolve()
    folder = (root / kept).resolve()
    return folder if folder != root and folder.is_relative_to(root) else None


def exported_file(engine: Engine, post: Post) -> Path | None:
    """The post's exported ``clip.mp4`` while it is there: in the folder it was exported to, else in its own
    folder, or under any day of its account's, as a post moved to another day keeps its export."""
    kept = _kept_folder(engine, post)
    for first in ([kept / "clip.mp4"] if kept is not None else []) + [
        export_folder(engine, post) / "clip.mp4"
    ]:
        if first.is_file():
            return first
    return next(
        (f for f in sorted(_account_folder(engine, post).glob(f"*/{post.id}/clip.mp4")) if f.is_file()), None
    )


def export_post(engine: Engine, post: Post) -> Path:
    """Writes ``exports/<handle>/<the post's day in Sydney>/<post id>/`` with ``clip.mp4`` and
    ``caption.txt``, opens it in Finder and gives it back."""
    render = render_for(engine, post)
    video = playable(render)
    if video is None and start_variant(engine, post):
        raise ClipForgeError("variant_rendering", VARIANT_RENDERING)
    if video is None:
        raise ClipForgeError("no_render", NO_VIDEO)
    folder = export_folder(engine, post)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video, folder / "clip.mp4")
    caption = full_caption(caption_for(engine, post, render), post.hashtags)
    (folder / "caption.txt").write_text(caption + "\n", encoding="utf-8")
    engine.run_cmd(["open", str(folder)])
    return folder


def open_upload(engine: Engine, post_id: str) -> None:
    """An exported post put up by hand: its platform's own upload page opened in your default browser, then
    its exported clip shown in Finder while it is there. LookupError for a post that isn't there;
    ClipForgeError for one that isn't exported."""
    post = store.get_post(engine.db(), post_id)
    if post is None:
        raise LookupError(post_id)
    if post.status != "exported":
        raise ClipForgeError("wrong_status", NOT_EXPORTED)
    engine.run_cmd(["open", Selectors.load(post.platform).url("upload")])
    clip = exported_file(engine, post)
    if clip is not None:
        engine.run_cmd(["open", "-R", str(clip)])


class ManualExportPublisher:
    """Export as a route of its own: ``publish`` writes the folder and says where it is."""

    method = "manual"

    def __init__(self, engine: Engine, platform: str):
        self.engine = engine
        self.platform = platform
        self.caps = CAPS[f"{platform}:browser"]

    def check(self, account: Account) -> Health:
        return Health(status="connected", sentence="Ready to export.")

    def publish(self, post: Post, report: object) -> PublishResult:
        folder = export_post(self.engine, post)
        return PublishResult(external_id=None, url=None, warnings=[f"Exported to {folder}"])
