"""The gameplay starter list (phase 5g Task 5, SPEC 12): nine vertical gameplay videos whose uploaders say
they are free to use, each downloaded only when you press Download, into the library with its credit,
licence and terms."""

import re
import shutil
from pathlib import Path

import pytest

from clipforge import repo
from clipforge.campaigns import links
from clipforge.errors import YouTubeRateLimited
from clipforge.ingest.ytdlp import RATE_LIMITED
from clipforge.library import starter
from tests.fakes import FakeYouTube, fake_engine
from tests.helpers import make_test_video
from tests.server import api_client

WATCH = re.compile(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}")
VIDEO_IDS = {
    "QPW3XwBoQlw", "hJcv2nZ8x84", "7YNdPOW4U5A", "m2XZ_HLq43o", "RbVMiu4ubT0",
    "s600FYgI5-s", "-lVgihPljuI", "fw_eWpb7uCE", "xKRNDalWE-E",
}  # fmt: skip


@pytest.fixture(scope="module")
def clip_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "gameplay.mp4"
    make_test_video(path, seconds=1.0, size="360x640")
    return path


class StarterTube(FakeYouTube):
    """A YouTube that hands back a short real video for a download, or writes part of one and raises
    ``error``."""

    def __init__(self, video: Path, error: Exception | None = None):
        super().__init__()
        self.video, self.error = video, error

    def download_video(self, url, dest_dir, quality, *, progress=None):
        self._record("download_video", url, Path(dest_dir), quality)
        return self._give(Path(dest_dir) / "video.mp4")

    def download_sections(self, url, dest_dir, ranges, quality, *, cancelled=None):
        self._record("download_sections", url, Path(dest_dir), list(ranges), quality)
        return [self._give(Path(dest_dir) / "video.0-1200.mp4")]

    def _give(self, path: Path) -> Path:
        if self.error is not None:
            path.write_bytes(b"part of a video")
            raise self.error
        shutil.copyfile(self.video, path)
        return path


@pytest.fixture
def tube(clip_file):
    return StarterTube(clip_file)


@pytest.fixture
def engine(tube):
    eng = fake_engine(youtube=tube)
    yield eng
    eng.close()


def entry(entry_id: str) -> starter.StarterEntry:
    return next(e for e in starter.entries() if e.id == entry_id)


def rows(client) -> dict[str, dict]:
    answer = client.get("/api/library/starter")
    assert answer.status_code == 200
    return {r["id"]: r for r in answer.json()["entries"]}


def left_behind(engine) -> list[Path]:
    """Download folders still in the library."""
    return [p for p in engine.paths.library.iterdir() if p.name.startswith(".starter")]


def test_pack_is_valid():
    entries = starter.entries()
    assert len(entries) == 9
    assert len({e.id for e in entries}) == 9
    assert {e.video_id for e in entries} == VIDEO_IDS
    for e in entries:
        assert WATCH.fullmatch(starter.url_of(e)), e.id
        assert starter.url_of(e) == f"https://www.youtube.com/watch?v={e.video_id}"
        assert e.personal_only == (e.game == "Minecraft"), e.id  # every Minecraft item, and only those
        assert e.informal == e.uploader.startswith("SYBO"), e.id
        assert e.duration_s > 0 and e.title and e.credit and e.licence and e.terms, e.id
        assert re.fullmatch(r"\d{3,4}x\d{4}", e.size), e.id  # all vertical
    sybo = entry("sybo-subway-9x16")
    assert (sybo.video_id, sybo.duration_s, sybo.size) == ("QPW3XwBoQlw", 193.0, "1216x2160")
    assert entry("orbital-plus-subway").duration_s == 4570.0  # 1:16:10
    assert entry("gameplaysforfree-minecraft").credit == (
        "Video by GameplaysForFree, licensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/"
    )
    assert starter.STARTER_MAX_S == 1200.0
    assert starter.NOTE == (
        "These uploaders say their videos are free to use, but YouTube's terms don't allow downloading even"
        " then. Your own recordings, or files a creator sends you, are the cleanest choice."
    )
    assert starter.PERSONAL_ONLY == (
        "Personal channels only, not for brand or agency work (Mojang's usage guidelines)"
    )
    assert starter.INFORMAL == "Informal permission only: keep it off paid or brand work"
    assert starter.FIRST_PART == "Downloads its first 20 minutes."


def test_nothing_downloads_until_pressed(engine, tube):
    client = api_client(engine)
    answer = client.get("/api/library/starter")
    assert answer.status_code == 200
    assert answer.json()["note"] == starter.NOTE
    listed = answer.json()["entries"]
    assert [r["id"] for r in listed] == [e.id for e in starter.entries()]
    assert {r["state"] for r in listed} == {"offered"}
    long = next(r for r in listed if r["id"] == "orbital-plus-subway")
    assert long["chips"] == [] and long["first_part"] is True
    mine = next(r for r in listed if r["id"] == "orbital-minecraft-16m")
    assert mine["chips"] == [starter.PERSONAL_ONLY] and mine["first_part"] is False
    assert next(r for r in listed if r["id"] == "sybo-subway-9x16")["chips"] == [starter.INFORMAL]
    assert tube.calls == []
    assert all(engine.runner.jobs_for(e.id) == [] for e in starter.entries())


