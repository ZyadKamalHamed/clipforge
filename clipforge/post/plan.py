"""The plan Confirm and post works from (SPEC 10.6, 10.7 and 14.5): which account gets which clip, when,
with what text, and what Zyad should know or fix first. Building it only reads: it makes no rows, jobs or
files and never opens the Keychain, so the Post dialog can ask for it again as often as it likes."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.brain.wording import units
from clipforge.campaigns.ending import end_reason
from clipforge.campaigns.rules import campaign_of, checks_for, read_edl, rule_sentence
from clipforge.models import ClipRow, Project, RenderRow
from clipforge.post import store
from clipforge.post.distribute import (
    HASHTAG,
    MAX_HASHTAGS,
    clip_length,
    compose,
    duplicate_of,
    every,
    full_caption,
    repeat_of,
    spread,
)
from clipforge.post.models import (
    ACTIVE,
    Account,
    Capabilities,
    Plan,
    PlannedPost,
    PlanRequest,
    PlanWarning,
    Post,
    PostEdit,
    PostOptions,
)
from clipforge.post.slots import Rules, check_slot, next_free, now_slots, rules_for, smart_slot, sydney_day
from clipforge.post.variants import needs_variant
from clipforge.render.qa import QAResult
from clipforge.trends.instagram import ONLY_FACEBOOK

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign, RuleCheck

EVERY_ACK = (
    "Posting the same clip to several accounts on one platform is what TikTok's spam rule describes"
    " and can get them restricted together"
)
CAPS: dict[str, Capabilities] = {
    "tiktok:browser": Capabilities(
        local_file=True,
        needs_public_url=False,
        caption=True,
        schedules_server_side=False,
        needs_human_finish=False,
        max_duration_s=600,
        max_size_mb=4096,
        daily_cap=10,
    ),
    "instagram:api": Capabilities(
        local_file=True,
        needs_public_url=False,
        caption=True,
        schedules_server_side=False,
        needs_human_finish=False,
        max_duration_s=900,
        max_size_mb=300,
        daily_cap=50,
    ),
    "instagram:browser": Capabilities(
        local_file=True,
        needs_public_url=False,
        caption=True,
        schedules_server_side=False,
        needs_human_finish=False,
        max_duration_s=900,
        max_size_mb=300,
        daily_cap=1,  # experimental: one a day, the default cap (SPEC 5, item 8), as a ceiling
    ),
}
NAMES = {"tiktok": "TikTok", "instagram": "Instagram"}
CAPTION_UNITS = {"tiktok": 4000, "instagram": 2200}  # counted as the apps count, in UTF-16 units
MIN_S = {"instagram": 3.0}
TAKEN = (*ACTIVE, "posted")  # the posts a cap, spacing or gap counts
SHOWN = (*TAKEN, "needs_you")  # the posts the already-posted guard looks at
LOOK_BACK = timedelta(days=2)  # far enough back for today's cap and spacing in any timezone
LOOK_AHEAD = timedelta(days=15)
PAST = timedelta(minutes=1)  # a custom time this far gone has passed

NO_CLIP = "One of the clips isn't there any more. Close this and select the clips again."
NO_ACCOUNT = "One of the accounts isn't there any more. Close this and choose the accounts again."
POSTING_PAUSED = "Posting is paused, so nothing goes out until you turn off Pause posting."
RENDERS_FIRST = "This clip isn't rendered yet, so ClipForge renders it before it posts."
RENDERS_VARIANT = "Renders this account's version first."  # its style kit or a stretch of gameplay
NOT_CLEARED = "This video's rights are Not cleared, so posting it may draw a copyright claim."
DRAFT_GONE = "This draft was already approved or removed."
REPEAT_QUEUED = "This moment is already in the Queue for {who}. Change or cancel that post there instead."
REPEAT_POSTED = "This moment already went up on {who}. Posting it there again can look like repeated content."
SOUND_FACEBOOK_ONLY = ONLY_FACEBOOK
SOUND_NOT_INSTAGRAM = (
    "A TikTok sound can't go on Instagram. Pick one of Instagram's trending sounds, or none."
)
SOUND_NOT_TIKTOK = "An Instagram sound can't go on TikTok. Pick one from your TikTok shortlist, or none."
SOUND_BY_HAND = "You add this sound yourself in TikTok's window: {title}."
TRIAL_API_ONLY = "Trial reels go up only through Meta's API, so this account can't post one."


def caps_for(account: Account) -> Capabilities:
    return CAPS.get(f"{account.platform}:{account.method}") or CAPS[f"{account.platform}:browser"]


def posts_today(engine: Engine, account: Account) -> dict[str, int]:
    """The account's posts today in its time zone, against its own cap and the platform's ceiling."""
    rules = rules_for(engine.settings.posting, account)
    midnight = engine.now().astimezone(rules.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = midnight + timedelta(days=1)  # wall-clock sums, so a 23 or 25 hour day still ends at midnight
    today = store.posts_between(engine.db(), account.id, midnight.timestamp(), tomorrow.timestamp())
    return {"posts": len(today), "cap": rules.cap, "ceiling": caps_for(account).daily_cap}


def video_key(project: Project) -> str:
    """What the already-posted guard knows a video by, which outlives the clip and the project."""
    return project.video_id or f"file:{project.id}"


def music_credit(render: RenderRow | None) -> str | None:
    """The credit line the render's music track asks for, or None when it has no track or the track's
    licence asks for none."""
    made = read_edl(render)
    return made.music["credit"] if made is not None and made.music else None


def who(account: Account) -> str:
    return f"@{account.handle}" if account.handle else (account.label or "This account")


def at_text(t: datetime, tz: ZoneInfo) -> str:
    """``Tue 3 Nov, 7:00 pm`` in Sydney time."""
    local = t.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{_day(t, tz)}, {hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def _day(t: datetime, tz: ZoneInfo) -> str:
    local = t.astimezone(tz)
    return f"{local:%a} {local.day} {local:%b}"


def _length(seconds: float) -> str:
    """``45 seconds`` or ``10 minutes 1 second``."""
    if seconds < 60:
        return f"{round(seconds, 1):g} seconds"
    minutes, rest = divmod(round(seconds), 60)
    text = f"{minutes} minute{'s' * (minutes != 1)}"
    return f"{text} {rest} second{'s' * (rest != 1)}" if rest else text


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def build_plan(
    engine: Engine,
    req: PlanRequest,
    *,
    plan_id: str,
    now: datetime,
    pairs: list[tuple[str, str]] | None = None,
    hold_drafts: bool = False,
) -> Plan:
    """The plan for ``req``, or for exactly the (clip, account) ``pairs`` given, which then stand in for
    the mode's own choice. With draft ids, the drafts that can still be approved are the pairs, each with
    its own words under the request's edits and its own slot while that is still ahead and free. With
    ``hold_drafts``, the drafts still ahead count as taken too, so one clipping channel's new drafts keep
    clear of another's. The jitter comes from ``plan_id``, so the same id gives the same slots. Raises
    LookupError with a sentence when a clip or an account isn't there any more, or none of the drafts is."""
    conn = engine.db()
    posting = engine.settings.posting
    now = now.astimezone(UTC)
    held = approvable(conn, req.draft_ids)
    if req.draft_ids and not held:
        raise LookupError(DRAFT_GONE)
    clip_ids = [d.clip_id for d in held.values()] if held else req.clip_ids
    account_ids = [d.account_id for d in held.values()] if held else req.account_ids
    clips = [_get(repo.get_clip(conn, cid), NO_CLIP) for cid in dict.fromkeys(clip_ids)]
    accounts = [_get(store.get_account(conn, aid), NO_ACCOUNT) for aid in dict.fromkeys(account_ids)]
    projects = {c.project_id: repo.get_project(conn, c.project_id) for c in clips}
    campaigns = {pid: campaign_of(engine, project) for pid, project in projects.items()}
    linked = {pid for pid, campaign in campaigns.items() if campaign is not None}
    renders = {
        c.id: repo.get_render(conn, c.current_render_id) if c.current_render_id else None for c in clips
    }
    by_clip, by_account = {c.id: c for c in clips}, {a.id: a for a in accounts}
    if held:
        pairs = [(d.clip_id, d.account_id) for d in held.values()]
    elif pairs is None:
        pairs = every(clips, accounts) if req.mode == "every" else spread(clips, accounts, linked=linked)

    warnings: list[PlanWarning] = []
    if posting.paused:
        warnings.append(PlanWarning(code="paused", sentence=POSTING_PAUSED))
    if req.mode == "every" and max(Counter(a.platform for a in accounts).values(), default=0) > 1:
        warnings.append(
            PlanWarning(code="every_ack", sentence=f"{EVERY_ACK}.", blocking=not req.acknowledged_every)
        )
    if pairs and any(p is not None and p.rights_basis == "not_cleared" for p in projects.values()):
        warnings.append(
            PlanWarning(code="rights_not_cleared", sentence=NOT_CLEARED)
        )  # once: it's the video's

    earlier = store.list_posts(conn, status=SHOWN)
    names = {a.id: a for a in store.list_accounts(conn)}
    mine: dict[str, list[datetime]] = defaultdict(list)  # each account's times, queued and planned
    others: dict[str, list[tuple[str, datetime]]] = defaultdict(list)  # each platform's (account, time)
    lo, hi = (now - LOOK_BACK).timestamp(), (now + LOOK_AHEAD).timestamp()
    taken = [p for p in earlier if p.status in TAKEN]
    if hold_drafts:
        taken += [p for p in store.list_posts(conn, status=["draft"]) if p.scheduled_at >= now.timestamp()]
    for p in taken:
        if lo <= p.scheduled_at < hi:
            mine[p.account_id].append(_utc(p.scheduled_at))
            others[p.platform].append((p.account_id, _utc(p.scheduled_at)))

    rng = random.Random(plan_id)
    firsts = _now_times(pairs, by_account, now, rng) if req.timing == "now" else {}
    posts: list[PlannedPost] = []
    for index, (cid, aid) in enumerate(pairs):
        clip, account = by_clip[cid], by_account[aid]
        project, render = projects[clip.project_id], renders[cid]
        key = f"{cid}:{aid}"
        rules = rules_for(posting, account)
        own = mine[aid]
        platform = [t for a, t in others[account.platform] if a != aid]
        found: list[PlanWarning] = []
        warn = partial(_add, found, key)
        draft = held.get(key)
        edit = _with_draft(draft, req.edits.get(key))
        off_campaign = bool(edit and edit.off_campaign)
        campaign = None if off_campaign else campaigns[clip.project_id]
        ticks = (edit.rule_ticks if edit else None) or []
        music = music_credit(render)  # the music credit is the track the render has
        body, tags = compose(
            clip,
            account,
            project,
            edit,
            credit_line=engine.settings.credit_line,
            campaign=campaign,
            music_credit=music,
        )
        options = _options(
            account,
            req.platform_options.get(account.platform),
            edit,
            draft and draft.options,
            linked=campaign is not None,
        )
        caption = full_caption(body, tags)
        _account_warnings(account, warn)
        _sound_warnings(account, options, warn)
        if options.trial_reel and (account.platform, account.method) != ("instagram", "api"):
            warn("trial_route", TRIAL_API_ONLY, blocking=True)
        copy_warnings(account, caption, body, tags, warn)
        _clip_warnings(clip, account, render, warn)
        if render is not None and needs_variant(engine, clip, account):
            warn("renders_variant", RENDERS_VARIANT)
        checked = []
        if campaign is not None and project is not None:
            checked = checks_for(
                campaign,
                project=project,
                clip=clip,
                render=render,
                account=account,
                body=body,
                hashtags=tags,
                options=options,
                ticks=ticks,
            )
            _campaign_warnings(campaign, checked, warn)
            ended = end_reason(campaign, sydney_day(now, rules.tz))
            if ended is not None:
                warn(
                    "campaign_ended", f"{ended} This {'post' if draft is None else 'draft'} may not be paid."
                )

        at = _custom_time(req, key, index) if req.timing == "custom" else None
        if at is not None:
            broken = ["past"] if at < now - PAST else check_slot(at, rules, mine=own, platform=platform)
            if broken:
                move = next_free(rules, after=max(at, now), mine=own, platform=platform)
                warn(
                    broken[0],
                    _slot_sentence(broken[0], account, at, rules, move is not None),
                    blocking=True,
                    move_to=move.timestamp() if move else None,
                )
        elif aid in firsts:
            at = firsts.pop(aid)
            if {"cap", "spacing"} & set(check_slot(at, rules, mine=own, platform=[])):
                at = smart_slot(rules, after=now, mine=own, platform=platform, rng=rng)
        elif draft is not None:
            at = _draft_slot(draft, account, rules, now, own, platform, warn)
        else:
            at = smart_slot(rules, after=now, mine=own, platform=platform, rng=rng)
        if at is None:
            warn(
                "no_slot",
                f"{who(account)} has no free slot in the next 14 days. Post fewer clips to it or raise its"
                " daily cap in Settings.",
                blocking=True,
            )
            at = now
        own.append(at)
        others[account.platform].append((aid, at))

        span = (clip.start_s, clip.end_s)
        if project is not None:
            again = repeat_of(video_key(project), span, aid, earlier)
            if again is not None:
                queued = again.status != "posted"
                warn(
                    "repeat",
                    (REPEAT_QUEUED if queued else REPEAT_POSTED).format(who=who(account)),
                    blocking=queued,
                )
            dup = duplicate_of(video_key(project), span, account.platform, aid, earlier)
            if dup is not None:
                other = names.get(dup.account_id) or store.get_account(conn, dup.account_id, deleted=True)
                where = who(other) if other is not None else "another account"
                warn(
                    "duplicate",
                    f"This moment already went to {where} on {NAMES[account.platform]}. {EVERY_ACK}.",
                )
        _edit_warnings(clip, account, render, warn, linked=campaign is not None)

        warnings.extend(found)
        posts.append(
            PlannedPost(
                key=key,
                clip_id=cid,
                account_id=aid,
                platform=account.platform,
                caption=body,
                hashtags=tags,
                options=options,
                scheduled_at=at.timestamp(),
                warnings=[w.code for w in found],
                rules=checked,
                rule_ticks=ticks,
                off_campaign=off_campaign,
                campaign_id=campaign.id if campaign is not None else None,
            )
        )
    return Plan(
        plan_id=plan_id,
        posts=posts,
        warnings=warnings,
        blocked=any(w.blocking for w in warnings),
        renders=list(dict.fromkeys(cid for cid, _ in pairs if renders[cid] is None)),
    )


