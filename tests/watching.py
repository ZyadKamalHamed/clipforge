"""Builders for the drafts tests: a clipping channel with its accounts, a finished check and the projects it
started, each with clips that rendered."""

from __future__ import annotations

from clipforge import repo
from clipforge.models import ClipRow, ProjectOptions
from clipforge.post.models import Account
from clipforge.render.qa import Check, QAResult
from clipforge.watch import store
from clipforge.watch.models import ClipChannel, WatchCheck, WatchedSource, WatchItem
from tests.posting import PACK, add_render

UC = "UC" + "x" * 22


def add_channel(engine, accounts: list[Account], name: str = "Podcasts", **fields) -> ClipChannel:
    conn = engine.db()
    channel = store.add_channel(
        conn,
        ClipChannel(
            id=repo.new_id("cc"),
            name=name,
            account_ids=[a.id for a in accounts],
            created_at=0,
            updated_at=0,
            **fields,
        ),
    )
    store.add_source(
        conn,
        WatchedSource(
            id=repo.new_id("ws"),
            clip_channel_id=channel.id,
            yt_channel_id=UC,
            url=f"https://www.youtube.com/channel/{UC}/videos",
            start_published_at=0,
            created_at=0,
        ),
    )
    return channel


def add_check(engine, channel: ClipChannel, *, finished: bool = True) -> WatchCheck:
    now = engine.now().timestamp()
    return store.add_check(
        engine.db(),
        WatchCheck(
            id=repo.new_id("wc"),
            clip_channel_id=channel.id,
            check_on="2026-11-03",
            started_at=now,
            finished_at=now if finished else None,
        ),
    )


def watched(
    engine,
    channel: ClipChannel,
    check: WatchCheck,
    scores: list[float],
    *,
    status: str = "ready",
    rendered: bool = True,
    packaging: dict | None = None,
    fails: dict[int, str] | None = None,
) -> tuple[str, list[ClipRow]]:
    """A project the check started for the clipping channel, its item clipping, with a clip for each score
    (rank in the order given). ``fails`` maps a clip's index to the app its render's QA fails for."""
    conn = engine.db()
    video = repo.new_id("v")
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url=f"https://www.youtube.com/watch?v={video}",
        video_id=video,
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
        title="A talk",
    )
    repo.update_project(conn, project.id, clip_channel_id=channel.id, status=status)
    rows = [
        ClipRow(
            id=repo.new_id("c"),
            project_id=project.id,
            rank=n + 1,
            start_s=10.0 + 40 * n,
            end_s=40.0 + 40 * n,
            sentence_ids=["S1"],
            packaging=packaging or PACK,
            final_score=score,
            status="ready",
        )
        for n, score in enumerate(scores)
    ]
    repo.replace_ideas(conn, project.id, rows)
    clips = []
    for n, row in enumerate(rows):
        clip = repo.get_clip(conn, row.id)
        if rendered:
            render = add_render(engine, clip)
            if fails and n in fails:
                bad = Check(name="layout", ok=False, hard=True, message="No.", platform=fails[n])
                qa = QAResult(checks=[bad]).model_dump_json()
                conn.execute("UPDATE renders SET qa_json = ? WHERE id = ?", (qa, render.id))
        clips.append(repo.get_clip(conn, row.id))
    (source,) = store.list_sources(conn, channel.id)
    now = engine.now().timestamp()
    store.add_item(
        conn,
        WatchItem(
            id=repo.new_id("wi"),
            source_id=source.id,
            clip_channel_id=channel.id,
            check_id=check.id,
            video_id=video,
            status="clipping",
            project_id=project.id,
            first_seen_at=now,
            updated_at=now,
        ),
    )
    store.update_check(conn, check.id, clipped=len(store.items_of_check(conn, check.id)))
    return project.id, clips
