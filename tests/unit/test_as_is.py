"""Post as it is (SPEC 14.2): a project whose videos post as they are. Its pipeline is the metadata, the
video and making the clip, nothing else. The clip is the whole video, so nothing edits or renders it, no
account needs a version of its own, a campaign's banned elements pass (ClipForge added none), and its card
and steps say so."""

import json
import time

import pytest

from clipforge import repo
from clipforge.campaigns.rules import checks_for
from clipforge.errors import ClipForgeError
from clipforge.jobs import pipeline
from clipforge.jobs.pipeline import AS_IS, FLOW, advance, flow_for
from clipforge.models import ProjectOptions
from clipforge.post.models import PostOptions
from clipforge.post.variants import needs_variant
from clipforge.render.styles import StyleKit
from clipforge.server.routes.projects import parse_options
from clipforge.server.views import clip_card, project_steps
from tests.fakes import fake_engine
from tests.posting import add_account, add_campaign, add_clip, confirm, linked_clip
from tests.server import api_client

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture
def engine():
    """An engine whose workers never start: each test ends jobs itself and advances the project."""
    eng = fake_engine()
    yield eng
    eng.close()


def as_is(engine, clip):
    """``clip`` made the whole video as it is, as asis.make leaves it."""
    repo.update_clip(engine.db(), clip.id, mode="as_is", status="rendered")
    return repo.get_clip(engine.db(), clip.id)


def settle(engine, pid: str, kind: str, status: str = "done", *, result=None) -> None:
    """End the newest job of ``kind`` as a worker would, then advance the project as on_finished does."""
    job = [j for j in engine.runner.jobs_for(pid) if j.kind == kind][-1]
    engine.db().execute(
        "UPDATE jobs SET status = ?, result_json = ?, finished_at = ? WHERE id = ?",
        (status, None if result is None else json.dumps(result), time.time(), job.id),
    )
    advance(engine, pid)


def kinds(engine, pid: str) -> list[str]:
    return [j.kind for j in engine.runner.jobs_for(pid)]


# Adding a video again, as every message about the option says to