def approvable(conn, draft_ids: list[str]) -> dict[str, Post]:
    """The drafts among ``draft_ids`` that can still be approved, by their plan key: still drafts, with
    their account and their clip, which isn't put aside. The first of two for one key."""
    held: dict[str, Post] = {}
    for draft in (store.get_post(conn, pid) for pid in dict.fromkeys(draft_ids)):
        if draft is None or draft.status != "draft" or draft.clip_id is None:
            continue
        clip = repo.get_clip(conn, draft.clip_id)
        if clip is not None and clip.discarded_at is None and store.get_account(conn, draft.account_id):
            held.setdefault(f"{draft.clip_id}:{draft.account_id}", draft)
    return held


def _with_draft(draft: Post | None, edit: PostEdit | None) -> PostEdit | None:
    """The edit a post is planned with: a draft's own words, rule ticks and unlinking, each replaced by the
    request's when it sent one."""
    if draft is None:
        return edit
    return PostEdit(
        caption=draft.caption if edit is None or edit.caption is None else edit.caption,
        hashtags=draft.hashtags if edit is None or edit.hashtags is None else edit.hashtags,
        options=None if edit is None else edit.options,
        rule_ticks=draft.state.get("rule_ticks")
        if edit is None or edit.rule_ticks is None
        else edit.rule_ticks,
        off_campaign=draft.state.get("off_campaign")
        if edit is None or edit.off_campaign is None
        else edit.off_campaign,
    )


