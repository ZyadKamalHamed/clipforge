"""A linked campaign's rules on renders, clip lengths and drafts (SPEC 10.10): banned elements are left out
of the render, the clip length limits win over the project's own length, and every rule becomes a check on
the draft, automatic or a tick by hand."""

import time
from types import SimpleNamespace

import pytest

from clipforge import repo
from clipforge.campaigns import rules, store
from clipforge.campaigns.models import BANNED, Campaign, Footage, Required
from clipforge.jobs.stages import brain
from clipforge.models import ClipRow, ProjectOptions, RenderRow
from clipforge.post.models import Account, PostOptions
from clipforge.render.edl import EDL, Zoom
from clipforge.render.styles import Style
from tests.fakes import fake_engine

SWITCHES = ("sfx", "emoji", "hook", "cards", "progress_bar", "punch_ins", "credit_line")


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def campaign(**kw) -> Campaign:
    now = time.time()
    return Campaign(
        id=kw.pop("id", repo.new_id("cp")),
        name=kw.pop("name", "Beast clips"),
        marketplace="Whop",
        created_at=now,
        updated_at=now,
        **kw,
    )


def a_project(
    engine, campaign_id: str | None = None, length: str = "standard", video_id: str = "arj7oStGLkU"
):
    conn = engine.db()
    made = repo.create_project(
        conn,
        source_kind="youtube",
        source_url=None,
        video_id=video_id,
        options=ProjectOptions(length=length),
        focus_s=None,
        prompt=None,
    )
    if campaign_id is not None:
        repo.update_project(conn, made.id, campaign_id=campaign_id, rights_basis="clipping_programme")
    return engine.project(made.id)


def test_enforce_turns_banned_elements_off():
    on = Style(progress_bar=True, music="auto")
    assert on.hook and on.cards
    assert rules.enforce(on, None) == on
    assert rules.enforce(on, campaign()) == on
    for name in BANNED:
        got = rules.enforce(on, campaign(banned=[name]))
        if name == "gameplay":
            assert got == on.model_copy(update={"gameplay_mode": "off"})
        elif name == "music":
            assert got == on.model_copy(update={"music": None})
        else:
            assert got == on.model_copy(update={name: False}), name
    everything = rules.enforce(on, campaign(banned=list(BANNED)))
    assert everything.gameplay_mode == "off" and not any(getattr(everything, n) for n in SWITCHES)
    assert everything.music is None
    assert on.gameplay_mode == "auto" and all(getattr(on, n) for n in SWITCHES)  # the style given is kept


def test_lengths_for_edges():
    base = (30.0, 60.0)
    assert rules.lengths_for(None, base) is None
    assert rules.lengths_for(campaign(), base) is None
    assert rules.lengths_for(campaign(max_s=45), base) == (30.0, 45.0)
    assert rules.lengths_for(campaign(min_s=70), base) == (70.0, 85.0)
    assert rules.lengths_for(campaign(min_s=40), base) == (40.0, 60.0)
    assert rules.lengths_for(campaign(min_s=20, max_s=40), base) == (20.0, 40.0)
    assert rules.lengths_for(campaign(max_s=20), base) == (5.0, 20.0)  # the low never above the high
    assert rules.lengths_for(campaign(max_s=25), (15.0, 35.0)) == (15.0, 25.0)


def test_campaign_of_reads_the_projects_link(engine):
    saved = store.add_campaign(engine.db(), campaign())
    assert rules.campaign_of(engine, None) is None
    assert rules.campaign_of(engine, a_project(engine)) is None
    assert rules.campaign_of(engine, a_project(engine, saved.id)) == saved


def test_brain_keeps_to_the_campaign_lengths(engine, monkeypatch):
    for read in ("read_meta", "read_transcript", "read_signals"):
        monkeypatch.setattr(brain, read, lambda pp: None)
    saved = store.add_campaign(engine.db(), campaign(min_s=70))

    def context(project):
        job = SimpleNamespace(job=SimpleNamespace(ref_id=project.id), settings=engine.settings, progress=None)
        bctx, _ = brain._context(engine, job, engine.paths.project(project.id))
        return bctx

    assert context(a_project(engine, saved.id)).length_range == (70.0, 85.0)
    assert context(a_project(engine, saved.id, "long")).length_range == (70.0, 90.0)
    assert context(a_project(engine)).length_range == (30.0, 60.0)


def a_clip(project, length: float = 30.0) -> ClipRow:
    return ClipRow(
        id="c1",
        project_id=project.id,
        rank=1,
        start_s=10.0,
        end_s=10.0 + length,
        sentence_ids=["S1"],
        status="ready",
    )


