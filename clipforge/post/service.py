"""What the Post dialog and the Queue do to posts (SPEC 10.6, 10.7, 14.5): plan and confirm a batch, then
cancel, move, edit, retry, mark as posted or export a post, and pause and resume posting or one account.
Each change ends with the jobs matched to the posts again."""

from __future__ import annotations

import random
import sqlite3
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge import repo
from clipforge.campaigns.rules import WORD_RULES, broken_rule, campaign_of_post
from clipforge.db import transaction
from clipforge.errors import ClipForgeError
from clipforge.jobs import pipeline
from clipforge.post import lifecycle, queue, store
from clipforge.post.distribute import clean_caption, clean_hashtags, full_caption
from clipforge.post.errors import LifecycleError, PlanChanged
from clipforge.post.export import caption_for, export_post, render_for
from clipforge.post.instagram_api import PAUSE_NOTES
from clipforge.post.models import Account, Plan, PlannedPost, PlanRequest, Post
from clipforge.post.plan import approvable, at_text, build_plan, copy_warnings, slot_problem, video_key
from clipforge.post.slots import check_slot, next_free, rules_for, smart_slot
from clipforge.post.variants import count_shown
from clipforge.server.views import hook_title

if TYPE_CHECKING:
    from clipforge.app import Engine

SHOWN_MAX = 200  # plans kept for Confirm to check against, the oldest let go first
MOVED_S = 1.0  # a smart or custom time further than this from the one shown means the plan changed
MOVABLE = ("scheduled", "needs_you", "failed")  # where a post can be moved, edited or exported from
EDITABLE = ("scheduled", "draft")
# What a draft takes from the plan and its clip as it is approved; the rest it keeps
APPROVED = {
    *("batch_id", "clip_title", "render_id", "method", "caption", "hashtags", "options", "scheduled_at"),
    *("video_key", "start_s", "end_s"),
}
RETRYABLE = ("needs_you", "failed")
# A post TikTok Studio was given to put up is never uploaded again, so it can't be moved, retried or exported
ON_TIKTOK = "This post is already on TikTok Studio, so it can't be moved, tried again or exported."
# What a post keeps of its campaign in its state: the hand rules ticked, its unlinking, and the campaign
# it was planned for
RULE_STATE = ("rule_ticks", "off_campaign", "campaign_id")
AGAIN = ("repeat", "duplicate")  # the warnings that a moment is going up twice


def _changed(shown: Plan | None, fresh: Plan, timing: str) -> bool:
    """Whether Confirm found a plan other than the one shown: blocked now, other posts, a moment that went
    to an account since (from another window, say), or a smart or custom time that moved. Post now's times
    are taken afresh."""
    if shown is None or fresh.blocked:
        return True
    said = {(w.key, w.code) for w in shown.warnings}
    if any(w.code in AGAIN and (w.key, w.code) not in said for w in fresh.warnings):
        return True
    then = {p.key: p.scheduled_at for p in shown.posts}
    now = {p.key: p.scheduled_at for p in fresh.posts}
    if then.keys() != now.keys():
        return True
    return timing != "now" and any(abs(now[k] - then[k]) > MOVED_S for k in now)


def post_from_plan(
    conn: sqlite3.Connection, batch_id: str, planned: PlannedPost, *, status: str = "scheduled"
) -> Post:
    """The post a planned post becomes in ``batch_id``: scheduled, as Confirm makes it, or a draft."""
    clip = repo.get_clip(conn, planned.clip_id)
    account = store.get_account(conn, planned.account_id)
    project = repo.get_project(conn, clip.project_id)
    now = time.time()
    return Post(
        id=repo.new_id("t"),
        batch_id=batch_id,
        clip_id=clip.id,
        clip_title=hook_title(clip) or "Untitled clip",
        render_id=clip.current_render_id,
        account_id=account.id,
        platform=account.platform,
        method=account.method,
        caption=planned.caption,
        hashtags=planned.hashtags,
        options=planned.options,
        scheduled_at=planned.scheduled_at,
        status=status,
        state=_rule_state(planned),
        video_key=video_key(project),
        start_s=clip.start_s,
        end_s=clip.end_s,
        created_at=now,
        updated_at=now,
    )


