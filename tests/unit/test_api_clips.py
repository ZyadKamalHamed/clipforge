"""SPEC 14.4, 14.5 and 18: the clips API. A project's clips read as their cards show them, the editor gets
the transcript around its clip, edits are checked as they are saved, and Render, Discard, Restore,
Shuffle and Download do what their buttons say."""

import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from clipforge import repo
from clipforge.brain.score import HARD_FLAGS, SOFT_PENALTY
from clipforge.edits import effective_edits, render_key
from clipforge.jobs import pipeline
from clipforge.jobs.runner import JobContext
from clipforge.jobs.stages import read_transcript
from clipforge.models import ClipRow, Packaging, ProjectOptions, RenderRow, Sentence, Transcript, Word
from clipforge.render.edl import CARD_GONE, EDL, NO_GAMEPLAY_ITEM, EdlWord
from clipforge.render.qa import Check, QAResult
from clipforge.render.styles import Style
from clipforge.server import views
from tests.fakes import fake_engine, make_meta
from tests.server import api_client

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
NOT_FOUND = {"error": "not_found", "message": "That isn't here.", "field": None}
TITLE = "Why the deadline never scares him"
EVIL = "Evil" + chr(0x202E) + "gnp.exe" + chr(7) + "x"
CARD = {
    "id", "project_id", "rank", "start_s", "end_s", "duration_s", "final_score", "tier", "passes_gates",
    "archetype", "reason", "hook_title", "layout", "layout_reason", "flags", "notes", "status", "error",
    "render", "progress", "edited", "stale", "discarded", "posted_to", "as_is",
}  # fmt: skip
DETAIL = CARD | {
    "scores", "evidence", "packaging", "packaging_ai", "edits", "renders", "review", "sentence_ids",
    "project", "cold_open_line",
}  # fmt: skip
PACKAGING = Packaging(
    hook_title=TITLE,
    hook_alts=["He waited until the last day"],
    accent_word="deadline",
    tiktok_caption="The deadline is coming. Would you start today?",
    tiktok_hashtags=["#deadlines"],
    instagram_caption="Why the deadline never scares him, in one minute.",
    instagram_hashtags=["#deadlines"],
)


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


@pytest.fixture
def client(engine):
    return api_client(engine)  # no lifespan, so the runner never starts and jobs only queue


