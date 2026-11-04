"""A linked campaign's rules (SPEC 10.10): the campaign a project serves, its banned elements turned off in
the render's style, its clip length limits over the project's own length, and every rule as a check on a
post, so none passes silently."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from clipforge import repo
from clipforge.campaigns import store
from clipforge.campaigns.models import BANNED, RuleCheck
from clipforge.post import store as post_store
from clipforge.post.distribute import clean_hashtags, clip_length, has_mention
from clipforge.render.edl import EDL

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.models import ClipRow, Project, RenderRow
    from clipforge.post.models import Account, Post, PostOptions
    from clipforge.render.styles import Style

LENGTH_ROOM_S = 15.0  # how far a limit on one end leaves the other end from it
SHORTEST_S = 5.0  # the shortest clip a campaign may ask for
SWITCHES = ("sfx", "emoji", "hook", "cards", "progress_bar", "punch_ins", "credit_line")
PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram"}
RENDER_AGAIN = "Render it again so ClipForge can check this."
WORD_RULES = ("hashtag:", "mention:", "text:")  # the rules a post's words keep
RENDER_RULES = ("length", "banned:")  # the rules its render keeps
# What a render that breaks a banned rule still has
LEFT_IN = {
    "gameplay": "a gameplay split",
    "music": "music",
    "sfx": "sound effects",
    "emoji": "emoji",
    "hook": "a hook title",
    "cards": "fact cards",
    "progress_bar": "a progress bar",
    "punch_ins": "punch-ins",
    "credit_line": "an on-screen credit line",
}


def campaign_of(engine: Engine, project: Project | None) -> Campaign | None:
    """The campaign the project is linked to, or None."""
    if project is None or project.campaign_id is None:
        return None
    return store.get_campaign(engine.db(), project.campaign_id)


def campaign_of_post(engine: Engine, post: Post) -> Campaign | None:
    """The campaign the post was planned for, or None: none for a post planned before its project was
    linked, or unlinked from its campaign, whatever its project is linked to now."""
    campaign_id = post.state.get("campaign_id")
    if not campaign_id or post.state.get("off_campaign"):
        return None
    return store.get_campaign(engine.db(), campaign_id)


def enforce(style: Style, campaign: Campaign | None) -> Style:
    """``style`` with the campaign's banned elements off."""
    if campaign is None:
        return style
    off: dict[str, object] = {name: False for name in SWITCHES if name in campaign.banned}
    if "gameplay" in campaign.banned:
        off["gameplay_mode"] = "off"
    if "music" in campaign.banned:
        off["music"] = None
    return style.model_copy(update=off) if off else style


def lengths_for(campaign: Campaign | None, base: tuple[float, float]) -> tuple[float, float] | None:
    """The clip lengths the campaign allows over ``base``, the project's own range: None without limits.
    A limit on one end only keeps the base's other end, moved 15 s clear of it when the limit passes it."""
    if campaign is None or (campaign.min_s is None and campaign.max_s is None):
        return None
    low = campaign.min_s if campaign.min_s is not None else base[0]
    high = campaign.max_s if campaign.max_s is not None else max(base[1], low + LENGTH_ROOM_S)
    if low > high:  # only a longest clip, shorter than the base's shortest
        low = max(SHORTEST_S, high - LENGTH_ROOM_S)
    return low, high


def _seconds(value: float) -> str:
    return f"{round(value, 1):g}"


def _length_label(campaign: Campaign) -> str:
    low, high = campaign.min_s, campaign.max_s
    if low is not None and high is not None:
        return f"{_seconds(low)} to {_seconds(high)} seconds long"
    if low is not None:
        return f"At least {_seconds(low)} seconds long"
    return f"At most {_seconds(high or 0.0)} seconds long"


def read_edl(render: RenderRow | None) -> EDL | None:
    """The edit list a render was made from, or None when it has none ClipForge can read."""
    if render is None or not render.edl_path:
        return None
    try:
        return EDL.model_validate_json(Path(render.edl_path).read_bytes())
    except (OSError, ValueError, ValidationError):
        return None


def _has(edl: EDL, name: str) -> bool:
    """Whether the render the EDL describes has the element a campaign can ban."""
    match name:
        case "gameplay":
            return edl.gameplay is not None or any(s.layout == "gameplay" for s in edl.spans)
        case "music":
            return edl.music is not None
        case "sfx":
            return bool(edl.sfx)
        case "emoji":
            return bool(edl.emoji)
        case "hook":
            return edl.hook is not None
        case "cards":
            return bool(edl.cards)
        case "progress_bar":
            return edl.style.progress_bar
        case "punch_ins":
            return bool(edl.zooms)
        case "credit_line":
            return edl.credit is not None
    return False


def hand_key(rule: str) -> str:
    """A hand rule's tick key, from its words (case and outer spaces aside), so a tick stays with its rule
    when others are added or taken away, and an edited rule starts unticked."""
    return "hand:" + hashlib.sha1(rule.strip().casefold().encode()).hexdigest()[:12]


def _auto(key: str, label: str, ok: bool, sentence: str) -> RuleCheck:
    return RuleCheck(key=key, label=label, kind="auto", ok=ok, sentence="" if ok else sentence)