def test_starter_download_adds_the_item(engine, tube):
    client = api_client(engine)
    city, long, mine = (
        entry("orbital-plus-subway-city"),
        entry("orbital-plus-subway"),
        entry("orbital-minecraft-16m"),
    )
    assert city.duration_s == 620.0  # 10:20
    for e in (city, long, mine):
        answer = client.post(f"/api/library/starter/{e.id}/download")
        assert answer.status_code == 202
        assert answer.json()["state"] == "downloading" and answer.json()["job_id"]
    (job,) = engine.runner.jobs_for(city.id)
    assert (job.kind, job.lane) == ("library.starter", "io")
    assert engine.runner.run_until_idle(timeout_s=30)
    whole = {c[1]: c for c in tube.called("download_video")}
    assert set(whole) == {starter.url_of(city), starter.url_of(mine)}
    _, _, dest, quality = whole[starter.url_of(city)]
    assert quality == "best" and dest.parent == engine.paths.library
    ((_, url, dest, ranges, quality),) = tube.called("download_sections")
    assert (url, ranges, quality) == (starter.url_of(long), [(0.0, 1200.0)], "best")
    assert dest.parent == engine.paths.library
    items = {i.tags[0]: i for i in repo.list_library(engine.db(), "gameplay")}
    assert set(items) == {f"starter:{e.id}" for e in (city, long, mine)}
    got = items[f"starter:{city.id}"]
    assert (got.name, got.credit, got.license) == (city.title, city.credit, city.licence)
    assert city.terms in got.notes and starter.PERSONAL_ONLY not in got.notes
    assert city.terms in got.notes and starter.PERSONAL_ONLY in items[f"starter:{mine.id}"].notes
    assert Path(got.path).parent == engine.paths.gameplay and Path(got.path).is_file()
    assert left_behind(engine) == []
    now = rows(client)
    assert (now[city.id]["state"], now[city.id]["item_id"]) == ("added", got.id)
    assert now["ludo-surfs-subway"]["state"] == "offered"
    again = client.post(f"/api/library/starter/{city.id}/download")
    assert again.status_code == 202 and again.json()["state"] == "added"
    assert len(engine.runner.jobs_for(city.id)) == 1  # in the library already, so nothing more downloads


def test_starter_download_failure_keeps_it_offered(engine, tube):
    client = api_client(engine)
    tube.error = YouTubeRateLimited("rate_limited", RATE_LIMITED)
    ludo = entry("ludo-surfs-subway")
    assert client.post(f"/api/library/starter/{ludo.id}/download").status_code == 202
    assert engine.runner.run_until_idle(timeout_s=30)
    row = rows(client)[ludo.id]
    assert (row["state"], row["error"], row["item_id"]) == ("failed", RATE_LIMITED, None)
    assert repo.list_library(engine.db(), "gameplay") == []
    assert left_behind(engine) == []
    assert not engine.paths.gameplay.exists() or list(engine.paths.gameplay.iterdir()) == []
    tube.error = None
    answer = client.post(f"/api/library/starter/{ludo.id}/download")  # Download again
    assert answer.status_code == 202 and answer.json()["state"] == "downloading"
    assert engine.runner.run_until_idle(timeout_s=30)
    assert rows(client)[ludo.id]["state"] == "added"
    assert len(tube.called("download_video")) == 2


def test_starter_download_needs_room(clip_file):
    tube = StarterTube(clip_file)
    engine = fake_engine(youtube=tube, min_free_gb=10_000_000.0)  # far more than this Mac has free
    try:
        client = api_client(engine)
        assert client.post("/api/library/starter/orbital-subway/download").status_code == 202
        assert engine.runner.run_until_idle(timeout_s=30)
        row = rows(client)["orbital-subway"]
        assert (row["state"], row["error"]) == ("failed", starter.LOW_DISK.format(gb=10_000_000.0))
        assert tube.called("download_video") == [] and tube.called("download_sections") == []
        assert repo.list_library(engine.db(), "gameplay") == [] and left_behind(engine) == []
    finally:
        engine.close()


def test_starter_download_twice_is_one_job(engine, monkeypatch):
    monkeypatch.setattr(engine.runner, "start", lambda: None)  # so the first stays queued
    client = api_client(engine)
    first = client.post("/api/library/starter/orbital-subway/download")
    second = client.post("/api/library/starter/orbital-subway/download")
    assert first.status_code == second.status_code == 202
    assert second.json()["state"] == "downloading" and second.json()["job_id"] == first.json()["job_id"]
    assert len(engine.runner.jobs_for("orbital-subway")) == 1


def test_unknown_entry_is_404(engine, tube):
    client = api_client(engine)
    assert client.post("/api/library/starter/nope/download").status_code == 404
    assert client.post("/api/library/starter/..%2Fx/download").status_code == 404
    assert client.post("/api/links/starter:nope/open").status_code == 404
    assert tube.calls == []


def test_open_shows_the_video_page(engine):
    with pytest.raises(LookupError):
        links.link_url(engine, "starter:nope")
    assert links.link_url(engine, "starter:sybo-subway-9x16") == "https://www.youtube.com/watch?v=QPW3XwBoQlw"
    assert api_client(engine).post("/api/links/starter:sybo-subway-9x16/open").status_code == 204
    assert [c for c in engine.run_cmd.calls if c[:1] == ["open"]] == [
        ["open", "https://www.youtube.com/watch?v=QPW3XwBoQlw"]
    ]