def talk(seconds: float = 120.0) -> Transcript:
    """A word every 0.5 s, each 0.4 s long, ten to a sentence (S1 runs from 0 to 4.9 s), and every 50th
    word from the 8th on an "um"."""
    n = int(seconds * 2)
    words = [
        Word(i=i, text="um" if i % 50 == 7 else f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4, filler=i % 50 == 7)
        for i in range(n)
    ]
    sentences = [
        Sentence(
            id=f"S{k + 1}",
            start=words[10 * k].start,
            end=words[10 * k + 9].end,
            w0=10 * k,
            w1=10 * k + 9,
            text=" ".join(w.text for w in words[10 * k : 10 * k + 10]),
        )
        for k in range(n // 10)
    ]
    return Transcript(
        engine="parakeet",
        engine_version="test",
        language="en",
        duration=seconds,
        words=words,
        sentences=sentences,
    )


def seeded(engine, **options) -> str:
    """A project whose metadata and 120 s transcript are in, as they are once it has found its clips."""
    (p,), _ = engine.add_sources(URL, ProjectOptions(**options))
    pp = engine.paths.project(p.id)
    pp.ensure()
    tr = talk()
    pp.transcript_json.write_text(tr.model_dump_json())
    pp.meta_json.write_text(make_meta(tr).model_dump_json())
    return p.id


def clip_row(
    pid: str, cid: str, rank: int | None, start: float = 40.0, end: float = 59.9, **fields
) -> ClipRow:
    """A packaged clip that passes the gates, from 40.0 to 59.9 s (S9 to S12) unless told otherwise."""
    base = {
        "id": cid,
        "project_id": pid,
        "rank": rank,
        "start_s": start,
        "end_s": end,
        "final_score": 80.0,
        "tier": "post",
        "passes_gates": True,
        "archetype": "story",
        "reason": "A setup and its payoff.",
        "status": "packaged",
        "packaging": PACKAGING.model_dump(mode="json"),
    }
    return ClipRow(**(base | fields))


def add_clips(engine, pid: str, *rows: ClipRow) -> None:
    repo.replace_ideas(engine.db(), pid, list(rows))


def add_render(
    engine, pid: str, cid: str, version: int = 1, *, words=(), notes=(), checks=(), edits_key=None, fps=30.0
) -> RenderRow:
    """A rendered version of the clip, made current: its files, its EDL and QA, and its row."""
    folder = engine.paths.project(pid).render_dir(cid, version)
    folder.mkdir(parents=True)
    video = folder / "clip.mp4"
    video.write_bytes(f"the video of {cid} v{version}".encode())
    for name in ("cover.jpg", "thumb.jpg"):
        (folder / name).write_bytes(b"a picture")
    edl = EDL(
        clip_id=cid,
        fps=fps,
        src_width=1280,
        src_height=720,
        duration=19.9,
        segments=[],
        words=list(words),
        style=Style(),
        notes=list(notes),
    )
    (folder / "edl.json").write_text(edl.model_dump_json())
    row = RenderRow(
        id=repo.new_id("r"),
        clip_id=cid,
        version=version,
        style_key="test",
        path=str(video),
        cover_path=str(folder / "cover.jpg"),
        thumb_path=str(folder / "thumb.jpg"),
        duration_s=19.9,
        size_bytes=video.stat().st_size,
        edl_path=str(folder / "edl.json"),
        qa=QAResult(checks=list(checks)).model_dump(mode="json"),
        edits_key=edits_key,
    )
    repo.add_render(engine.db(), row)
    engine.update_clip(cid, current_render_id=row.id, status="rendered")
    return row


def key_now(engine, cid: str) -> str:
    """The edits key a render of the clip as it is now would have."""
    clip = repo.get_clip(engine.db(), cid)
    return render_key(clip, effective_edits(engine.project(clip.project_id).options, clip.edits))


def set_job(engine, job_id: int, **columns) -> None:
    sets = ", ".join(f"{name} = ?" for name in columns)
    engine.db().execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*columns.values(), job_id))


def listed(client, pid: str, **params) -> list[dict]:
    r = client.get(f"/api/projects/{pid}/clips", params=params)
    assert r.status_code == 200
    return r.json()["clips"]


# The cards


def test_clip_list_shape_and_order(engine, client):
    pid = seeded(engine)
    add_clips(
        engine,
        pid,
        clip_row(pid, "c3", 3, 70.0, 89.9),
        clip_row(pid, "c1", 1),
        clip_row(pid, "cx", None, 100.0, 119.9, passes_gates=False, status="idea"),
        clip_row(pid, "c2", 2, 10.0, 29.9),
    )
    row = add_render(engine, pid, "c1", edits_key=key_now(engine, "c1"))
    engine.render_clip("c2")
    (job,) = engine.runner.jobs_for("c2")
    set_job(engine, job.id, status="running", progress=0.4)
    cards = listed(client, pid)
    assert [c["id"] for c in cards] == ["c1", "c2", "c3", "cx"]
    assert all(set(c) == CARD for c in cards)
    one, two = cards[0], cards[1]
    assert {k: v for k, v in one.items() if k not in ("duration_s", "render")} == {
        "id": "c1",
        "project_id": pid,
        "rank": 1,
        "start_s": 40.0,
        "end_s": 59.9,
        "final_score": 80.0,
        "tier": "post",
        "passes_gates": True,
        "archetype": "story",
        "reason": "A setup and its payoff.",
        "hook_title": TITLE,
        "layout": None,
        "layout_reason": None,
        "flags": [],
        "notes": [],
        "status": "rendered",
        "error": None,
        "progress": None,
        "edited": False,
        "stale": False,
        "discarded": False,
        "posted_to": [],
        "as_is": False,
    }
    assert one["duration_s"] == pytest.approx(19.9)
    v1 = f"/media/projects/{pid}/clips/c1/v1"
    assert one["render"] == {
        "id": row.id,
        "version": 1,
        "poster": f"{v1}/cover.jpg",
        "thumb": f"{v1}/thumb.jpg",
        "video": f"{v1}/clip.mp4",
        "duration_s": 19.9,
        "size_bytes": row.size_bytes,
        "qa_ok": True,
    }
    assert (two["status"], two["progress"], two["render"]) == ("queued", 0.4, None)
    assert cards[3]["rank"] is None and cards[3]["passes_gates"] is False
    r = client.get("/api/projects/nope/clips")
    assert r.status_code == 404 and r.json() == NOT_FOUND


