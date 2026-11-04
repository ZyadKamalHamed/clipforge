import pytest

from clipforge.campaigns.models import Campaign, Required
from clipforge.models import ClipRow, Project, ProjectOptions
from clipforge.post.distribute import compose, duplicate_of, every, full_caption, iou, recredit, spread
from clipforge.post.models import Account, AccountSettings, Post, PostEdit, PostOptions

KEY = "arj7oStGLkU"
PACK = {
    "tiktok_caption": "Line one\nLine two",
    "tiktok_hashtags": ["#money", "#Startups"],
    "instagram_caption": "The Instagram one",
    "instagram_hashtags": ["#money"],
    "credit": "Source: Lex Fridman",
}


def clip(cid, rank, *, tags=(), start=0.0, end=30.0, packaging=None):
    return ClipRow(
        id=cid,
        project_id="p1",
        rank=rank,
        start_s=start,
        end_s=end,
        topic_tags=list(tags),
        packaging=packaging,
    )


def acct(aid, platform="tiktok", **settings):
    return Account(
        id=aid,
        platform=platform,
        method="browser" if platform == "tiktok" else "api",
        handle=aid,
        status="connected",
        settings=AccountSettings(**settings),
        created_at=0.0,
    )


def project(**fields):
    fields.setdefault("options", ProjectOptions())
    return Project(id="p1", source_kind="youtube", video_id=KEY, created_at=0.0, updated_at=0.0, **fields)


def stored(account_id, platform, span, *, clip_id=None, video_key=KEY):
    return Post(
        id=f"t{account_id}",
        batch_id="b1",
        clip_id=clip_id,
        clip_title="A clip",
        render_id=None,
        account_id=account_id,
        platform=platform,
        method="browser",
        caption="",
        hashtags=[],
        options=PostOptions(),
        scheduled_at=0.0,
        status="posted",
        video_key=video_key,
        start_s=span[0],
        end_s=span[1],
        created_at=0.0,
        updated_at=0.0,
    )


# Who gets which clip


def test_spread_round_robins_in_rank_order():
    clips = [clip(f"c{k}", k) for k in (3, 1, 4, 2, 5)]
    accounts = [acct("t1"), acct("t2"), acct("i1", "instagram"), acct("t3")]
    pairs = spread(clips, accounts)
    assert [p for p in pairs if p[1].startswith("t")] == [
        ("c1", "t1"),
        ("c2", "t2"),
        ("c3", "t3"),
        ("c4", "t1"),
        ("c5", "t2"),
    ]
    assert [p for p in pairs if p[1] == "i1"] == [(f"c{k}", "i1") for k in range(1, 6)]
    assert pairs[:2] == [("c1", "t1"), ("c1", "i1")]  # a clip's posts together, TikTok first


def test_spread_prefers_niche_matches():
    clips = [
        clip("c1", 1, tags=["Gaming"]),
        clip("c2", 2, tags=["money"]),
        clip("c3", 3, tags=["gaming", "fails"]),
        clip("c4", 4),
    ]
    accounts = [acct("t1", niche_tags=["money"]), acct("t2", niche_tags=["gaming"]), acct("t3")]
    assert spread(clips, accounts) == [("c1", "t2"), ("c2", "t1"), ("c3", "t2"), ("c4", "t3")]


def test_spread_gives_short_clips_to_accounts_without_creator_rewards():
    clips = [clip("c1", 1, end=30.0), clip("c2", 2, end=75.0)]
    accounts = [acct("t1", creator_rewards=True), acct("t2")]
    assert spread(clips, accounts) == [("c1", "t2"), ("c2", "t1")]


def test_linked_clip_ignores_creator_rewards():
    clips = [clip("c1", 1, end=30.0), clip("c2", 2, end=75.0)]
    accounts = [acct("t1", creator_rewards=True), acct("t2")]
    assert spread(clips, accounts, linked={"p1"}) == [("c1", "t1"), ("c2", "t2")]  # its limits win
    assert spread(clips, accounts, linked={"p2"}) == [("c1", "t2"), ("c2", "t1")]


def test_spread_keeps_pairs_together():
    accounts = [
        acct("t1", pair_id="i2"),
        acct("t2"),
        acct("i1", "instagram"),
        acct("i2", "instagram", pair_id="t1"),
    ]
    pairs = spread([clip(f"c{k}", k) for k in range(1, 5)], accounts)
    assert pairs == [
        ("c1", "t1"),
        ("c1", "i2"),
        ("c2", "t2"),
        ("c2", "i1"),
        ("c3", "t1"),
        ("c3", "i2"),
        ("c4", "t2"),
        ("c4", "i1"),
    ]


def test_spread_follows_a_pair_only_to_a_chosen_account():
    pairs = spread([clip("c1", 1)], [acct("t1", pair_id="i9")])
    assert pairs == [("c1", "t1")]


