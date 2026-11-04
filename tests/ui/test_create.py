"""The Create and Projects pages (SPEC 14.2). A project starts from a link pasted, dropped, typed or put on
the clipboard, or from a chosen file, with the options last used; the projects list filters and searches,
finds clips again, and deletes a project only once asked."""

from __future__ import annotations

import re
import time

from playwright.sync_api import Page, expect

from clipforge.doctor import Check
from clipforge.ingest.urls import OTHER_SITE, PLAYLIST
from clipforge.models import ProjectOptions
from clipforge.server.routes import system
from clipforge.server.routes.projects import NOTHING_TO_RERUN
from tests.helpers import make_test_video
from tests.ui.seed import seed_project
from tests.ui.test_library_settings import CLAUDE_FIX
from tests.ui.test_shell import NOT_A_VIDEO, NOTHING, OVERLAY, PROJECT_PAGE, URL, drag, opened, toasts
from tests.unit.test_api_projects import LIST, VIMEO, link, wait_for

ZONE = "Drop a YouTube or Drive link, or a video file"
PASTE_REFUSED = "Paste with Cmd+V instead."
EMPTY, EMPTY_NEXT = "No projects yet.", "Drop a YouTube or Drive link anywhere to start."
NO_MATCH = "No projects match."
ASK = "Delete this project?"
WHAT_GOES = "Its video, transcript and 6 clips are removed from this Mac. This can't be undone."
RERUN_ASK = "Find clips again?"
RERUN_GOES = (
    "Its 3 rendered clips you haven't edited or posted are deleted, videos and all, and new ones take their "
    "place. Clips you've edited or posted stay."
)
SETUP = "Setup needed"
BASIC_TOPIC = "Basic picks don't read this. Choose an AI provider in Settings for clips about a topic."
CLIPBOARD = ["clipboard-read", "clipboard-write"]
WRITE_CLIPBOARD = "(text) => navigator.clipboard.writeText(text)"
PICTURE = re.compile(r"^/media/projects/[0-9A-Za-z_-]+/source/thumb\.jpg$")

# Every toast's text as it changes, so one that comes and goes between two looks is still seen.
WATCH_TOASTS = """() => {
  window.seenToasts = [];
  const region = document.querySelector('[aria-label="Notifications"]');
  new MutationObserver(() => window.seenToasts.push(region.textContent)).observe(region, {
    subtree: true, childList: true, characterData: true,
  });
}"""


def field(page: Page):
    return page.get_by_label("Link or file path")


def button(page: Page, name: str):
    return page.get_by_role("button", name=name, exact=True)


def cards(page: Page):
    return page.get_by_role("article")


def titles(page: Page):
    return cards(page).get_by_role("heading")


def menu(page: Page, title: str, item: str) -> None:
    page.get_by_role("article", name=title).get_by_role("button", name=f"More for {title}").click()
    page.get_by_role("menuitem", name=item).click()


def choose(page: Page, path) -> None:
    with page.expect_file_chooser() as chooser:
        button(page, "Choose file").click()
    chooser.value.set_files(path)


# Starting a project


def test_paste_a_link_opens_the_project_page_within_1_s(page, ui_engine, ui_server):
    page.context.grant_permissions(CLIPBOARD, origin=ui_server.url)
    opened(page, ui_server.url + "/create")
    page.evaluate(WRITE_CLIPBOARD, URL)
    began = time.monotonic()
    button(page, "Paste").click()
    page.wait_for_url(PROJECT_PAGE)
    took = time.monotonic() - began
    (project,) = ui_engine.projects()
    assert page.url.endswith(f"/projects/{project.id}")
    assert took < 1.0, f"the project page took {took:.2f} s"


