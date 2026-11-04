"""Each account's own version of a clip (Phase 5g, SPEC 11): a post whose account wears a style kit the
clip's render doesn't, or whose gameplay stretch another account's post has shown, goes up with a render
made for that account, which never becomes the clip's own."""

import shutil
import time
from pathlib import Path

import pytest

from clipforge import db, repo
from clipforge.db import connect, migrate
from clipforge.errors import ClipForgeError
from clipforge.jobs.stages import clips
from clipforge.library import gameplay
from clipforge.models import ClipRow, ProjectOptions
from clipforge.post import store
from clipforge.post.export import VARIANT_RENDERING
from clipforge.post.plan import RENDERS_VARIANT
from clipforge.post.variants import (
    VARIANT_FAILED,
    needs_variant,
    queue_variant,
    variant_for,
    variant_ref,
    wanted_style,
)
from clipforge.render.styles import StyleKit, style_for, style_key
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.helpers import make_test_video
from tests.posting import add_account, at, claim, custom, jobs, request, run, sydney
from tests.server import api_client
from tests.unit.test_clip_jobs import OneFace, SectionTube
from tests.unit.test_render_job import edl_of, job, ready_clip

EPS = 1e-6


@pytest.fixture
def clock():
    return Clock(at(6))


@pytest.fixture
def tiktok():
    return FakePublisher()


@pytest.fixture
def engine(clock, tiktok):
    eng = fake_engine(youtube=SectionTube(), now=clock, publishers={"tiktok:browser": tiktok})
    eng.face_detector = OneFace()
    yield eng
    eng.close()


def rendered(engine, **edits):
    """A clip with its own render made, as the clip's render job makes it."""
    pid, cid = ready_clip(engine, edits=edits or None)
    clips.clip_render(engine, job(engine, cid, pid))
    return repo.get_clip(engine.db(), cid)


def render_variant(engine, clip, account):
    """The account's queued version of ``clip`` rendered as a worker runs it: the job after."""
    queued = claim(engine, variant_ref(clip.id, account.id))
    engine.runner._run(engine.db(), queued)
    return engine.runner.get(queued.id)


def notes(engine, clip, account):
    return [w.sentence for w in engine.posting.plan(request([clip], [account])).warnings]


def test_second_account_gets_its_own_gameplay_stretch(engine, clock, tiktok, tmp_path):
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    clip = rendered(engine, layout="gameplay")
    own = repo.get_render(engine.db(), clip.current_render_id)
    a, b = add_account(engine), add_account(engine)
    first, second = custom(engine, clip, a, at(7, 10)), custom(engine, clip, b, at(7, 40))

    clock.set(at(7, 10))
    _, p1 = run(engine, first.id)
    assert (p1.status, p1.render_id) == ("posted", own.id)
    (shown,) = [u for u in repo.gameplay_uses(engine.db(), item.id) if u.post_id is not None]
    assert (shown.clip_id, shown.account_id, shown.post_id) == (clip.id, a.id, first.id)
    assert shown.start_s == pytest.approx(edl_of(own.path).gameplay["start_s"])
    assert needs_variant(engine, clip, b) and not needs_variant(engine, clip, a)

    clock.set(at(7, 40))
    waiting, p2 = run(engine, second.id)
    assert (p2.status, waiting.status, len(tiktok.published)) == ("rendering", "queued", 1)
    assert waiting.message == "Waiting for the clip to finish rendering."
    (queued,) = jobs(engine, variant_ref(clip.id, b.id))
    assert (queued.kind, queued.payload) == ("clip.variant", {"clip_id": clip.id, "account_id": b.id})
    assert render_variant(engine, clip, b).status == "done"

    _, p2 = run(engine, second.id)
    mine = repo.get_render(engine.db(), p2.render_id)
    assert p2.status == "posted" and tiktok.published[-1].render_id == mine.id
    assert (mine.account_id, mine.id != own.id) == (b.id, True)
    edl = edl_of(mine.path)
    start, end = edl.gameplay["start_s"], edl.gameplay["start_s"] + edl.duration
    assert edl.gameplay["item_id"] == item.id
    assert end <= shown.start_s + EPS or start >= shown.end_s - EPS  # nothing post 1 showed
    (posted,) = [u for u in repo.gameplay_uses(engine.db(), item.id) if u.post_id == second.id]
    assert (posted.account_id, posted.start_s) == (b.id, pytest.approx(start))
    assert repo.get_clip(engine.db(), clip.id).current_render_id == own.id


