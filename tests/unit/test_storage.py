"""Storage (SPEC 16): what each kind of file takes, and the clean-up that deletes the big source files of
old YouTube projects, which can be fetched again, while keeping everything their clips were made from."""

import itertools
import os
import time

import pytest

from clipforge import storage
from clipforge.errors import ClipForgeError
from clipforge.jobs.stages import clips
from clipforge.models import ProjectOptions
from clipforge.post.variants import variant_ref
from tests.fakes import fake_engine
from tests.unit.test_clip_jobs import add_clips, ctx, project

DAY = 86400.0
SOURCE = 1000 + 500 + 200  # video, audio and sections
WORK = 300 + 50  # the 16 kHz audio and the transcript
FREED = SOURCE + 300  # all but the transcript
VIDEO_IDS = (f"storage{n:04d}" for n in itertools.count())  # a project of its own each


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def write(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)
    return path


def seed(pp):
    write(pp.source / "video.mp4", 1000)
    write(pp.source / "audio.m4a", 500)
    write(pp.sections / "s1.mp4", 200)
    write(pp.audio16k, 300)
    write(pp.transcript_json, 50)
    write(pp.clips / "c1" / "render.mp4", 70)


def settled(engine, *, status="ready", kind="youtube", age_days=30.0):
    """A project with its source files, no jobs, last changed ``age_days`` ago."""
    (p,), _ = engine.add_sources(f"https://www.youtube.com/watch?v={next(VIDEO_IDS)}", ProjectOptions())
    pid = p.id
    engine.runner.delete_jobs([pid])
    with engine.db() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, source_kind = ?, updated_at = ? WHERE id = ?",
            (status, kind, time.time() - age_days * DAY, pid),
        )
    seed(engine.paths.project(pid))
    return pid


def age(path, days):
    when = time.time() - days * DAY
    for p in [path, *path.rglob("*")]:
        os.utime(p, (when, when))


def kept(engine, pid):
    return (engine.paths.project(pid).source / "video.mp4").exists()


def test_usage_counts_each_kind(engine):
    pp = engine.paths.project(project(engine))
    before = storage.usage(engine.paths)
    seed(pp)
    write(engine.paths.gameplay / "g.mp4", 11)
    write(engine.paths.models / "m.bin", 13)
    write(engine.paths.logs / "x.log", 17)
    write(engine.paths.uploads / "u1" / "a.mp4", 19)
    after = storage.usage(engine.paths)
    assert after.sources - before.sources == SOURCE
    assert after.work - before.work == WORK
    assert after.renders - before.renders == 70
    assert (after.library - before.library, after.assets - before.assets) == (11, 13)
    assert after.logs - before.logs >= 17
    assert after.uploads - before.uploads == 19
    assert 0 < after.free_bytes <= after.total_bytes


def test_cleanup_deletes_old_youtube_sources_only(engine):
    old, failed = settled(engine), settled(engine, status="failed")
    young = settled(engine, age_days=3)
    local = settled(engine, kind="file")
    busy = settled(engine, status="waiting")
    assert dict(storage.cleanable(engine.db(), engine.paths, engine.settings, time.time())) == {
        old: FREED,
        failed: FREED,
    }
    result = engine.cleanup()
    assert (result.projects, result.freed_bytes) == (2, 2 * FREED)
    for pid in (old, failed):
        pp = engine.paths.project(pid)
        assert not list(pp.source.glob("video.*")) and not list(pp.source.glob("audio.*"))
        assert not pp.sections.exists() and not pp.audio16k.exists()
        assert pp.transcript_json.exists() and (pp.clips / "c1" / "render.mp4").exists()
    assert all(kept(engine, pid) for pid in (young, local, busy))
    assert engine.cleanup().projects == 0


def test_cleanup_never_when_days_is_zero(engine):
    engine.update_settings(engine.settings.model_copy(update={"delete_sources_after_days": 0}))
    pid = settled(engine, age_days=400)
    assert engine.cleanup().projects == 0
    assert kept(engine, pid)


def test_cleanup_skips_projects_with_active_jobs(engine):
    waiting = settled(engine)
    engine.runner.enqueue("ingest.video", waiting)
    clipping = settled(engine)
    (cid,) = add_clips(engine, clipping)
    engine.runner.enqueue("clip.render", cid, {"project": clipping})
    assert engine.cleanup().projects == 0
    assert kept(engine, waiting) and kept(engine, clipping)


def test_cleanup_waits_for_an_accounts_version_of_a_clip(engine):
    """A version for an account renders from the project's video too, so it holds the video as the clip's
    own render does."""
    pid = settled(engine)
    (cid,) = add_clips(engine, pid)
    engine.runner.enqueue("clip.variant", variant_ref(cid, "a1"), {"clip_id": cid, "account_id": "a1"})
    assert engine.cleanup().projects == 0
    assert kept(engine, pid)


def test_cleanup_removes_orphan_uploads(engine):
    up = engine.paths.uploads
    orphan = write(up / "old1" / "a.mp4", 40).parent
    used = write(up / "old2" / "b.mp4", 50)
    recent = write(up / "new1" / "c.mp4", 60).parent
    age(orphan, 2)
    age(used.parent, 2)
    pid = settled(engine, kind="file")
    with engine.db() as conn:
        conn.execute("UPDATE projects SET source_url = ? WHERE id = ?", (used.as_uri(), pid))
    result = engine.cleanup()
    assert not orphan.exists() and used.exists() and recent.exists()
    assert (result.projects, result.freed_bytes) == (0, 40)


@pytest.mark.parametrize("stage", ["clip_source", "clip_render"])
def test_render_refuses_when_disk_is_low(engine, monkeypatch, stage):
    engine.update_settings(engine.settings.model_copy(update={"min_free_gb": 10.0}))
    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    monkeypatch.setattr(clips, "free_gb", lambda path: 0.5)
    with pytest.raises(ClipForgeError) as got:
        getattr(clips, stage)(engine, ctx(engine, cid, pid))
    assert (got.value.code, got.value.retryable) == ("low_disk", False)
    gb = engine.settings.min_free_gb
    assert (
        got.value.message
        == f"Free up some disk space: ClipForge needs at least {gb:g} GB free to make clips."
    )
