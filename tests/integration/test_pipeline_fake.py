"""The whole pipeline, from a link or a file to packaged clip ideas, with fakes for YouTube, speech to text
and the LLM. Real ffmpeg, a real database and real job workers."""

import json
import re
import threading
import time

import pytest

from clipforge.asr.base import LANGUAGE_WARNING
from clipforge.errors import LLMError
from clipforge.jobs.stages import ingest
from clipforge.models import Comment, ProjectOptions, Transcript
from tests.fakes import (
    LIVE_CHAT,
    TITLE,
    FakeProvider,
    FakeSpeech,
    FakeYouTube,
    brain_handlers,
    discover_answer,
    fake_engine,
    lines,
    make_json3,
    rerank_about,
)
from tests.helpers import make_test_video

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


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
def talk_video(tmp_path_factory):
    """Three minutes of test pattern and tone, small and at 10 fps so it is quick to make."""
    return make_test_video(
        tmp_path_factory.mktemp("media") / "A talk.mp4", seconds=180.0, size="320x180", fps=10
    )


def run(engine, source: str, **options):
    """The project for ``source`` once its work is done."""
    (project,), _ = engine.add_sources(source, ProjectOptions(**options))
    engine.start(project.id)
    assert engine.wait(timeout_s=60, projects=[project.id])
    return engine.project(project.id)


def statuses(engine, pid: str) -> dict[str, str]:
    """The status of the newest job of each kind."""
    return {job.kind: job.status for job in engine.runner.jobs_for(pid)}


def transcript(engine, pid: str) -> Transcript:
    return Transcript.model_validate_json(engine.paths.project(pid).transcript_json.read_text())


def tagged(tr: Transcript, tag: str) -> list[str]:
    """The ids of the sentences that carry ``tag``."""
    return [s.id for s in tr.sentences if tag in s.tags]


def titles(engine, pid: str) -> list[str]:
    """The hook titles of the packaged clips, best first, including those already sent on to render."""
    return [c.packaging["hook_title"] for c in engine.clips(pid) if c.packaging]


# From start to finish


def test_pipeline_local_file_end_to_end(opened, talk_video):
    fake = FakeProvider(brain_handlers())
    engine = opened(speech=FakeSpeech(lines(36)), provider=fake)
    project = run(engine, str(talk_video))
    assert (project.status, project.mode, project.message) == ("ready", "llm", None)
    assert (project.source_kind, project.title) == ("file", "A talk")
    assert project.duration_s == pytest.approx(180.0, abs=0.5)
    assert titles(engine, project.id) == [TITLE, TITLE]
    assert len(transcript(engine, project.id).sentences) == 36
    assert statuses(engine, project.id) == {
        "ingest.metadata": "done",
        "ingest.audio": "done",
        "ingest.captions": "done",
        "ingest.extras": "done",
        "asr.transcribe": "done",
        "signals.compute": "done",
        "brain.select": "done",
        "brain.package": "done",
    }  # no ingest.video: the file is the video
    assert (engine.paths.project(project.id).source / "video.mp4").is_file()

    asked = len(fake.requests)
    engine.rerun_selection(project.id)
    assert engine.wait(timeout_s=60, projects=[project.id])
    assert engine.project(project.id).status == "ready"
    assert len(fake.requests) == asked  # every answer comes from the cache
    assert titles(engine, project.id) == [TITLE, TITLE]


