"""The window's shell (SPEC 14.1): the rail, the theme, dropping and pasting, toasts, dialogs, the jobs
indicator, and a window that carries on when the engine restarts under it."""

from __future__ import annotations

import base64
import re
import time

import pytest
from playwright.sync_api import Page, expect

from clipforge.jobs.runner import job_event
from clipforge.models import ProjectOptions
from clipforge.watch import drafts
from tests.helpers import make_test_video
from tests.posting import add_account
from tests.ui.conftest import listening, live_server
from tests.unit.test_api_projects import set_job
from tests.watching import add_channel, add_check, watched

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
NOTHING = "Nothing to clip. Give a YouTube or Google Drive link, or the path of a video file."
NOT_A_VIDEO = "That file type isn't a video ClipForge can use. Use MP4, MOV, MKV, WEBM or M4V."
OVERLAY = "Drop to start a project"
PAGES = [
    "Today",
    "Create",
    "Projects",
    "Library",
    "Settings",
    "Queue",
    "Accounts",
    "Trends",
    "Drafts",
    "Channels",
]
BUILT = [
    "Today",
    "Create",
    "Projects",
    "Queue",
    "Channels",
    "Campaigns",
    "Money",
    "Accounts",
    "Library",
    "Trends",
    "Settings",
]  # the rail's pages so far, in order (SPEC 14.1); each page's task adds its own
PROJECT_PAGE = re.compile(r"/projects/[0-9A-Za-z_-]+$")
AS_IS_ON = "Added with Post as it is on"
DARK, LIGHT = "rgb(14, 14, 18)", "rgb(247, 247, 249)"

# A drag or a drop as the browser sends one, carrying a link, some text or a file.
DRAG = """([type, uri, text, file]) => {
  const dt = new DataTransfer();
  if (uri) dt.setData("text/uri-list", uri);
  if (text) dt.setData("text/plain", text);
  if (file) {
    const bytes = Uint8Array.from(atob(file.data), (c) => c.charCodeAt(0));
    dt.items.add(new File([bytes], file.name, { type: file.type }));
  }
  document.body.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""

PASTE = """([selector, text]) => {
  const dt = new DataTransfer();
  dt.setData("text/plain", text);
  const target = selector ? document.querySelector(selector) : document.body;
  target.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
}"""

INSIDE_DIALOG = "() => document.querySelector('[role=dialog]').contains(document.activeElement)"


def drag(page: Page, kind: str, *, uri=None, text=None, file=None) -> None:
    page.evaluate(DRAG, [kind, uri, text, file])


def a_file(path, kind="video/mp4") -> dict:
    return {"name": path.name, "type": kind, "data": base64.b64encode(path.read_bytes()).decode()}


def opened(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.get_by_role("navigation", name="Pages")).to_be_visible()
    expect(page.get_by_role("main")).to_have_attribute(
        "aria-busy", "false"
    )  # the page waits for the settings


def toasts(page: Page):
    return page.get_by_role("status", name="Notifications")


def test_rail_shows_only_built_pages(page, ui_server):
    opened(page, ui_server.url)
    rail = page.get_by_role("navigation", name="Pages")
    links = rail.get_by_role("link")
    assert links.all_inner_texts() == ["ClipForge", *BUILT]
    for name in PAGES:
        if name not in BUILT:
            expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
    page.set_viewport_size({"width": 800, "height": 800})  # under 900 px the rail is icons only
    expect(links.first).to_have_accessible_name("ClipForge")
    assert rail.bounding_box()["width"] <= 72


def test_narrow_rail_names_each_page_on_hover_and_focus(page, ui_server):
    """Under 900 px the rail is icons only, so each one names its page when the pointer or the keyboard is
    on it; wider, the label is there and nothing else shows."""
    opened(page, ui_server.url)
    rail = page.get_by_role("navigation", name="Pages")
    tip = rail.locator("[data-tip]:visible")
    page.set_viewport_size({"width": 800, "height": 800})
    rail.get_by_role("link", name="Queue", exact=True).hover()
    expect(tip).to_have_text("Queue")
    rail.get_by_role("link", name="Library", exact=True).focus()
    page.mouse.move(600, 600)
    expect(tip).to_have_text("Library")
    page.set_viewport_size({"width": 1200, "height": 800})
    rail.get_by_role("link", name="Queue", exact=True).hover()
    expect(tip).to_have_count(0)


def test_today_badge_counts_the_drafts(page, ui_engine, ui_server):
    """The rail's Today link carries how many drafts wait for review, and says so to a screen reader."""
    opened(page, ui_server.url + "/projects")
    rail = page.get_by_role("navigation", name="Pages")
    expect(rail.get_by_role("link", name="Today", exact=True)).to_be_visible()
    account = add_account(ui_engine, "tiktok", "t0", daily_cap=1)
    channel = add_channel(ui_engine, [account])
    project_id, _ = watched(ui_engine, channel, add_check(ui_engine, channel), [90])
    drafts.settle(ui_engine, project_id)
    today = rail.get_by_role("link", name="Today, 1 draft to review")
    expect(today).to_be_visible()
    expect(today).to_contain_text("1")