def a_render(tmp_path, length: float = 30.0, *, edl: bool = True, **parts) -> RenderRow:
    """A render of 'c1' with an EDL file holding ``parts``, or without an EDL."""
    path = None
    if edl:
        parts.setdefault("style", Style())
        made = EDL(
            clip_id="c1", fps=30.0, src_width=1920, src_height=1080, duration=length, segments=[], **parts
        )
        path = tmp_path / "edl.json"
        path.write_text(made.model_dump_json())
    return RenderRow(
        id="r1",
        clip_id="c1",
        version=1,
        style_key="x",
        path="clip.mp4",
        duration_s=length,
        edl_path=str(path) if path else None,
    )


def an_account(platform: str = "tiktok") -> Account:
    return Account(id="a1", platform=platform, method="browser", handle="me", created_at=0.0)


def checks(rules_of: Campaign, project, *, render, account=None, clip=None, **kw):
    return rules.checks_for(
        rules_of,
        project=project,
        clip=clip or a_clip(project),
        render=render,
        account=account or an_account(),
        body=kw.pop("body", ""),
        hashtags=kw.pop("hashtags", []),
        options=kw.pop("options", PostOptions(disclosure=True)),  # on, as a linked post's is
        ticks=kw.pop("ticks", []),
    )


def test_every_rule_becomes_a_check(engine, tmp_path):
    project = a_project(engine)
    everything = campaign(
        min_s=20,
        max_s=45,
        platforms=["tiktok"],
        footage=Footage(videos=["arj7oStGLkU"]),
        required=Required(hashtags=["Beast"], mentions=["@MrBeast"], text=["Link in bio"]),
        banned=list(reversed(BANNED)),
        hand_rules=["No reaction content", "No watermark"],
    )
    got = checks(
        everything,
        project,
        render=a_render(tmp_path, style=Style()),
        body="Words\n\nLink in bio\n@MrBeast",
        hashtags=["beast"],
        options=PostOptions(disclosure=True),
        ticks=[rules.hand_key("No watermark")],
    )
    banned = [f"banned:{name}" for name in BANNED]
    assert [c.key for c in got] == [
        "length",
        "platform",
        "footage",
        "hashtag:Beast",
        "mention:@MrBeast",
        "text:0",
        *banned,
        "disclosure",
        rules.hand_key("No reaction content"),
        rules.hand_key("No watermark"),
    ]
    assert [c.kind for c in got] == ["auto"] * (7 + len(BANNED)) + ["hand", "hand"]
    assert all(c.label for c in got)
    assert [c.key for c in got if not c.ok] == [
        rules.hand_key("No reaction content")
    ]  # one of everything, and only the unticked fails
    assert all(c.sentence for c in got if not c.ok)
    plain = campaign()
    assert [c.key for c in checks(plain, project, render=None)] == ["disclosure"]  # every linked TikTok post
    assert checks(plain, project, render=None, account=an_account("instagram")) == []
    (off,) = checks(plain, project, render=None, options=PostOptions())
    assert not off.ok and "Content disclosure" in off.sentence

    on_instagram = checks(everything, project, render=a_render(tmp_path), account=an_account("instagram"))
    assert "disclosure" not in [c.key for c in on_instagram]  # Instagram has no disclosure switch
    platform = next(c for c in on_instagram if c.key == "platform")
    assert not platform.ok and "Instagram" in platform.sentence
    other = a_project(engine, video_id="dQw4w9WgXcQ")
    footage = next(c for c in checks(everything, other, render=None) if c.key == "footage")
    assert not footage.ok and "Beast clips" in footage.sentence


def test_mention_check_wants_the_handle_itself(engine):
    project = a_project(engine)
    nike = campaign(required=Required(mentions=["@nike"]))

    def mention(body):
        (check,) = [c for c in checks(nike, project, render=None, body=body) if c.key == "mention:@nike"]
        return check.ok

    assert not mention("Thanks @nikefan")
    assert not mention("Thanks @nike_au and @nike.running")
    assert not mention("mail me at team@nike")
    assert mention("Thanks @Nike.") and mention("(@nike)") and mention("@nike, @adidas")


