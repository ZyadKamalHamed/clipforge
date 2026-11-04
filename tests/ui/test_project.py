"""The project page (SPEC 14.3): its header with the rights badge and the steps, then its clips as 9:16
cards that play on hover one at a time, with the selection and its bar, sorting and the tier filter. Nothing
posts from here."""

from __future__ import annotations

import re
import time
import zipfile

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.jobs.runner import job_event
from tests.ui.conftest import listening
from tests.ui.contrast import low_text
from tests.ui.seed import TITLE, hook, render_clip, seed_project
from tests.ui.test_screens import SETTLED
from tests.ui.test_shell import opened, toasts
from tests.unit.test_api_clips import PACKAGING
from tests.unit.test_api_projects import set_job

NOT_CLEARED = "This video isn't cleared for clipping yet. Set its rights when you have permission."
NOT_HERE = "This project isn't here."
FAILED = "YouTube wouldn't let ClipForge download this video. Try again later."
WAITING = "Claude's usage limit is reached. ClipForge carries on at 4:00 pm."
CLIP_FAILED = "The clip's video couldn't be fetched. Try again."
BASES = ["Not cleared", "Own channel", "Creative Commons", "Permission", "Clipping programme"]
PERMISSION = "The creator said you can clip it."
NO_RENDER = "None of those clips has a render to download yet."
HOSTILE = '<img src=x onerror="document.title=1">'
HOSTILE_HOOK = "<b>Bold</b> & <i>loud</i>"
LONG_TASKS = """() => {
  window.longTasks = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) window.longTasks.push(Math.round(entry.duration));
  }).observe({ type: "longtask" });
}"""
PLAYING = "() => { const v = document.querySelector('video'); return !!v && !v.paused && v.muted; }"


def open_project(page: Page, server, pid: str) -> None:
    opened(page, f"{server.url}/projects/{pid}")
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def card(page: Page, k: int):
    return page.get_by_role("article", name=hook(k), exact=True)


def check(page: Page, k: int):
    return card(page, k).get_by_role("checkbox")


def bar(page: Page):
    return page.get_by_role("toolbar", name="Selected clips")


def shown(page: Page):
    return page.locator("article h3")


def rights(page: Page, basis: str):
    return page.get_by_role("button", name=f"Rights: {basis}")


def test_project_header_and_steps(page, ui_engine, server):
    pid = seed_project(ui_engine, clips=0, rendered=0, status="running", message="Transcribing the talk")
    for kind in ("ingest.metadata", "ingest.audio", "ingest.captions", "ingest.extras"):
        set_job(ui_engine, ui_engine.runner.enqueue(kind, pid), status="done", progress=1.0)
    job_id = ui_engine.runner.enqueue("asr.transcribe", pid)
    set_job(ui_engine, job_id, status="running", progress=0.4, started_at=time.time())
    page.clock.install()
    open_project(page, server, pid)
    head = page.get_by_role("region", name=TITLE)
    expect(page.get_by_role("heading", level=1)).to_have_text(TITLE)
    expect(head).to_contain_text("TED")
    expect(head).to_contain_text("14:04")
    expect(head).to_contain_text("Transcribing the talk")
    expect(head.locator("img")).to_have_attribute("src", re.compile(r"/source/thumb\.jpg$"))
    expect(rights(page, "Not cleared")).to_be_visible()
    items = page.get_by_role("list", name="Steps").get_by_role("listitem")
    expect(items).to_have_count(7)
    expect(items).to_contain_text(
        ["Metadata", "Download", "Transcribe", "Signals", "Find moments", "Package", "Render"]
    )
    expect(items.nth(0)).to_contain_text("Done")
    expect(items.nth(2)).to_contain_text("40%")
    expect(items.nth(3)).to_contain_text("To do")

    def progress(fraction: float) -> None:
        set_job(ui_engine, job_id, progress=fraction)
        ui_engine.events.publish("job", job_event(ui_engine.runner.get(job_id)))

    listening(ui_engine)
    progress(0.5)
    expect(items.nth(2)).to_contain_text("50%")
    page.clock.fast_forward(20_000)
    progress(0.7)
    expect(items.nth(2)).to_contain_text("70%")
    expect(items.nth(2)).to_contain_text(re.compile(r"About \d+:\d\d left"))