def test_clip_card_uses_the_effective_hook_title(engine, client):
    pid = seeded(engine)
    add_clips(
        engine,
        pid,
        clip_row(pid, "c1", 1),
        clip_row(pid, "c2", 2, edits={"hook_title": "My own title"}),
        clip_row(
            pid, "c3", 3, packaging=None, hook_titles=["Discover's idea", "Another idea"], status="idea"
        ),
        clip_row(pid, "c4", 4, packaging=None, status="idea"),
    )
    assert [c["hook_title"] for c in listed(client, pid)] == [TITLE, "My own title", "Discover's idea", None]
    detail = client.get("/api/clips/c2").json()
    assert set(detail) == DETAIL
    assert detail["hook_title"] == detail["packaging"]["hook_title"] == "My own title"
    assert detail["packaging_ai"]["hook_title"] == TITLE
    idea = client.get("/api/clips/c3").json()
    assert (idea["packaging"], idea["packaging_ai"]) == (None, None)


def test_clip_detail_names_the_cold_open_line(engine, client):
    """The editor's Cold open switch quotes the line the AI picked, cleaned; with none picked, or a line
    the transcript doesn't have, there is no line to quote."""
    pid = seeded(engine)
    line = PACKAGING.model_copy(update={"cold_open_sid": "S10"}).model_dump(mode="json")
    gone = PACKAGING.model_copy(update={"cold_open_sid": "S99"}).model_dump(mode="json")
    add_clips(
        engine,
        pid,
        clip_row(pid, "c1", 1, packaging=line),
        clip_row(pid, "c2", 2),
        clip_row(pid, "c3", 3, packaging=gone),
    )
    assert client.get("/api/clips/c1").json()["cold_open_line"] == " ".join(f"w{i}" for i in range(90, 100))
    assert client.get("/api/clips/c2").json()["cold_open_line"] is None
    assert client.get("/api/clips/c3").json()["cold_open_line"] is None
    tr = read_transcript(engine.paths.project(pid))
    tr.sentences[9].text = EVIL
    engine.paths.project(pid).transcript_json.write_text(tr.model_dump_json())
    assert client.get("/api/clips/c1").json()["cold_open_line"] == "Evilgnp.exe x"
    engine.paths.project(pid).transcript_json.unlink()
    assert client.get("/api/clips/c1").json()["cold_open_line"] is None


