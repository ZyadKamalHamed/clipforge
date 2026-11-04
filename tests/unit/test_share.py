import pytest

from clipforge.errors import WaitUntil
from clipforge.jobs import share
from clipforge.models import ProjectOptions
from tests.fakes import fake_engine

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def audio_job(engine, project_id):
    engine.runner.enqueue("ingest.audio", project_id)
    return next(j for j in engine.runner.jobs_for(project_id) if j.kind == "ingest.audio")


def test_only_the_newer_project_waits(engine):
    (older,), _ = engine.add_sources(URL, ProjectOptions())
    (newer,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    theirs, mine = audio_job(engine, older.id), audio_job(engine, newer.id)
    share.borrow(engine, theirs)  # the older one never waits, so two never wait for each other
    with pytest.raises(WaitUntil) as err:
        share.borrow(engine, mine)
    assert err.value.message == share.WAITING


def test_a_newer_projects_half_made_files_are_left_alone(engine):
    (older,), _ = engine.add_sources(URL, ProjectOptions())
    (newer,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    audio_job(engine, newer.id)  # still on its way
    theirs = engine.paths.project(newer.id)
    (theirs.source / "audio.m4a").write_bytes(b"the audio, as far as it got")
    share.borrow(engine, audio_job(engine, older.id))
    assert not (engine.paths.project(older.id).source / "audio.m4a").exists()


def test_settled_files_are_linked_once_the_wait_is_over(engine):
    (older,), _ = engine.add_sources(URL, ProjectOptions())
    (newer,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    engine.runner.delete_jobs([older.id])  # its stages are over
    theirs, mine = engine.paths.project(older.id), engine.paths.project(newer.id)
    (theirs.source / "audio.m4a").write_bytes(b"the audio")
    theirs.work.mkdir(parents=True, exist_ok=True)
    theirs.audio16k.write_bytes(b"the speech track")
    share.borrow(engine, audio_job(engine, newer.id))
    assert (mine.source / "audio.m4a").samefile(theirs.source / "audio.m4a")
    assert mine.audio16k.samefile(theirs.audio16k)
