import re

import pytest

from clipforge import repo
from clipforge.config import Settings
from clipforge.db import connect, migrate
from clipforge.models import ClipRow, ProjectOptions
from tests.posting import bare_post


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def make_project(conn, video_id="arj7oStGLkU", **kw):
    return repo.create_project(
        conn,
        source_kind="youtube",
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        options=kw.pop("options", ProjectOptions()),
        focus_s=kw.pop("focus_s", None),
        prompt=kw.pop("prompt", None),
        **kw,
    )


def clip(pid, rank, status="idea", start=10.0):
    return ClipRow(
        id=repo.new_id("c"),
        project_id=pid,
        rank=rank,
        start_s=start,
        end_s=start + 30.0,
        sentence_ids=["S1", "S4"],
        scores={"C1": 8},
        flags={"red": [], "soft": ["needs_context"]},
        status=status,
    )


def test_new_id_format():
    assert re.fullmatch(r"p[0-9a-f]{10}", repo.new_id("p"))
    assert re.fullmatch(r"c[0-9a-f]{10}", repo.new_id("c"))


def test_project_roundtrip(conn):
    options = ProjectOptions(length="short", clip_count=6, prompt="the money advice")
    p = make_project(conn, options=options, focus_s=842.0, prompt="the money advice", title="A talk")
    got = repo.get_project(conn, p.id)
    assert got is not None
    assert got.options == options and isinstance(got.options, ProjectOptions)
    assert got.status == "queued" and got.rights_basis == "not_cleared" and got.progress == 0
    assert got.focus_s == 842.0 and got.title == "A talk"
    assert got.created_at <= got.updated_at
    repo.update_project(
        conn, p.id, status="running", stage="ingest.audio", options=ProjectOptions(render=False)
    )
    got2 = repo.get_project(conn, p.id)
    assert got2.status == "running" and got2.stage == "ingest.audio" and got2.options.render is False
    assert got2.updated_at >= got.updated_at
    assert repo.get_project(conn, "pmissing000") is None


def test_update_project_rejects_unknown_fields(conn):
    p = make_project(conn)
    with pytest.raises(ValueError):
        repo.update_project(conn, p.id, bogus=1)


def test_list_projects_newest_first(conn):
    a = make_project(conn, "aaaaaaaaaaa")
    b = make_project(conn, "bbbbbbbbbbb")
    assert [p.id for p in repo.list_projects(conn)] == [b.id, a.id]
    assert len(repo.list_projects(conn, limit=1)) == 1


def test_find_projects_by_video(conn):
    a = make_project(conn, "arj7oStGLkU")
    make_project(conn, "zzzzzzzzzzz")
    b = make_project(conn, "arj7oStGLkU")
    assert [p.id for p in repo.find_projects_by_video(conn, "arj7oStGLkU")] == [b.id, a.id]
    assert repo.find_projects_by_video(conn, "nothere0000") == []


def test_options_prompt_is_capped():
    assert len(ProjectOptions(prompt="x" * 500).prompt) == 300
    assert ProjectOptions(prompt="   ").prompt is None


def test_replace_ideas_replaces_every_clip_without_edits(conn):
    p = make_project(conn)
    kept = clip(p.id, 1, status="rendered").model_copy(update={"edits": {"preset": "clean"}})
    plain, packaged, idea = clip(p.id, 2, status="rendered"), clip(p.id, 3, status="packaged"), clip(p.id, 4)
    assert repo.replace_ideas(conn, p.id, [kept, plain, packaged, idea]) == []
    assert repo.replace_ideas(conn, p.id, [clip(p.id, 5, start=99.0)]) == [plain.id, packaged.id, idea.id]
    clips = repo.list_clips(conn, p.id)
    assert [c.status for c in clips] == ["rendered", "idea"]
    assert clips[0].id == kept.id and clips[0].edits == {"preset": "clean"} and clips[1].start_s == 99.0
    assert clips[0].sentence_ids == ["S1", "S4"] and clips[0].flags == {"red": [], "soft": ["needs_context"]}


