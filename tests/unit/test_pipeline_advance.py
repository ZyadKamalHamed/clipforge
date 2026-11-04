import json
import time

import pytest

from clipforge.config import Settings
from clipforge.jobs.pipeline import REFUSED_CODES, STAGES, WATCHED_PRIORITY, WEIGHTS, advance
from clipforge.jobs.stages.ingest import video_wanted
from clipforge.models import ProjectOptions, SourceMeta
from clipforge.watch import store
from clipforge.watch.models import ClipChannel
from tests.fakes import fake_engine

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
BEFORE_SELECTION = ("ingest.audio", "ingest.captions", "ingest.extras", "asr.transcribe", "signals.compute")


@pytest.fixture
def engine():
    """An engine whose workers never start: each test ends jobs itself and advances the project."""
    eng = fake_engine()
    yield eng
    eng.close()


def started(engine, **options) -> str:
    (project,), _ = engine.add_sources(URL, ProjectOptions(**options))
    engine.start(project.id)
    return project.id


def jobs(engine, pid: str) -> list[tuple[str, str]]:
    """Each of the project's jobs as (kind, status), oldest first."""
    return [(j.kind, j.status) for j in engine.runner.jobs_for(pid)]


def kinds(engine, pid: str) -> list[str]:
    return [kind for kind, _ in jobs(engine, pid)]


def settle(engine, pid: str, kind: str, status: str = "done", *, result=None, code=None, error=None) -> None:
    """End the newest job of ``kind`` as a worker would, then advance the project as on_finished does."""
    job = [j for j in engine.runner.jobs_for(pid) if j.kind == kind][-1]
    engine.db().execute(
        "UPDATE jobs SET status = ?, result_json = ?, error_code = ?, error = ?, finished_at = ? "
        "WHERE id = ?",
        (status, None if result is None else json.dumps(result), code, error, time.time(), job.id),
    )
    advance(engine, pid)


def selected(engine, **options) -> str:
    """A project whose selection has just finished."""
    pid = started(engine, **options)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    for kind in (*BEFORE_SELECTION, "brain.select"):
        settle(engine, pid, kind)
    return pid


def meta(**fields) -> SourceMeta:
    base = {
        "source_kind": "youtube",
        "video_id": "arj7oStGLkU",
        "title": "A talk",
        "channel": "TED",
        "channel_url": None,
        "duration_s": 600.0,
        "video_bytes": {"best": 2_000_000_000, "balanced": 900_000_000, "fast": None},
    }
    return SourceMeta(**(base | fields))


# The stages


def test_stages_in_order_with_their_lanes_and_prerequisites():
    assert [(s.kind, s.lane, list(s.after), s.required) for s in STAGES] == [
        ("ingest.metadata", "io", [], True),
        ("ingest.audio", "io", ["ingest.metadata"], True),
        ("ingest.captions", "io", ["ingest.metadata"], False),
        ("ingest.extras", "io", ["ingest.metadata"], False),
        ("ingest.video", "io", ["ingest.metadata"], False),
        ("asr.transcribe", "cpu", ["ingest.audio", "ingest.captions"], True),
        ("signals.compute", "cpu", ["asr.transcribe", "ingest.extras"], True),
        ("brain.select", "llm", ["signals.compute"], True),
        ("brain.package", "llm", ["brain.select"], True),
    ]
    assert WEIGHTS == {
        "ingest.metadata": 0.02,
        "ingest.audio": 0.10,
        "ingest.captions": 0.01,
        "ingest.extras": 0.02,
        "asr.transcribe": 0.35,
        "signals.compute": 0.05,
        "brain.select": 0.30,
        "brain.package": 0.15,
    }
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
    assert {"live", "processing", "upcoming", "private", "members", "age", "unavailable"} <= REFUSED_CODES
    assert "too_long" in REFUSED_CODES and "network" not in REFUSED_CODES


