"""Which account gets which clip, the already-posted guard and the text under each post (SPEC 10.6)."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING

from clipforge.models import ClipRow, Project
from clipforge.post.models import Account, Post, PostEdit

if TYPE_CHECKING:
    from clipforge.campaigns.models import Campaign

PLATFORMS = ("tiktok", "instagram")
OVERLAP = 0.3  # IoU over this is the same moment
REWARDS_S = 60.0  # Creator Rewards only pays for clips longer than this (call 16)
TURNS = {*range(0x202A, 0x202F), *range(0x2066, 0x206A)}  # marks that turn text around
MAX_HASHTAGS = {"instagram": 30}
HASHTAG = re.compile(r"(?<!\w)#\w")


def _in_order(clips: Iterable[ClipRow]) -> list[ClipRow]:
    """Best first: by rank, and in the order given where ranks are equal or missing."""
    ranked = sorted(enumerate(clips), key=lambda kc: (kc[1].rank is None, kc[1].rank or 0, kc[0]))
    return [c for _, c in ranked]


def _niche(clip: ClipRow, account: Account) -> bool:
    tags = {t.casefold() for t in clip.topic_tags}
    return any(t.casefold() in tags for t in account.settings.niche_tags)


def _rewards_misfit(clip: ClipRow, account: Account, linked: Collection[str]) -> bool:
    """A short clip for a Creator Rewards account, unless the clip's campaign set its length."""
    return (
        account.platform == "tiktok"
        and account.settings.creator_rewards
        and clip_length(clip) <= REWARDS_S
        and clip.project_id not in linked
    )


def clip_length(clip: ClipRow) -> float:
    return clip.end_s - clip.start_s


def spread(
    clips: Iterable[ClipRow], accounts: Iterable[Account], *, linked: Collection[str] = ()
) -> list[tuple[str, str]]:
    """Each clip, best first, to one account per platform among ``accounts``: the one with the fewest clips
    so far (the order given breaks ties), choosing first an account whose niche tags meet the clip's topic
    tags and never a Creator Rewards account for a clip of 60 s or less while another will do, unless the
    clip's project is among the ``linked`` ones, whose campaign's lengths win. A clip that goes to an
    account paired with another chosen account goes to the pair too. Pairs of (clip, account) ids, each
    clip's together, TikTok first."""
    accounts = list(accounts)
    chosen = {a.id: a for a in accounts}
    counts = dict.fromkeys(chosen, 0)
    pairs: list[tuple[str, str]] = []
    for clip in _in_order(clips):
        given: dict[str, str] = {}  # platform to account id, for this clip
        for platform in PLATFORMS:
            if platform in given:
                continue
            ring = [a for a in accounts if a.platform == platform]
            # An account whose pair already missed out on this clip would send it to a second account on
            # the pair's platform, so it waits while another will do.
            free = [a for a in ring if _pair(a, chosen) is None or _pair(a, chosen).platform not in given]
            ring = free or ring
            if not ring:
                continue
            pick = min(
                ring,
                key=lambda a: (
                    _rewards_misfit(clip, a, linked),
                    not _niche(clip, a),
                    counts[a.id],
                    accounts.index(a),
                ),
            )
            given[platform] = pick.id
            mate = _pair(pick, chosen)
            if mate is not None and mate.platform not in given:
                given[mate.platform] = mate.id
        for platform in PLATFORMS:
            if platform in given:
                pairs.append((clip.id, given[platform]))
                counts[given[platform]] += 1
    return pairs


def _pair(account: Account, chosen: dict[str, Account]) -> Account | None:
    """The account's pair when it was chosen too and is on the other platform."""
    mate = chosen.get(account.settings.pair_id or "")
    return mate if mate is not None and mate.platform != account.platform else None


def every(clips: Iterable[ClipRow], accounts: Iterable[Account]) -> list[tuple[str, str]]:
    """Each clip, best first, to each account in the order given."""
    accounts = list(accounts)
    return [(c.id, a.id) for c in _in_order(clips) for a in accounts]


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    """How much two time ranges overlap: their shared part over their union, 0 to 1."""
    shared = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - shared
    return shared / union if union > 0 else 0.0


def duplicate_of(
    video_key: str, span: tuple[float, float], platform: str, account_id: str, posts: Iterable[Post]
) -> Post | None:
    """A post of the same moment (IoU over 0.3 of the same video) on another account on ``platform``,
    or None. It goes by the video and range the post keeps, so a deleted clip's post still counts."""
    for p in posts:
        same = p.video_key == video_key and p.platform == platform and p.account_id != account_id
        if same and iou((p.start_s, p.end_s), span) > OVERLAP:
            return p
    return None


def repeat_of(
    video_key: str, span: tuple[float, float], account_id: str, posts: Iterable[Post]
) -> Post | None:
    """A post of the same moment on ``account_id`` itself, one still in the Queue before one already up,
    or None."""
    same = [
        p
        for p in posts
        if p.video_key == video_key
        and p.account_id == account_id
        and iou((p.start_s, p.end_s), span) > OVERLAP
    ]
    return next((p for p in same if p.status != "posted"), same[0] if same else None)