def test_ideas_show_before_renders_and_are_replaced_in_place(page, ui_engine, server, video):
    pid = seed_project(ui_engine, clips=3, rendered=0)
    open_project(page, server, pid)
    expect(shown(page)).to_have_text([hook(0), hook(1), hook(2)])
    idea = card(page, 1)
    expect(idea).to_contain_text("0:20 to 0:40")
    expect(idea).to_contain_text("80")
    expect(idea).to_contain_text("A setup and its payoff.")
    expect(idea).to_contain_text("Idea")
    expect(idea.get_by_role("button", name="Render")).to_be_visible()
    expect(idea.get_by_role("link", name=hook(1))).to_have_attribute("href", f"/clips/{pid}_c1")
    idea.evaluate("(el) => { el.dataset.seen = 'yes'; }")
    render_clip(ui_engine, pid, f"{pid}_c1", video=video)
    expect(idea).to_contain_text("Ready")
    expect(idea.locator("img")).to_have_attribute("src", re.compile(r"/v1/cover\.jpg$"))
    expect(idea.get_by_role("button", name="Render")).to_have_count(0)
    expect(idea).to_have_attribute("data-seen", "yes")  # the same card, changed where it was
    expect(shown(page)).to_have_text([hook(0), hook(1), hook(2)])


def test_hover_plays_one_video_at_a_time(page, ui_engine, server, video):
    pid = seed_project(ui_engine, clips=3, rendered=3, video=video)
    open_project(page, server, pid)
    videos = page.locator("video")
    expect(card(page, 0).locator("img")).to_be_visible()
    expect(videos).to_have_count(0)
    card(page, 0).hover()
    expect(card(page, 0).locator("video")).to_have_count(1)
    page.wait_for_function(PLAYING)
    card(page, 2).hover()
    expect(card(page, 2).locator("video")).to_have_count(1)
    expect(videos).to_have_count(1)
    page.mouse.move(2, 2)  # off every card
    expect(videos).to_have_count(0)


def test_shift_click_selects_a_range(page, ui_engine, server):
    starts = [40.0, 100.0, 0.0, 80.0, 20.0, 60.0]
    pid = seed_project(ui_engine, each=lambda k: {"start_s": starts[k]})
    open_project(page, server, pid)
    page.get_by_role("radio", name="In video order").check()
    expect(shown(page)).to_have_text([hook(2), hook(4), hook(0), hook(5), hook(3), hook(1)])
    check(page, 0).click()
    check(page, 3).click(modifiers=["Shift"])
    expect(bar(page)).to_contain_text("3 selected")
    for k in range(6):
        if k in (0, 5, 3):
            expect(check(page, k)).to_be_checked()
        else:
            expect(check(page, k)).not_to_be_checked()


def test_select_all_post_tier(page, ui_engine, server):
    tiers = ["post", "test", "post", "weak", "no", "post"]
    pid = seed_project(ui_engine, each=lambda k: {"tier": tiers[k]})
    open_project(page, server, pid)
    page.get_by_role("button", name="Select all Post-tier").click()
    expect(bar(page)).to_contain_text("3 selected")
    for k, tier in enumerate(tiers):
        if tier == "post":
            expect(check(page, k)).to_be_checked()
        else:
            expect(check(page, k)).not_to_be_checked()


def test_cmd_a_selects_all_and_esc_clears(page, ui_engine, server):
    pid = seed_project(ui_engine)
    open_project(page, server, pid)
    expect(shown(page)).to_have_count(6)
    expect(bar(page)).to_be_hidden()
    page.keyboard.press("Meta+a")
    expect(bar(page)).to_contain_text("6 selected")
    page.keyboard.press("Escape")
    expect(bar(page)).to_be_hidden()
    for k in range(6):
        expect(check(page, k)).not_to_be_checked()