def test_rerun_drops_counts_the_rendered_clips_a_rerun_deletes(conn):
    p = make_project(conn)
    edited = clip(p.id, 1, status="rendered").model_copy(update={"edits": {"preset": "clean"}})
    plain, busy, idea = clip(p.id, 2, status="rendered"), clip(p.id, 3, status="rendering"), clip(p.id, 4)
    set_aside = clip(p.id, 5, status="rendered").model_copy(update={"discarded_at": 1.0})
    repo.replace_ideas(conn, p.id, [edited, plain, busy, idea, set_aside])
    assert repo.rerun_drops(repo.list_clips(conn, p.id), set()) == 2  # edits and running jobs keep theirs
    assert repo.replace_ideas(conn, p.id, []) == [plain.id, idea.id, set_aside.id]


def test_a_rerun_keeps_the_clips_posts_were_made_from(conn):
    """A post waiting in the Queue, a draft and one that went up all keep their clip; a post you cancelled
    doesn't."""
    p = make_project(conn)
    rows = [clip(p.id, rank, status="rendered") for rank in (1, 2, 3, 4)]
    repo.replace_ideas(conn, p.id, rows)
    for row, status in zip(rows, ["scheduled", "posted", "draft", "cancelled"], strict=True):
        conn.execute("UPDATE posts SET clip_id = ? WHERE id = ?", (row.id, bare_post(conn, status)))
    assert repo.replace_ideas(conn, p.id, []) == [rows[3].id]
    assert repo.rerun_drops(repo.list_clips(conn, p.id), repo.posted_clips(conn, p.id)) == 0


def test_clip_hook_titles_roundtrip(conn):
    p = make_project(conn)
    titled = clip(p.id, 1).model_copy(update={"hook_titles": ["He quit", "Why he quit"]})
    repo.replace_ideas(conn, p.id, [titled, clip(p.id, 2)])
    assert [c.hook_titles for c in repo.list_clips(conn, p.id)] == [["He quit", "Why he quit"], []]
    repo.update_clip(conn, titled.id, hook_titles=["She stayed"])
    assert repo.list_clips(conn, p.id)[0].hook_titles == ["She stayed"]


def test_update_clip_json_fields(conn):
    p = make_project(conn)
    c = clip(p.id, 1)
    repo.replace_ideas(conn, p.id, [c])
    repo.update_clip(conn, c.id, status="packaged", packaging={"hook_title": "He quit"}, final_score=77.5)
    got = repo.list_clips(conn, p.id)[0]
    assert got.status == "packaged" and got.packaging == {"hook_title": "He quit"} and got.final_score == 77.5


def test_delete_project_cascades_clips(conn):
    p = make_project(conn)
    repo.replace_ideas(conn, p.id, [clip(p.id, 1), clip(p.id, 2)])
    conn.execute("DELETE FROM projects WHERE id = ?", (p.id,))
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


def test_settings_roundtrip_and_unknown_keys_ignored(conn):
    assert repo.load_settings(conn) == Settings()
    repo.save_settings(conn, Settings(llm_parallel=2, own_channels=["@me"]))
    conn.execute("INSERT INTO settings (key, value_json) VALUES ('bogus', '1')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value_json) VALUES ('asr_engine', '\"nonsense\"')")
    s = repo.load_settings(conn)
    assert s.llm_parallel == 2 and s.own_channels == ["@me"]
    assert s.asr_engine == "auto"  # a stored value that no longer validates falls back to the default


def test_replace_ideas_replaces_clips_only_waiting_to_render(conn):
    p = make_project(conn)
    busy = [clip(p.id, 1, status="analyzing"), clip(p.id, 2, status="rendering")]
    repo.replace_ideas(conn, p.id, [*busy, clip(p.id, 3, status="queued"), clip(p.id, 4, status="failed")])
    repo.replace_ideas(conn, p.id, [clip(p.id, 5, start=99.0)])
    assert [c.status for c in repo.list_clips(conn, p.id)] == ["analyzing", "rendering", "idea"]
