import pytest

from clipforge.errors import ClipForgeError
from clipforge.models import ProjectOptions
from tests.fakes import fake_engine

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


# Adding sources


def test_add_sources_makes_a_project_for_a_link(engine):
    options = ProjectOptions(clip_count=3, prompt="  deadlines  ")
    (project,), rejected = engine.add_sources(f"{URL}&t=1m30s", options)
    assert rejected == []
    assert (project.source_kind, project.video_id, project.source_url) == ("youtube", "arj7oStGLkU", URL)
    assert (project.focus_s, project.prompt, project.options.clip_count) == (90.0, "deadlines", 3)
    assert engine.project(project.id) == project
    assert engine.paths.project(project.id).work.is_dir()


def test_add_sources_reuses_the_project_of_a_video(engine):
    (first,), _ = engine.add_sources(URL, ProjectOptions())
    (again,), _ = engine.add_sources("https://youtu.be/arj7oStGLkU", ProjectOptions())
    assert again.id == first.id
    (fresh,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    assert fresh.id != first.id


def test_new_clips_of_a_downloaded_video_share_its_files(engine):
    (first,), _ = engine.add_sources(URL, ProjectOptions())
    source = engine.paths.project(first.id).source
    (source / "meta.json").write_text("{}")
    (source / "audio.m4a").write_bytes(b"the audio")
    (source / "audio.m4a.part").write_bytes(b"half a download")
    (fresh,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    linked = engine.paths.project(fresh.id).source
    assert sorted(p.name for p in linked.iterdir()) == ["audio.m4a", "meta.json"]
    assert (linked / "audio.m4a").stat().st_ino == (source / "audio.m4a").stat().st_ino  # a hard link


def test_add_sources_makes_a_project_per_file(engine, tmp_path):
    video = tmp_path / "My talk.mp4"
    video.write_bytes(b"not decoded here")
    (first,), _ = engine.add_sources(str(video), ProjectOptions())
    (second,), _ = engine.add_sources(str(video), ProjectOptions())
    assert first.id != second.id
    assert (first.source_kind, first.title, first.video_id) == ("file", "My talk", None)
    assert first.source_url == video.resolve().as_uri()


def test_add_sources_gives_back_what_it_cannot_use(engine):
    text = "https://vimeo.com/123\n/nope/missing.mp4 hello"  # a plain word is passed over
    projects, rejected = engine.add_sources(text, ProjectOptions())
    assert projects == []
    assert [r.token for r in rejected] == ["https://vimeo.com/123", "/nope/missing.mp4"]


# One engine at a time


def test_a_second_engine_on_the_same_home_is_refused(engine):
    with pytest.raises(ClipForgeError) as caught:
        fake_engine()
    assert caught.value.code == "busy"
    assert str(caught.value) == (
        "ClipForge is already running in another window. Use that one, or close it first."
    )
    engine.close()
    fake_engine().close()  # free once the first one closes
    engine.close()  # closing twice is harmless