def _draft_slot(
    draft: Post,
    account: Account,
    rules: Rules,
    now: datetime,
    mine: list[datetime],
    platform: list[datetime],
    warn,
) -> datetime | None:
    """A draft's slot while it is still ahead and free, else the next free one, with a note saying it
    moved. None when there is none."""
    at = _utc(draft.scheduled_at)
    passed = at < now - PAST
    if not passed and not check_slot(at, rules, mine=mine, platform=platform):
        return at
    move = next_free(rules, after=now, mine=mine, platform=platform)
    if move is not None:
        why = "has passed" if passed else "is no longer free"
        old, new = at_text(at, rules.tz), at_text(move, rules.tz)
        warn("slot_moved", f"{who(account)}'s slot at {old} {why}, so it moves to {new}.")
    return move


def _options(
    account: Account,
    shared: PostOptions | None,
    edit: PostEdit | None,
    draft: PostOptions | None = None,
    *,
    linked: bool = False,
) -> PostOptions:
    """A post's switches: the account's assisted posting, then a draft's own switches, then the switches
    the platform's block sent (never its sound: a sound is picked per post), then the ones the post's own
    edit sent. A request holds only the switches someone changed. A post for a campaign has the paid
    partnership label on before the request's switches, so only a switch turned off in this request takes
    it off. A TikTok post with a shortlisted sound is always assisted, since you add the sound yourself in
    TikTok's window."""
    out = {"assisted": account.settings.assisted}
    if draft is not None:
        out |= draft.model_dump(exclude={"assisted"})
    if linked:
        out["disclosure"] = True
    if shared is not None:
        out |= shared.model_dump(exclude_unset=True, exclude={"assisted", "sound"})
    if edit is not None and edit.options is not None:
        out |= edit.options.model_dump(exclude_unset=True)
    options = PostOptions(**out)
    if account.platform == "tiktok" and options.sound is not None and options.sound.source == "shortlist":
        return options.model_copy(update={"assisted": True})
    return options


