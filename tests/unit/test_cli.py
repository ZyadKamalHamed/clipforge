import json
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from clipforge import cli, repo, service
from clipforge.db import connect
from clipforge.doctor import Check
from clipforge.errors import LLMError, ToolError
from clipforge.ingest.urls import OTHER_SITE
from clipforge.jobs import pipeline
from clipforge.jobs.runner import STUCK
from clipforge.jobs.stages import clips
from clipforge.models import ClipRow, ProjectOptions, RenderRow
from clipforge.paths import Paths
from clipforge.render.edl import EDL
from clipforge.render.qa import Check as QACheck
from clipforge.render.qa import QAResult
from tests.fakes import (
    TITLE,
    FakeProvider,
    FakeSpeech,
    brain_handlers,
    discover_answer,
    fake_engine,
    lines,
    rerank_about,
    review_answer,
)
from tests.helpers import make_test_video, make_wav
from tests.unit.test_clip_jobs import OneFace, SectionTube
from tests.unit.test_render_job import UNREADABLE, ready_clip

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
PID = re.compile(r"\b(p[0-9a-f]{10})\b")
STAGES = [
    "Reading the video's details",
    "Getting the audio",
    "Transcribing",
    "Reading the signals",
    "Finding the best moments",
    "Writing hook titles and captions",
]


@pytest.fixture
def use_fakes(monkeypatch):
    """Points the CLI at engines with fakes, ``use_fakes(**handlers)``, and gives back their provider."""

    def use(**handlers):
        provider, speech = FakeProvider(brain_handlers(**handlers)), FakeSpeech(lines(36))
        monkeypatch.setattr(
            cli,
            "open_engine",
            lambda *, verbose=False: fake_engine(speech=speech, provider=provider, verbose=verbose),
        )
        return provider

    return use


def run_cli(capsys, *argv: str) -> tuple[int, str, str]:
    """The exit code and what ``clipforge <argv>`` printed to stdout and stderr."""
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ran(capsys, *options: str) -> str:
    """The id of the project that ``clipforge run`` made for URL, once it is ready."""
    code, out, _ = run_cli(capsys, "run", URL, "--no-render", *options)
    assert code == 0, out
    return PID.search(out).group(1)


def distorted(req):
    """A review that finds every clip changes the speaker's meaning."""
    answer = review_answer(req)
    for clip in answer["clips"]:
        clip["meaning_changed"] = True
    return answer


# Doctor


def test_cli_doctor_prints_checks_and_exit_code(monkeypatch, capsys):
    checks = [
        Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg", "", True),
        Check("speech model", False, "not downloaded yet", "It downloads on the first run.", False),
    ]
    monkeypatch.setattr(cli, "run_checks", lambda paths, settings: checks)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out and "speech model" in out and "It downloads on the first run." in out

    checks.append(Check("ffprobe", False, "not found", "brew install ffmpeg", True))
    assert cli.main(["doctor"]) == 1


# Running


def test_cli_run_prints_stages_then_the_clips(use_fakes, capsys):
    use_fakes()
    code, out, _ = run_cli(capsys, "run", URL)
    assert code == 0
    pid = PID.search(out).group(1)
    assert out.startswith(f"Project {pid}: {URL}\n")
    places = [out.index(stage) for stage in STAGES]
    assert places == sorted(places)  # one line per stage, in order
    assert places[-1] < out.index("Ready: 2 clips.") < out.index(TITLE)
    assert out.count(TITLE) == 2 and " to " in out and chr(0x2013) not in out


def test_cli_has_a_line_for_every_step_a_project_waits_on():
    required = [stage for stage in pipeline.STAGES if stage.required]
    waited_on = {stage.kind for stage in required} | {kind for stage in required for kind in stage.after}
    assert waited_on <= set(cli.STAGE_TEXT)


def test_cli_has_a_line_for_every_step_of_post_as_it_is():
    assert {stage.kind for stage in pipeline.AS_IS.stages} <= set(cli.STAGE_TEXT)


def test_cli_run_json_prints_only_the_clip_rows(use_fakes, capsys):
    use_fakes()
    code, out, err = run_cli(capsys, "run", URL, "--no-render", "--json")
    assert code == 0
    rows = json.loads(out)
    assert [r["packaging"]["hook_title"] for r in rows if r["status"] == "packaged"] == [TITLE, TITLE]
    assert {r["project_id"] for r in rows} == {PID.search(err).group(1)}
    assert "Finding the best moments" in err  # the progress goes to stderr