def test_paste_refused_says_to_use_cmd_v(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")  # no clipboard permission, so reading it is refused
    button(page, "Paste").click()
    expect(toasts(page)).to_contain_text(PASTE_REFUSED)
    assert ui_engine.projects() == []


def test_drop_a_link_anywhere_starts_a_project(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")
    zone = page.get_by_text(ZONE)
    expect(zone).to_be_visible()
    drag(page, "dragenter", uri=URL)
    expect(page.get_by_text(OVERLAY)).to_have_count(1)  # Create's own zone says it, with no overlay on it
    expect(zone).to_be_hidden()
    drag(page, "dragleave", uri=URL)
    expect(zone).to_be_visible()
    expect(page.get_by_text(OVERLAY)).to_have_count(0)

    page.get_by_role("link", name="Projects", exact=True).click()
    drag(page, "dragenter", uri=URL)
    expect(page.get_by_text(OVERLAY)).to_be_visible()  # any other page has the overlay
    drag(page, "drop", uri=URL)
    page.wait_for_url(PROJECT_PAGE)
    (project,) = ui_engine.projects()
    assert page.url.endswith(f"/projects/{project.id}")


def test_cmd_v_with_a_link_starts_a_project(page, ui_engine, ui_server):
    page.context.grant_permissions(CLIPBOARD, origin=ui_server.url)
    opened(page, ui_server.url + "/create")
    expect(field(page)).not_to_be_focused()  # the page has a field, but Cmd+V outside it is the window's
    page.evaluate(WRITE_CLIPBOARD, URL)
    page.keyboard.press("ControlOrMeta+V")
    page.wait_for_url(PROJECT_PAGE)
    assert len(ui_engine.projects()) == 1


def test_cmd_v_inside_a_text_field_does_not_start_a_project(page, ui_engine, ui_server):
    page.context.grant_permissions(CLIPBOARD, origin=ui_server.url)
    opened(page, ui_server.url + "/create")
    page.evaluate(WRITE_CLIPBOARD, URL)
    field(page).focus()
    page.keyboard.press("ControlOrMeta+V")
    expect(field(page)).to_have_value(URL)
    page.wait_for_timeout(500)
    assert ui_engine.projects() == []
    assert page.url == ui_server.url + "/create"


def test_rejected_inputs_show_their_reasons(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")
    field(page).fill(f"{VIMEO}\n{LIST}")
    button(page, "Start").click()
    expect(toasts(page)).to_contain_text(OTHER_SITE)
    expect(toasts(page)).to_contain_text(PLAYLIST)
    expect(toasts(page)).to_contain_text(VIMEO)  # each one says which link it is about
    expect(field(page)).to_have_value(f"{VIMEO}\n{LIST}")  # kept, to be put right
    field(page).fill("just some words")
    field(page).press("Enter")
    expect(toasts(page)).to_contain_text(NOTHING)
    assert ui_engine.projects() == []
    assert page.url == ui_server.url + "/create"


def test_ten_links_make_ten_projects_and_open_projects(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")
    field(page).fill("\n".join(link(n) for n in range(10)))
    button(page, "Start").click()
    page.wait_for_url(ui_server.url + "/projects")
    expect(page.get_by_role("heading", name="Projects", level=1)).to_be_visible()
    expect(cards(page)).to_have_count(10)
    assert len(ui_engine.projects()) == 10


def test_choose_a_video_file_uploads_it(page, ui_engine, ui_server, tmp_path):
    video = tmp_path / "team talk.mp4"
    make_test_video(video)
    opened(page, ui_server.url + "/create")
    page.evaluate(WATCH_TOASTS)
    choose(page, video)
    page.wait_for_url(PROJECT_PAGE, timeout=20_000)
    (project,) = ui_engine.projects()
    assert page.url.endswith(f"/projects/{project.id}")
    assert project.source_kind == "file"
    seen = " ".join(page.evaluate("() => window.seenToasts"))
    assert "Uploading" in seen and "team talk.mp4" in seen  # its progress was in a toast


def test_non_video_file_is_refused_plainly(page, ui_engine, ui_server, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not a video")
    opened(page, ui_server.url + "/create")
    choose(page, notes)
    expect(toasts(page)).to_contain_text(NOT_A_VIDEO)
    expect(toasts(page)).to_contain_text("notes.txt")
    assert ui_engine.projects() == []


def test_upload_errors_show_their_sentence(page, ui_engine, ui_server, tmp_path, console):
    console.allow(r"status of 507")  # the refusal itself, which Chrome logs
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"min_free_gb": 100000.0}))
    video = tmp_path / "team talk.mp4"
    make_test_video(video)
    opened(page, ui_server.url + "/create")
    choose(page, video)
    expect(toasts(page)).to_contain_text("Free up some space first.")
    expect(toasts(page)).to_contain_text("team talk.mp4")
    expect(toasts(page).get_by_role("progressbar")).to_have_count(0)  # the upload's toast became the reason
    assert ui_engine.projects() == []


def test_options_are_remembered(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")
    options = page.get_by_role("button", name="Options")
    expect(options).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_role("group", name="Clip length")).to_be_hidden()
    options.click()
    expect(options).to_have_attribute("aria-expanded", "true")
    page.get_by_role("group", name="Clip length").get_by_role("radio", name="Short").check()
    page.get_by_label("Number of clips").select_option("5")
    page.get_by_role("group", name="Content type").get_by_role("radio", name="Podcast").check()
    page.get_by_label("What should clips be about?").fill("the money advice")
    page.get_by_label("Caption style").select_option(label="Hormozi")
    page.get_by_label("Layout").select_option(label="Stacked speakers")
    page.get_by_label("Quality").select_option(label="Fast")
    page.get_by_role("group", name="Credit").get_by_role("radio", name="Off", exact=True).check()
    want = ProjectOptions(
        length="short",
        clip_count=5,
        content_type="podcast",
        prompt="the money advice",
        caption_preset="hormozi",
        layout="stacked",
        quality="fast",
        credit=False,
    )
    wait_for(lambda: ui_engine.settings.create_options == want)

    page.reload()
    expect(page.get_by_text("Short (15 to 35 s), 5 clips, Podcast")).to_be_visible()  # said while closed
    page.get_by_role("button", name="Options").click()
    expect(page.get_by_role("group", name="Clip length").get_by_role("radio", name="Short")).to_be_checked()
    expect(page.get_by_label("Number of clips")).to_have_value("5")
    expect(
        page.get_by_role("group", name="Content type").get_by_role("radio", name="Podcast")
    ).to_be_checked()
    expect(page.get_by_label("What should clips be about?")).to_have_value("the money advice")
    expect(page.get_by_label("Caption style")).to_have_value("hormozi")
    expect(page.get_by_label("Layout")).to_have_value("stacked")
    expect(page.get_by_label("Quality")).to_have_value("fast")
    expect(
        page.get_by_role("group", name="Credit").get_by_role("radio", name="Off", exact=True)
    ).to_be_checked()

    field(page).fill(URL)
    button(page, "Start").click()
    page.wait_for_url(PROJECT_PAGE)
    (project,) = ui_engine.projects()
    assert project.options == want


def test_post_as_it_is_hides_what_only_cutting_uses(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/create")
    page.get_by_role("button", name="Options").click()
    whole = page.get_by_role("group", name="Post as it is")
    expect(whole.get_by_role("radio", name="Off", exact=True)).to_be_checked()
    whole.get_by_role("radio", name="On", exact=True).check()
    wait_for(lambda: ui_engine.settings.create_options.as_is is True)
    expect(page.get_by_role("group", name="Clip length")).to_have_count(0)
    expect(page.get_by_label("Layout")).to_have_count(0)
    expect(page.get_by_label("What should clips be about?")).to_have_count(0)
    expect(page.get_by_label("Quality")).to_be_visible()  # the download's quality and the credit still apply
    expect(page.get_by_role("group", name="Credit")).to_be_visible()

    field(page).fill(URL)
    button(page, "Start").click()
    page.wait_for_url(PROJECT_PAGE)
    (project,) = ui_engine.projects()
    assert project.options.as_is is True


def test_basic_picks_say_they_skip_the_topic(page, ui_engine, ui_server):
    """With No AI, the topic box says Basic picks don't read it, so a topic typed there isn't taken for a
    promise; with an AI provider it says nothing."""
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"llm_provider": "heuristic"}))
    opened(page, ui_server.url + "/create")
    page.get_by_role("button", name="Options").click()
    expect(page.get_by_text(BASIC_TOPIC)).to_be_visible()
    expect(page.get_by_text(BASIC_TOPIC).get_by_role("link", name="Settings")).to_be_visible()

    ui_engine.update_settings(ui_engine.settings.model_copy(update={"llm_provider": "claude_cli"}))
    page.reload()
    page.get_by_role("button", name="Options").click()
    expect(page.get_by_label("What should clips be about?")).to_be_visible()
    expect(page.get_by_text(BASIC_TOPIC)).to_have_count(0)