def test_clip_card_notes_include_edl_notes_and_qa_problems(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    big = "The file is 301 MB, over Instagram's 300 MB limit."
    quiet = "The sound is quieter than most clips."
    checks = [
        Check(name="size", ok=False, hard=True, message=big, platform="instagram"),
        Check(name="loudness", ok=False, hard=False, message=quiet),
        Check(name="fps", ok=True, hard=True),
    ]
    row = add_render(engine, pid, "c1", notes=[NO_GAMEPLAY_ITEM], checks=checks)
    (card,) = listed(client, pid)
    assert card["notes"] == [NO_GAMEPLAY_ITEM + ".", big, quiet]
    assert card["render"]["qa_ok"] is False
    edl_path = Path(row.edl_path)
    edl = EDL.model_validate_json(edl_path.read_text())
    edl_path.write_text(edl.model_copy(update={"notes": [CARD_GONE]}).model_dump_json())
    later = edl_path.stat().st_mtime_ns + 5_000_000_000
    os.utime(edl_path, ns=(later, later))
    (card,) = listed(client, pid)
    assert card["notes"] == [CARD_GONE + ".", big, quiet]


def test_clip_card_is_stale_after_an_edit(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1), clip_row(pid, "c2", 2, 10.0, 29.9))
    add_render(engine, pid, "c1", edits_key=key_now(engine, "c1"))
    add_render(engine, pid, "c2")  # made before renders kept their edits key

    def chips(cid):
        got = client.get(f"/api/clips/{cid}").json()
        return got["edited"], got["stale"]

    assert chips("c1") == (False, False)
    client.patch("/api/clips/c1", json={"edits": {"tiktok_caption": "Would you start today?"}})
    assert chips("c1") == (True, False)  # the post's words aren't in the video
    client.patch("/api/clips/c1", json={"edits": {"preset": "beast"}})
    assert chips("c1") == (True, True)
    client.patch("/api/clips/c1", json={"edits": {"preset": None, "tiktok_caption": None}})
    assert chips("c1") == (False, False)
    client.patch("/api/clips/c1", json={"range": [41.0, 52.2]})
    assert chips("c1") == (True, True)
    client.patch("/api/clips/c2", json={"edits": {"preset": "beast"}})
    assert chips("c2") == (True, False)  # without a key it isn't known to be out of date


def test_flags_are_sentences(engine, client):
    pid = seeded(engine)
    flags = {
        "red": ["meaning_distortion", "made_up_code"],
        "soft": ["jargon"],
        "notes": ["over_90s_instagram", "rerank_missing"],
    }
    add_clips(
        engine,
        pid,
        clip_row(pid, "c1", 1, 0.0, 99.9, flags=flags),
        clip_row(pid, "c2", 2, flags={"red": ["too_short"], "soft": [], "notes": ["over_90s_instagram"]}),
        clip_row(pid, "c3", 3, 40.0, 44.9),
    )
    cards = {c["id"]: c for c in listed(client, pid)}
    assert cards["c1"]["flags"] == [
        {
            "code": "meaning_distortion",
            "text": "The cut makes the speaker seem to say something they didn't mean.",
            "level": "red",
        },
        {"code": "made_up_code", "text": "The AI flagged this clip: made up code.", "level": "red"},
        {"code": "jargon", "text": "It uses terms a general viewer won't know.", "level": "soft"},
        {
            "code": "over_90s_instagram",
            "text": "It is over 90 seconds, which is long for Instagram.",
            "level": "soft",
        },
    ]
    assert cards["c2"]["flags"] == []  # its length was fixed when it moved
    assert cards["c3"]["flags"] == [{"code": "too_short", "text": "It is under 12 seconds.", "level": "red"}]
    for code in sorted(HARD_FLAGS | set(SOFT_PENALTY)):
        text = views.flag_text(code)
        assert text[0].isupper() and text.endswith(".") and not text.startswith("The AI flagged"), code


# The editor's transcript


def test_transcript_window_is_30_s_either_side_and_clamped(engine, client):
    pid = seeded(engine)
    add_clips(
        engine,
        pid,
        clip_row(pid, "c1", 1),
        clip_row(pid, "c2", 2, 5.0, 19.9),
        clip_row(pid, "c3", 3, 100.0, 115.0),
    )
    got = client.get("/api/clips/c1/transcript").json()
    assert set(got) == {"range", "orig_range", "window", "fps", "words", "sentences"}
    assert got["range"] == pytest.approx([40.0, 59.9]) and got["orig_range"] is None
    assert got["window"] == pytest.approx([10.0, 89.9])
    assert got["fps"] is None  # nothing rendered yet
    words = got["words"]
    assert [w["i"] for w in words] == list(range(20, 180))
    assert words[0] == {
        "i": 20,
        "text": "w20",
        "start": 10.0,
        "end": 10.4,
        "sid": "S3",
        "filler": False,
        "struck": False,
        "fix": None,
        "out": None,
    }
    assert [(s["id"], s["in_clip"]) for s in got["sentences"]] == [
        (f"S{k}", 9 <= k <= 12) for k in range(3, 19)
    ]
    assert got["sentences"][0] == {"id": "S3", "start": 10.0, "end": 14.9, "in_clip": False}
    early = client.get("/api/clips/c2/transcript").json()
    assert early["window"] == pytest.approx([0.0, 49.9]) and early["words"][0]["i"] == 0
    late = client.get("/api/clips/c3/transcript").json()
    assert late["window"] == pytest.approx([70.0, 120.0]) and late["words"][-1]["i"] == 239
    r = client.get("/api/clips/nope/transcript")
    assert r.status_code == 404 and r.json() == NOT_FOUND