def _campaign_warnings(campaign: Campaign, checked: list[RuleCheck], warn) -> None:
    """A blocking note for each of the campaign's rules the post breaks, or that you haven't ticked yet,
    naming the rule (SPEC 10.10: a draft breaking a rule can't be approved until it is fixed)."""
    for check in checked:
        if check.ok:
            continue
        code = "rule_unchecked" if check.kind == "hand" else "rule_broken"
        warn(code, rule_sentence(campaign, check), blocking=True)


def _add(
    found: list[PlanWarning],
    key: str,
    code: str,
    sentence: str,
    *,
    blocking: bool = False,
    move_to: float | None = None,
) -> None:
    found.append(PlanWarning(code=code, sentence=sentence, key=key, blocking=blocking, move_to=move_to))


def _get(row, sentence: str):
    if row is None:
        raise LookupError(sentence)
    return row


def _now_times(
    pairs: list[tuple[str, str]], accounts: dict[str, Account], now: datetime, rng: random.Random
) -> dict[str, datetime]:
    """Post now: each account's first post, staggered per platform in the order the accounts come up."""
    times: dict[str, datetime] = {}
    for platform in NAMES:
        order = list(dict.fromkeys(aid for _, aid in pairs if accounts[aid].platform == platform))
        times |= dict(zip(order, now_slots(len(order), now=now, rng=rng), strict=True))
    return times


