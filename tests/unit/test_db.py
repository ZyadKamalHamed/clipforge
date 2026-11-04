from importlib import resources

import pytest

from clipforge.db import connect, migrate, transaction


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


def columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migrate_fresh_db_sets_version_13(conn):
    assert migrate(conn) == 13
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"projects", "clips", "jobs", "settings"} <= tables
    assert {"clip_channels", "watched_sources", "watch_items", "watch_checks"} <= tables
    assert {"todo_items", "item_timings", "days"} <= tables
    assert {"brief_sources", "briefs", "advice_hidden", "site_reads"} <= tables
    assert {"sounds", "hashtag_sets", "trend_reads"} <= tables
    assert {"brief_id", "joined_at"} <= columns(conn, "campaigns")
    assert "clip_channel_id" in columns(conn, "projects")
    assert {"hook_titles_json", "discarded_at"} <= columns(conn, "clips")
    assert "edits_key" in columns(conn, "renders")
    assert "error_details" in columns(conn, "jobs") & columns(conn, "projects")


def test_migrate_gives_old_clips_no_titles(conn):
    first = (resources.files("clipforge.migrations") / "001_initial.sql").read_text(encoding="utf-8")
    conn.executescript(f"BEGIN;\n{first}\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute("INSERT INTO projects (id, source_kind, created_at, updated_at) VALUES ('p1', 'file', 0, 0)")
    conn.execute(
        "INSERT INTO clips (id, project_id, start_s, end_s, sentence_ids, created_at, updated_at)"
        " VALUES ('c1', 'p1', 0, 30, '[]', 0, 0)"
    )
    assert migrate(conn) == 13
    row = conn.execute("SELECT hook_titles_json FROM clips WHERE id = 'c1'").fetchone()
    assert row["hook_titles_json"] == "[]"


def test_migrate_twice_is_noop(conn):
    migrate(conn)
    conn.execute(
        "INSERT INTO settings (key, value_json) VALUES ('llm_parallel', '2')",
    )
    assert migrate(conn) == 13
    assert conn.execute("SELECT value_json FROM settings").fetchone()[0] == "2"


def test_migrate_from_6_keeps_projects_unwatched(conn):
    for number in range(1, 7):
        name = next(
            e.name
            for e in resources.files("clipforge.migrations").iterdir()
            if e.name.startswith(f"{number:03d}_")
        )
        sql = (resources.files("clipforge.migrations") / name).read_text(encoding="utf-8")
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("INSERT INTO projects (id, source_kind, created_at, updated_at) VALUES ('p1', 'file', 0, 0)")
    assert migrate(conn) == 13
    assert conn.execute("SELECT clip_channel_id FROM projects WHERE id = 'p1'").fetchone()[0] is None


def test_connection_uses_wal(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_transaction_rolls_back_on_error(conn):
    migrate(conn)
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO settings (key, value_json) VALUES ('a', '1')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    with transaction(conn):
        conn.execute("INSERT INTO settings (key, value_json) VALUES ('a', '1')")
        with transaction(conn):  # an inner block joins the outer transaction
            conn.execute("INSERT INTO settings (key, value_json) VALUES ('b', '2')")
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 2