def test_transcript_words_carry_output_times(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1, edits={"struck": [85], "word_fixes": {"90": "ninety"}}))
    shown = [i for i in range(80, 120) if i not in (85, 107)]  # 107 is an "um", cut as a filler
    words = [
        EdlWord(i=i, sid=f"S{i // 10 + 1}", text=f"w{i}", start=n * 0.5, end=n * 0.5 + 0.4)
        for n, i in enumerate(shown)
    ]
    add_render(engine, pid, "c1", words=words, fps=29.97)
    got = client.get("/api/clips/c1/transcript").json()
    by_i = {w["i"]: w for w in got["words"]}
    assert got["fps"] == 29.97
    assert (by_i[80]["out"], by_i[86]["out"]) == (0.0, 2.5)
    assert (by_i[85]["struck"], by_i[85]["out"]) == (True, None)
    assert (by_i[107]["filler"], by_i[107]["out"]) == (True, None)
    assert (by_i[90]["fix"], by_i[90]["text"]) == ("ninety", "w90")
    assert by_i[20]["out"] is None and by_i[120]["out"] is None


# Saving edits


@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        ({"edits": {"accent": "yellow"}}, "edits.accent", "The accent colour must look like #FFD60A."),
        (
            {"edits": {"accent_word": "banana"}},
            "edits.accent_word",
            "The accent word must be one of the hook title's words.",
        ),
        (
            {"edits": {"fact_cards": [{"sid": "S1", "text": "Tim Urban writes Wait But Why."}]}},
            "edits.fact_cards.0",
            "Fact card 1's sentence isn't in the clip.",
        ),
        (
            {"edits": {"gameplay_item": "gone"}},
            "edits.gameplay_item",
            "That gameplay video isn't in the library.",
        ),
        ({"edits": {"sparkles": True}}, "edits.sparkles", "ClipForge doesn't have that edit."),
        ({"range": [40.0, 41.0]}, "range", "The clip must be at least 3 seconds long."),
        ({"range": [100.0, 130.0]}, "range", "The clip must start and end inside the video."),
        ({"range": [50.0, 45.0]}, "range", "The clip's end must come after its start."),
        ({"range": "soon"}, "range", "The clip's range must be a start and an end, in seconds."),
        (
            {"edits": {"preset": "beast"}, "range": [40.0, 41.0]},
            "range",
            "The clip must be at least 3 seconds long.",
        ),
        ({"edits": "beast"}, "edits", "ClipForge couldn't read that request."),
        ({"sparkles": 1}, "sparkles", "ClipForge couldn't read that request."),
    ],
)
def test_patch_returns_422_with_field_and_sentence(engine, client, body, field, message):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    before = repo.get_clip(engine.db(), "c1")
    r = client.patch("/api/clips/c1", json=body)
    assert r.status_code == 422
    assert r.json() == {"error": "invalid", "message": message, "field": field}
    assert repo.get_clip(engine.db(), "c1") == before  # nothing was saved


