"""SPEC 9.2 step 5 and 14.4: a clip the user edited is checked again, beside its render. The re-check reads
the clip as it is now (its range, the words struck from it, the fillers its style keeps, and the title,
cards and captions it goes out with), puts its own red flags in place of the review's, and never changes
the user's words."""

import re
import threading
import time

import pytest

from clipforge import repo
from clipforge.edits import review_key
from clipforge.errors import LLMError
from clipforge.jobs import pipeline
from clipforge.jobs.stages import clips
from clipforge.server import views
from tests.fakes import FakeProvider, fake_engine
from tests.unit.test_api_clips import add_clips, clip_row, seeded
from tests.unit.test_clip_jobs import SectionTube, project
from tests.unit.test_clip_jobs import add_clips as add_ideas
from tests.unit.test_clip_jobs import ctx as clip_ctx

BRAIN_REVIEW = {
    "complete_story": True,
    "meaning_changed": False,
    "true_title_index": 0,
    "cards_supported": [],
    "tiktok_caption_true": True,
    "instagram_caption_true": True,
    "notes": "The brain's review.",
}
NEW_TITLE = "He never feared the deadline"
CARD = {"sid": "S10", "text": "Tim Urban gave this talk in 2016."}
SKIPPED = "Not re-checked after your edits."


def answer(**fields):
    """A review handler that answers for every clip asked about: complete and faithful, its first title
    true, except for ``fields``."""

    def handler(req):
        base = {
            "notes": "Checked again.",
            "complete_story": True,
            "meaning_changed": False,
            "true_title_index": 0,
            "cards_supported": [],
        }
        ids = re.findall(r"^Clip (k\d+) \(", req.prompt, re.M)
        return {"clips": [{"id": cid, **(base | fields)} for cid in ids]}

    return handler


@pytest.fixture
def make():
    """Engines whose LLM answers the review with ``handler``, closed after the test."""
    made = []

    def build(handler=None, **settings):
        provider = FakeProvider({"review": handler or answer()})
        engine = fake_engine(provider=provider, **settings)
        made.append(engine)
        return engine, provider

    yield build
    for engine in made:
        engine.close()


def packaged(engine, *, red=(), **options) -> tuple[str, str]:
    """A project with one clip that the brain packaged and reviewed, from 40.0 to 59.9 s: the words w80 to
    w119, with the filler "um" in place of w107."""
    pid = seeded(engine, **options)
    flags = {"red": list(red), "soft": [], "notes": []}
    tier = "no" if red else "post"
    add_clips(engine, pid, clip_row(pid, "c1", 1, flags=flags, tier=tier, review=BRAIN_REVIEW))
    return pid, "c1"


def kinds(engine, cid: str) -> list[str]:
    return [job.kind for job in engine.runner.jobs_for(cid)]


def reviews(engine, cid: str) -> list:
    return [job for job in engine.runner.jobs_for(cid) if job.kind == "clip.review"]


def rechecked(engine, cid: str):
    """The clip once a re-check of it has run on the engine's runner."""
    clip = repo.get_clip(engine.db(), cid)
    engine.runner.enqueue("clip.review", cid, {"project": clip.project_id}, rerun=True)
    assert engine.wait(10, clips=[cid])
    return repo.get_clip(engine.db(), cid)


def untrusted(prompt: str, kind: str) -> str:
    """The text of the prompt's untrusted block of ``kind``."""
    found = re.search(rf'<untrusted kind="{kind}">\n(.*?)\n</untrusted>', prompt, re.S)
    assert found, f"the prompt has no {kind} block"
    return found.group(1)


def said(first: int, last: int, *, fillers: bool, struck: tuple[int, ...] = ()) -> str:
    """The words from ``first`` to ``last`` of the test talk as the reviewer should read them."""
    words = ("um" if i == 107 else f"w{i}" for i in range(first, last + 1) if i not in struck)
    return " ".join(w for w in words if fillers or w != "um")