def test_discard_with_undo(page, ui_engine, server):
    pid = seed_project(ui_engine)
    open_project(page, server, pid)
    check(page, 1).click()
    check(page, 2).click()
    bar(page).get_by_role("button", name="Discard").click()
    expect(shown(page)).to_have_text([hook(0), hook(3), hook(4), hook(5)])
    expect(bar(page)).to_be_hidden()
    note = toasts(page)
    expect(note).to_contain_text("2 clips discarded")
    assert repo.get_clip(ui_engine.db(), f"{pid}_c1").discarded_at is not None
    note.get_by_role("button", name="Undo").click()
    expect(shown(page)).to_have_text([hook(k) for k in range(6)])
    assert repo.get_clip(ui_engine.db(), f"{pid}_c1").discarded_at is None
    assert repo.get_clip(ui_engine.db(), f"{pid}_c2").discarded_at is None


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_undo_toast_reads_in_both_themes(page, ui_engine, server, theme):
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    pid = seed_project(ui_engine)
    open_project(page, server, pid)
    check(page, 1).click()
    bar(page).get_by_role("button", name="Discard").click()
    expect(toasts(page).get_by_role("button", name="Undo")).to_be_visible()
    page.wait_for_function(SETTLED)
    assert not [line for line in low_text(page) if '"Undo"' in line]


def test_rerender_shows_progress_then_ready(page, renders):
    engine, live, gate = renders
    pid = seed_project(engine, clips=2, rendered=1)
    open_project(page, live, pid)
    first = card(page, 0)
    expect(first).to_contain_text("Ready")
    check(page, 0).click()
    bar(page).get_by_role("button", name="Re-render").click()
    expect(toasts(page)).to_contain_text("Rendering 1 clip")
    expect(first).to_contain_text("Rendering")
    expect(first).to_contain_text("50%")
    gate.set()
    expect(first).to_contain_text("Ready")
    expect(first.locator("img")).to_have_attribute("src", re.compile(r"/v2/cover\.jpg$"))
    expect(card(page, 1)).to_contain_text("Idea")  # only the one asked for


def test_sort_and_filter(page, ui_engine, server):
    rows = [(90.0, "post", 100.0), (75.0, "post", 0.0), (66.0, "test", 40.0)]
    rows += [(62.0, "test", 20.0), (50.0, "weak", 80.0), (30.0, "no", 60.0)]
    pid = seed_project(
        ui_engine, each=lambda k: {"final_score": rows[k][0], "tier": rows[k][1], "start_s": rows[k][2]}
    )
    ui_engine.discard_clip(f"{pid}_c4")
    open_project(page, server, pid)
    expect(shown(page)).to_have_text([hook(k) for k in (0, 1, 2, 3, 5)])
    page.get_by_role("radio", name="In video order").check()
    expect(shown(page)).to_have_text([hook(k) for k in (1, 3, 2, 5, 0)])
    page.get_by_role("radio", name="Test 2").check()
    expect(shown(page)).to_have_text([hook(3), hook(2)])
    page.get_by_role("radio", name="Don't post 1").check()
    expect(shown(page)).to_have_text([hook(5)])
    page.get_by_role("radio", name="All 5").check()
    page.get_by_role("radio", name="Best first").check()
    expect(shown(page)).to_have_text([hook(k) for k in (0, 1, 2, 3, 5)])
    page.get_by_role("checkbox", name="Show discarded").check()
    gone = card(page, 4)
    expect(gone).to_contain_text("Discarded")
    gone.get_by_role("button", name="Restore").click()
    expect(gone).not_to_contain_text("Discarded")
    page.get_by_role("checkbox", name="Show discarded").uncheck()
    expect(shown(page)).to_have_text([hook(k) for k in range(6)])


def test_rights_badge_changes_the_basis(page, ui_engine, server):
    pid = seed_project(ui_engine)
    open_project(page, server, pid)
    expect(page.get_by_text(NOT_CLEARED)).to_be_visible()
    rights(page, "Not cleared").click()
    popover = page.get_by_role("dialog", name="Rights")
    for basis in BASES:
        expect(popover.get_by_role("radio", name=basis, exact=True)).to_be_visible()
    expect(popover.get_by_role("radio", name="Not cleared", exact=True)).to_be_checked()
    expect(popover).to_contain_text(PERMISSION)
    popover.get_by_role("radio", name="Permission", exact=True).check()
    expect(rights(page, "Permission")).to_be_visible()
    expect(popover).to_be_hidden()
    expect(page.get_by_text(NOT_CLEARED)).to_be_hidden()
    assert ui_engine.project(pid).rights_basis == "permission"