def test_patch_range_moves_the_clip(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1, sentence_ids=["S9", "S10", "S11", "S12"]))
    r = client.patch("/api/clips/c1", json={"range": [41.0, 52.2]})
    assert r.status_code == 200
    got = r.json()
    assert set(got) == DETAIL
    assert (got["start_s"], got["end_s"]) == pytest.approx((41.0, 51.9))
    assert got["sentence_ids"] == ["S9", "S10", "S11"]
    assert got["edits"] == {"orig_range": [40.0, 59.9]} and got["edited"] is True
    window = client.get("/api/clips/c1/transcript").json()
    assert window["range"] == pytest.approx([41.0, 51.9]) and window["orig_range"] == pytest.approx(
        [40.0, 59.9]
    )
    kept = client.patch("/api/clips/c1", json={"edits": {"preset": "beast"}}).json()
    assert (kept["start_s"], kept["end_s"]) == pytest.approx((41.0, 51.9))
    back = client.patch("/api/clips/c1", json={"range": None}).json()
    assert (back["start_s"], back["end_s"]) == pytest.approx((40.0, 59.9))
    assert back["edits"] == {"preset": "beast"} and back["sentence_ids"] == ["S9", "S10", "S11", "S12"]
    r = client.patch("/api/clips/nope", json={"range": [41.0, 52.2]})
    assert r.status_code == 404 and r.json() == NOT_FOUND


# The buttons


def test_render_saves_and_queues(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    r = client.post("/api/clips/c1/render", json={"edits": {"preset": "beast"}, "range": [41.0, 52.2]})
    assert r.status_code == 202
    got = r.json()
    assert set(got) == DETAIL
    assert got["edits"] == {"preset": "beast", "orig_range": [40.0, 59.9]}
    assert got["end_s"] == pytest.approx(51.9)
    assert (got["status"], got["error"]) == ("queued", None)
    job, review = engine.runner.jobs_for("c1")
    assert (job.kind, job.status, job.priority) == ("clip.source", "queued", pipeline.USER_PRIORITY)
    assert review.kind == "clip.review"  # it was moved, so it is checked again beside its render
    again = client.post("/api/clips/c1/render")  # no body: render it as it is
    assert again.status_code == 202 and again.json()["edits"] == got["edits"]
    bad = client.post("/api/clips/c1/render", json={"edits": {"accent": "yellow"}})
    assert bad.status_code == 422 and bad.json()["field"] == "edits.accent"
    assert len(engine.runner.jobs_for("c1")) == 2  # the render and its re-check, each queued once
    r = client.post("/api/clips/nope/render")
    assert r.status_code == 404 and r.json() == NOT_FOUND


def test_discard_cancels_jobs_and_restore_brings_it_back(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1), clip_row(pid, "c2", 2, 10.0, 29.9))
    client.post("/api/clips/c1/render")
    (queued,) = engine.runner.jobs_for("c1")
    add_render(engine, pid, "c2")
    client.post("/api/clips/c2/render")
    (running,) = engine.runner.jobs_for("c2")
    set_job(engine, running.id, status="running")
    r = client.post("/api/clips/c1/discard")
    assert r.status_code == 200
    card = r.json()
    assert set(card) == CARD
    assert (card["discarded"], card["status"], card["error"]) == (True, "idea", None)
    assert engine.runner.get(queued.id).status == "cancelled"
    two = client.post("/api/clips/c2/discard").json()
    assert (two["discarded"], two["status"], two["error"]) == (
        True,
        "rendered",
        None,
    )  # its last render stays
    assert engine.runner.get(running.id).status == "cancelled"
    assert listed(client, pid) == []
    assert [(c["id"], c["discarded"]) for c in listed(client, pid, discarded=1)] == [
        ("c1", True),
        ("c2", True),
    ]
    assert client.get(f"/api/projects/{pid}").json()["clip_count"] == 0
    back = client.post("/api/clips/c1/restore")
    assert back.status_code == 200
    assert {k: back.json()[k] for k in ("discarded", "status", "error")} == {
        "discarded": False,
        "status": "idea",
        "error": None,
    }
    assert [c["id"] for c in listed(client, pid)] == ["c1"]
    for action in ("discard", "restore"):
        r = client.post(f"/api/clips/nope/{action}")
        assert r.status_code == 404 and r.json() == NOT_FOUND