# The projects


def test_create_shows_the_six_newest_projects(page, ui_engine, ui_server):
    for n in range(7):
        seed_project(ui_engine, title=f"Talk number {n}")
    opened(page, ui_server.url + "/create")
    expect(cards(page)).to_have_count(6)
    expect(titles(page)).to_have_text([f"Talk number {n}" for n in range(6, 0, -1)])
    newest = cards(page).first
    expect(newest).to_contain_text("6 clips")
    thumb = newest.locator("img")
    expect(thumb).to_have_attribute("src", PICTURE)
    assert thumb.evaluate("(img) => img.decode().then(() => img.naturalWidth)") > 0
    page.get_by_role("link", name="See all").click()
    expect(page).to_have_url(ui_server.url + "/projects")
    expect(cards(page)).to_have_count(7)


def test_projects_page_filters_and_search(page, ui_engine, ui_server):
    opened(page, ui_server.url + "/projects")
    expect(page.get_by_text(EMPTY)).to_be_visible()
    expect(page.get_by_text(EMPTY_NEXT)).to_be_visible()
    seed_project(ui_engine, title="Ready talk")
    seed_project(ui_engine, title="Broken talk", status="failed", error="The download stopped. Try again.")
    seed_project(ui_engine, title="Refused talk", status="refused", error="This video is over 4 hours long.")
    seed_project(ui_engine, title="Working talk", status="running", progress=0.4, clips=0, rendered=0)
    page.reload()
    expect(titles(page)).to_have_text(["Working talk", "Refused talk", "Broken talk", "Ready talk"])
    ready = page.get_by_role("article", name="Ready talk")
    for shown in ("TED", "14:04", "6 clips", "Ready", "just now"):
        expect(ready).to_contain_text(shown)
    working = page.get_by_role("article", name="Working talk")
    expect(working.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "40")
    expect(page.get_by_role("article", name="Broken talk")).to_contain_text(
        "The download stopped. Try again."
    )

    show = page.get_by_role("group", name="Show")
    show.get_by_role("radio", name="Working").check()
    expect(titles(page)).to_have_text(["Working talk"])
    show.get_by_role("radio", name="Ready").check()
    expect(titles(page)).to_have_text(["Ready talk"])
    show.get_by_role("radio", name="Failed").check()
    expect(titles(page)).to_have_text(["Refused talk", "Broken talk"])
    show.get_by_role("radio", name="All").check()
    search = page.get_by_label("Search projects")
    search.fill("BROKEN")
    expect(titles(page)).to_have_text(["Broken talk"])
    search.fill("nothing like it")
    expect(page.get_by_text(NO_MATCH)).to_be_visible()
    button(page, "Show all").click()
    expect(search).to_have_value("")
    expect(titles(page)).to_have_count(4)