def _at(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def _rule_state(planned: PlannedPost) -> dict[str, Any]:
    """The hand rules ticked, the unlinking from its campaign and the campaign it is for, as the post keeps
    them."""
    return {k: v for k in RULE_STATE if (v := getattr(planned, k))}


def _approve(conn: sqlite3.Connection, batch_id: str, plan: Plan, draft_ids: list[str]) -> None:
    """Each planned post's draft moved into ``batch_id`` and scheduled, with the plan's time and words and
    its clip as it is now. Raises PlanChanged when one isn't a draft any more."""
    held = approvable(conn, draft_ids)
    for planned in plan.posts:
        draft = held.get(planned.key)
        if draft is None:
            raise PlanChanged(plan)
        fresh = post_from_plan(conn, batch_id, planned)
        kept = {k: v for k, v in draft.state.items() if k not in RULE_STATE}
        lifecycle.move(
            conn,
            draft.id,
            "scheduled",
            note=None,
            state=kept | fresh.state,
            **fresh.model_dump(include=APPROVED),
        )


class Posting:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._shown: OrderedDict[str, Plan] = OrderedDict()

    def _remember(self, plan: Plan) -> None:
        self._shown[plan.plan_id] = plan
        self._shown.move_to_end(plan.plan_id)
        while len(self._shown) > SHOWN_MAX:
            self._shown.popitem(last=False)

    def _get(self, post_id: str) -> Post:
        post = store.get_post(self.engine.db(), post_id)
        if post is None:
            raise LifecycleError(f"no post {post_id}")
        return post

    def _not_on_tiktok(self, post: Post) -> None:
        if post.state.get("on_tiktok"):
            raise LifecycleError(ON_TIKTOK)

    def _done(self, post: Post) -> Post:
        queue.sync_jobs(self.engine)
        return post

    # The Post dialog

    def plan(self, req: PlanRequest) -> Plan:
        """The plan to show for ``req``, kept so Confirm can tell when it changed. Raises LookupError with a
        sentence when a clip or an account isn't there any more."""
        plan = build_plan(self.engine, req, plan_id=repo.new_id("plan"), now=self.engine.now())
        with self.engine.lock:
            self._remember(plan)
        return plan

    def confirm(self, req: PlanRequest, plan_id: str) -> tuple[str, list[Post]]:
        """Writes the batch and its posts, scheduled, as the plan shown says, renders the clips that need it
        and queues each post's job; with draft ids, those drafts become the batch's posts. The same batch
        again for a ``plan_id`` already confirmed. Raises PlanChanged with the new plan, kept to confirm
        next, when it isn't the one shown, and LookupError with a sentence when none of the drafts is
        left."""
        engine = self.engine
        with engine.lock:
            conn = engine.db()
            done = store.batch_for_plan(conn, plan_id)
            if done is not None:
                return done, store.list_posts(conn, batch_id=done)
            fresh = build_plan(engine, req, plan_id=plan_id, now=engine.now())
            shown = self._shown.get(plan_id)
            self._remember(fresh)
            if _changed(shown, fresh, req.timing):
                raise PlanChanged(fresh)
            try:
                with transaction(conn):
                    bid = store.add_batch(
                        conn, plan_id, req.mode, req.timing, {"every": req.acknowledged_every}
                    )
                    if req.draft_ids:
                        _approve(conn, bid, fresh, req.draft_ids)
                    else:
                        store.add_posts(conn, [post_from_plan(conn, bid, p) for p in fresh.posts])
                    now = engine.now()
                    for p in store.list_posts(conn, batch_id=bid):
                        state = queue.studio_state(engine, p, _at(p.scheduled_at), now)
                        if state != p.state:
                            store.update_post(conn, p.id, state=state)
            except sqlite3.IntegrityError:  # confirmed from another window first
                done = store.batch_for_plan(conn, plan_id)
                if done is None:
                    raise
                return done, store.list_posts(conn, batch_id=done)
            for clip_id in fresh.renders:
                pipeline.queue_render(engine.runner, conn, clip_id)
                engine.publish_clip(clip_id)
            posts = store.list_posts(conn, batch_id=bid)
            for p in posts:
                queue.post_event(engine, p)
            queue.sync_jobs(engine)
            if req.draft_ids:
                from clipforge.watch import drafts  # it imports this module

                drafts.publish_count(engine)
        return bid, posts

    # The Queue

    def cancel(self, post_id: str) -> Post:
        with self.engine.lock:
            post = queue.move_post(self.engine, post_id, "cancelled")
        return self._done(post)

    def reschedule(self, post_id: str, at: float) -> Post:
        """The post scheduled again at ``at``, or now when that has passed."""
        with self.engine.lock:
            post = self._get(post_id)
            if post.status not in MOVABLE:
                raise LifecycleError(f"a {post.status} post can't be moved")
            self._not_on_tiktok(post)
            now = self.engine.now()
            at = max(at, now.timestamp())
            if at > now.timestamp():
                self._keeps_rules(post, _at(at), now)
            post = queue.move_post(
                self.engine,
                post_id,
                "scheduled",
                scheduled_at=at,
                state=queue.studio_state(self.engine, post, _at(at), now),
                note=None,
                error=None,
                error_code=None,
                details=None,
            )
        return self._done(post)

    def _keeps_rules(self, post: Post, at: datetime, now: datetime) -> None:
        """Raises ClipForgeError with the sentence and the next free time when ``at`` breaks the account's
        windows, daily cap or spacing, or the platform's gap, as a time picked in the Post dialog would."""
        account = store.get_account(self.engine.db(), post.account_id)
        if account is None:
            return
        mine, others = queue.taken(self.engine, post, now)
        rules = rules_for(self.engine.settings.posting, account)
        broken = check_slot(at, rules, mine=mine, platform=others)
        if broken:
            free = next_free(rules, after=at, mine=mine, platform=others)
            then = f"The next free time is {at_text(free, rules.tz)}." if free else "Pick another time."
            raise ClipForgeError(broken[0], f"{slot_problem(broken[0], account, at, rules)} {then}")

    def edit(
        self,
        post_id: str,
        *,
        caption: str | None = None,
        hashtags: list[str] | None = None,
        rule_ticks: list[str] | None = None,
        off_campaign: bool | None = None,
        drop_sound: bool = False,
    ) -> Post:
        """The caption or hashtags of a scheduled post or a draft changed, cleaned as the plan cleans them,
        a draft's hand rules ticked or its unlinking from its campaign (an empty list or False takes them
        off), and with ``drop_sound`` the sound picked for it taken off. Raises ClipForgeError with the
        sentence when the platform wouldn't take the words."""
        engine = self.engine
        with engine.lock:
            post = self._get(post_id)
            if post.status not in EDITABLE:
                raise LifecycleError(f"a {post.status} post can't be edited")
            marks: dict[str, Any] = {}
            if rule_ticks is not None:
                marks["rule_ticks"] = list(dict.fromkeys(rule_ticks))
            if off_campaign is not None:
                marks["off_campaign"] = off_campaign
            if marks and post.status != "draft":
                raise LifecycleError("only a draft's campaign rules can be ticked")
            account = store.get_account(engine.db(), post.account_id, deleted=True)
            body = post.caption if caption is None else clean_caption(caption)
            tags = post.hashtags if hashtags is None else clean_hashtags(hashtags)
            refused: list[tuple[str, str]] = []

            def warn(
                code: str, sentence: str, *, blocking: bool = False, move_to: float | None = None
            ) -> None:
                if blocking:
                    refused.append((code, sentence))

            copy_warnings(account, full_caption(body, tags), body, tags, warn)
            if refused:
                raise ClipForgeError(*refused[0])
            # A draft's checklist holds it back itself; a scheduled post keeps the words its campaign needs
            campaign = campaign_of_post(engine, post) if post.status != "draft" else None
            if campaign is not None:
                broken = broken_rule(
                    engine, post, campaign, kinds=WORD_RULES, body=body, hashtags=tags, render=None
                )
                if broken is not None:
                    raise ClipForgeError("rule_broken", broken)
            state = post.state
            options = post.options
            if drop_sound and options.sound is not None:
                options = options.model_copy(update={"sound": None})
            publisher = engine.publishers.get(f"{post.platform}:{post.method}")
            changed = (body, tags, options) != (post.caption, post.hashtags, post.options)
            if changed and hasattr(publisher, "edit_state"):
                state = publisher.edit_state(
                    post
                )  # what the route keeps of a post whose words or sound changed
            state = {k: v for k, v in (state | marks).items() if v or k not in marks}
            return queue.change_post(
                engine, post_id, caption=body, hashtags=tags, options=options, state=state
            )

    def retry(self, post_id: str, *, drop_sound: bool = False) -> Post:
        """A post with the person, or failed, scheduled again at the next free slot from now (now when there
        is none), with what went wrong cleared. With ``drop_sound`` it goes without the sound picked for it,
        as Post without the sound does for one the platform turned down: the route then keeps what a post
        whose sound changed keeps, so one Instagram may have up already is refused with its sentence (a
        TikTok post stays assisted, the safe way)."""
        engine = self.engine
        with engine.lock:
            post = self._get(post_id)
            if post.status not in RETRYABLE:
                raise LifecycleError(f"a {post.status} post can't be tried again")
            self._not_on_tiktok(post)
            publisher = engine.publishers.get(f"{post.platform}:{post.method}")
            options = post.options
            if drop_sound and options.sound is not None:
                options = options.model_copy(update={"sound": None})
                if hasattr(publisher, "edit_state"):  # its container has the sound in it, so it is made again
                    post = post.model_copy(update={"state": publisher.edit_state(post)})
            now = engine.now()
            account = store.get_account(engine.db(), post.account_id)
            at = None
            if account is not None:
                mine, others = queue.taken(engine, post, now)
                rng = random.Random(f"{post.id}:{post.attempts}")
                rules = rules_for(engine.settings.posting, account)
                at = smart_slot(rules, after=now, mine=mine, platform=others, rng=rng)
            # the variant_job it waited on is forgotten, so its account's version of the clip renders again
            forgotten = ("actions", "screenshot", "variant_job", "waiting")
            state = {k: v for k, v in post.state.items() if k not in forgotten} | {"retries": 0}
            if hasattr(publisher, "retry_state"):  # what the route keeps of a post it tries again
                state = publisher.retry_state(post, state)
            state = queue.studio_state(engine, post, at or now, now, state)
            resume = (
                account is not None
                and hasattr(publisher, "resumes_on_retry")
                and publisher.resumes_on_retry(post, account)
            )
            post = queue.move_post(
                engine,
                post_id,
                "scheduled",
                scheduled_at=(at or now).timestamp(),
                state=state,
                options=options,
                note=None,
                error=None,
                error_code=None,
                details=None,
            )
        if resume:
            self.pause_account(post.account_id, False)
        return self._done(post)

    def publish_anyway(self, post_id: str) -> Post:
        """A post Instagram held for material someone else owns, sent now all the same."""
        engine = self.engine
        with engine.lock:
            post = self._get(post_id)
            if post.status != "needs_you" or "publish_anyway" not in post.state.get("actions", []):
                raise LifecycleError("only a post held for someone else's material can be published anyway")
            state = {k: v for k, v in post.state.items() if k not in ("actions", "screenshot")}
            post = queue.move_post(
                engine,
                post_id,
                "scheduled",
                scheduled_at=engine.now().timestamp(),
                state=state | {"copyright_ok": True},
                note=None,
                error=None,
                error_code=None,
                details=None,
            )
        return self._done(post)

    def mark_posted(self, post_id: str, url: str | None = None) -> Post:
        """A post the person finished on the site themselves, or exported and posted by hand."""
        engine = self.engine
        with engine.lock:
            if self._get(post_id).status not in ("needs_you", "exported"):
                raise LifecycleError(
                    "only a post that needs you, or one you exported, can be marked as posted"
                )
            post = queue.move_post(
                engine,
                post_id,
                "posted",
                url=url,
                posted_at=engine.now().timestamp(),
                error=None,
                error_code=None,
                details=None,
            )
        return self._done(post)

    def export(self, post_id: str) -> Path:
        """The post's video and caption in a folder of their own, opened in Finder, and the post exported,
        keeping where the folder is under ``exports/``."""
        with self.engine.lock:
            post = self._get(post_id)
            if post.status not in MOVABLE:
                raise LifecycleError(f"a {post.status} post can't be exported")
            self._not_on_tiktok(post)
            folder = export_post(self.engine, post)
            kept = folder.relative_to(self.engine.paths.exports).as_posix()
            render, sent = render_for(self.engine, post), {}
            count_shown(self.engine, post, render)  # it goes up by hand, so its gameplay counts as shown
            if render is not None and render.id != post.render_id:
                sent = {"render_id": render.id, "caption": caption_for(self.engine, post, render)}
            post = queue.move_post(
                self.engine, post_id, "exported", state=post.state | {"export_dir": kept}, **sent
            )
        self._done(post)
        return folder

    # Pausing

    def _set_paused(self, paused: bool) -> None:
        settings = self.engine.settings
        posting = settings.posting.model_copy(update={"paused": paused})
        self.engine.update_settings(settings.model_copy(update={"posting": posting}))

    def pause(self) -> None:
        """Nothing new starts posting until ``resume``; a post under way stops before it goes public."""
        with self.engine.lock:
            self._set_paused(True)
        queue.sync_jobs(self.engine)

    def resume(self) -> None:
        """Posting again: posts that missed their window while paused move to their next free slot."""
        with self.engine.lock:
            self._set_paused(False)
            self._catch_up()
        queue.sync_jobs(self.engine)

    def pause_account(self, account_id: str, paused: bool) -> Account:
        engine = self.engine
        with engine.lock:
            fields: dict[str, object] = {"paused": paused}
            before = store.get_account(engine.db(), account_id)
            if before is not None and before.status_note in PAUSE_NOTES:  # the person has decided now
                fields["status_note"] = None
            account = store.update_account(engine.db(), account_id, **fields)
            queue.account_event(engine, account)
            if not paused and not engine.settings.posting.paused:
                self._catch_up(account_id)
        queue.sync_jobs(engine)
        return account

    def _catch_up(self, account_id: str | None = None) -> None:
        """Each scheduled post (of ``account_id``, when given) whose window closed while it was held moves to
        its next free slot with a note, or to the person when there is none."""
        engine, now = self.engine, self.engine.now()
        conn = engine.db()
        for post in store.list_posts(conn, status=("scheduled",), account_id=account_id):
            account = store.get_account(conn, post.account_id)
            if account is None or account.paused or not queue.missed(engine, post, account, now):
                continue
            if queue.reslot(engine, post, account, now, queue.PAUSED_AT) is None:
                queue.move_post(engine, post.id, "needs_you", error=queue.NO_SLOT)