def test_cli_run_options_reach_the_project(use_fakes, capsys):
    use_fakes()
    pid = ran(capsys, "--clips", "1", "--length", "short", "--about", "deadlines", "--content", "solo")
    engine = fake_engine()
    try:
        project = engine.project(pid)
        assert project.options == ProjectOptions(
            length="short", clip_count=1, content_type="solo", prompt="deadlines", render=False
        )
        assert project.prompt == "deadlines"
    finally:
        engine.close()


def test_cli_run_follows_two_sources_at_once(use_fakes, capsys):
    use_fakes()
    code, out, _ = run_cli(capsys, "run", URL, "https://youtu.be/dQw4w9WgXcQ", "--no-render")
    assert code == 0
    first, second = dict.fromkeys(PID.findall(out))
    for pid in (first, second):
        assert f"{pid}: Finding the best moments" in out  # each line says which project it is about
    assert out.count("Ready: 2 clips.") == 2


def test_cli_run_says_what_it_cannot_use(use_fakes, capsys):
    use_fakes()
    code, _, err = run_cli(capsys, "run", "https://vimeo.com/123")
    assert code == 1
    assert f"Can't use https://vimeo.com/123: {OTHER_SITE}" in err
    assert "Nothing to clip." in err


def test_cli_run_uses_the_existing_project_of_a_video(use_fakes, capsys):
    fake = use_fakes()
    pid = ran(capsys)
    asked = len(fake.requests)
    code, out, _ = run_cli(capsys, "run", "https://youtu.be/arj7oStGLkU", "--no-render")
    assert code == 0
    assert f"Using the existing project {pid} for this video. Add --new to make new clips." in out
    assert out.count(TITLE) == 2 and len(fake.requests) == asked  # its clips, found once


def test_cli_run_prints_a_waiting_message_once(use_fakes, capsys):
    calls = []

    def discover(req):
        """The first question hits the usage limit, which lifts half a second later."""
        calls.append(req)
        if len(calls) == 1:
            return LLMError("usage_limit", retry_at=time.time() + 0.5)
        return discover_answer(req)

    use_fakes(discover=discover)
    code, out, _ = run_cli(capsys, "run", URL, "--no-render")
    assert code == 0
    assert out.count("Claude's usage limit is reached.") == 1
    assert "Ready: 2 clips." in out


BIG = "The file is 301 MB, over Instagram's 300 MB limit."


@pytest.fixture
def quick_renders(monkeypatch):
    """Clip jobs that only write a render row, the second clip's with a problem that blocks Instagram.
    Each render waits until ``run`` has printed the table, so its lines come after it."""
    printed = threading.Event()
    done = cli._Follower.done

    def after(self, pid):
        done(self, pid)
        printed.set()

    def nothing(engine, ctx):
        return {}

    def render(engine, ctx):
        assert printed.wait(10)
        with engine.lock:
            clip = repo.get_clip(engine.db(), ctx.job.ref_id)
            video = engine.paths.project(clip.project_id).render_dir(clip.id, 1) / "clip.mp4"
            checks = [QACheck(name="file_size", ok=False, hard=True, message=BIG, platform="instagram")]
            qa = QAResult(checks=checks if clip.rank == 2 else []).model_dump()
            row = RenderRow(
                id=f"r{clip.rank}", clip_id=clip.id, version=1, style_key="s", path=str(video), qa=qa
            )
            repo.add_render(engine.db(), row)
            repo.update_clip(
                engine.db(),
                clip.id,
                status="rendered",
                current_render_id=row.id,
                layout="fit",
                layout_reason="Wide",
            )
        return {}

    monkeypatch.setattr(cli._Follower, "done", after)
    for kind in ("clip.source", "clip.analyze"):
        monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, kind, nothing)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.render", render)


def videos_of(pid):
    """The paths of the project's first two clips' videos."""
    conn = connect(Paths.from_env().db)
    try:
        return [repo.get_render(conn, c.current_render_id).path for c in repo.list_clips(conn, pid)[:2]]
    finally:
        conn.close()