def checks_for(
    campaign: Campaign,
    *,
    project: Project,
    clip: ClipRow,
    render: RenderRow | None,
    account: Account,
    body: str,
    hashtags: list[str],
    options: PostOptions,
    ticks: list[str],
) -> list[RuleCheck]:
    """Each of the campaign's rules as a check on posting ``clip`` to ``account`` with these words and
    switches, in a fixed order: length (the render's, else the clip's), platform, footage, each required
    hashtag, mention and line of text, each banned element (read from the render's EDL), disclosure on
    TikTok, then each hand rule, which passes only when its key is among ``ticks``. A rule the campaign
    doesn't set has no check."""
    from clipforge.campaigns.service import footage_allows  # it imports the posting modules

    found: list[RuleCheck] = []
    as_is = clip.mode == "as_is"  # the video as it is (SPEC 14.2): ClipForge added nothing to it
    if campaign.min_s is not None or campaign.max_s is not None:
        seconds = render.duration_s if render is not None and render.duration_s else clip_length(clip)
        low, high = campaign.min_s or 0.0, campaign.max_s or float("inf")
        fix = (
            "Add the video again with Post as it is off to cut a clip that fits."
            if as_is
            else "Change its length and render it again."
        )
        found.append(
            _auto(
                "length",
                _length_label(campaign),
                low <= seconds <= high,
                f"The clip is {_seconds(seconds)} seconds long. {fix}",
            )
        )
    if len(campaign.platforms) < len(PLATFORM_NAMES):
        names = " and ".join(PLATFORM_NAMES[p] for p in campaign.platforms)
        found.append(
            _auto(
                "platform",
                f"Posts only to {names}",
                account.platform in campaign.platforms,
                f"This post is for {PLATFORM_NAMES[account.platform]}, which the campaign doesn't allow.",
            )
        )
    if not campaign.footage.any:
        refusal = footage_allows(campaign, project)
        found.append(_auto("footage", "Footage the campaign allows", refusal is None, refusal or ""))
    have = {tag.casefold() for tag in hashtags}
    for tag in clean_hashtags(campaign.required.hashtags):
        in_body = re.search(rf"(?<!\w)#{re.escape(tag)}(?!\w)", body, re.IGNORECASE) is not None
        found.append(
            _auto(
                f"hashtag:{tag}",
                f"#{tag} in the hashtags",
                tag.casefold() in have or in_body,
                f"Add #{tag} back to the hashtags.",
            )
        )
    seen = body.casefold()
    for mention in campaign.required.mentions:
        found.append(
            _auto(
                f"mention:{mention}",
                f"{mention} in the caption",
                has_mention(body, mention),
                f"Add {mention} back to the caption.",
            )
        )
    for n, line in enumerate(campaign.required.text):
        found.append(
            _auto(
                f"text:{n}",
                f'"{line}" in the caption',
                line.casefold() in seen,
                "Add it back to the caption.",
            )
        )
    edl = read_edl(render)
    for name, rule in BANNED.items():
        if name not in campaign.banned:
            continue
        label = f"No {rule.lower()}"
        if as_is:
            found.append(_auto(f"banned:{name}", label, True, ""))
        elif edl is None:
            found.append(_auto(f"banned:{name}", label, False, RENDER_AGAIN))
        else:
            sentence = f"The render still has {LEFT_IN[name]}. Render it again to take it out."
            found.append(_auto(f"banned:{name}", label, not _has(edl, name), sentence))
    if account.platform == "tiktok":
        found.append(
            _auto(
                "disclosure",
                "Content disclosure on",
                options.disclosure,
                "Turn Content disclosure back on for this post.",
            )
        )
    for line in dict.fromkeys(campaign.hand_rules):
        key = hand_key(line)
        ok = key in ticks
        found.append(
            RuleCheck(
                key=key,
                label=line,
                kind="hand",
                ok=ok,
                sentence="" if ok else "Check this yourself, then tick it.",
            )
        )
    return found


def rule_sentence(campaign: Campaign, check: RuleCheck) -> str:
    """What a rule the post doesn't keep says: the campaign, the rule and what to do."""
    return f"{campaign.name}: {check.label}. {check.sentence}"


def broken_rule(
    engine: Engine,
    post: Post,
    campaign: Campaign,
    *,
    kinds: tuple[str, ...],
    body: str,
    hashtags: list[str],
    render: RenderRow | None,
) -> str | None:
    """The sentence for the first of the campaign's rules, among those whose keys start with one of
    ``kinds``, that the scheduled post would break with these words and this render; None when it keeps
    them, or when its clip or account is gone (the queue says so itself)."""
    conn = engine.db()
    clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
    project = engine.project(clip.project_id) if clip is not None else None
    account = post_store.get_account(conn, post.account_id, deleted=True)
    if clip is None or project is None or account is None:
        return None
    for check in checks_for(
        campaign,
        project=project,
        clip=clip,
        render=render,
        account=account,
        body=body,
        hashtags=hashtags,
        options=post.options,
        ticks=post.state.get("rule_ticks") or [],
    ):
        if not check.ok and check.key.startswith(kinds):
            return rule_sentence(campaign, check)
    return None