def test_failed_project_shows_sentence_details_and_retry(page, ui_engine, server):
    details = "Traceback (most recent call last):\n  yt-dlp said no"
    pid = seed_project(
        ui_engine,
        clips=0,
        rendered=0,
        status="failed",
        error=FAILED,
        error_code="download",
        error_details=details,
    )
    open_project(page, server, pid)
    expect(page.get_by_text(FAILED)).to_be_visible()
    shown_details = page.get_by_text("yt-dlp said no")
    expect(shown_details).to_be_hidden()
    page.get_by_role("button", name="Show details").click()
    expect(shown_details).to_be_visible()
    page.get_by_role("button", name="Hide details").click()
    expect(shown_details).to_be_hidden()
    page.get_by_role("button", name="Retry").click()
    expect(page.get_by_text(FAILED)).to_be_hidden()
    expect(page.get_by_role("region", name=TITLE)).to_contain_text("In the queue")
    assert ui_engine.project(pid).status == "queued"


def test_hostile_titles_render_as_text(page, ui_engine, server):
    hostile = PACKAGING.model_copy(update={"hook_title": HOSTILE_HOOK}).model_dump(mode="json")
    pid = seed_project(
        ui_engine,
        clips=2,
        rendered=1,
        title=HOSTILE,
        channel="<b>Channel</b>",
        each=lambda k: {"packaging": hostile, "reason": "<script>bad()</script>"} if k == 1 else {},
    )
    open_project(page, server, pid)
    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_text(HOSTILE)
    expect(heading).to_have_attribute("dir", "auto")
    expect(page.get_by_role("region", name=HOSTILE)).to_contain_text("<b>Channel</b>")
    hostile_card = page.get_by_role("article", name=HOSTILE_HOOK, exact=True)
    expect(hostile_card).to_contain_text("<script>bad()</script>")
    expect(hostile_card.locator("h3")).to_have_attribute("dir", "auto")
    expect(page.locator("img[src='x'], main b, main i, main script")).to_have_count(0)
    assert page.title() != "1"


def test_gallery_with_40_clips_has_no_long_tasks(page, ui_engine, server, video):
    pid = seed_project(ui_engine, clips=40, rendered=40, video=video)
    open_project(page, server, pid)
    expect(page.locator("article")).to_have_count(40)
    page.evaluate(LONG_TASKS)
    for k in range(10):
        card(page, k).hover()
        page.mouse.wheel(0, 120)
    expect(page.locator("video")).to_have_count(1)
    long_tasks = page.evaluate("() => window.longTasks")
    assert all(ms <= 100 for ms in long_tasks), long_tasks


def test_two_windows_both_update(page, browser, console, ui_engine, server):
    pid = seed_project(ui_engine)
    other = browser.new_context(viewport={"width": 1280, "height": 800})
    second = other.new_page()
    second.on("console", lambda m: console.record(m.text) if m.type == "error" else None)
    second.on("pageerror", lambda e: console.record(f"uncaught: {e}"))
    try:
        open_project(page, server, pid)
        open_project(second, server, pid)
        rights(page, "Not cleared").click()
        page.get_by_role("radio", name="Own channel", exact=True).check()
        expect(rights(second, "Own channel")).to_be_visible()
        check(page, 0).click()
        bar(page).get_by_role("button", name="Discard").click()
        expect(card(second, 0)).to_have_count(0)
        expect(shown(second)).to_have_count(5)
    finally:
        other.close()


def test_flags_show_their_sentences_on_hover(page, ui_engine, server):
    flagged = {"flags": {"red": ["defamation"], "soft": ["sponsor_read"]}, "tier": "no"}
    pid = seed_project(ui_engine, clips=2, rendered=0, each=lambda k: flagged if k == 1 else {})
    open_project(page, server, pid)
    expect(card(page, 0).get_by_role("button", name=re.compile("flag"))).to_have_count(0)
    card(page, 1).get_by_role("button", name="2 flags").hover()
    tip = page.get_by_role("tooltip")
    expect(tip).to_contain_text("It rests on an unproven claim of wrongdoing against a real person.")
    expect(tip).to_contain_text("It has a sponsor read or an ad in it.")