@pytest.mark.parametrize(
    ("theme", "mac", "background"),
    [("dark", "light", DARK), ("light", "dark", LIGHT), ("system", "dark", DARK), ("system", "light", LIGHT)],
)
def test_theme_follows_the_setting(page, ui_engine, ui_server, theme, mac, background):
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    page.emulate_media(color_scheme=mac)
    opened(page, ui_server.url)
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("body")).to_have_css("background-color", background)


def test_unknown_route_shows_not_found(page, ui_server):
    opened(page, ui_server.url + "/no/such/page")
    expect(page.get_by_role("heading", name="That page isn't here.")).to_be_visible()
    page.get_by_role("link", name="Go to the start").click()
    expect(page).to_have_url(ui_server.url + "/")


def test_drop_overlay_appears_on_drag(page, ui_server):
    opened(page, ui_server.url)
    overlay = page.get_by_text(OVERLAY)
    expect(overlay).to_be_hidden()
    drag(page, "dragenter", uri=URL)
    expect(overlay).to_be_visible()
    drag(page, "dragleave", uri=URL)
    expect(overlay).to_be_hidden()
    drag(page, "dragenter", text="some text")
    expect(overlay).to_be_visible()
    drag(page, "drop", text="some text")
    expect(overlay).to_be_hidden()


def test_no_console_errors_on_load(page, ui_server, console):
    with page.expect_request(re.compile(r"/api/events$")):
        opened(page, ui_server.url)
    expect(page.get_by_role("button", name="No jobs")).to_be_visible()
    page.wait_for_timeout(500)
    assert console.errors == []


def test_old_cookie_reloads_the_window_once(page, ui_engine, console):
    """A restart on the same port: the old cookie gets a 401, the window reloads itself once (never in a
    loop) and carries on with the new cookie."""
    console.allow(r"\b401\b|Unauthorized")
    console.allow(r"net::ERR_")  # the stream and its probe while nothing listens on the port
    console.allow(r"EventSource's response has a MIME type")
    loads: list[float] = []
    page.on("load", lambda _: loads.append(time.monotonic()))
    with live_server(ui_engine) as first:
        port = first.port
        opened(page, first.url)
    with live_server(ui_engine, port):
        deadline = time.monotonic() + 20
        while len(loads) < 2 and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert len(loads) == 2, "the window didn't reload itself"
        expect(page.get_by_role("navigation", name="Pages")).to_be_visible()
        page.wait_for_timeout(5000)  # long enough for two more rounds of the stream's 2 s reconnect
        assert len(loads) == 2, "the window reloaded itself more than once"
        assert page.evaluate("() => fetch('/api/settings').then((r) => r.status)") == 200


def test_toasts_are_announced(page, ui_server):
    opened(page, ui_server.url)
    expect(toasts(page)).to_have_attribute("aria-live", "polite")
    drag(page, "drop", text="hello there")
    expect(toasts(page)).to_contain_text(NOTHING)