def test_video_wanted_only_when_rendering_a_download_of_known_size_that_fits():
    settings = Settings()  # best quality, at most 2.5 GB
    assert video_wanted(meta(), ProjectOptions(), settings)
    assert not video_wanted(meta(), ProjectOptions(render=False), settings)
    # a quality whose size is unknown: only the clips download
    assert not video_wanted(meta(), ProjectOptions(quality="fast"), settings)
    big = meta(video_bytes={"best": 3_000_000_000})
    assert not video_wanted(big, ProjectOptions(), settings)
    assert video_wanted(big, ProjectOptions(), Settings(full_download_max_gb=4.0))
    balanced = Settings(download_quality="balanced", full_download_max_gb=1.0)
    assert video_wanted(meta(), ProjectOptions(), balanced)
    # a file is the video already
    assert not video_wanted(meta(source_kind="file", video_bytes={}), ProjectOptions(), settings)


def test_a_drive_video_downloads_whole_unless_known_to_be_too_big():
    settings = Settings()
    # Drive serves whole files, so a size it doesn't give is no reason to fetch the clips one by one
    assert video_wanted(meta(source_kind="drive"), ProjectOptions(quality="fast"), settings)
    assert not video_wanted(
        meta(source_kind="drive", video_bytes={"best": 3_000_000_000}), ProjectOptions(), settings
    )
    assert not video_wanted(meta(source_kind="drive"), ProjectOptions(render=False), settings)


# Advancing


def test_start_queues_the_metadata_first(engine):
    pid = started(engine)
    assert jobs(engine, pid) == [("ingest.metadata", "queued")]
    project = engine.project(pid)
    assert (project.status, project.stage, project.progress) == ("queued", "ingest.metadata", 0.0)
    engine.start(pid)
    assert jobs(engine, pid) == [("ingest.metadata", "queued")]  # starting again queues nothing new