def test_adding_a_link_again_with_as_is_flipped_makes_a_new_project(engine):
    (cut,), _ = engine.add_sources(URL, ProjectOptions())
    (whole,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    assert whole.id != cut.id
    assert whole.options.as_is and not cut.options.as_is
    (again,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    assert again.id == whole.id  # the same choice again is still the project you have
    (back,), _ = engine.add_sources(URL, ProjectOptions())
    assert back.id == cut.id


def test_flipping_as_is_shares_the_video_already_downloaded(engine):
    (cut,), _ = engine.add_sources(URL, ProjectOptions())
    source = engine.paths.project(cut.id).source
    source.mkdir(parents=True, exist_ok=True)
    (source / "video.mp4").write_bytes(b"the video")
    (whole,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    assert whole.id != cut.id
    assert (engine.paths.project(whole.id).source / "video.mp4").read_bytes() == b"the video"


# The option and the flow


def test_post_as_is_is_off_unless_asked():
    assert ProjectOptions().as_is is False
    assert parse_options({"as_is": True}).as_is is True


def test_as_is_flow_is_the_metadata_the_video_and_the_clip():
    assert [(s.kind, s.lane, list(s.after), s.required) for s in AS_IS.stages] == [
        ("ingest.metadata", "io", [], True),
        ("ingest.video", "io", ["ingest.metadata"], True),  # the video is the clip, so it can't be skipped
        ("asis.make", "cpu", ["ingest.video"], True),
    ]
    assert AS_IS.last == "asis.make"
    assert sum(AS_IS.weights.values()) == pytest.approx(1.0)
    assert set(AS_IS.weights) == {s.kind for s in AS_IS.stages}


def test_flow_for_follows_the_option(engine):
    (plain,), _ = engine.add_sources(URL, ProjectOptions())
    (whole,), _ = engine.add_sources(URL, ProjectOptions(as_is=True), new=True)
    assert flow_for(plain) is FLOW
    assert flow_for(whole) is AS_IS


def test_as_is_project_gets_the_video_then_makes_the_clip_and_is_ready(engine):
    (project,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    engine.start(project.id)
    assert kinds(engine, project.id) == ["ingest.metadata"]
    settle(engine, project.id, "ingest.metadata", result={"video": False})  # wanted or not, it downloads
    assert kinds(engine, project.id) == ["ingest.metadata", "ingest.video"]
    assert engine.project(project.id).stage == "ingest.video"
    assert engine.project(project.id).progress == pytest.approx(AS_IS.weights["ingest.metadata"])
    settle(engine, project.id, "ingest.video")
    assert kinds(engine, project.id) == ["ingest.metadata", "ingest.video", "asis.make"]
    settle(engine, project.id, "asis.make")
    done = engine.project(project.id)
    assert (done.status, done.stage, done.progress) == ("ready", None, 1.0)
    assert "ingest.audio" not in kinds(engine, project.id)


def test_as_is_project_stops_when_the_video_fails(engine):
    (project,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    engine.start(project.id)
    settle(engine, project.id, "ingest.metadata", result={"video": False})
    settle(engine, project.id, "ingest.video", "failed")
    stopped = engine.project(project.id)
    assert (stopped.status, stopped.stage) == ("failed", "ingest.video")
    assert "asis.make" not in kinds(engine, project.id)


def test_as_is_steps_are_metadata_download_and_make(engine):
    (project,), _ = engine.add_sources(URL, ProjectOptions(as_is=True))
    engine.start(project.id)
    settle(engine, project.id, "ingest.metadata", result={"video": False})
    steps = project_steps(engine.project(project.id), engine.runner.jobs_for(project.id), [])
    assert [(s["key"], s["label"], s["status"]) for s in steps] == [  # a queued job's step reads todo
        ("metadata", "Metadata", "done"),
        ("download", "Download", "todo"),
        ("make", "Make the clip", "todo"),
    ]


# The clip


def test_as_is_clip_refuses_edits_and_renders(engine):
    clip = as_is(engine, add_clip(engine))
    for attempt in (
        lambda: engine.render_clip(clip.id),
        lambda: engine.save_edits(clip.id, {"layout": "fit"}),
        lambda: engine.shuffle_gameplay(clip.id),
    ):
        with pytest.raises(ClipForgeError) as err:
            attempt()
        assert err.value.code == "as_is"
        assert "as it is" in err.value.message
    assert pipeline.queue_render(engine.runner, engine.db(), clip.id) is False
    assert engine.runner.jobs_for(clip.id) == []
    assert repo.get_clip(engine.db(), clip.id).edits is None


def test_as_is_clip_api_says_why_it_cant_change(engine):
    clip = as_is(engine, add_clip(engine))
    with api_client(engine) as client:
        for method, path, body in (
            ("PATCH", f"/api/clips/{clip.id}", {"edits": {"layout": "fit"}}),
            ("POST", f"/api/clips/{clip.id}/render", None),
            ("POST", f"/api/clips/{clip.id}/gameplay/shuffle", None),
            ("POST", f"/api/clips/{clip.id}/voiceover?name=take.wav", None),
        ):
            got = client.request(method, path, json=body) if body else client.request(method, path)
            assert got.status_code == 409, (path, got.text)
            assert got.json()["error"] == "as_is"
    assert engine.runner.jobs_for(clip.id) == []


def test_no_account_needs_its_own_version_of_an_as_is_clip(engine):
    clip = add_clip(engine)
    account = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    assert needs_variant(engine, clip, account) is True  # a clip ClipForge made takes the kit's look
    assert needs_variant(engine, as_is(engine, clip), account) is False


def test_as_is_clip_passes_banned_element_checks(engine):
    campaign = add_campaign(engine, banned=["music", "hook", "credit_line"])
    clip = as_is(engine, linked_clip(engine, campaign))
    render = repo.get_render(engine.db(), clip.current_render_id)
    found = checks_for(
        campaign,
        project=engine.project(clip.project_id),
        clip=clip,
        render=render,  # no edit list: nothing was added to the video
        account=add_account(engine),
        body="",
        hashtags=[],
        options=PostOptions(disclosure=True),
        ticks=[],
    )
    banned = [c for c in found if c.key.startswith("banned:")]
    assert [(c.key, c.ok) for c in banned] == [
        ("banned:music", True),
        ("banned:hook", True),
        ("banned:credit_line", True),
    ]


def test_as_is_card_says_so(engine):
    made = add_clip(engine)
    assert clip_card(engine, made)["as_is"] is False
    assert clip_card(engine, as_is(engine, made))["as_is"] is True


def test_an_as_is_clip_posts_from_its_render(engine):
    clip = as_is(engine, add_clip(engine))
    account = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    _, (post,) = confirm(engine, [clip], [account])
    assert post.render_id == clip.current_render_id
    assert engine.runner.jobs_for(clip.id) == []  # nothing renders first
