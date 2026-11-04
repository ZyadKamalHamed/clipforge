"""A real video through the whole window (SPEC 22, Phase 3): a 3 minute synthetic talk chosen as a file, its
moments found in Basic mode with the real Parakeet model and rendered by the real renderer, then the top
clip edited, rendered again and downloaded. Slow, and skipped without parakeet-mlx, its cached model and
macOS say. Nothing is fetched and nothing posts."""

from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import expect

from clipforge import repo
from clipforge.app import Engine
from tests.fakes import FakeSpeech, FakeYouTube, offline
from tests.helpers import make_block_talk_video
from tests.integration.test_pipeline_parakeet import cached_parakeet
from tests.integration.test_render_synthetic import TALK, cached_faces, streams
from tests.ui.conftest import live_server
from tests.ui.seed import picture
from tests.ui.test_create import choose
from tests.ui.test_editor import neutral, panel, rendered, tab
from tests.ui.test_shell import PROJECT_PAGE, opened

pytestmark = pytest.mark.slow

NEW_TITLE = "Move your phone out tonight"
NEW_ACCENT = "#ff3b30"
# Whether the video plays: its time moves on, at once or at its next timeupdate.
PLAYS = """(v) => new Promise((done) => {
  const moving = () => !v.paused && v.currentTime > 0.1;
  if (moving()) return done(true);
  v.addEventListener("timeupdate", () => moving() && done(true));
})"""


@pytest.fixture
def real_engine():
    parakeet = cached_parakeet()
    if not parakeet.available() or not parakeet.model_ready() or shutil.which("say") is None:
        pytest.skip("needs parakeet-mlx, its cached model and macOS say")
    engine = Engine.open(
        settings_overrides={"min_free_gb": 0.0, "llm_provider": "heuristic"},
        youtube=FakeYouTube(),
        asr_engines={"parakeet": parakeet, "whisper": FakeSpeech([], name="whisper", ok=False)},
        provider_factory=lambda settings, name: None,
    )
    engine.face_detector = cached_faces()
    engine.emoji_fetch = offline
    engine.thumb_fetch = picture
    yield engine
    engine.close()


def test_synthetic_video_through_the_ui(page, real_engine, tmp_path):
    # Twice through the talk, about 3 minutes: once is 92 s, where Basic mode's 30 to 60 s windows leave
    # room for only two clips (SPEC 9.4).
    video = make_block_talk_video(tmp_path / "media" / "Sleep talk.mp4", " ".join(TALK * 2))
    with live_server(real_engine) as server:
        opened(page, server.url + "/create")
        # Three asked for, as SPEC 7.5 would aim for 3 at this length anyway.
        page.get_by_role("button", name="Options").click()
        page.get_by_label("Number of clips").select_option("3")
        choose(page, video)
        page.wait_for_url(PROJECT_PAGE, timeout=30_000)
        steps = page.get_by_role("list", name="Steps").get_by_role("listitem")
        expect(steps.last).to_contain_text("Render")
        expect(steps.last).to_contain_text("Done", timeout=900_000)

        cards = page.get_by_role("article")
        (project,) = real_engine.projects()
        made = [
            (c.rank, c.status, c.tier, c.start_s, c.end_s)
            for c in repo.list_clips(real_engine.db(), project.id)
        ]
        assert cards.count() >= 3, (project.options, made)
        for k in range(3):
            cards.nth(k).hover()
            player = cards.nth(k).locator("video")
            expect(player).to_have_count(1)
            assert player.evaluate(PLAYS, timeout=20_000)
        page.mouse.move(2, 2)

        top = cards.first.get_by_role("heading").inner_text()
        cards.first.get_by_role("link").first.click()
        expect(page.get_by_role("heading", level=1)).to_contain_text(top)
        cid = next(c.id for c in repo.list_clips(real_engine.db(), project.id) if c.rank == 1)
        tab(page, "Text")
        hook = panel(page).get_by_label("Hook title")
        hook.fill(NEW_TITLE)
        tab(page, "Look")
        panel(page).get_by_label("Accent colour").fill(NEW_ACCENT)
        neutral(page)
        with rendered(page, cid) as answer:
            page.keyboard.press("Meta+Enter")
        assert answer.value.status == 202
        versions = page.get_by_role("combobox", name="Version")
        expect(versions.locator("option")).to_have_text(["Version 2 (current)", "Version 1"], timeout=300_000)
        expect(page.get_by_test_id("clip-status")).to_have_text("Ready")
        player = page.locator("video")
        v2 = repo.get_render(real_engine.db(), repo.get_clip(real_engine.db(), cid).current_render_id)
        assert v2.version == 2
        expect(page.get_by_role("heading", level=1)).to_contain_text(NEW_TITLE)
        player.evaluate("(v) => { v.muted = true; return v.play(); }")
        assert player.evaluate(PLAYS, timeout=20_000)
        edits = repo.get_clip(real_engine.db(), cid).edits or {}
        assert edits.get("accent") == NEW_ACCENT.upper()  # the engine keeps colours in capitals

        with page.expect_download(timeout=60_000) as download:
            page.get_by_role("link", name="Download").click()
        saved = tmp_path / "downloaded.mp4"
        download.value.save_as(saved)
    picture_stream = streams(saved)["video"]
    assert (picture_stream["width"], picture_stream["height"]) == (1080, 1920)
    assert picture_stream["codec_name"] == "h264"