def test_advance_queues_the_downloads_after_the_metadata(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": True})
    assert jobs(engine, pid)[1:] == [
        ("ingest.audio", "queued"),
        ("ingest.captions", "queued"),
        ("ingest.extras", "queued"),
        ("ingest.video", "queued"),
    ]
    project = engine.project(pid)
    assert project.stage == "ingest.audio" and project.progress == pytest.approx(0.02)


def test_advance_leaves_the_video_out_when_the_metadata_says_so(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    assert kinds(engine, pid) == ["ingest.metadata", "ingest.audio", "ingest.captions", "ingest.extras"]


def test_advance_waits_for_optional_prereqs(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    settle(engine, pid, "ingest.audio")
    assert "asr.transcribe" not in kinds(engine, pid)  # the captions are still queued
    assert engine.project(pid).progress == pytest.approx(0.12)
    settle(engine, pid, "ingest.captions", "failed", code="internal", error="Something went wrong.")
    assert jobs(engine, pid)[-1] == ("asr.transcribe", "queued")
    project = engine.project(pid)
    # an optional stage never fails the project
    assert project.status == "queued" and project.stage == "asr.transcribe"
    assert project.progress == pytest.approx(0.13)


def test_advance_transcribes_without_waiting_for_the_comments_and_chat(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    settle(engine, pid, "ingest.audio")
    settle(engine, pid, "ingest.captions")
    assert jobs(engine, pid)[-1] == ("asr.transcribe", "queued")
    assert dict(jobs(engine, pid))["ingest.extras"] == "queued"  # a long chat replay is still coming
    settle(engine, pid, "asr.transcribe")
    assert "signals.compute" not in kinds(engine, pid)  # the signals read the comments and chat
    project = engine.project(pid)
    assert project.stage == "ingest.extras" and project.progress == pytest.approx(0.48)
    settle(engine, pid, "ingest.extras", "failed", code="internal", error="Something went wrong.")
    assert jobs(engine, pid)[-1] == ("signals.compute", "queued")
    project = engine.project(pid)
    assert project.stage == "signals.compute" and project.progress == pytest.approx(0.50)


def test_advance_marks_failed_on_required_failure(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    settle(engine, pid, "ingest.extras")
    settle(engine, pid, "ingest.audio", "failed", code="ytdlp_failed", error="YouTube download failed.")
    project = engine.project(pid)
    assert (project.status, project.stage) == ("failed", "ingest.audio")
    assert (project.error_code, project.error) == ("ytdlp_failed", "YouTube download failed.")
    assert "asr.transcribe" not in kinds(engine, pid)


def test_advance_marks_refused(engine):
    note = "This video is over 6 hours. Download the part you want and drop the file in."
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", "failed", code="too_long", error=note)
    project = engine.project(pid)
    assert (project.status, project.error_code, project.error) == ("refused", "too_long", note)
    assert jobs(engine, pid) == [("ingest.metadata", "failed")]


def test_start_tries_the_failed_stage_again(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    settle(engine, pid, "ingest.extras", "failed", code="internal", error="Something went wrong.")
    settle(engine, pid, "ingest.audio", "failed", code="network", error="Network trouble.")
    engine.start(pid)
    project = engine.project(pid)
    assert (project.status, project.error_code, project.error) == ("queued", None, None)
    assert jobs(engine, pid)[-1] == ("ingest.audio", "queued")
    assert kinds(engine, pid).count("ingest.extras") == 1  # a failed optional stage stays as it is


def test_advance_ends_ready_after_packaging(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": True})
    for kind in (*BEFORE_SELECTION, "brain.select"):
        settle(engine, pid, kind)
    assert engine.project(pid).stage == "brain.package"
    assert engine.project(pid).progress == pytest.approx(0.85)
    settle(engine, pid, "brain.package")
    project = engine.project(pid)
    assert (project.status, project.stage, project.progress) == ("ready", None, 1.0)
    assert dict(jobs(engine, pid))["ingest.video"] == "queued"  # the video carries on in the background


def test_rerun_selection_runs_the_packaging_again_after_it(engine):
    pid = selected(engine)
    settle(engine, pid, "brain.package")
    engine.rerun_selection(pid)
    assert jobs(engine, pid)[-1] == ("brain.select", "queued")
    project = engine.project(pid)
    assert (project.status, project.stage) == ("queued", "brain.select")
    assert project.progress == pytest.approx(0.55)
    advance(engine, pid)
    assert kinds(engine, pid).count("brain.package") == 1  # the packaging waits for the new selection
    settle(engine, pid, "brain.select")
    assert jobs(engine, pid)[-1] == ("brain.package", "queued")
    assert engine.project(pid).status == "queued"
    settle(engine, pid, "brain.package")
    assert engine.project(pid).status == "ready"


def test_rerun_selection_wakes_a_waiting_selection(engine):
    pid = started(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    for kind in BEFORE_SELECTION:
        settle(engine, pid, kind)
    (select,) = [j for j in engine.runner.jobs_for(pid) if j.kind == "brain.select"]
    engine.db().execute("UPDATE jobs SET not_before = ? WHERE id = ?", (time.time() + 3600, select.id))
    engine.update_project(pid, status="waiting", message="Claude's usage limit is reached.")
    engine.rerun_selection(pid, provider="heuristic")
    assert engine.runner.get(select.id).not_before == 0
    assert kinds(engine, pid).count("brain.select") == 1
    project = engine.project(pid)
    assert (project.status, project.message, project.options.provider) == ("queued", None, "heuristic")


def test_rerun_selection_before_the_selection_only_changes_the_provider(engine):
    pid = started(engine)
    engine.rerun_selection(pid, provider="heuristic")
    assert jobs(engine, pid) == [("ingest.metadata", "queued")]  # the pipeline gets there on its own
    assert engine.project(pid).options.provider == "heuristic"


# A clipping channel's projects (SPEC 10.9)


def watched(engine) -> str:
    """A project a clipping channel started, set going."""
    channel = store.add_channel(
        engine.db(), ClipChannel(id="cc1", name="Podcasts", created_at=0, updated_at=0)
    )
    (project,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    engine.update_project(project.id, clip_channel_id=channel.id)
    engine.start(project.id)
    return project.id


def test_watched_jobs_queue_behind_hand_started(engine):
    later = watched(engine)
    (hand,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    engine.start(hand.id)
    (behind,) = engine.runner.jobs_for(later)
    (ahead,) = engine.runner.jobs_for(hand.id)
    assert (behind.kind, behind.priority, ahead.priority) == ("ingest.metadata", WATCHED_PRIORITY, 0)
    assert engine.runner._claim(engine.db(), ["ingest.metadata"]).id == ahead.id


def test_a_watched_projects_later_stages_stay_behind(engine):
    pid = watched(engine)
    settle(engine, pid, "ingest.metadata", result={"video": False})
    later = [j for j in engine.runner.jobs_for(pid) if j.kind != "ingest.metadata"]
    assert later and {j.priority for j in later} == {WATCHED_PRIORITY}
