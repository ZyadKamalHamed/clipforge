"""brain.package writes its titles onto clips you may have rendered or moved while it ran (SPEC 9.2, 14.5):
what you did stays, so a clip you rendered isn't put back in the queue with no job and a range you moved
isn't moved back."""

import json
from types import SimpleNamespace

from clipforge import repo
from clipforge.jobs.stages import brain
from clipforge.models import ClipRow, ProjectOptions
from tests.fakes import fake_engine
from tests.unit.test_package import TR, cand


def test_packaging_leaves_a_clip_you_rendered_or_moved_meanwhile(monkeypatch):
    engine = fake_engine()
    try:
        conn = engine.db()
        project = repo.create_project(
            conn,
            source_kind="file",
            source_url=None,
            video_id=None,
            options=ProjectOptions(),
            focus_s=None,
            prompt=None,
        )
        cands = [cand("k1", 8, 13), cand("k2", 14, 19), cand("k3", 20, 25)]
        repo.replace_ideas(
            conn,
            project.id,
            [
                ClipRow(id=f"c{k}", project_id=project.id, rank=k, start_s=c.start_s, end_s=c.end_s)
                for k, c in enumerate(cands, 1)
            ],
        )
        pp = engine.paths.project(project.id)
        pp.candidates_json.parent.mkdir(parents=True, exist_ok=True)
        pp.candidates_json.write_text(json.dumps([c.model_dump(mode="json") for c in cands]))
        monkeypatch.setattr(brain, "job_result", lambda conn, pid, kind: {"clips": ["c1", "c2", "c3"]})
        bctx = SimpleNamespace(transcript=TR, llm_disabled=False, notes=[])
        monkeypatch.setattr(brain, "_context", lambda *args, **kwargs: (bctx, None))

        def packaging(bctx, given):
            """While the titles are written, you render the first idea and move the second one's start."""
            repo.update_clip(conn, "c1", status="queued")
            repo.update_clip(
                conn, "c2", start_s=40.0, edits={"orig_range": [given[1].start_s, given[1].end_s]}
            )
            return [
                c.model_copy(update={"start_s": c.start_s + 1, "final_score": 80.0 - k})
                for k, c in enumerate(given)
            ]

        monkeypatch.setattr(brain, "run_packaging", packaging)
        ctx = SimpleNamespace(job=SimpleNamespace(ref_id=project.id), conn=conn, settings=engine.settings)
        assert brain.package(engine, ctx)["packaged"] == 3
        got = {c.id: c for c in repo.list_clips(conn, project.id)}
        assert (got["c1"].status, got["c1"].start_s) == ("queued", cands[0].start_s)
        assert (got["c2"].status, got["c2"].start_s) == ("packaged", 40.0)
        assert (got["c3"].status, got["c3"].start_s) == ("packaged", cands[2].start_s + 1)
        assert [got[cid].rank for cid in ("c1", "c2", "c3")] == [1, 2, 3]
    finally:
        engine.close()
