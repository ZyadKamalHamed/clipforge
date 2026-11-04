"""SPEC 6.2 and 14: every change to a clip, and every new set of clips, reaches the window as it happens."""

from dataclasses import replace

import pytest

from clipforge import repo
from clipforge.errors import ToolError
from clipforge.jobs.runner import job_event
from clipforge.jobs.stages import clips
from clipforge.models import ProjectOptions
from tests.fakes import FakeProvider, FakeSpeech, brain_handlers, fake_engine, lines
from tests.unit.test_api_clips import add_clips, clip_row, seeded
from tests.unit.test_clip_jobs import URL, OneFace, SectionTube
from tests.unit.test_render_job import UNREADABLE, ready_clip
from tests.unit.test_runner import drain

FIELDS = {"id", "project_id", "status", "current_render_id", "error", "updated_at", "discarded_at"}


@pytest.fixture
def engine():
    eng = fake_engine(youtube=SectionTube())
    eng.face_detector = OneFace()
    yield eng
    eng.close()


def clip_events(sub, cid):
    return [e.data for e in drain(sub) if e.topic == "clip" and e.data["id"] == cid]


def test_clip_changes_publish_clip_events(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    sub = engine.events.subscribe()
    assert engine.render_clip(cid)
    (asked,) = clip_events(sub, cid)
    assert set(asked) == FIELDS
    assert (asked["project_id"], asked["status"], asked["error"]) == (pid, "queued", None)

    assert engine.wait(60, clips=[cid])
    seen = clip_events(sub, cid)
    assert "rendering" in [e["status"] for e in seen]
    (row,) = repo.list_renders(engine.db(), cid)
    clip = repo.get_clip(engine.db(), cid)
    assert seen[-1] == clip.model_dump(include=FIELDS)  # the last event is the clip as it now is
    assert (seen[-1]["status"], seen[-1]["current_render_id"]) == ("rendered", row.id)

    def broken(edl, src, out, **kw):
        raise ToolError("decode_failed", UNREADABLE)

    monkeypatch.setattr(clips, "render_video", broken)
    assert engine.render_clip(cid)
    assert engine.wait(60, clips=[cid])
    seen = clip_events(sub, cid)
    assert seen[0]["status"] == "queued"
    assert (seen[-1]["status"], seen[-1]["error"], seen[-1]["current_render_id"]) == (
        "failed",
        UNREADABLE,
        row.id,
    )


def order(events, pid):
    """The brain's jobs finishing and the clips events, in the order they came."""
    out = []
    for e in events:
        if e.topic == "job" and e.data["kind"].startswith("brain.") and e.data["status"] == "done":
            out.append(e.data["kind"] + " done")
        elif e.topic == "clips":
            assert e.data == {"project_id": pid}
            out.append("clips")
    return out


def test_select_and_package_publish_clips_events():
    engine = fake_engine(
        speech=FakeSpeech(lines(36)), provider=FakeProvider(brain_handlers()), auto_render="none"
    )
    try:
        sub = engine.events.subscribe()
        (project,), _ = engine.add_sources(URL, ProjectOptions())
        engine.start(project.id)
        assert engine.wait(60, projects=[project.id])
        expected = ["brain.select done", "clips", "brain.package done", "clips"]
        assert order(drain(sub), project.id) == expected
        assert engine.clips(project.id)

        assert engine.rerun_selection(project.id)  # a rerun replaces the ideas, so they come again
        assert engine.wait(60, projects=[project.id])
        assert order(drain(sub), project.id) == expected
    finally:
        engine.close()


def test_job_events_name_their_project():
    engine = fake_engine()
    try:
        sub = engine.events.subscribe()
        engine.runner.enqueue("clip.render", "c1", {"project": "p1"})
        engine.runner.enqueue("ingest.metadata", "p2", {"rerun": True})
        got = [(e.data["ref_id"], e.data["project"]) for e in drain(sub) if e.topic == "job"]
        assert got == [("c1", "p1"), ("p2", "p2")]
    finally:
        engine.close()


def test_a_rights_change_reaches_every_window():
    """Why a project's clips may be posted shows the same in every window, so a change to it rides the
    project's event like its status does (SPEC 6.2)."""
    engine = fake_engine()
    try:
        pid = seeded(engine)
        sub = engine.events.subscribe()
        engine.update_project(pid, rights_basis="permission")
        (project,) = [e.data for e in drain(sub) if e.topic == "project"]
        assert (project["id"], project["rights_basis"]) == (pid, "permission")
    finally:
        engine.close()


def test_events_carry_text_cleaned_like_the_api():
    """Event text reaches the window as the API's JSON does: control characters as spaces and no marks
    that turn text around, whatever a tool or a video put in it (SPEC 14.1)."""
    hostile = "bad\x07" + chr(0x202E) + "gnp.exe" + chr(0x2066) + " news"  # a bell, and two turn marks
    engine = fake_engine()
    try:
        pid = seeded(engine)
        add_clips(engine, pid, clip_row(pid, "c1", 1))
        sub = engine.events.subscribe()
        engine.update_project(pid, message=hostile, error=hostile, error_details=hostile)
        engine.update_clip("c1", error=hostile)
        job_id = engine.runner.enqueue("ingest.metadata", pid, rerun=True)
        events = drain(sub)
        (project,) = [e.data for e in events if e.topic == "project"]
        (clip,) = [e.data for e in events if e.topic == "clip"]
        cleaned = "bad gnp.exe news"
        assert (project["message"], project["error"], project["error_details"]) == (cleaned,) * 3
        assert clip["error"] == cleaned
        assert job_event(replace(engine.runner.get(job_id), message=hostile))["message"] == cleaned
    finally:
        engine.close()