def _custom_time(req: PlanRequest, key: str, index: int) -> datetime | None:
    """The post's own time, else the start plus ``index`` intervals, else None (it gets a smart slot)."""
    if key in req.custom_times:
        return _utc(req.custom_times[key])
    if req.custom_start is not None:
        return _utc(req.custom_start + index * 60 * (req.custom_interval_min or 0))
    return None


def _slot_sentence(code: str, account: Account, at: datetime, rules: Rules, movable: bool) -> str:
    problem = slot_problem(code, account, at, rules)
    return f"{problem} {'Move it to the next free slot or pick' if movable else 'Pick'} another time."


def slot_problem(code: str, account: Account, at: datetime, rules: Rules) -> str:
    """What ``at`` breaks of the account's rules, ``code`` being one check_slot gives, or ``past``."""
    when, day = at_text(at, rules.tz), _day(at, rules.tz)
    name = NAMES[account.platform]
    return {
        "past": f"{when} has already passed.",
        "outside": f"{when} is outside the posting windows.",
        "cap": f"{who(account)} already has {rules.cap} post{'s' * (rules.cap != 1)} on {day}, its daily"
        " cap.",
        "spacing": f"{who(account)} has another post less than {rules.spacing.total_seconds() / 3600:g} hours"
        f" from {when}.",
        "gap": f"Another {name} account posts less than {rules.platform_gap.total_seconds() / 60:g} minutes"
        f" from {when}.",
    }[code]