def test_cli_run_ends_with_the_videos_it_made(use_fakes, quick_renders, capsys):
    use_fakes()
    code, out, _ = run_cli(capsys, "run", URL)
    assert code == 0
    one, two = videos_of(PID.search(out).group(1))
    rows = out.splitlines()
    assert rows[rows.index("Making the videos of 2 clips.") :] == [
        "Making the videos of 2 clips.",
        "Videos:",
        f"    1  Video: {one}",
        f"{cli.PAD}Layout: whole frame. Wide.",
        f"    2  Video: {two}",
        f"{cli.PAD}Layout: whole frame. Wide.",
        f"{cli.PAD}Blocks Instagram: {BIG}",
    ]

    code, out, _ = run_cli(capsys, "run", URL)  # its project is made already
    assert code == 0
    assert out.count(f"Video: {one}") == out.count(BIG) == 1 and "Videos:" not in out  # under its row


def test_cli_run_json_rows_carry_their_render(use_fakes, quick_renders, capsys):
    use_fakes()
    code, out, _ = run_cli(capsys, "run", URL, "--json")
    assert code == 0
    rows = json.loads(out)
    assert [r["status"] for r in rows] == ["rendered", "rendered"]
    assert [r["render"]["path"] for r in rows] == videos_of(rows[0]["project_id"])
    assert rows[1]["render"]["qa"]["checks"][0]["message"] == BIG