def test_pipeline_youtube_fake_end_to_end(opened):
    heat = [0.1 + 0.001 * k for k in range(18)]
    heat[5:9] = [0.9, 1.0, 0.95, 0.85]  # viewers replay 50 s to 90 s
    youtube = FakeYouTube(
        heatmap=[{"start_time": 10.0 * k, "end_time": 10.0 * k + 10, "value": v} for k, v in enumerate(heat)],
        comments=[Comment(text="1:05 this part", likes=4)] * 3,
        license="Creative Commons Attribution license (reuse allowed)",
        http_headers={"User-Agent": "yt-dlp"},
        requested_downloads=[{"filepath": "/Users/someone/video.mp4"}],
    )
    engine = opened(youtube=youtube, speech=FakeSpeech(lines(36)), provider=FakeProvider(brain_handlers()))
    project = run(engine, URL)
    assert (project.status, project.mode, project.message) == ("ready", "llm", None)
    assert (project.title, project.channel, project.language) == (youtube.info["title"], "TED", "en")
    assert (project.license, project.rights_basis) == (youtube.info["license"], "cc")
    info = json.loads(engine.paths.project(project.id).info_json.read_text())
    assert info["id"] == "arj7oStGLkU" and not {"http_headers", "requested_downloads"} & set(info)
    tr = transcript(engine, project.id)
    assert tagged(tr, "[replayed]") == [f"S{k}" for k in range(11, 19)]
    assert tagged(tr, "[comments: 3]") == ["S13", "S14", "S15"]  # the bucket from 60 s to 75 s
    assert tagged(tr, "[Laughter]") == ["S1", "S2"]  # from the captions, at 6 s
    assert youtube.called("download_captions") == [("download_captions", "en-orig")]
    assert youtube.called("download_video") == [("download_video", "best")]
    assert set(statuses(engine, project.id).values()) == {"done"}
    assert titles(engine, project.id) == [TITLE, TITLE]


def test_pipeline_keeps_the_clips_on_the_topic_first_after_packaging(opened):
    fake = FakeProvider(brain_handlers(rerank=rerank_about("k2")))
    engine = opened(speech=FakeSpeech(lines(36)), provider=fake)
    project = run(engine, URL, prompt="the deadline")
    clips = sorted(engine.clips(project.id), key=lambda c: c.rank)
    assert ["off_topic" in c.flags["notes"] for c in clips] == [False, True]
    assert clips[0].start_s > clips[1].start_s  # k2, the later moment, ranks first


def test_pipeline_marks_your_own_channel(opened):
    engine = opened(speech=FakeSpeech(lines(36)), own_channels=["youtube.com/@ted/"])
    project = run(engine, URL, render=False)
    assert (project.status, project.rights_basis) == ("ready", "own")


# Stopping and carrying on


def test_pipeline_resumes_after_crash(opened):
    youtube, speech = FakeYouTube(), FakeSpeech(lines(36))
    engine = opened(youtube=youtube, speech=speech)
    (project,), _ = engine.add_sources(URL, ProjectOptions(render=False))
    engine.start(project.id)
    (job,) = engine.runner.jobs_for(project.id)
    now = time.time()
    engine.db().execute(
        "UPDATE jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (now, now, job.id),
    )
    engine.close()  # as if ClipForge quit in the middle of the job

    engine = opened(youtube=youtube, speech=speech)
    assert engine.runner.get(job.id).status == "queued"
    assert engine.wait(timeout_s=60, projects=[project.id])
    assert engine.project(project.id).status == "ready"
    assert set(statuses(engine, project.id).values()) == {"done"}


def test_pipeline_picks_up_where_it_stopped(opened):
    youtube, speech = FakeYouTube(), FakeSpeech(lines(36))
    engine = opened(youtube=youtube, speech=speech)
    project = run(engine, URL, render=False)
    assert project.status == "ready"
    engine.db().execute("DELETE FROM jobs WHERE ref_id = ? AND kind != 'ingest.metadata'", (project.id,))
    engine.update_project(project.id, status="running", stage="ingest.audio", progress=0.02)
    engine.close()  # as if ClipForge quit between two stages

    engine = opened(youtube=youtube, speech=speech)
    assert engine.wait(timeout_s=60, projects=[project.id])
    assert engine.project(project.id).status == "ready"
    for name in ("download_audio", "download_captions", "fetch_comments"):
        assert len(youtube.called(name)) == 1, name  # the files from the first run are used again
    assert len(speech.called("transcribe")) == 1


class SlowSpeech(FakeSpeech):
    """Takes its time over the first pass and says nothing while it does, as the speech worker does."""

    def transcribe(self, wav, *, window=None, **kw):
        if window is None:
            time.sleep(1.2)
        return super().transcribe(wav, window=window, **kw)


class SlowChatYouTube(FakeYouTube):
    def download_live_chat(self, url, dest_dir, *, timeout_s=600.0):
        time.sleep(1.2)  # the chat child prints nothing until it is done
        return super().download_live_chat(url, dest_dir, timeout_s=timeout_s)