def clean_caption(text: str | None) -> str:
    """Text for a caption: Windows and old Mac line breaks made plain, tabs as spaces, and control
    characters and the marks that turn text around taken out; line breaks stay."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(
        ch for ch in text if ch == "\n" or not (ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0 or ord(ch) in TURNS)
    ).strip()


def has_mention(text: str, mention: str) -> bool:
    """Whether ``text`` holds the handle ``mention`` itself, in any case: not inside a longer handle such as
    @nikefan or @nike.running, nor an email address. A full stop after it ends the sentence, as handles
    never end in one."""
    handle = rf"(?<![A-Za-z0-9._@]){re.escape(mention)}(?![A-Za-z0-9_]|\.[A-Za-z0-9_])"
    return re.search(handle, text, re.IGNORECASE) is not None


def clean_hashtags(tags: Iterable[str]) -> list[str]:
    """Hashtags without ``#`` or spaces, the first spelling of each kept, empty ones left out."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        word = "".join(clean_caption(tag).split()).lstrip("#")
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(word)
    return out


def _credit_on(project: Project) -> bool:
    """The credit rule ``wording.credit_channel`` uses, so a rights change since packaging still counts."""
    return project.options.credit if project.options.credit is not None else project.rights_basis != "own"


def compose(
    clip: ClipRow,
    account: Account,
    project: Project,
    edit: PostEdit | None,
    *,
    credit_line: bool = True,
    campaign: Campaign | None = None,
    music_credit: str | None = None,
) -> tuple[str, list[str]]:
    """The caption body and hashtags for posting ``clip`` to ``account``: the platform's caption from the
    clip's packaging (its hook title before there is one), the account's call to action and the credit
    line when ``credit_line`` is on and the project gets a credit, or whenever the ``campaign`` bans the
    on-screen credit line; packaging's hashtags then the account's. An edit's caption or hashtags replace
    them. The render's ``music_credit``, when its track's licence asks for one, ends the body, edited or
    not, unless the body has it already. A campaign's required text and mentions end the body and its
    required hashtags come first, each only where it is missing, edited words too; they stay when the
    hashtags are cut to the platform's maximum."""
    pack = clip.packaging or {}
    if edit is not None and edit.caption is not None:
        body = clean_caption(edit.caption)
    else:
        caption = pack.get(f"{account.platform}_caption") or next(iter(clip.hook_titles), "")
        moved = campaign is not None and "credit_line" in campaign.banned
        credit = pack.get("credit") if moved or (credit_line and _credit_on(project)) else None
        parts = (clean_caption(caption), clean_caption(account.settings.cta), clean_caption(credit))
        body = "\n\n".join(part for part in parts if part)
    music = clean_caption(music_credit)
    if music and music.casefold() not in body.casefold():
        body = "\n\n".join(part for part in (body, music) if part)
    if edit is not None and edit.hashtags is not None:
        tags = clean_hashtags(edit.hashtags)
    else:
        tags = clean_hashtags([*(pack.get(f"{account.platform}_hashtags") or []), *account.settings.hashtags])
    if campaign is None:
        return body, tags
    return _required(body, tags, campaign, account.platform)


def recredit(body: str, old: str | None, new: str | None, campaign: Campaign | None = None) -> str:
    """``body`` with the music credit of the render it was written for (``old``) swapped for the credit of
    the render that goes up (``new``): in the old one's place, taken out with its paragraph when the new
    render needs none, or added as ``compose`` adds it (before the ``campaign``'s required lines) when the
    body doesn't have it."""
    old, new = clean_caption(old), clean_caption(new)
    if old.casefold() == new.casefold():
        return body
    found = re.search(re.escape(old), body, re.IGNORECASE) if old else None
    if found is not None:
        body = body[: found.start()] + new + body[found.end() :]
        return "\n\n".join(part for part in body.split("\n\n") if part.strip())
    if not new or new.casefold() in body.casefold():
        return body
    parts = [part for part in body.split("\n\n") if part.strip()]
    end = (
        len(parts) - 1 if campaign is not None and parts and _is_required(parts[-1], campaign) else len(parts)
    )
    return "\n\n".join([*parts[:end], new, *parts[end:]])


def _is_required(paragraph: str, campaign: Campaign) -> bool:
    """Whether ``paragraph`` is the campaign's required lines and mentions that ``_required`` ends a body
    with."""
    text = {line.casefold() for line in campaign.required.text}
    mentions = {m.casefold() for m in campaign.required.mentions}
    return all(
        line.casefold() in text or (line.split() and all(w.casefold() in mentions for w in line.split()))
        for line in paragraph.split("\n")
    )


def _required(body: str, tags: list[str], campaign: Campaign, platform: str) -> tuple[str, list[str]]:
    """``body`` and ``tags`` with the campaign's required parts each added where missing: a last paragraph
    of its text, a line each, then its mentions on one line; its hashtags first, all of them kept when
    the rest are cut to the platform's maximum, counting the hashtags in the body."""
    seen = body.casefold()
    text = [line for line in campaign.required.text if line.casefold() not in seen]
    mentions = " ".join(m for m in campaign.required.mentions if not has_mention(body, m))
    end = "\n".join([*text, mentions] if mentions else text)
    body = "\n\n".join(part for part in (body, end) if part)
    first = clean_hashtags(campaign.required.hashtags)
    tags = clean_hashtags([*first, *tags])
    most = MAX_HASHTAGS.get(platform)
    if most is not None:
        tags = tags[: max(len(first), most - len(HASHTAG.findall(body)))]
    return body, tags


def full_caption(body: str, hashtags: list[str]) -> str:
    """What is posted: the body, then the hashtags a blank line below it."""
    return "\n\n".join(part for part in (body, " ".join(f"#{t}" for t in hashtags)) if part)