def test_every_sends_each_clip_to_each_account():
    clips = [clip("c2", 2), clip("c1", 1)]
    accounts = [acct("t1"), acct("t2"), acct("i1", "instagram")]
    assert every(clips, accounts) == [
        ("c1", "t1"),
        ("c1", "t2"),
        ("c1", "i1"),
        ("c2", "t1"),
        ("c2", "t2"),
        ("c2", "i1"),
    ]


# The already-posted guard


def test_duplicate_guard_iou():
    assert iou((0.0, 31.0), (0.0, 100.0)) == pytest.approx(0.31)
    assert iou((0.0, 30.0), (0.0, 100.0)) == 0.3
    assert iou((0.0, 10.0), (20.0, 30.0)) == 0.0
    assert iou((5.0, 5.0), (5.0, 5.0)) == 0.0
    gone = stored("t2", "tiktok", (0.0, 100.0), clip_id=None)  # its clip was deleted since
    assert duplicate_of(KEY, (0.0, 31.0), "tiktok", "t1", [gone]) == gone
    assert duplicate_of(KEY, (0.0, 30.0), "tiktok", "t1", [gone]) is None
    assert duplicate_of(KEY, (0.0, 31.0), "tiktok", "t2", [gone]) is None  # the same account
    assert duplicate_of(KEY, (0.0, 31.0), "instagram", "i1", [gone]) is None  # the other platform
    assert duplicate_of("other", (0.0, 31.0), "tiktok", "t1", [gone]) is None  # another video


# Captions


def test_caption_built_from_packaging_and_account():
    c = clip("c1", 1, packaging=PACK)
    t = acct("t1", cta="Follow for more", hashtags=["#clips", "money", "STARTUPS"])
    body, tags = compose(c, t, project(), None)
    assert body == "Line one\nLine two\n\nFollow for more\n\nSource: Lex Fridman"
    assert tags == ["money", "Startups", "clips"]
    assert full_caption(body, tags) == f"{body}\n\n#money #Startups #clips"
    assert compose(c, t, project(), None, credit_line=False)[0] == "Line one\nLine two\n\nFollow for more"
    own = project(rights_basis="own")
    assert compose(c, t, own, None)[0] == "Line one\nLine two\n\nFollow for more"
    asked = project(rights_basis="own", options=ProjectOptions(credit=True))
    assert compose(c, t, asked, None)[0].endswith("Source: Lex Fridman")
    assert compose(c, acct("i1", "instagram"), project(), None) == (
        "The Instagram one\n\nSource: Lex Fridman",
        ["money"],
    )


def test_caption_edit_replaces_the_body_and_hashtags():
    c = clip("c1", 1, packaging=PACK)
    edit = PostEdit(caption="Mine\u0007 now\r\nnext \u202eline", hashtags=["#A", "a", " b ", "#", ""])
    body, tags = compose(c, acct("t1", cta="Follow"), project(), edit)
    assert body == "Mine now\nnext line"
    assert tags == ["A", "b"]
    assert full_caption("", []) == ""
    assert full_caption("", ["a"]) == "#a"


def test_caption_falls_back_to_the_hook_title_without_packaging():
    c = clip("c1", 1).model_copy(update={"hook_titles": ["The idea that changed it"]})
    assert compose(c, acct("t1"), project(), None) == ("The idea that changed it", [])


def linked(**rules) -> Campaign:
    return Campaign(id="cp1", name="Beast clips", marketplace="Whop", created_at=0.0, updated_at=0.0, **rules)


def test_compose_adds_required_parts_once():
    c = clip("c1", 1, packaging=PACK | {"tiktok_caption": "Line one\nUse code BEAST now"})
    t = acct("t1", cta="Follow for more @mrbeast", hashtags=["clips"])
    rules = linked(
        required=Required(
            hashtags=["#Beast", "money"],
            mentions=["@MrBeast", "@beastgames", "@Clips"],
            text=["use code beast", "Link in bio"],
        )
    )
    body, tags = compose(c, t, project(), None, campaign=rules)
    assert body == (
        "Line one\nUse code BEAST now\n\nFollow for more @mrbeast\n\nSource: Lex Fridman"
        "\n\nLink in bio\n@beastgames @Clips"
    )
    assert tags == ["Beast", "money", "Startups", "clips"]
    again = compose(c, t, project(), PostEdit(caption=body, hashtags=tags), campaign=rules)
    assert again == (body, tags)  # nothing twice
    mine = compose(c, t, project(), PostEdit(caption="Mine", hashtags=["x"]), campaign=rules)
    assert mine == (
        "Mine\n\nuse code beast\nLink in bio\n@MrBeast @beastgames @Clips",
        ["Beast", "money", "x"],
    )
    assert compose(c, t, project(), None, campaign=linked()) == compose(c, t, project(), None)