def test_discard_leaves_a_blocked_render_as_it_was(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    big = "The file is over both apps' size limits."
    row = add_render(engine, pid, "c1", checks=[Check(name="size", ok=False, hard=True, message=big)])
    engine.db().execute("UPDATE renders SET status = 'failed' WHERE id = ?", (row.id,))
    engine.update_clip("c1", status="failed", error=big)
    client.post("/api/clips/c1/render")  # Try again, then Discard while it waits
    card = client.post("/api/clips/c1/discard").json()
    assert (card["status"], card["error"], card["render"]["qa_ok"]) == ("failed", big, False)


def test_discarded_clips_are_not_auto_rendered(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1), clip_row(pid, "c2", 2, 10.0, 29.9))
    engine.settings = engine.settings.model_copy(update={"auto_render": "all"})
    client.post("/api/clips/c1/discard")
    assert pipeline.render_queue(engine, pid) == ["c2"]
    assert engine.runner.jobs_for("c1") == []
    assert repo.get_clip(engine.db(), "c1").status == "packaged"


def test_a_clip_job_cancelled_while_it_waits_for_the_lock_stops_there(engine):
    """Discarding a clip can cancel its job just as a worker picks it up: the worker then stops without
    marking the clip busy again."""
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    engine.render_clip("c1")
    (job,) = engine.runner.jobs_for("c1")
    set_job(engine, job.id, status="running")
    _, handler = pipeline.handlers(engine)["clip.source"]
    got = {}

    def pick_up():
        ctx = JobContext(engine.runner.get(job.id), engine.db(), engine.paths, engine.settings, engine.events)
        got["result"] = handler(ctx)

    worker = threading.Thread(target=pick_up)
    with engine.lock:
        worker.start()
        time.sleep(0.3)  # the worker has seen its job running and waits for the lock
        engine.runner.cancel(job.id)
    worker.join(10)
    assert got == {"result": None}
    assert repo.get_clip(engine.db(), "c1").status == "failed"  # as the cancel left it, not "analyzing"


def test_shuffle_route_queues_a_render(engine, client):
    pid = seeded(engine)
    add_clips(engine, pid, clip_row(pid, "c1", 1))
    r = client.post("/api/clips/c1/gameplay/shuffle")
    assert r.status_code == 202
    got = r.json()
    assert set(got) == DETAIL
    assert (got["edits"], got["status"], got["edited"]) == ({"gameplay_seed": 1}, "queued", False)
    (job,) = engine.runner.jobs_for("c1")
    assert (job.kind, job.priority) == ("clip.source", pipeline.USER_PRIORITY)
    assert client.post("/api/clips/c1/gameplay/shuffle").json()["edits"] == {"gameplay_seed": 2}
    r = client.post("/api/clips/nope/gameplay/shuffle")
    assert r.status_code == 404 and r.json() == NOT_FOUND


# Downloads