def test_length_check_reads_the_render(engine, tmp_path):
    project = a_project(engine)
    limits = campaign(min_s=20, max_s=45)

    def length(**kw):
        (check,) = [c for c in checks(limits, project, **kw) if c.key == "length"]
        return check

    assert length(render=a_render(tmp_path, 40.0)).ok
    long = length(render=a_render(tmp_path, 50.0), clip=a_clip(project, 30.0))
    assert not long.ok and "50 seconds" in long.sentence  # the render's length, not the clip's span
    assert length(render=a_render(tmp_path, 45.0)).ok and length(render=a_render(tmp_path, 20.0)).ok
    assert not length(render=a_render(tmp_path, 19.5)).ok
    assert length(render=None, clip=a_clip(project, 30.0)).ok  # not rendered yet: the clip's span
    assert not length(render=None, clip=a_clip(project, 60.0)).ok
    assert "At least 20" in checks(campaign(min_s=20), project, render=None)[0].label
    assert "At most 45" in checks(campaign(max_s=45), project, render=None)[0].label


def test_banned_checks_read_the_edl(engine, tmp_path):
    project = a_project(engine)
    banning = campaign(banned=list(BANNED))

    def failing(render):
        return {
            c.key.removeprefix("banned:"): c.sentence
            for c in checks(banning, project, render=render)
            if not c.ok
        }

    assert failing(a_render(tmp_path, style=Style())) == {}
    everything = a_render(
        tmp_path,
        gameplay={"item": "g1"},
        sfx=[{"kind": "whoosh"}],
        emoji=[{"char": "x"}],
        hook={"text": "The deadline"},
        cards=[{"text": "A fact"}],
        zooms=[Zoom(start=0.0, end=1.0, scale=1.2)],
        credit={"text": "Source: Lex Fridman"},
        style=Style(progress_bar=True),
        music={"item_id": "l1", "title": "Night Drive", "artist": None, "db": -12.0, "credit": None},
    )
    assert set(failing(everything)) == set(BANNED)
    assert failing(everything)["music"] == "The render still has music. Render it again to take it out."
    again = "Render it again so ClipForge can check this."
    for render in (None, a_render(tmp_path, edl=False)):
        assert failing(render) == dict.fromkeys(BANNED, again)
    gone = a_render(tmp_path, style=Style())
    (tmp_path / "edl.json").unlink()
    assert set(failing(gone)) == set(BANNED)


def test_campaign_banning_music_renders_none(engine, tmp_path):
    """A clip set to music renders none for a campaign that bans it, and the rule check reads the render."""
    project = a_project(engine)
    banning = campaign(banned=["music"])
    assert rules.enforce(Style(music="lm1", music_db=-18.0), banning).music is None
    assert rules.enforce(Style(music="lm1"), campaign(banned=["sfx"])).music == "lm1"
    bed = {"item_id": "l1", "title": "Night Drive", "artist": "Me", "db": -12.0, "credit": None}

    def music_check(render):
        return next(c for c in checks(banning, project, render=render) if c.key == "banned:music")

    found = music_check(a_render(tmp_path, music=bed))
    assert (found.label, found.ok) == ("No music", False)
    assert music_check(a_render(tmp_path)).ok


def test_hand_rule_passes_only_when_ticked(engine):
    project = a_project(engine)
    two = campaign(hand_rules=["No reaction content", "No watermark"])

    def ok(ticks):
        return [c.ok for c in checks(two, project, render=None, ticks=ticks) if c.kind == "hand"]

    assert ok([]) == [False, False]
    assert ok([rules.hand_key("No watermark")]) == [False, True]
    assert ok(["hand:5", "No reaction content", "hand:"]) == [False, False]
    hand = [c.label for c in checks(two, project, render=None) if c.kind == "hand"]
    assert hand == ["No reaction content", "No watermark"]


def test_hand_ticks_follow_the_rule_not_its_place(engine):
    """A tick is for the rule it was given for: a rule added above it, or one taken away, moves no tick."""
    project = a_project(engine)
    (given,) = [
        c
        for c in checks(campaign(hand_rules=["No reaction content"]), project, render=None)
        if c.kind == "hand"
    ]
    ticks = [given.key]
    added = campaign(hand_rules=["No AI voice", "No reaction content"])
    hand = {c.label: c.ok for c in checks(added, project, render=None, ticks=ticks) if c.kind == "hand"}
    assert hand == {"No AI voice": False, "No reaction content": True}
    gone = campaign(hand_rules=["No AI voice"])
    assert [c.ok for c in checks(gone, project, render=None, ticks=ticks) if c.kind == "hand"] == [False]
    assert (
        given.key == rules.hand_key("no reaction content ") and len(given.key) <= 20
    )  # a tick fits its field