def test_delete_project_asks_first_and_removes_it(page, ui_engine, ui_server):
    old = seed_project(ui_engine, title="Old talk")
    seed_project(ui_engine, title="New talk")
    opened(page, ui_server.url + "/projects")
    menu(page, "Old talk", "Delete")
    dialog = page.get_by_role("dialog", name=ASK)
    expect(dialog).to_contain_text(WHAT_GOES)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert ui_engine.project(old) is not None

    menu(page, "Old talk", "Delete")
    page.get_by_role("dialog", name=ASK).get_by_role("button", name="Delete", exact=True).click()
    expect(page.get_by_role("article", name="Old talk")).to_have_count(0)
    expect(page.get_by_role("article", name="New talk")).to_be_visible()
    expect(toasts(page)).to_contain_text("Project deleted")
    assert ui_engine.project(old) is None
    assert not ui_engine.paths.project(old).root.exists()


def test_find_clips_again_reruns_or_says_why_not(page, ui_engine, ui_server, monkeypatch, console):
    console.allow(r"status of 409")  # the refusal itself, which Chrome logs
    pid = seed_project(ui_engine, title="Old talk", rendered=0)  # nothing a rerun would delete, so no dialog
    # The engine's own answer is the API tests' to check. A seeded project has no jobs behind it, so the real
    # one would start its pipeline over, and the fakes would rename it mid-test.
    asked: list[str] = []
    can = [False]
    monkeypatch.setattr(
        ui_engine, "rerun_selection", lambda project_id, **_: asked.append(project_id) or can[0]
    )
    opened(page, ui_server.url + "/projects")
    menu(page, "Old talk", "Find clips again")  # nothing to make again yet
    expect(toasts(page)).to_contain_text(NOTHING_TO_RERUN)

    can[0] = True
    menu(page, "Old talk", "Find clips again")
    expect(toasts(page)).to_contain_text("Finding clips again")
    assert asked == [pid, pid]

    menu(page, "Old talk", "Open")
    expect(page).to_have_url(ui_server.url + f"/projects/{pid}")