def test_an_exported_post_counts_its_gameplay_stretch(engine, tmp_path):
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    clip = rendered(engine, layout="gameplay")
    own = repo.get_render(engine.db(), clip.current_render_id)
    a, b = add_account(engine), add_account(engine)
    first = custom(engine, clip, a, at(7, 10))
    assert api_client(engine).post(f"/api/posts/{first.id}/export").status_code == 200
    exported = store.get_post(engine.db(), first.id)
    assert (exported.status, exported.render_id) == ("exported", own.id)
    (shown,) = [u for u in repo.gameplay_uses(engine.db(), item.id) if u.post_id is not None]
    assert (shown.clip_id, shown.account_id, shown.post_id) == (clip.id, a.id, first.id)
    assert shown.start_s == pytest.approx(edl_of(own.path).gameplay["start_s"])
    assert needs_variant(engine, clip, b) and not needs_variant(engine, clip, a)


def test_kit_that_changes_nothing_renders_nothing(engine, clock, tiktok):
    clip = rendered(engine)
    a = add_account(engine, style_kit=StyleKit(preset=style_for(engine.settings, {}).preset))
    assert not needs_variant(engine, clip, a)
    assert RENDERS_VARIANT not in notes(engine, clip, a)
    p = custom(engine, clip, a, at(7, 10))
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert (after.status, after.render_id) == ("posted", clip.current_render_id)
    assert engine.runner.jobs_for(variant_ref(clip.id, a.id)) == []


def test_kit_change_renders_again(engine, clock, tiktok):
    clip = rendered(engine)
    a = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    assert RENDERS_VARIANT in notes(engine, clip, a)
    p = custom(engine, clip, a, at(7, 10))
    clock.set(at(7, 10))
    assert run(engine, p.id)[1].status == "rendering"
    assert render_variant(engine, clip, a).status == "done"
    first = variant_for(engine, clip, a)
    assert first is not None and first.account_id == a.id
    assert first.style_key == style_key(wanted_style(engine, clip, a))

    kit = StyleKit(accent="#FF00AA")  # changed before the post is due again
    a = store.update_account(engine.db(), a.id, settings=a.settings.model_copy(update={"style_kit": kit}))
    assert variant_for(engine, clip, a) is None
    assert run(engine, p.id)[1].status == "rendering"
    assert render_variant(engine, clip, a).status == "done"
    _, after = run(engine, p.id)
    second = repo.get_render(engine.db(), after.render_id)
    assert after.status == "posted" and second.account_id == a.id and second.id != first.id
    assert edl_of(second.path).style.accent == "#FF00AA"


def test_variant_never_becomes_current(engine, monkeypatch):
    clip = rendered(engine)
    a = add_account(engine, style_kit=StyleKit(preset="neon"))
    seen = []
    real = clips.make_cover

    def spy(*args):
        seen.append(repo.get_clip(engine.db(), clip.id).status)
        return real(*args)

    monkeypatch.setattr(clips, "make_cover", spy)
    assert queue_variant(engine, clip, a) is not None
    assert render_variant(engine, clip, a).status == "done"
    after = repo.get_clip(engine.db(), clip.id)
    assert seen == [clip.status]  # the clip doesn't say it's rendering while an account's version is
    assert (after.current_render_id, after.status, after.layout) == (
        clip.current_render_id,
        clip.status,
        clip.layout,
    )
    assert [r.account_id for r in repo.list_renders(engine.db(), clip.id)] == [None, a.id]
    shown = api_client(engine).get(f"/api/clips/{clip.id}").json()["renders"]
    assert [r["id"] for r in shown] == [clip.current_render_id]  # the editor lists the clip's own


def test_failed_variant_fails_the_post(engine, clock, tiktok, monkeypatch):
    clip = rendered(engine)
    a = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    p = custom(engine, clip, a, at(7, 10))
    clock.set(at(7, 10))
    assert run(engine, p.id)[1].status == "rendering"
    real = clips.make_cover

    def broken(*args):
        raise ClipForgeError("cover_broke", "The picture broke.")

    monkeypatch.setattr(clips, "make_cover", broken)
    failed = render_variant(engine, clip, a)
    assert (failed.status, failed.error) == ("failed", "The picture broke.")
    _, after = run(engine, p.id)
    assert (after.status, after.error_code) == ("failed", "variant_failed") and not tiktok.published
    assert after.error == VARIANT_FAILED.format(error="The picture broke.")

    monkeypatch.setattr(clips, "make_cover", real)
    retried = engine.posting.retry(p.id)
    clock.set(sydney(retried.scheduled_at))
    assert run(engine, p.id)[1].status == "rendering"  # Retry renders it again
    (again,) = jobs(engine, variant_ref(clip.id, a.id))
    assert again.id != failed.id
    assert render_variant(engine, clip, a).status == "done"
    assert run(engine, p.id)[1].status == "posted"