class HeldChatYouTube(FakeYouTube):
    """The live chat replay keeps downloading until ``release`` is set, as a long stream's does."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.release = threading.Event()

    def download_live_chat(self, url, dest_dir, *, timeout_s=600.0):
        self.release.wait(10)
        return super().download_live_chat(url, dest_dir, timeout_s=timeout_s)


class HangingAudioYouTube(FakeYouTube):
    """The first audio download hangs without a word until ``release`` is set, then gives up."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.release = threading.Event()

    def download_audio(self, url, dest_dir, *, progress=None):
        if not self.called("download_audio"):
            self._record("download_audio", url)
            self.release.wait(10)
            raise ConnectionError("the hung download gave up")
        return super().download_audio(url, dest_dir, progress=progress)


def test_pipeline_a_slow_silent_transcription_is_not_stuck(opened):
    engine = opened(speech=SlowSpeech(lines(36)))
    engine.runner.stuck_s = 0.5
    project = run(engine, URL, render=False)
    assert project.status == "ready"
    assert statuses(engine, project.id)["asr.transcribe"] == "done"


def test_pipeline_a_slow_live_chat_is_not_stuck(opened):
    youtube = SlowChatYouTube(live_chat=LIVE_CHAT, live_status="was_live")
    engine = opened(youtube=youtube, speech=FakeSpeech(lines(36)))
    engine.runner.stuck_s = 0.5
    project = run(engine, URL, render=False)
    assert project.status == "ready"
    assert statuses(engine, project.id)["ingest.extras"] == "done"
    assert engine.paths.project(project.id).live_chat_json.is_file()


def test_pipeline_transcribes_while_the_live_chat_downloads(opened):
    youtube = HeldChatYouTube(live_chat=LIVE_CHAT, live_status="was_live")
    engine = opened(youtube=youtube, speech=FakeSpeech(lines(36)))
    try:
        (project,), _ = engine.add_sources(URL, ProjectOptions(render=False))
        engine.start(project.id)
        engine.runner.start()
        deadline = time.monotonic() + 10
        while statuses(engine, project.id).get("asr.transcribe") != "done" and time.monotonic() < deadline:
            time.sleep(0.05)
        now = statuses(engine, project.id)
        assert (now.get("asr.transcribe"), now["ingest.extras"]) == ("done", "running")
        assert "signals.compute" not in now  # the signals wait for the chat
    finally:
        youtube.release.set()
    assert engine.wait(timeout_s=60, projects=[project.id])
    assert engine.project(project.id).status == "ready"
    assert engine.paths.project(project.id).live_chat_json.is_file()


def test_pipeline_a_slow_file_copy_is_not_stuck(opened, talk_video, monkeypatch):
    copy_in = ingest.copy_in

    def slow_copy(src, dst):
        time.sleep(1.2)  # a big file from a slow drive
        return copy_in(src, dst)

    monkeypatch.setattr(ingest, "copy_in", slow_copy)
    engine = opened(speech=FakeSpeech(lines(36)))
    engine.runner.stuck_s = 0.5
    assert run(engine, str(talk_video)).status == "ready"


def test_pipeline_a_stuck_step_stops_the_project_and_can_be_tried_again(opened):
    youtube = HangingAudioYouTube()
    engine = opened(youtube=youtube, speech=FakeSpeech(lines(36)))
    engine.runner.stuck_s = 0.5
    try:
        (project,), _ = engine.add_sources(URL, ProjectOptions(render=False))
        engine.start(project.id)
        assert engine.wait(timeout_s=10, projects=[project.id])
        project = engine.project(project.id)
        assert (project.status, project.stage, project.error_code) == ("failed", "ingest.audio", "stuck")
        assert project.error.endswith("so ClipForge stopped waiting for it. Try again.")
    finally:
        youtube.release.set()
    engine.start(project.id)
    assert engine.wait(timeout_s=60, projects=[project.id])
    assert engine.project(project.id).status == "ready"


# Sources that can't be clipped


def test_pipeline_refuses_long_video(opened):
    youtube = FakeYouTube(seconds=25200.0)  # 7 hours
    engine = opened(youtube=youtube)
    project = run(engine, URL)
    assert (project.status, project.error_code) == ("refused", "too_long")
    assert project.error == "This video is over 6 hours. Download the part you want and drop the file in."
    assert project.title == youtube.info["title"]  # the card still says which video it was
    assert youtube.called("download_audio") == []
    assert not engine.paths.project(project.id).meta_json.exists()