def test_find_clips_again_asks_before_it_deletes_rendered_clips(page, ui_engine, ui_server, monkeypatch):
    pid = seed_project(ui_engine, title="Old talk")  # 4 rendered clips
    ui_engine.save_edits(f"{pid}_c0", {"hook_title": "Kept"})  # an edited one stays, so it isn't counted
    asked: list[str] = []
    monkeypatch.setattr(
        ui_engine, "rerun_selection", lambda project_id, **_: asked.append(project_id) or True
    )
    opened(page, ui_server.url + "/projects")
    menu(page, "Old talk", "Find clips again")
    dialog = page.get_by_role("dialog", name=RERUN_ASK)
    expect(dialog).to_contain_text(RERUN_GOES)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert asked == []

    menu(page, "Old talk", "Find clips again")
    page.get_by_role("dialog", name=RERUN_ASK).get_by_role(
        "button", name="Find clips again", exact=True
    ).click()
    expect(toasts(page)).to_contain_text("Finding clips again")
    assert asked == [pid]


def test_create_says_what_setup_is_needed(page, server, monkeypatch):
    checks = [
        Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
        Check("claude", False, "not found", CLAUDE_FIX),
        Check(
            "speech model", False, "Parakeet is not downloaded yet", "It downloads by itself.", required=False
        ),
    ]
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(checks))
    opened(page, server.url + "/create")
    notice = page.get_by_role("region", name=SETUP)
    expect(notice).to_contain_text("Claude Code")
    expect(notice).to_contain_text(CLAUDE_FIX)
    expect(notice).not_to_contain_text("Speech model")  # optional, so it waits for Settings
    expect(notice).not_to_contain_text("ffmpeg")
    notice.get_by_role("link", name="Open Settings").click()
    expect(page).to_have_url(server.url + "/settings")


def test_create_checks_setup_again_once_it_is_fixed(page, server, monkeypatch):
    """The Doctor's answer is kept 5 minutes, so the notice asks again itself rather than send a person to
    the bottom of Settings after a fix in Terminal."""
    checks = [
        Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
        Check("claude", False, "not found", CLAUDE_FIX),
    ]
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(checks))
    opened(page, server.url + "/create")
    notice = page.get_by_role("region", name=SETUP)
    expect(notice).to_contain_text("Claude Code")
    checks[1] = Check("claude", True, "2.1.0 (Claude Code)")
    with page.expect_response(lambda r: r.url.endswith("/api/doctor")):
        notice.get_by_role("button", name="Check again").click()
    expect(notice).to_have_count(0)


def test_create_has_no_setup_notice_when_every_check_passes(page, server):
    with page.expect_response(lambda r: r.url.endswith("/api/doctor")):
        opened(page, server.url + "/create")
    expect(page.get_by_role("region", name="Start a project")).to_be_visible()
    expect(page.get_by_role("region", name=SETUP)).to_have_count(0)
