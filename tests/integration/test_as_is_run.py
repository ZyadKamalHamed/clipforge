"""Post as it is from start to finish (SPEC 14.2), with a fake YouTube, real ffmpeg, a real database and
real job workers: a vertical H.264 video is copied untouched into one ready clip, any other is fitted to
9:16 over its own blurred frame, a video over TikTok's 10 minutes is refused, a project that finds no clips
says why and points to Post as it is, and a Google Drive link runs as a YouTube one does."""

import cv2
import pytest

from clipforge import repo
from clipforge.media.ffmpeg import ffprobe_bin, probe, run
from clipforge.models import ProjectOptions
from tests.fakes import FakeProvider, FakeSpeech, FakeYouTube, brain_handlers, fake_engine
from tests.helpers import make_test_video

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
DRIVE = "https://drive.google.com/file/d/1AaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA/view"


@pytest.fixture
def opened():
    """Opens engines with fakes, ``opened(**fakes)``, and closes each one after the test."""
    engines = []

    def open_one(**fakes):
        engine = fake_engine(**fakes)
        engines.append(engine)
        return engine

    yield open_one
    for engine in engines:
        engine.close()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="module")
def vertical(media):
    return make_test_video(media / "Summer drop.mp4", seconds=8.0, size="360x640", fps=30)


@pytest.fixture(scope="module")
def wide(media):
    return make_test_video(media / "Wide ad.mp4", seconds=4.0, size="640x360", fps=30)


def run_project(engine, source: str, **options):
    (project,), _ = engine.add_sources(source, ProjectOptions(**options))
    engine.start(project.id)
    assert engine.wait(timeout_s=120, projects=[project.id])
    return engine.project(project.id)


def statuses(engine, pid: str) -> dict[str, str]:
    return {job.kind: job.status for job in engine.runner.jobs_for(pid)}


def packets(path) -> list[str]:
    """The size of each of the file's video packets, in order: the same list means the same encode."""
    args = ["-v", "error", "-select_streams", "v:0", "-show_entries", "packet=size", "-of", "csv=p=0"]
    return run([ffprobe_bin(), *args, str(path)]).stdout.decode().split()


def width(picture) -> int:
    return cv2.imread(str(picture)).shape[1]


def the_clip(engine, pid: str):
    (clip,) = engine.clips(pid)
    return clip, repo.get_render(engine.db(), clip.current_render_id)


def test_as_is_vertical_file_is_one_ready_clip_copied_untouched(opened, vertical):
    speech = FakeSpeech([])
    engine = opened(speech=speech)
    project = run_project(engine, str(vertical), as_is=True)
    assert (project.status, project.error) == ("ready", None)
    assert statuses(engine, project.id) == {
        "ingest.metadata": "done",
        "ingest.video": "done",
        "asis.make": "done",
    }
    clip, render = the_clip(engine, project.id)
    assert (clip.mode, clip.status, clip.rank, clip.passes_gates) == ("as_is", "rendered", 1, True)
    assert clip.start_s == 0.0
    assert clip.end_s == pytest.approx(8.0, abs=0.1)
    assert clip.packaging["hook_title"] == "Summer drop"
    assert (render.status, render.style_key, render.edl_path, render.version) == ("ready", "as_is", None, 1)
    made = probe(render.path)
    assert (made.width, made.height, made.vcodec, made.acodec) == (360, 640, "h264", "aac")
    assert packets(render.path) == packets(vertical)  # the picture as it was, not encoded again
    assert width(render.thumb_path) == 360
    assert width(render.cover_path) == 360  # the cover is the video's own first frame, full size
    assert engine.runner.jobs_for(clip.id) == []  # nothing renders it
    assert speech.calls == []  # nothing listens to it


def test_as_is_wide_file_is_fitted_to_9_16(opened, wide):
    engine = opened()
    project = run_project(engine, str(wide), as_is=True)
    assert project.status == "ready"
    _, render = the_clip(engine, project.id)
    made = probe(render.path)
    assert (made.width, made.height, made.vcodec, made.acodec) == (1080, 1920, "h264", "aac")
    assert made.duration_s == pytest.approx(4.0, abs=0.1)
    assert render.qa["passed"] is True


def test_as_is_link_downloads_the_video_and_makes_the_clip(opened, vertical):
    youtube = FakeYouTube(seconds=8.0, video=vertical)
    engine = opened(youtube=youtube)
    project = run_project(engine, URL, as_is=True)
    assert project.status == "ready"
    assert "ingest.audio" not in statuses(engine, project.id)
    assert statuses(engine, project.id)["ingest.video"] == "done"
    clip, render = the_clip(engine, project.id)
    assert clip.packaging["hook_title"] == project.title
    assert packets(render.path) == packets(vertical)


@pytest.mark.parametrize("source", ["link", "file"])
def test_as_is_refuses_a_video_over_ten_minutes(opened, media, source):
    youtube = FakeYouTube(seconds=601.0)
    engine = opened(youtube=youtube)
    if source == "file":
        long = make_test_video(media / "Long.mp4", seconds=601.0, size="160x90", fps=1)
        project = run_project(engine, str(long), as_is=True)
    else:
        project = run_project(engine, URL, as_is=True)
    assert (project.status, project.error_code) == ("refused", "too_long")
    assert "10 minutes" in project.error
    assert "Post as it is" in project.error
    assert "download_video" not in [name for name, *_ in youtube.calls]
    assert engine.clips(project.id) == []


def test_no_clips_says_why_and_points_to_as_is(opened, media):
    trailer = make_test_video(media / "Trailer.mp4", seconds=30.0, size="320x180", fps=10)
    speech = FakeSpeech(["The new season starts this Friday night."])
    engine = opened(speech=speech, provider=FakeProvider(brain_handlers()))  # it finds no moment in 5 s
    project = run_project(engine, str(trailer))
    assert project.status == "ready"
    assert engine.clips(project.id) == []
    assert "5 seconds of talking" in project.message
    assert "Post as it is" in project.message


def test_a_link_with_no_clips_posts_whole_when_added_again_as_it_is(opened, media):
    trailer = make_test_video(media / "Link trailer.mp4", seconds=30.0, size="360x640", fps=30)
    engine = opened(
        youtube=FakeYouTube(seconds=30.0, video=trailer, captions=None),
        speech=FakeSpeech(["The new season starts this Friday night."]),
        provider=FakeProvider(brain_handlers()),
    )
    first = run_project(engine, URL)
    assert engine.clips(first.id) == []
    assert "Post as it is" in first.message
    again = run_project(engine, URL, as_is=True)
    assert again.id != first.id
    assert again.status == "ready"
    clip, _ = the_clip(engine, again.id)
    assert clip.mode == "as_is"


def test_as_is_drive_link_makes_the_clip(opened, vertical):
    youtube = FakeYouTube(seconds=8.0, video=vertical, title="Summer drop.mp4")
    engine = opened(youtube=youtube)
    project = run_project(engine, DRIVE, as_is=True)
    assert (project.status, project.source_kind, project.title) == ("ready", "drive", "Summer drop")
    assert [call[0] for call in youtube.calls] == ["fetch_info", "download_video"]  # no captions or comments
    assert youtube.calls[0] == ("fetch_info", DRIVE)
    clip, render = the_clip(engine, project.id)
    assert clip.packaging["hook_title"] == "Summer drop"  # the file's name, not its extension
    assert packets(render.path) == packets(vertical)