def test_download_zip_of_selected_clips(engine, client):
    pid = seeded(engine)
    add_clips(
        engine,
        pid,
        clip_row(pid, "c1", 1),
        clip_row(pid, "c2", 2, 10.0, 29.9, edits={"hook_title": "Start today?"}),
        clip_row(pid, "c3", 3, 70.0, 89.9),
    )
    add_render(engine, pid, "c1")
    newest = add_render(engine, pid, "c1", version=2)
    add_render(engine, pid, "c2")
    r = client.get("/api/clips/download", params={"ids": "c2,c1,c3"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="ClipForge clips.zip"'
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        infos = zf.infolist()
        assert [i.filename for i in infos] == ["2 Start today.mp4", f"1 {TITLE}.mp4"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in infos)
        assert zf.read(f"1 {TITLE}.mp4") == Path(newest.path).read_bytes()
    r = client.get("/api/clips/download", params=[("ids", "c1"), ("ids", "c1,c2")])  # asked twice: once
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == [f"1 {TITLE}.mp4", "2 Start today.mp4"]
    r = client.get("/api/clips/download", params={"ids": "c3"})
    assert r.status_code == 409
    assert r.json() == {
        "error": "nothing_to_download",
        "message": "None of those clips has a render to download yet.",
        "field": None,
    }
    r = client.get("/api/clips/download", params={"ids": ""})
    assert r.status_code == 422 and r.json()["field"] == "ids"


def test_download_refuses_more_than_40(engine, client):
    r = client.get("/api/clips/download", params={"ids": ",".join(f"c{n}" for n in range(41))})
    assert r.status_code == 422
    assert r.json() == {"error": "invalid", "message": "Download at most 40 clips at a time.", "field": "ids"}
    r = client.get("/api/clips/download", params={"ids": ",".join(f"c{n}" for n in range(40))})
    assert r.status_code == 409  # 40 is allowed; none of these has a render


# Text from outside


def test_hostile_text_in_clip_fields_is_cleaned(engine, client):
    pid = seeded(engine)
    pp = engine.paths.project(pid)
    tr = talk()
    tr.words[85] = tr.words[85].model_copy(update={"text": EVIL})
    pp.transcript_json.write_text(tr.model_dump_json())
    pp.meta_json.write_text(make_meta(tr, channel=EVIL).model_dump_json())
    repo.update_project(engine.db(), pid, title=EVIL)
    hostile = PACKAGING.model_copy(update={"hook_title": EVIL, "hook_alts": [EVIL], "tiktok_caption": EVIL})
    review = {"complete_story": True, "meaning_changed": False, "true_title_index": 0, "cards_supported": []}
    add_clips(
        engine,
        pid,
        clip_row(
            pid,
            "c1",
            1,
            reason=EVIL,
            archetype=EVIL,
            layout_reason=EVIL,
            error=EVIL,
            hook_titles=[EVIL],
            evidence=[{"criterion": "C1", "quote": EVIL}],
            flags={"red": [EVIL], "soft": [], "notes": []},
            packaging=hostile.model_dump(mode="json"),
            review=review | {"notes": EVIL},
            edits={"word_fixes": {"90": EVIL}},
        ),
    )
    add_render(
        engine, pid, "c1", notes=[EVIL], checks=[Check(name="odd", ok=False, hard=False, message=EVIL)]
    )
    shown = json.dumps(
        [
            listed(client, pid),
            client.get("/api/clips/c1").json(),
            client.get("/api/clips/c1/transcript").json(),
        ],
        ensure_ascii=False,
    )
    assert "Evil" in shown and "Source: Evil" in shown
    assert chr(0x202E) not in shown and chr(7) not in shown
    r = client.get("/api/clips/download", params={"ids": "c1"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        (name,) = zf.namelist()
    assert name.startswith("1 Evil") and name.endswith(".mp4")
    assert chr(0x202E) not in name and chr(7) not in name


def test_captions_keep_their_line_breaks(engine, client):
    """A caption of several lines shows as written, so the Copy tab's next save keeps its lines; its other
    control characters and the marks that turn text around still go, and other text stays on one line."""
    pid = seeded(engine)
    written = PACKAGING.model_copy(update={"tiktok_caption": "Line one\nLine two" + chr(0x202E) + chr(7)})
    add_clips(engine, pid, clip_row(pid, "c1", 1, packaging=written.model_dump(mode="json"), reason="A\nB"))
    got = client.get("/api/clips/c1").json()
    assert (
        got["packaging"]["tiktok_caption"] == got["packaging_ai"]["tiktok_caption"] == "Line one\nLine two "
    )
    assert got["reason"] == "A B"
    assert (
        client.patch("/api/clips/c1", json={"edits": {"instagram_caption": "First\n\nSecond"}}).status_code
        == 200
    )
    got = client.get("/api/clips/c1").json()
    assert got["edits"]["instagram_caption"] == got["packaging"]["instagram_caption"] == "First\n\nSecond"