def test_download_one_clip_or_the_zip(page, ui_engine, server, video):
    pid = seed_project(ui_engine, clips=3, rendered=2, video=video)
    open_project(page, server, pid)
    check(page, 0).click()
    with page.expect_download() as one:
        bar(page).get_by_role("button", name="Download").click()
    assert one.value.suggested_filename == "1 Hook 1 why we wait.mp4"
    check(page, 1).click()
    check(page, 2).click()  # an idea, which the zip leaves out
    with page.expect_download() as several:
        bar(page).get_by_role("button", name="Download").click()
    assert several.value.suggested_filename == "ClipForge clips.zip"
    with zipfile.ZipFile(several.value.path()) as zipped:
        assert len(zipped.namelist()) == 2
    page.keyboard.press("Escape")
    check(page, 2).click()
    bar(page).get_by_role("button", name="Download").click()
    expect(toasts(page)).to_contain_text(NO_RENDER)


def test_edited_and_stale_clips_say_so(page, ui_engine, server):
    pid = seed_project(ui_engine, clips=2, rendered=0)
    cid = f"{pid}_c0"
    render_clip(ui_engine, pid, cid, edits_key="made before the edits")
    ui_engine.update_clip(cid, edits={"emoji": False})
    open_project(page, server, pid)
    expect(card(page, 0)).to_contain_text("Edited")
    expect(card(page, 0)).to_contain_text("Re-render to see your edits")
    expect(card(page, 1)).not_to_contain_text("Edited")


def test_failed_clip_says_why_and_tries_again(page, ui_engine, server):
    failed = {"status": "failed", "error": CLIP_FAILED}
    pid = seed_project(ui_engine, clips=2, rendered=0, each=lambda k: failed if k == 0 else {})
    open_project(page, server, pid)
    first = card(page, 0)
    expect(first).to_contain_text("Failed")
    expect(first).to_contain_text(CLIP_FAILED)
    first.get_by_role("button", name="Try again").click()
    expect(first).to_contain_text("In the queue")
    expect(first).not_to_contain_text(CLIP_FAILED)
    card(page, 1).get_by_role("button", name="Render").click()
    expect(card(page, 1)).to_contain_text("In the queue")


def test_as_is_clip_says_so_and_offers_no_render(page, ui_engine, server):
    whole = {"mode": "as_is", "status": "failed", "error": CLIP_FAILED}
    pid = seed_project(ui_engine, clips=2, rendered=0, each=lambda k: whole if k == 0 else {})
    open_project(page, server, pid)
    expect(card(page, 0)).to_contain_text("As it is")
    expect(card(page, 0)).to_contain_text(CLIP_FAILED)
    expect(card(page, 0).get_by_role("button", name="Try again")).to_have_count(0)
    expect(card(page, 1).get_by_role("button", name="Render")).to_be_visible()
    check(page, 0).check()
    expect(bar(page)).to_be_visible()
    expect(bar(page).get_by_role("button", name="Re-render")).to_have_count(0)
    check(page, 1).check()
    expect(bar(page).get_by_role("button", name="Re-render")).to_be_visible()


def test_waiting_project_says_why_and_tries_now(page, ui_engine, server):
    pid = seed_project(ui_engine, clips=0, rendered=0, status="waiting", message=WAITING)
    open_project(page, server, pid)
    expect(page.get_by_text(WAITING)).to_be_visible()
    page.get_by_role("button", name="Try now").click()
    expect(page.get_by_text(WAITING)).to_be_hidden()
    assert ui_engine.project(pid).status == "queued"


def test_missing_project_says_so(page, server, console):
    console.allow(r"Failed to load resource: the server responded with a status of 404")
    opened(page, f"{server.url}/projects/nothing_here")
    expect(page.get_by_role("heading", name=NOT_HERE)).to_be_visible()
    page.get_by_role("link", name="Go to Projects").click()
    expect(page.get_by_role("heading", name="Projects", level=1)).to_be_visible()