def test_compose_adds_a_mention_only_a_longer_handle_has():
    rules = linked(required=Required(mentions=["@nike"]))
    c = clip("c1", 1, packaging=PACK | {"tiktok_caption": "Thanks @nikefan"})
    body, _ = compose(c, acct("t1"), project(), None, campaign=rules)
    assert body.endswith("\n\n@nike")
    c = clip("c1", 1, packaging=PACK | {"tiktok_caption": "Thanks @Nike."})
    body, _ = compose(c, acct("t1"), project(), None, campaign=rules)
    assert not body.endswith("@nike")


def test_required_hashtags_survive_the_cut():
    many = PACK | {
        "instagram_caption": "Big #news today",
        "instagram_hashtags": [f"tag{n}" for n in range(40)],
        "tiktok_hashtags": [f"tag{n}" for n in range(40)],
    }
    c = clip("c1", 1, packaging=many)
    rules = linked(required=Required(hashtags=["one", "two", "three"]))
    body, tags = compose(c, acct("i1", "instagram", hashtags=["mine"]), project(), None, campaign=rules)
    assert tags == ["one", "two", "three", *(f"tag{n}" for n in range(26))]  # 30 with the one in the body
    assert len(compose(c, acct("t1"), project(), None, campaign=rules)[1]) == 43  # TikTok takes any number
    assert len(compose(c, acct("i1", "instagram"), project(), None)[1]) == 40  # unlinked: the plan warns


def test_banned_credit_line_moves_credit_to_caption():
    c = clip("c1", 1, packaging=PACK)
    own = project(rights_basis="own")
    banned = linked(banned=["credit_line"])
    moved = compose(c, acct("t1"), own, None, credit_line=False, campaign=banned)[0]
    assert moved == "Line one\nLine two\n\nSource: Lex Fridman"
    assert compose(c, acct("t1"), own, None, credit_line=False, campaign=linked())[0] == "Line one\nLine two"


def test_music_credit_ends_the_caption():
    c = clip("c1", 1, packaging=PACK)
    t = acct("t1", cta="Follow for more")
    credit = "Music: Night Drive by Me"
    body, _ = compose(c, t, project(), None, music_credit=credit)
    assert body == "Line one\nLine two\n\nFollow for more\n\nSource: Lex Fridman\n\nMusic: Night Drive by Me"
    mine = compose(c, t, project(), PostEdit(caption="Mine"), music_credit=credit)[0]
    assert mine == "Mine\n\nMusic: Night Drive by Me"  # an edited caption gets it too
    assert compose(c, t, project(), PostEdit(caption=body), music_credit=credit)[0] == body  # never twice
    said = compose(
        c, t, project(), PostEdit(caption="Mine\n\nmusic: night drive by me"), music_credit=credit
    )[0]
    assert said == "Mine\n\nmusic: night drive by me"  # whatever its case
    assert compose(c, t, project(), None, music_credit=None) == compose(c, t, project(), None)
    # a campaign's required lines still end the post, after the music credit
    rules = linked(required=Required(text=["Link in bio"]))
    linked_body = compose(c, t, project(), PostEdit(caption="Mine"), music_credit=credit, campaign=rules)[0]
    assert linked_body == "Mine\n\nMusic: Night Drive by Me\n\nLink in bio"


def test_recredit_follows_the_render_that_goes_up():
    old, new = "Music: Night Drive by Lena Hart", "Music: Sunrise by Lena Hart"
    body = f"Mine\n\n{old}\n\nLink in bio"
    assert recredit(body, old, new) == f"Mine\n\n{new}\n\nLink in bio"  # in the old one's place
    assert recredit(body, old, None) == "Mine\n\nLink in bio"  # music off takes its credit with it
    assert recredit(f"Mine\n\n{old}", old, None) == "Mine"
    assert recredit("Mine\n\nmusic: night drive by lena hart", old, new) == f"Mine\n\n{new}"  # any case
    assert recredit(body, old, old) == body
    assert recredit("Mine", None, None) == "Mine"
    assert recredit("Mine", None, new) == f"Mine\n\n{new}"  # a credit the body never had is added
    assert recredit(f"Mine\n\n{new}", old, new) == f"Mine\n\n{new}"  # never twice
    # added before a campaign's required lines, which still end the post
    rules = linked(required=Required(text=["Link in bio", "Use code BEAST"], mentions=["@mrbeast"]))
    end = "Link in bio\nUse code BEAST\n@mrbeast"
    assert recredit(f"Mine\n\n{end}", None, new, rules) == f"Mine\n\n{new}\n\n{end}"
    assert recredit("Link in bio first\n\nMine", None, new, rules) == f"Link in bio first\n\nMine\n\n{new}"
