"""SPEC 14.3: each project's card shows its source's thumbnail: YouTube's own picture, fetched only from
YouTube's image hosts and checked before it is kept, or a frame of a dropped file."""

import io
import shutil

import httpx
import pytest
from PIL import Image

from clipforge.ingest.thumbs import MAX_BYTES, ensure_thumb, frame_time
from clipforge.jobs.stages import write_text
from clipforge.models import ProjectOptions, SourceMeta
from tests.fakes import fake_engine
from tests.helpers import make_test_video
from tests.server import api_client

THUMB = "https://i.ytimg.com/vi/arj7oStGLkU/maxresdefault.jpg"
URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


def picture(w: int = 1280, h: int = 720, fmt: str = "WEBP", mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), 200).save(buf, fmt)
    return buf.getvalue()


def youtube_project(engine, thumbnail_url: str | None = THUMB):
    """A new project for the video, as the metadata stage leaves it: meta.json written, no thumbnail yet."""
    (project,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
    meta = SourceMeta(
        source_kind="youtube",
        video_id="arj7oStGLkU",
        title="A talk",
        channel="TED",
        channel_url=None,
        duration_s=100.0,
        thumbnail_url=thumbnail_url,
    )
    # Replaced, as the stage does, not written into: a second project of the video shares the first's files.
    write_text(engine.paths.project(project.id).meta_json, meta.model_dump_json())
    return project


def web(monkeypatch, routes: dict[str, httpx.Response]) -> list[str]:
    """httpx answering from ``routes`` (url to response, 404 otherwise); gives back every url asked for."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    return asked


def never(url: str, max_bytes: int) -> bytes:
    raise AssertionError(f"{url} should never be fetched")


def leftovers(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("."))


def test_youtube_thumb_is_fetched_resized_and_saved(engine, monkeypatch):
    moved = "https://i9.ytimg.com/vi/arj7oStGLkU/maxresdefault.jpg"
    asked = web(
        monkeypatch,
        {
            THUMB: httpx.Response(302, headers={"Location": moved}),
            moved: httpx.Response(200, content=picture()),
        },
    )
    project = youtube_project(engine)
    path = ensure_thumb(engine.paths, project)
    assert path == engine.paths.project(project.id).thumb
    assert asked == [THUMB, moved]  # a redirect between YouTube's own hosts is fine
    with Image.open(path) as img:
        assert (img.format, img.size) == ("JPEG", (640, 360))
    assert leftovers(path.parent) == []  # written through a temp file, and nothing of it left
    assert ensure_thumb(engine.paths, project) == path
    assert asked == [THUMB, moved]  # kept, not fetched again


def test_thumb_from_another_host_is_refused(engine):
    for url in (
        "https://evil.example.com/vi/x/hq.jpg",
        "http://i.ytimg.com/vi/x/hq.jpg",
        "https://i.ytimg.com.evil.example/vi/x/hq.jpg",
        "https://i.ytimg.com:8443/vi/x/hq.jpg",
        "https://someone@i.ytimg.com/vi/x/hq.jpg",
        "file:///etc/passwd",
        None,
    ):
        project = youtube_project(engine, url)
        assert ensure_thumb(engine.paths, project, fetch=never) is None, url
        assert not engine.paths.project(project.id).thumb.exists()


def test_thumb_redirect_off_the_list_is_refused(engine, monkeypatch):
    plain = "https://i.ytimg.com/vi/plain/hq.jpg"
    asked = web(
        monkeypatch,
        {
            THUMB: httpx.Response(302, headers={"Location": "https://evil.example.com/x.jpg"}),
            plain: httpx.Response(302, headers={"Location": "http://i.ytimg.com/vi/plain/hq.jpg"}),
            "https://evil.example.com/x.jpg": httpx.Response(200, content=picture()),
            "http://i.ytimg.com/vi/plain/hq.jpg": httpx.Response(200, content=picture()),
        },
    )
    for url in (THUMB, plain):
        project = youtube_project(engine, url)
        assert ensure_thumb(engine.paths, project) is None
        assert not engine.paths.project(project.id).thumb.exists()
    assert asked == [THUMB, plain]  # neither place off the list was ever asked


def test_oversized_or_broken_thumb_is_refused(engine, monkeypatch):
    bodies = {
        "big": b"x" * (MAX_BYTES + 1),
        "huge": picture(5000, 4001, "PNG", "1"),  # small to send, but over 20,000,000 pixels
        "junk": b"not a picture at all",
        "cut": picture(fmt="JPEG")[:400],
    }
    urls = {name: f"https://i.ytimg.com/vi/{name}/hq.jpg" for name in bodies}
    web(monkeypatch, {urls[name]: httpx.Response(200, content=body) for name, body in bodies.items()})
    assert len(bodies["huge"]) < MAX_BYTES
    for name in bodies:
        project = youtube_project(engine, urls[name])
        assert ensure_thumb(engine.paths, project) is None, name
        folder = engine.paths.project(project.id).source
        assert not (folder / "thumb.jpg").exists() and leftovers(folder) == [], name


def test_file_thumb_is_a_frame(engine, tmp_path):
    video = make_test_video(tmp_path / "clip.mp4", seconds=2.0, size="1280x720")
    project = engine.add_file(video, ProjectOptions())
    pp = engine.paths.project(project.id)
    shutil.copyfile(video, pp.source / "video.mp4")  # as the metadata stage leaves it
    engine.update_project(project.id, duration_s=2.0)
    meta = SourceMeta(
        source_kind="file", video_id=None, title="clip", channel=None, channel_url=None, duration_s=2.0
    )
    pp.meta_json.write_text(meta.model_dump_json(), encoding="utf-8")
    path = ensure_thumb(engine.paths, engine.project(project.id), fetch=never)
    assert path == pp.thumb
    with Image.open(path) as img:
        assert (img.format, img.size) == ("JPEG", (640, 360))
    assert (frame_time(2.0), frame_time(1000.0), frame_time(None)) == (0.2, 30.0, 0.0)


def test_media_makes_a_missing_thumb_once(engine):
    broken = "https://i.ytimg.com/vi/broken/hq.jpg"
    calls: list[str] = []

    def fetch(url: str, max_bytes: int) -> bytes:
        calls.append(url)
        if url == broken:
            raise OSError("no route to the host")
        return picture()

    engine.thumb_fetch = fetch
    good, bad = youtube_project(engine), youtube_project(engine, broken)
    client = api_client(engine)
    for _ in range(2):
        r = client.get(f"/media/projects/{good.id}/source/thumb.jpg")
        assert (r.status_code, r.headers["content-type"]) == (200, "image/jpeg")
        assert client.get(f"/media/projects/{bad.id}/source/thumb.jpg").status_code == 404
    assert calls == [THUMB, broken]  # each asked once: one made, the other remembered as missing
    assert (
        client.get(f"/api/projects/{good.id}").json()["thumb"]
        == f"/media/projects/{good.id}/source/thumb.jpg"
    )
    assert client.get(f"/api/projects/{bad.id}").json()["thumb"] is None

    other = "https://www.youtube.com/watch?v=vid00000001"  # another video, so it shares no files
    (later,), _ = engine.add_sources(other, ProjectOptions())  # not that far yet: no meta.json
    assert client.get(f"/media/projects/{later.id}/source/thumb.jpg").status_code == 404
    assert client.get("/media/projects/pnothere0000/source/thumb.jpg").status_code == 404
    assert later.id not in engine.thumb_misses and calls == [THUMB, broken]