def test_dialog_traps_focus_and_esc_closes(page, ui_server):
    opened(page, ui_server.url)
    trigger = page.get_by_role("button", name="No jobs")
    trigger.click()
    dialog = page.get_by_role("dialog", name="Jobs")
    expect(dialog).to_be_visible()
    assert page.evaluate(INSIDE_DIALOG)
    for key in ("Tab", "Tab", "Shift+Tab", "Tab"):
        page.keyboard.press(key)
        assert page.evaluate(INSIDE_DIALOG), f"{key} took the focus out of the dialog"
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()


def test_dropped_link_opens_its_project_with_the_last_options(page, ui_engine, ui_server):
    options = ProjectOptions(length="short")
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"create_options": options}))
    opened(page, ui_server.url)
    drag(page, "drop", uri=URL)
    page.wait_for_url(PROJECT_PAGE)
    (project,) = ui_engine.projects()
    assert page.url.endswith(f"/projects/{project.id}")
    assert project.options.length == "short"


def test_a_dropped_link_says_so_when_post_as_it_is_is_on(page, ui_engine, ui_server):
    options = ProjectOptions(as_is=True)
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"create_options": options}))
    opened(page, ui_server.url)
    drag(page, "drop", uri=URL)
    page.wait_for_url(PROJECT_PAGE)
    (project,) = ui_engine.projects()
    assert project.options.as_is
    expect(toasts(page)).to_contain_text(AS_IS_ON)


def test_a_dropped_link_says_nothing_of_it_when_post_as_it_is_is_off(page, ui_engine, ui_server):
    opened(page, ui_server.url)
    drag(page, "drop", uri=URL)
    page.wait_for_url(PROJECT_PAGE)
    expect(toasts(page)).not_to_contain_text(AS_IS_ON)


def test_pasting_a_link_outside_a_field_starts_a_project(page, ui_engine, ui_server):
    opened(page, ui_server.url)
    page.evaluate("() => document.body.append(Object.assign(document.createElement('input'), {id: 'field'}))")
    page.evaluate(PASTE, ["#field", URL])
    page.wait_for_timeout(500)
    assert ui_engine.projects() == []  # a paste into a text field is the field's own
    page.evaluate(PASTE, [None, f"have a look at {URL} today"])
    page.wait_for_url(PROJECT_PAGE)
    assert len(ui_engine.projects()) == 1


def test_dropped_video_file_uploads_and_opens_its_project(page, ui_engine, ui_server, tmp_path):
    video = tmp_path / "team talk.mp4"
    make_test_video(video)
    opened(page, ui_server.url)
    drag(page, "drop", file=a_file(video))
    page.wait_for_url(PROJECT_PAGE, timeout=20_000)
    (project,) = ui_engine.projects()
    assert page.url.endswith(f"/projects/{project.id}")
    assert project.source_kind == "file"


def test_a_dropped_file_that_isnt_a_video_says_so(page, ui_engine, ui_server, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not a video")
    opened(page, ui_server.url)
    drag(page, "drop", file=a_file(notes, "text/plain"))
    expect(toasts(page)).to_contain_text(NOT_A_VIDEO)
    assert ui_engine.projects() == []


def test_jobs_indicator_follows_job_events(page, ui_engine, monkeypatch):
    monkeypatch.setattr(ui_engine.runner, "start", lambda: None)  # nothing runs: each job stays as set
    with live_server(ui_engine) as server:
        opened(page, server.url)
        expect(page.get_by_role("button", name="No jobs")).to_be_visible()
        listening(ui_engine)
        job_id = ui_engine.runner.enqueue("ingest.metadata", "p1")
        button = page.get_by_role("button", name="1 job")
        expect(button).to_be_visible()
        set_job(ui_engine, job_id, status="running", progress=0.4)
        ui_engine.events.publish("job", job_event(ui_engine.runner.get(job_id)))
        button.click()
        dialog = page.get_by_role("dialog", name="Jobs")
        expect(dialog.get_by_text("Reading the video's details")).to_be_visible()
        expect(dialog.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "40")