def _account_warnings(account: Account, warn) -> None:
    if account.status in ("needs_login", "error"):
        warn(
            "needs_login",
            f"{who(account)} needs you to log in again before it can post. Open Accounts and log in.",
            blocking=True,
        )
    if account.paused:
        warn("paused", f"{who(account)} is paused, so this post waits until you resume the account.")


def _sound_warnings(account: Account, options: PostOptions, warn) -> None:
    """A sound the post's route can't take: one of Instagram's on TikTok or on an Instagram account linked
    another way than through Facebook, and a TikTok sound on Instagram. A shortlisted sound on TikTok is a
    note that you add it yourself."""
    sound = options.sound
    if sound is None:
        return
    if account.platform == "tiktok":
        if sound.source == "ig_trending":
            warn("sound_route", SOUND_NOT_TIKTOK, blocking=True)
        else:
            warn("sound_by_hand", SOUND_BY_HAND.format(title=sound.title))
    elif sound.source == "shortlist":
        warn("sound_route", SOUND_NOT_INSTAGRAM, blocking=True)
    elif (account.method, account.ig_mode) != ("api", "facebook"):
        warn("sound_route", SOUND_FACEBOOK_ONLY, blocking=True)


def copy_warnings(account: Account, caption: str, body: str, tags: list[str], warn) -> None:
    name, limit = NAMES[account.platform], CAPTION_UNITS[account.platform]
    if (n := units(caption)) > limit:
        warn(
            "caption_too_long",
            f"The {name} caption is {n} characters, over the {limit} {name} takes. Shorten it to post.",
            blocking=True,
        )
    most = MAX_HASHTAGS.get(account.platform)
    if most is not None and (n := len(tags) + len(HASHTAG.findall(body))) > most:
        warn(
            "too_many_hashtags",
            f"The {name} post has {n} hashtags, over the {most} {name} takes. Remove some to post.",
            blocking=True,
        )


def _clip_warnings(clip: ClipRow, account: Account, render: RenderRow | None, warn) -> None:
    name = NAMES[account.platform]
    seconds = render.duration_s if render is not None and render.duration_s else clip_length(clip)
    longest, shortest = caps_for(account).max_duration_s, MIN_S.get(account.platform, 0.0)
    if seconds > longest:
        warn(
            "too_long",
            f"The clip is {_length(seconds)} long, over the {_length(longest)} {name} takes. Trim it to post"
            " it there.",
            blocking=True,
        )
    elif seconds < shortest:
        warn(
            "too_short",
            f"The clip is {_length(seconds)} long, under the {_length(shortest)} {name} needs. Lengthen it to"
            " post it there.",
            blocking=True,
        )
    qa = _qa(render)
    if render is None:
        warn("renders_first", RENDERS_FIRST)
    elif qa is not None and not qa.passed_for(account.platform):
        problem = next(
            c.message for c in qa.checks if not c.ok and c.hard and c.platform in (None, account.platform)
        )
        warn("qa_failed", f"{problem} It can't go to {name} until that's fixed.", blocking=True)


def _edit_warnings(
    clip: ClipRow, account: Account, render: RenderRow | None, warn, *, linked: bool = False
) -> None:
    """Notes on the edit, none blocking. A clip for a campaign keeps to the campaign's lengths, so its
    length gets no Creator Rewards note."""
    qa = _qa(render)
    light = next((c for c in qa.checks if c.name == "originality" and not c.ok), None) if qa else None
    if light is not None:
        warn("light_edits", light.message)
    seconds = clip_length(clip)
    if not linked and account.platform == "tiktok" and account.settings.creator_rewards and seconds <= 60:
        warn(
            "creator_rewards_short",
            f"{who(account)} is set for Creator Rewards, which only pays for clips over 60 seconds, and this"
            f" clip is {_length(seconds)}.",
        )


def _qa(render: RenderRow | None) -> QAResult | None:
    return QAResult.model_validate(render.qa) if render is not None and render.qa else None