def test_a_version_whose_gameplay_left_as_it_started_renders_again(
    engine, clock, tiktok, tmp_path, monkeypatch
):
    """The gameplay video a version picked is deleted before it records its use: the version is queued to
    run again, as its sentence says, and its post waits for it rather than failing."""
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    clip = rendered(engine, layout="gameplay")
    a = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    p = custom(engine, clip, a, at(7, 10))
    clock.set(at(7, 10))
    assert run(engine, p.id)[1].status == "rendering"
    pick, picks = gameplay.pick_item, []

    def first_one_deleted(conn, clip_id, **kw):
        got = pick(conn, clip_id, **kw)
        if got is not None and not picks:
            repo.delete_library_item(conn, got.id)
        picks.append(got)
        return got

    monkeypatch.setattr(gameplay, "pick_item", first_one_deleted)
    first = render_variant(engine, clip, a)
    assert (first.status, first.error) == ("queued", clips.ITEM_LEFT)
    assert run(engine, p.id)[1].status == "rendering"  # its post still waits for it
    assert render_variant(engine, clip, a).status == "done"
    assert run(engine, p.id)[1].status == "posted" and len(tiktok.published) == 1


def test_migration_012(tmp_path, monkeypatch):
    c = connect(tmp_path / "old.db")
    everything = db._migrations
    monkeypatch.setattr(db, "_migrations", lambda: [m for m in everything() if m[0] <= 11])
    assert migrate(c) == 11
    project = repo.create_project(
        c,
        source_kind="youtube",
        source_url="https://www.youtube.com/watch?v=arj7oStGLkU",
        video_id="v1",
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
        title="A talk",
    )
    repo.replace_ideas(c, project.id, [ClipRow(id="c1", project_id=project.id, rank=1, start_s=0, end_s=5)])
    for n in (1, 2):
        c.execute(
            "INSERT INTO renders (id, clip_id, version, style_key, path, status, created_at)"
            " VALUES (?, 'c1', ?, 'k', '/x.mp4', 'ready', 1)",
            (f"r{n}", n),
        )
    c.commit()
    monkeypatch.setattr(db, "_migrations", everything)
    assert migrate(c) == 13
    renders = repo.list_renders(c, "c1")
    assert [(r.id, r.version, r.account_id) for r in renders] == [("r1", 1, None), ("r2", 2, None)]
    c.close()


def test_export_renders_the_variant_first(engine):
    clip = rendered(engine)
    a = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    p = custom(engine, clip, a, at(7, 10))
    refused = api_client(engine).post(f"/api/posts/{p.id}/export")
    assert refused.status_code == 409
    assert refused.json()["message"] == VARIANT_RENDERING
    assert store.get_post(engine.db(), p.id).status == "scheduled"
    assert render_variant(engine, clip, a).status == "done"
    assert api_client(engine).post(f"/api/posts/{p.id}/export").status_code == 200
    exported = store.get_post(engine.db(), p.id)
    folder = engine.paths.exports / exported.state["export_dir"]
    assert (folder / "clip.mp4").read_bytes() == Path(variant_for(engine, clip, a).path).read_bytes()


def test_a_version_after_the_clean_up_downloads_its_stretch_again(engine, tmp_path):
    """The clean-up takes a YouTube project's video after 14 days (SPEC 16); a version for an account asked
    for after that downloads the clip's stretch back, as the clip's own render does, and doesn't fail."""
    clip = rendered(engine)
    pp = engine.paths.project(clip.project_id)
    kept = shutil.copy(next(pp.source.glob("video.*")), tmp_path / "source.mp4")
    repo.update_project(engine.db(), clip.project_id, status="ready")
    assert engine.cleanup(now=time.time() + 15 * 86400).projects == 1
    fetched = []

    def download_sections(url, dest_dir, ranges, quality, *, cancelled=None):
        fetched.extend(ranges)
        return [shutil.copy(kept, Path(dest_dir) / f"video.{a}-{b}.mp4") for a, b in ranges]

    engine.youtube.download_sections = download_sections
    account = add_account(engine, style_kit=StyleKit(accent="#00E5FF"))
    queue_variant(engine, repo.get_clip(engine.db(), clip.id), account)
    done = render_variant(engine, clip, account)
    assert (done.status, done.error_code) == ("done", None)
    assert fetched == [(0.0, 9.5)]  # the clip's 1 s to 4.5 s, with the 5 s either side a section has