def test_cli_ctrl_c_leaves_the_work_for_next_time(monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    def open_engine(*, verbose=False):
        engine = fake_engine(speech=FakeSpeech(lines(36)))
        engine.wait = interrupted  # as if Ctrl+C came while it waited
        return engine

    monkeypatch.setattr(cli, "open_engine", open_engine)
    code, out, err = run_cli(capsys, "run", URL, "--no-render")
    assert code == 130
    assert "carries on the next time ClipForge runs" in err
    engine = fake_engine(speech=FakeSpeech(lines(36)))
    try:
        pid = PID.search(out).group(1)
        assert engine.wait(timeout_s=60, projects=[pid])
        assert engine.project(pid).status == "ready"
    finally:
        engine.close()


# Showing


def test_cli_show_prints_table(use_fakes, capsys):
    use_fakes()
    pid = ran(capsys)
    code, out, _ = run_cli(capsys, "show", pid)
    assert code == 0
    assert out.count(TITLE) == 2 and " to " in out and chr(0x2013) not in out
    assert re.search(rf"^\s*1\s+\w+\s+[\d.]+\s+0:00 to 0:35\s+35 s\s+{re.escape(TITLE)}", out, re.M)


def test_cli_show_text_adds_the_words_and_captions(use_fakes, capsys):
    use_fakes()
    pid = ran(capsys)
    code, out, _ = run_cli(capsys, "show", pid, "--text")
    assert code == 0
    assert "Part 1 of the talk says the deadline is coming." in out
    assert "The deadline is coming. Would you start today?" in out  # the TikTok caption
    assert "Why the deadline never scares him, in one minute." in out  # the Instagram caption


def test_cli_show_text_says_which_clips_are_off_the_topic(use_fakes, capsys):
    use_fakes(rerank=rerank_about("k1"))
    pid = ran(capsys, "--about", "the deadline")
    _, out, _ = run_cli(capsys, "show", pid, "--text")
    assert out.count("Not about the topic you asked for.") == 1  # k2, not k1


def test_cli_show_leaves_out_clips_not_to_post_unless_all(use_fakes, capsys):
    use_fakes(review=distorted)
    pid = ran(capsys)
    _, out, _ = run_cli(capsys, "show", pid)
    assert TITLE not in out
    assert "Left out: 2 clips marked Not recommended. Add --all to see them." in out
    _, out, _ = run_cli(capsys, "show", pid, "--all")
    assert out.count(TITLE) == 2 and re.search(r"^\s*1\s+no\s", out, re.M)


def test_cli_show_strips_control_characters(capsys):
    engine = fake_engine()
    (project,), _ = engine.add_sources(URL, ProjectOptions())
    engine.update_project(project.id, title="A title\x1b]0;owned\x07 with escapes")
    engine.close()
    code, out, _ = run_cli(capsys, "show", project.id)
    assert code == 0
    assert "A title" in out and "\x1b" not in out and "\x07" not in out


def test_cli_show_says_which_step_stopped(capsys):
    engine = fake_engine()
    (stuck,), _ = engine.add_sources(URL, ProjectOptions())
    (other,), _ = engine.add_sources("https://youtu.be/dQw4w9WgXcQ", ProjectOptions())
    quiet = STUCK.format(span="10 minutes")
    engine.update_project(stuck.id, status="failed", stage="asr.transcribe", error_code="stuck", error=quiet)
    engine.update_project(other.id, status="failed", stage=None, error_code="internal", error="It broke.")
    engine.close()
    assert f"\nStopped while transcribing: {quiet}\n" in run_cli(capsys, "show", stuck.id)[1]
    assert "\nStopped: It broke.\n" in run_cli(capsys, "show", other.id)[1]


def test_cli_show_says_why_no_clip_was_found(capsys):
    engine = fake_engine()
    (project,), _ = engine.add_sources(URL, ProjectOptions())
    why = "ClipForge heard only 6 seconds of talking, and it finds clips in what's said."
    engine.update_project(project.id, status="ready", message=why)
    engine.close()
    out = run_cli(capsys, "show", project.id)[1]
    assert f"Ready, but no moment in this video made a clip.\n{why}\n" in out


def test_cli_show_unknown_project(capsys):
    code, _, err = run_cli(capsys, "show", "p0000000000")
    assert code == 1 and "No project p0000000000." in err


def test_cli_show_prints_render_paths_and_problems(use_fakes, capsys):
    use_fakes()
    pid = ran(capsys)
    note = "The picture is scaled up 1.6 times, so it may look soft."
    big = "The file is 301 MB, over Instagram's 300 MB limit."
    missing = "The video for this clip is missing. Try again."
    reason = "One speaker: the speaker fills the frame"
    engine = fake_engine()
    try:
        first, second = engine.clips(pid)[:2]
        video = engine.paths.project(pid).render_dir(first.id, 1) / "clip.mp4"
        checks = [
            QACheck(name="upscale", ok=False, hard=False, message=note),
            QACheck(name="file_size", ok=False, hard=True, message=big, platform="instagram"),
            QACheck(name="codec", ok=True, hard=True),
        ]
        qa = QAResult(checks=checks).model_dump()
        repo.add_render(
            engine.db(),
            RenderRow(id="r1", clip_id=first.id, version=1, style_key="s", path=str(video), qa=qa),
        )
        repo.update_clip(
            engine.db(),
            first.id,
            status="rendered",
            current_render_id="r1",
            layout="face",
            layout_reason=reason,
        )
        repo.update_clip(engine.db(), second.id, status="failed", error=missing)
    finally:
        engine.close()
    code, out, _ = run_cli(capsys, "show", pid)
    assert code == 0
    rows = out.splitlines()
    one = next(i for i, line in enumerate(rows) if re.match(r"\s*1\s", line))
    two = next(i for i, line in enumerate(rows) if re.match(r"\s*2\s", line))
    assert rows[one + 1 : two] == [
        f"{cli.PAD}Video: {video}",
        f"{cli.PAD}Layout: speaker. {reason}.",
        f"{cli.PAD}Blocks Instagram: {big}",
        f"{cli.PAD}Note: {note}",
    ]
    assert rows[two + 1] == f"{cli.PAD}Failed: {missing}"


def test_cli_projects_lists_newest_first(capsys):
    assert run_cli(capsys, "projects")[:2] == (0, "No projects yet.\n")
    engine = fake_engine()
    (first,), _ = engine.add_sources(URL, ProjectOptions())
    (second,), _ = engine.add_sources("https://youtu.be/dQw4w9WgXcQ", ProjectOptions())
    engine.close()
    code, out, _ = run_cli(capsys, "projects")
    assert code == 0
    assert [line.split()[0] for line in out.splitlines()] == [second.id, first.id]
    assert "queued" in out and "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in out


# Making the clips again


def test_cli_rerun_in_basic_mode_now(use_fakes, capsys):
    fake = use_fakes()
    pid = ran(capsys)
    asked = len(fake.requests)
    code, out, _ = run_cli(capsys, "rerun", pid, "--provider", "heuristic")
    assert code == 0 and "Ready: " in out
    assert len(fake.requests) == asked  # Basic mode asks nothing
    _, out, _ = run_cli(capsys, "show", pid, "--json")
    rows = json.loads(out)
    assert rows and {r["mode"] for r in rows} == {"basic"}


def test_cli_rerun_hands_over_to_the_open_window(use_fakes, capsys):
    use_fakes()
    pid = ran(capsys)
    held = fake_engine(speech=FakeSpeech(lines(36)))  # ClipForge is open in another window
    try:
        code, out, _ = run_cli(capsys, "rerun", pid, "--provider", "heuristic")
        assert code == 0 and "another window" in out
        assert held.project(pid).options.provider == "heuristic"
        assert [j.kind for j in held.runner.jobs_for(pid) if j.status == "queued"] == ["brain.select"]
        assert held.wait(timeout_s=60, projects=[pid])
        assert (held.project(pid).status, held.project(pid).mode) == ("ready", "basic")
    finally:
        held.close()


def test_cli_rerun_unknown_project(use_fakes, capsys):
    use_fakes()
    code, _, err = run_cli(capsys, "rerun", "p0000000000")
    assert code == 1 and "No project p0000000000." in err


# Rendering


def opened(*, verbose=False):
    """An engine that renders with the fakes, as ``clipforge render`` opens it."""
    engine = fake_engine(youtube=SectionTube(), verbose=verbose)
    engine.face_detector = OneFace()
    return engine


def stored(cid):
    """The clip ``cid`` and its renders, as the database has them."""
    conn = connect(Paths.from_env().db)
    try:
        return repo.get_clip(conn, cid), repo.list_renders(conn, cid)
    finally:
        conn.close()


def test_cli_render_saves_edits_and_renders(monkeypatch, capsys):
    engine = opened()
    try:
        _, cid = ready_clip(engine)
    finally:
        engine.close()
    monkeypatch.setattr(cli, "open_engine", opened)
    code, out, err = run_cli(capsys, "render", cid, "--layout", "fit", "--captions", "clean")
    assert code == 0, out + err
    clip, (row,) = stored(cid)
    assert clip.edits == {"layout": "fit", "preset": "clean"}
    assert (clip.status, clip.current_render_id, row.version) == ("rendered", row.id, 1)
    edl = EDL.model_validate_json(Path(row.edl_path).read_text())
    assert edl.style.preset == "clean" and {s.layout for s in edl.spans} == {"fit"}
    assert f"Video: {row.path}" in out
    assert "Layout: whole frame. You chose the whole frame layout for this clip." in out

    code, out, _ = run_cli(capsys, "render", cid, "--layout", "auto")  # ClipForge picks the layout again
    assert code == 0
    clip, renders = stored(cid)
    assert (clip.edits, clip.layout, [r.version for r in renders]) == ({"preset": "clean"}, "face", [1, 2])
    assert "Layout: speaker." in out

    def broken(edl, src, out, **kw):
        raise ToolError("decode_failed", UNREADABLE)

    monkeypatch.setattr(clips, "render_video", broken)
    code, out, _ = run_cli(capsys, "render", cid)
    assert code == 1
    assert f"Previous video: {renders[-1].path}" in out and f"Failed: {UNREADABLE}" in out


def test_cli_render_flags_make_the_edits():
    parse = cli.build_parser().parse_args
    assert cli._edits(parse(["render", "c1"])) == {}
    assert cli._edits(parse(["render", "c1", "--layout", "stream"])) == {"layout": "stream"}
    flags = ["--layout", "auto", "--captions", "clean", "--best"]
    assert cli._edits(parse(["render", "c1", *flags])) == {
        "layout": None,
        "preset": "clean",
        "encoder": "best",
    }


def test_cli_render_hands_over_to_the_open_window(monkeypatch, capsys):
    engine = opened()
    try:
        _, cid = ready_clip(engine)
    finally:
        engine.close()
    monkeypatch.setattr(cli, "open_engine", opened)
    held = opened()  # ClipForge is open in another window
    try:
        code, out, _ = run_cli(capsys, "render", cid, "--captions", "clean")
        assert code == 0 and "another window" in out
        assert held.wait(timeout_s=60, clips=[cid])
        clip = repo.get_clip(held.db(), cid)
        assert (clip.status, clip.edits) == ("rendered", {"preset": "clean"}) and clip.current_render_id
    finally:
        held.close()


def test_cli_render_rejects_a_bad_edit_plainly(monkeypatch, capsys):
    engine = opened()
    try:
        _, cid = ready_clip(engine, edits={"preset": "clean"})
    finally:
        engine.close()
    before, _ = stored(cid)
    monkeypatch.setattr(cli, "open_engine", opened)
    monkeypatch.setattr(cli, "_edits", lambda args: {"accent": "yellow"})
    bad = (1, "", "The accent colour must look like #FFD60A.\n")
    assert run_cli(capsys, "render", cid) == bad
    clip, renders = stored(cid)
    assert (clip.edits, clip.status, renders) == ({"preset": "clean"}, before.status, [])
    held = opened()  # ClipForge is open in another window
    try:
        assert run_cli(capsys, "render", cid) == bad
        queued = held.db().execute("SELECT COUNT(*) FROM jobs WHERE ref_id = ?", (cid,)).fetchone()[0]
        assert queued == 0 and repo.get_clip(held.db(), cid).edits == {"preset": "clean"}
    finally:
        held.close()


def test_render_lines_say_the_previous_video_and_each_problem_once():
    black = "The first frame is black, so the clip would open on nothing."
    qa = QAResult(checks=[QACheck(name="first_frame", ok=False, hard=True, message=black)]).model_dump()
    render = RenderRow(id="r1", clip_id="c1", version=1, style_key="s", path="/v1/clip.mp4", qa=qa)
    clip = ClipRow(id="c1", project_id="p1", start_s=0.0, end_s=30.0, status="failed", error=black)
    assert cli.render_lines(clip, render) == ["Video: /v1/clip.mp4", f"Blocks posting: {black}"]
    good = render.model_copy(update={"qa": QAResult(checks=[]).model_dump()})
    stopped = clip.model_copy(update={"error": "The render was stopped."})
    assert cli.render_lines(stopped, good) == [
        "Previous video: /v1/clip.mp4",
        "Failed: The render was stopped.",
    ]
    assert cli.render_lines(stopped.model_copy(update={"error": None}), None) == [f"Failed: {cli.NO_VIDEO}"]


def test_cli_render_unknown_clip(capsys):
    code, _, err = run_cli(capsys, "render", "c0000000000")
    assert code == 1 and "No clip c0000000000." in err


# The library


def test_cli_library_add_and_list(capsys, tmp_path):
    assert run_cli(capsys, "library", "list")[:2] == (0, cli.EMPTY_LIBRARY + "\n")
    original = make_test_video(tmp_path / "parkour.mp4", seconds=3.0)
    escape = chr(27)
    notes = f"level 3{escape}]0;owned"
    code, out, err = run_cli(
        capsys,
        "library",
        "add",
        str(original),
        "--credit",
        "Filmed by me",
        "--license",
        "My own",
        "--notes",
        notes,
    )
    assert code == 0, err
    assert re.search(r"^Added l[0-9a-f]{10}: parkour \(0:03\)\.$", out, re.M)
    (copy,) = Paths.from_env().gameplay.iterdir()
    assert copy.read_bytes() == original.read_bytes() and original.exists()
    code, out, _ = run_cli(capsys, "library", "list")
    assert code == 0
    assert all(
        text in out for text in ("parkour", "0:03", "Filmed by me", "My own", "level 3", "used 0 times")
    )
    assert escape not in out

    sound = make_wav(tmp_path / "song.wav", [(1.0, 0.5)])
    code, _, err = run_cli(capsys, "library", "add", str(sound), "--credit", "x", "--license", "y")
    assert code == 1 and "no video" in err
    code, _, err = run_cli(
        capsys, "library", "add", str(tmp_path / "gone.mp4"), "--credit", "x", "--license", "y"
    )
    assert code == 1 and "There's no file at" in err
    assert len(list(Paths.from_env().gameplay.iterdir())) == 1

    conn = connect(Paths.from_env().db)
    try:
        (item,) = repo.list_library(conn)
        repo.record_gameplay_use(conn, item.id, 0.0, 2.0, clip_id=None, post_id="t1")
    finally:
        conn.close()
    assert run_cli(capsys, "library", "list")[1].splitlines()[0].endswith("  used 1 time")
    copy.unlink()
    assert "(its file is missing)" in run_cli(capsys, "library", "list")[1]


def test_service_commands(tmp_path, monkeypatch, capsys):
    calls = []

    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "\tstate = running\n\tpid = 77\n", "")

    monkeypatch.setenv("CLIPFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "RUN", run)
    assert cli.main(["service", "status"]) == 0
    assert capsys.readouterr().out.strip() == service.OFF
    assert cli.main(["service", "install"]) == 0
    assert capsys.readouterr().out.strip() == service.INSTALLED
    assert [c[1] for c in calls] == ["bootout", "bootstrap"]
    assert cli.main(["service", "status"]) == 0
    assert capsys.readouterr().out.strip() == f"{service.RUNNING} (process 77)"
    assert cli.main(["service", "uninstall"]) == 0
    assert capsys.readouterr().out.strip() == service.REMOVED

    def missing(args):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(service, "RUN", missing)
    assert cli.main(["service", "install"]) == 1
    assert capsys.readouterr().err.strip() == service.NO_LAUNCHCTL