def poll(check, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    while not (found := check()) and time.monotonic() < deadline:
        time.sleep(0.02)
    return found


# When it is queued


@pytest.mark.parametrize(
    ("edits", "range_"),
    [
        ({"struck": [90]}, "keep"),
        ({"hook_title": NEW_TITLE}, "keep"),
        ({"fact_cards": [CARD]}, "keep"),
        ({"tiktok_caption": "Would you wait this long?"}, "keep"),
        ({"instagram_hashtags": ["#deadlines", "#focus"]}, "keep"),
        ({}, (45.0, 59.9)),
    ],
    ids=["struck", "title", "cards", "caption", "hashtags", "range"],
)
def test_recheck_queued_when_the_cut_or_text_changed(make, edits, range_):
    engine, _ = make()
    pid, cid = packaged(engine)
    assert engine.render_clip(cid, edits, range_)
    source, review = engine.runner.jobs_for(cid)
    assert source.kind == "clip.source" and review.kind == "clip.review"
    assert (review.lane, review.status, review.priority) == ("llm", "queued", pipeline.USER_PRIORITY)
    assert review.payload == {"project": pid}


def test_recheck_not_queued_for_look_only_edits(make):
    engine, provider = make()
    _, cid = packaged(engine)
    look = {"preset": "hormozi", "layout": "stacked", "filler_removal": False, "punch_ins": False}
    assert engine.render_clip(cid, look)
    engine.shuffle_gameplay(cid)
    assert kinds(engine, cid) == ["clip.source"]
    assert provider.requests == []


@pytest.mark.parametrize("where", ["project", "settings"])
def test_recheck_not_queued_in_basic_mode(make, where):
    engine, _ = make(**({"llm_provider": "heuristic"} if where == "settings" else {}))
    _, cid = packaged(engine, **({"provider": "heuristic"} if where == "project" else {}))
    assert engine.render_clip(cid, {"hook_title": NEW_TITLE})
    assert kinds(engine, cid) == ["clip.source"]


def test_recheck_not_queued_twice_for_the_same_edits(make):
    engine, provider = make()
    _, cid = packaged(engine)
    engine.render_clip(cid, {"hook_title": NEW_TITLE})
    engine.render_clip(cid, {"hook_title": NEW_TITLE})  # while the first is still queued
    assert len(reviews(engine, cid)) == 1
    engine.runner.cancel(engine.runner.jobs_for(cid)[0].id)  # the render, so only the re-check runs
    assert engine.wait(10, clips=[cid])
    assert len(provider.requests) == 1
    engine.render_clip(cid, {"hook_title": NEW_TITLE})  # checked already, as it is
    assert len(reviews(engine, cid)) == 1
    engine.render_clip(cid, {"hook_title": "He waited until the very last day"})
    assert len(reviews(engine, cid)) == 2


def test_recheck_queued_when_the_edits_are_undone(make):
    engine, _ = make()
    _, cid = packaged(engine)
    engine.save_edits(cid, {"hook_title": NEW_TITLE})
    rechecked(engine, cid)
    engine.render_clip(cid, {"hook_title": None})  # back to the AI's title, which the re-check replaced
    assert len(reviews(engine, cid)) == 2


# What it does


def test_recheck_starts_from_the_flags_before_the_review(make):
    engine, _ = make()
    pid = seeded(engine)
    review_flags = ["incomplete_story", "meaning_distortion", "no_faithful_hook"]
    rows = [
        clip_row(
            pid,
            "c1",
            1,
            flags={"red": ["third_party_media", *review_flags], "soft": ["jargon"], "notes": []},
            tier="no",
            review=BRAIN_REVIEW,
            edits={"hook_title": NEW_TITLE},
        ),
        clip_row(
            pid,
            "c2",
            2,
            flags={"red": review_flags, "soft": [], "notes": []},
            tier="no",
            review=BRAIN_REVIEW,
            edits={"hook_title": NEW_TITLE},
        ),
    ]
    add_clips(engine, pid, *rows)
    kept = rechecked(engine, "c1")
    assert kept.flags == {"red": ["third_party_media"], "soft": ["jargon"], "notes": []}
    assert kept.tier == "no"  # a flag the review didn't raise still holds the clip back
    cleared = rechecked(engine, "c2")
    assert cleared.flags["red"] == [] and cleared.tier == "post"
    assert cleared.review["notes"] == "Checked again."


def test_recheck_never_changes_the_users_text(make):
    engine, provider = make(answer(true_title_index=-1, cards_supported=[False], tiktok_caption_true=False))
    _, cid = packaged(engine)
    edits = {
        "hook_title": NEW_TITLE,
        "accent_word": "deadline",
        "fact_cards": [CARD],
        "tiktok_caption": "Would you wait this long?",
    }
    before = engine.save_edits(cid, edits)
    after = rechecked(engine, cid)
    assert after.edits == before.edits and after.packaging == before.packaging
    assert after.flags["red"] == ["no_faithful_hook"] and after.tier == "no"
    assert after.review["true_title_index"] == -1 and after.review["cards_supported"] == [False]
    assert after.review["tiktok_caption_true"] is False and after.review["instagram_caption_true"] is True
    assert after.review["key"] == review_key(after, after.edits)
    assert after.review["rechecked_at"] > 0
    (req,) = provider.requests
    assert untrusted(req.prompt, "titles") == f"0. {NEW_TITLE}"  # only the title the clip goes out with
    assert untrusted(req.prompt, "cards") == f"0. {CARD['text']}"
    assert "TikTok: Would you wait this long?" in untrusted(req.prompt, "captions")
    assert untrusted(req.prompt, "clip") == said(80, 119, fillers=False)
    assert '<untrusted kind="said">' not in req.prompt  # nothing was struck from it


@pytest.mark.parametrize("fillers", [False, True], ids=["fillers_cut", "fillers_kept"])
def test_recheck_sees_struck_words_and_the_filler_setting(make, fillers):
    engine, provider = make()
    _, cid = packaged(engine)
    engine.save_edits(cid, {"struck": [90, 91]} | ({"filler_removal": False} if fillers else {}))
    rechecked(engine, cid)
    (req,) = provider.requests
    assert untrusted(req.prompt, "clip") == said(80, 119, fillers=fillers, struck=(90, 91))
    assert untrusted(req.prompt, "said") == said(80, 119, fillers=fillers)  # as it was, before the cuts
    assert "Fillers such as" in req.system and "checked again" in req.system


@pytest.mark.parametrize(
    "handler",
    [lambda req: LLMError("timeout"), lambda req: {"clips": []}],
    ids=["failed", "unanswered"],
)
def test_recheck_failure_notes_review_skipped(make, handler):
    engine, provider = make(handler)
    _, cid = packaged(engine, red=["incomplete_story"])
    engine.save_edits(cid, {"hook_title": NEW_TITLE})
    failed = rechecked(engine, cid)
    assert failed.flags == {"red": ["incomplete_story"], "soft": [], "notes": ["review_skipped"]}
    assert failed.review == BRAIN_REVIEW and failed.edits == {"hook_title": NEW_TITLE}
    assert reviews(engine, cid)[-1].status == "done"
    assert SKIPPED in views.clip_card(engine, failed)["notes"]
    provider.handlers["review"] = answer()
    answered = rechecked(engine, cid)
    assert answered.flags["notes"] == [] and answered.flags["red"] == []
    assert SKIPPED not in views.clip_card(engine, answered)["notes"]


def test_a_brain_review_skipped_without_edits_is_not_a_recheck(make):
    engine, _ = make()
    pid = seeded(engine)
    flags = {"red": [], "soft": [], "notes": ["review_skipped"]}
    add_clips(engine, pid, clip_row(pid, "c1", 1, flags=flags, review=None))
    assert SKIPPED not in views.clip_card(engine, repo.get_clip(engine.db(), "c1"))["notes"]


def test_recheck_waits_out_a_usage_limit(make):
    engine, _ = make(lambda req: LLMError("usage_limit", retry_at=time.time() + 3600))
    pid, cid = packaged(engine)
    engine.save_edits(cid, {"hook_title": NEW_TITLE})
    engine.runner.enqueue("clip.review", cid, {"project": pid})
    engine.runner.start()

    def waiting():
        job = reviews(engine, cid)[-1]
        return job.status == "queued" and job.not_before > time.time() + 600 and job

    job = poll(waiting)
    assert job and job.attempts == 0
    assert "ClipForge will check the edited clip again at" in job.message and "rerun" not in job.message
    clip = repo.get_clip(engine.db(), cid)
    assert clip.flags["notes"] == [] and clip.review == BRAIN_REVIEW


def test_recheck_of_edits_changed_meanwhile_runs_again(make):
    seen = []

    def handler(req):
        seen.append(req.prompt)
        if len(seen) == 1:  # the user changes the title while the clip is being checked
            engine.save_edits(cid, {"hook_title": "He waited until the very last day"})
        return answer()(req)

    engine, provider = make(handler)
    _, cid = packaged(engine)
    engine.save_edits(cid, {"hook_title": NEW_TITLE})
    after = rechecked(engine, cid)
    assert len(seen) == 2 and "He waited until the very last day" in seen[1]
    assert after.review["key"] == review_key(after, after.edits)
    assert [job.status for job in reviews(engine, cid)] == ["done", "done"]


# Beside the render


def test_recheck_runs_beside_the_render(make, monkeypatch):
    started, release = threading.Event(), threading.Event()
    subs = []

    def source(engine, ctx):  # the render's first job, held up until the test lets it go
        started.set()
        release.wait(10)

    def handler(req):
        started.wait(10)
        subs.append(engine.events.subscribe())  # what is published once the clip is checked
        return answer()(req)

    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.source", source)
    engine, _ = make(handler)
    _, cid = packaged(engine)
    engine.render_clip(cid, {"hook_title": NEW_TITLE})
    engine.runner.start()
    try:
        assert poll(lambda: reviews(engine, cid)[-1].status == "done")
        render = engine.runner.jobs_for(cid)[0]
        assert render.kind == "clip.source" and render.status == "running"  # the two ran side by side
        clip = repo.get_clip(engine.db(), cid)
        assert clip.status == "analyzing"  # the re-check leaves the render's status alone
        assert clip.review["key"] == review_key(clip, clip.edits)
        events = [e for e in iter(lambda: subs[0].get(timeout=0), None) if e.topic == "clip"]
        assert [e.data["id"] for e in events] == [cid]
        assert kinds(engine, cid) == ["clip.source", "clip.review"]
        engine.runner.cancel(render.id)
    finally:
        release.set()


def test_a_running_recheck_is_not_part_of_the_render(make):
    engine, _ = make()
    _, cid = packaged(engine)
    engine.render_clip(cid, {"hook_title": NEW_TITLE})
    source, review = engine.runner.jobs_for(cid)
    conn = engine.db()
    conn.execute("UPDATE jobs SET status = 'running', progress = 0.5 WHERE id = ?", (review.id,))
    assert views.clip_card(engine, repo.get_clip(conn, cid))["progress"] is None
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (source.id,))
    engine.update_clip(cid, status="analyzing")
    pipeline.clip_advance(engine, cid)
    assert kinds(engine, cid) == ["clip.source", "clip.review", "clip.analyze"]
    assert engine.runner.jobs_for(cid)[-1].priority == pipeline.USER_PRIORITY
    assert repo.get_clip(conn, cid).status == "queued"


def test_a_better_clips_recheck_holds_up_no_section():
    """SPEC 19's best clip first is about renders: a better clip waiting to be checked again, which can wait
    hours on a usage limit, holds up no other clip's section."""
    engine = fake_engine(youtube=SectionTube())
    try:
        pid = project(engine)
        add_ideas(engine, pid, 2)
        repo.update_clip(engine.db(), "c2", start_s=130.0, end_s=160.0)
        engine.runner.enqueue("clip.review", "c1", {"project": pid})
        clips.clip_source(engine, clip_ctx(engine, "c2", pid))
        assert engine.youtube.ranges == [(125.0, 165.0)]
    finally:
        engine.close()


def test_restore_rests_a_clip_whose_render_stopped_beside_a_recheck(make):
    engine, _ = make()
    _, cid = packaged(engine)
    engine.render_clip(cid, {"hook_title": NEW_TITLE})
    source, review = engine.runner.jobs_for(cid)
    conn = engine.db()
    conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = ?", (source.id,))
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (review.id,))
    engine.update_clip(cid, status="failed", error=pipeline.STOPPED, discarded_at=time.time())
    restored = engine.restore_clip(cid)
    assert (restored.status, restored.error, restored.discarded_at) == ("idea", None, None)