def test_pipeline_refuses_a_video_with_no_speech(opened):
    fake = FakeProvider(brain_handlers())
    engine = opened(youtube=FakeYouTube(captions=None), speech=FakeSpeech([]), provider=fake)
    project = run(engine, URL, render=False)
    assert (project.status, project.stage, project.error_code) == ("refused", "asr.transcribe", "no_speech")
    assert project.error == (
        "ClipForge heard no speech in this video, so there is nothing to clip. To post the whole video, "
        "add it again with Post as it is on in Create's Options."
    )
    assert "brain.select" not in statuses(engine, project.id)
    assert fake.requests == []  # no Claude usage spent on it
    assert not engine.paths.project(project.id).transcript_json.exists()  # trying again listens again


def test_pipeline_stops_when_the_disk_is_nearly_full(opened, monkeypatch):
    monkeypatch.setattr(ingest, "free_gb", lambda path: 3.0)
    youtube = FakeYouTube()
    engine = opened(youtube=youtube, min_free_gb=10.0)
    project = run(engine, URL)
    assert (project.status, project.error_code) == ("failed", "low_disk")
    assert project.error == (
        "Free up some disk space: ClipForge needs at least 10 GB free to start a project."
    )
    assert youtube.calls == []


# When the LLM can't answer


def test_pipeline_waits_then_resumes_on_usage_limit(opened):
    seen, lock, second = [], threading.Lock(), threading.Event()

    def discover(req):
        """The first question hits the usage limit, which lifts in half a second, once the other part
        has been asked too."""
        with lock:
            seen.append(re.findall(r"^(S\d+) \[", req.prompt, re.M)[0])
            first = len(seen) == 1
        if first:
            second.wait(5.0)
            return LLMError("usage_limit", retry_at=time.time() + 0.5)
        second.set()
        return discover_answer(req)

    youtube = FakeYouTube(seconds=1260.0, captions=None)  # 21 minutes: two parts
    fake = FakeProvider(brain_handlers(discover=discover))
    engine = opened(youtube=youtube, speech=FakeSpeech(lines(252)), provider=fake)
    sub = engine.events.subscribe()
    project = run(engine, URL, render=False)
    assert (project.status, project.mode, project.message) == ("ready", "llm", None)
    assert len(seen) == 3 and sorted(seen[:2]) == ["S1", "S163"]  # both parts were asked at once
    assert seen[2] == seen[0]  # only the part that hit the limit is asked again
    events = []
    while (event := sub.get(timeout=0)) is not None:
        events.append(event)
    waiting = [e.data for e in events if e.topic == "project" and e.data["status"] == "waiting"]
    assert waiting and waiting[0]["id"] == project.id
    assert waiting[0]["message"].startswith("Claude's usage limit is reached. ClipForge will carry on at ")


def test_pipeline_basic_on_limit_finishes(opened):
    def limit(req):
        return LLMError("usage_limit")

    fake = FakeProvider({name: limit for name in brain_handlers()})
    engine = opened(speech=FakeSpeech(lines(36)), provider=fake, on_llm_limit="basic")
    project = run(engine, URL, render=False)
    assert (project.status, project.mode) == ("ready", "basic")
    assert project.message == "Claude's usage limit is reached. These clips come from Basic mode."
    assert [r.pass_name for r in fake.requests] == ["discover"]  # the rest of the run never asks
    assert titles(engine, project.id)  # Basic mode packages the clips


# Languages


def test_pipeline_language_fallback_warns(opened):
    youtube = FakeYouTube(
        language="ar", automatic_captions={"ar-orig": []}, subtitles={}, captions=make_json3(lines(36))
    )
    speech = FakeSpeech(lines(36))
    engine = opened(youtube=youtube, speech=speech, provider=FakeProvider(brain_handlers()))
    project = run(engine, URL, render=False)
    assert (project.status, project.language, project.message) == ("ready", "ar", LANGUAGE_WARNING)
    tr = transcript(engine, project.id)
    assert (tr.engine, tr.language, len(tr.sentences)) == ("youtube-captions", "ar", 36)
    assert youtube.called("download_captions") == [("download_captions", "ar-orig")]
    assert speech.called("transcribe") == []
    assert titles(engine, project.id)
