"""The clip editor (SPEC 14.4 and 14.8): the 9:16 player with its keys, loop and safe zone, and the tabs.
Cut, Text and Look changes wait for one render (Re-render or Cmd+Enter); post copy saves as each field is
left. Nothing posts from here."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.brain.wording import post_text, units
from clipforge.models import LibraryItem
from clipforge.render.edl import EdlWord
from clipforge.server import views
from clipforge.trends import store as trends_store
from tests.helpers import make_test_audio, make_test_video
from tests.ui.seed import render_clip, seed_project
from tests.ui.test_shell import opened, toasts
from tests.unit.test_api_clips import PACKAGING
from tests.unit.test_trends_store import shortlist

START, END = 40.0, 59.9  # the clip: words 80 to 119, sentences S9 to S12
TITLE = "Hook 1: why we wait"
ALTS = ["He waited until the last day", "Nobody starts before the deadline"]
SCORES = {"C1": 8, "C2": 7, "C3": 9, "C4": 6, "C5": 5, "C6": 7, "C7": 4, "C8": 6, "C9": 3}
EVIDENCE = [{"criterion": "C1", "quote": "w80 w81 w82"}]
REASON = "One speaker faces the camera."
TOO_SHORT = "The clip must be at least 3 seconds long."
TITLE_LONG = "The hook title can be at most 42 characters."
MAX_CARDS = "A clip can have at most 3 fact cards."
LEAVE = "Leave without rendering?"
CREDIT_NOTE = "Its licence asks for credit, so the caption names it."
TABS = ["Cut", "Text", "Look", "Sound", "Post copy"]
NOW = "() => document.querySelector('video').currentTime"
PLAYING = "() => { const v = document.querySelector('video'); return !!v && !v.paused; }"
PAUSED = "() => { const v = document.querySelector('video'); return !!v && v.paused; }"
# The scrubber showing where the video is: it follows a seek's events, so it can trail a key pressed at
# machine speed.
SHOWN = "() => Math.abs(Number(document.querySelector('input[type=range]').value) - (" + NOW + ")()) < 0.02"


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """An 8 s portrait video Chrome plays, long enough to step through, made once for the module."""
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    make_test_video(path, seconds=8.0, size="360x640")
    return path


def said() -> list[EdlWord]:
    """Where the render says each of the clip's words: word 80 at 0.0 s, 81 at 0.5 s and on."""
    return [
        EdlWord(i=80 + n, sid=f"S{(80 + n) // 10 + 1}", text=f"w{80 + n}", start=n * 0.5, end=n * 0.5 + 0.4)
        for n in range(40)
    ]


def seed_clip(engine, video, **fields) -> str:
    """A project with one packaged clip from 40.0 to 59.9 s, scored, with two other titles, rendered once
    with ``video`` as its video. ``fields`` set more of the clip's row (its layout, say)."""
    packaging = PACKAGING.model_copy(update={"hook_title": TITLE, "hook_alts": ALTS}).model_dump(mode="json")
    row = {"start_s": START, "packaging": packaging, "scores": SCORES, "evidence": EVIDENCE}
    row |= {"layout": "face", "layout_reason": REASON} | fields
    pid = seed_project(engine, clips=1, rendered=0, each=lambda k: dict(row))
    cid = f"{pid}_c0"
    render_clip(engine, pid, cid, video=video, words=said())
    return cid


def open_editor(page: Page, server, cid: str) -> None:
    opened(page, f"{server.url}/clips/{cid}")
    expect(page.get_by_role("heading", level=1)).to_contain_text(TITLE)


def word(page: Page, i: int):
    return page.locator(f'[data-word="{i}"]')


def tab(page: Page, name: str) -> None:
    page.get_by_role("tab", name=name, exact=True).click()


def panel(page: Page):
    return page.get_by_role("tabpanel")


def length(page: Page):
    return page.get_by_test_id("clip-length")


def waiting(page: Page):
    return page.get_by_test_id("changes-waiting")


def neutral(page: Page) -> None:
    """The focus somewhere that takes no keys, so the next key is the editor's."""
    page.get_by_role("heading", level=1).click()


def rendered(page: Page, cid: str):
    """The render request the next Cmd+Enter sends."""
    return page.expect_response(lambda r: r.url.endswith(f"/api/clips/{cid}/render"))


def edits(engine, cid: str) -> dict:
    return repo.get_clip(engine.db(), cid).edits or {}


def src(engine, cid: str, version: int) -> str:
    """Where the window plays version ``version`` of the clip."""
    row = next(r for r in repo.list_renders(engine.db(), cid) if r.version == version)
    return views.media_url(engine, row.path)


def at(page: Page, seconds: float) -> None:
    page.wait_for_function(f"() => Math.abs(({NOW})() - {seconds}) < 0.02")


def test_editor_loads_player_and_tabs(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    pid = cid.rsplit("_c", 1)[0]
    open_editor(page, server, cid)
    back = page.get_by_role("link", name="Back to project")
    expect(back).to_have_attribute("href", f"/projects/{pid}")
    expect(page.get_by_role("heading", level=1)).to_contain_text("#1")
    expect(page.get_by_test_id("clip-status")).to_have_text("Ready")
    versions = page.get_by_role("combobox", name="Version")
    expect(versions.locator("option")).to_have_text(["Version 1 (current)"])
    download = page.get_by_role("link", name="Download")
    expect(download).to_have_attribute(
        "href", f"{src(ui_engine, cid, 1)}?download=1%20Hook%201%3A%20why%20we%20wait"
    )
    expect(page.get_by_role("button", name="Re-render")).to_be_visible()
    expect(page.locator("video")).to_have_attribute("src", src(ui_engine, cid, 1))
    for control in ("Play", "Loop", "Safe zone"):
        expect(page.get_by_role("button", name=control)).to_be_visible()
    expect(page.get_by_role("slider", name="Position")).to_be_visible()
    expect(page.get_by_role("tab")).to_have_text(TABS)
    expect(page.get_by_role("tab", name="Cut")).to_have_attribute("aria-selected", "true")
    score = page.get_by_role("region", name="Score")
    expect(score).to_contain_text("80")
    expect(score).to_contain_text("Post")
    expect(score.get_by_role("listitem").filter(has_text="Hook")).to_contain_text("8 of 10")
    expect(score).to_contain_text("w80 w81 w82")
    expect(score).to_contain_text("A setup and its payoff.")
    words = page.locator("[data-word]")
    expect(words).to_have_count(160)  # 30 s either side: words 20 to 179
    expect(word(page, 70)).to_have_attribute("data-outside", "yes")
    expect(word(page, 80)).not_to_have_attribute("data-outside", "yes")
    expect(word(page, 107)).to_have_accessible_name("um, filler")
    expect(length(page)).to_have_text("19.9 s")
    expect(waiting(page)).to_have_count(0)
    tab(page, "Sound")
    expect(panel(page).get_by_role("button", name="Play")).to_have_count(5)
    expect(panel(page).get_by_role("switch", name="Sound effects")).to_be_checked()


def test_as_is_clip_shows_a_notice_in_place_of_the_tabs(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video, mode="as_is", status="rendered")
    open_editor(page, server, cid)
    expect(page.get_by_test_id("as-is-notice")).to_contain_text("nothing to edit or render")
    expect(page.get_by_role("tab", name="Cut")).to_have_count(0)
    expect(page.get_by_role("button", name="Re-render")).to_have_count(0)
    expect(page.get_by_role("button", name="Post")).to_be_visible()
    page.keyboard.press("Meta+Enter")  # the render key does nothing here
    expect(toasts(page)).to_have_text("")
    assert ui_engine.runner.jobs_for(cid) == []


def test_a_clip_not_rendered_yet_says_so(page, ui_engine, server):
    packaging = PACKAGING.model_copy(update={"hook_title": TITLE}).model_dump(mode="json")
    pid = seed_project(
        ui_engine, clips=1, rendered=0, each=lambda k: {"start_s": START, "packaging": packaging}
    )
    open_editor(page, server, f"{pid}_c0")
    expect(page.get_by_test_id("player-frame")).to_contain_text("No video yet.")
    expect(page.get_by_role("link", name="Download")).to_have_count(0)
    expect(page.get_by_role("combobox", name="Version")).to_have_count(0)
    expect(page.get_by_role("button", name="Re-render")).to_be_enabled()


def test_keys_control_the_player(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    neutral(page)
    page.keyboard.press(" ")
    page.wait_for_function(PLAYING)
    expect(page.get_by_role("button", name="Pause")).to_be_visible()
    page.keyboard.press(" ")
    page.wait_for_function(PAUSED)
    start = page.evaluate(NOW)
    page.keyboard.press("l")
    at(page, start + 1)
    page.keyboard.press("l")
    at(page, start + 2)
    page.keyboard.press("j")
    at(page, start + 1)
    page.keyboard.press(".")
    at(page, start + 1 + 1 / 30)  # one frame at the render's 30 fps
    page.keyboard.press(",")
    page.keyboard.press(",")
    at(page, start + 1 - 1 / 30)
    page.wait_for_function(SHOWN)
    page.get_by_role("slider", name="Position").focus()  # a key the focused control takes isn't the editor's
    page.keyboard.press("Home")
    at(page, 0)
    page.evaluate("() => { document.querySelector('video').currentTime = 7.5; }")
    neutral(page)
    page.keyboard.press("l")
    at(page, 8.0)  # never past the end
    loop = page.get_by_role("button", name="Loop")
    expect(loop).to_have_attribute("aria-pressed", "true")
    loop.click()
    expect(loop).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("() => document.querySelector('video').loop") is False


@pytest.mark.parametrize("width,height", [(1440, 900), (820, 1000)])
def test_player_keeps_its_shape_and_leaves_the_tabs_in_view(page, ui_engine, server, video, width, height):
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, server, seed_clip(ui_engine, video))
    page.get_by_role("button", name="Safe zone").click()
    frame = page.get_by_test_id("player-frame").bounding_box()
    zone = page.get_by_test_id("safe-zone").bounding_box()
    tabs = page.get_by_role("tablist").bounding_box()
    assert frame and zone and tabs
    assert abs(frame["width"] / frame["height"] - 9 / 16) < 0.01, frame
    assert frame["x"] - 0.5 <= zone["x"] and zone["x"] + zone["width"] <= frame["x"] + frame["width"] + 0.5
    assert tabs["y"] < height, f"the tabs start {tabs['y'] - height:.0f} px below the window"


def test_safe_zone_overlay_toggles(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    toggle = page.get_by_role("button", name="Safe zone")
    zone = page.get_by_test_id("safe-zone")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(zone).to_have_count(0)
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    frame = page.get_by_test_id("player-frame").bounding_box()
    box = zone.bounding_box()
    assert frame and box
    assert abs(frame["width"] / frame["height"] - 9 / 16) < 0.01
    sx, sy = frame["width"] / 1080, frame["height"] / 1920  # SPEC 8.6: x 90 to 940, y 270 to 1250
    assert abs(box["x"] - frame["x"] - 90 * sx) <= 1.5
    assert abs(box["y"] - frame["y"] - 270 * sy) <= 1.5
    assert abs(box["width"] - 850 * sx) <= 1.5
    assert abs(box["height"] - 980 * sy) <= 1.5
    toggle.click()
    expect(zone).to_have_count(0)


def test_click_word_seeks_and_delete_strikes(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    word(page, 84).click()
    at(page, 2.0)  # where the render says word 84
    expect(word(page, 84)).to_have_attribute("aria-current", "true")  # the word playing
    expect(word(page, 84)).to_have_attribute("aria-pressed", "true")
    word(page, 86).click(modifiers=["Shift"])
    for i in (84, 85, 86):
        expect(word(page, i)).to_have_attribute("aria-pressed", "true")
    expect(word(page, 87)).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("Delete")
    for i in (84, 85, 86):
        expect(word(page, i)).to_have_accessible_name(f"w{i}, cut")
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    page.keyboard.press("Backspace")  # all of them struck, so they come back
    expect(word(page, 85)).to_have_accessible_name("w85")
    expect(waiting(page)).to_have_count(0)
    page.keyboard.press("Delete")
    assert edits(ui_engine, cid) == {}  # nothing is saved before the render
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 202
    assert answer.value.request.post_data_json == {"edits": {"struck": [84, 85, 86]}}
    assert edits(ui_engine, cid) == {"struck": [84, 85, 86]}
    expect(waiting(page)).to_have_count(0)
    expect(word(page, 85)).to_have_accessible_name("w85, cut")  # as saved


def test_i_and_o_set_the_range_and_reset_restores(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    word(page, 70).click()
    page.keyboard.press("i")
    expect(length(page)).to_have_text("24.9 s")
    expect(word(page, 70)).not_to_have_attribute("data-outside", "yes")
    word(page, 125).click()
    page.keyboard.press("o")
    expect(length(page)).to_have_text("27.9 s")
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {}, "range": [35.0, 62.9]}
    clip = repo.get_clip(ui_engine.db(), cid)
    assert (clip.start_s, clip.end_s) == (35.0, 62.9)
    assert edits(ui_engine, cid) == {"orig_range": [START, END]}
    expect(waiting(page)).to_have_count(0)
    page.get_by_role("button", name="Reset cut").click()
    expect(length(page)).to_have_text("19.9 s")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {}, "range": None}
    clip = repo.get_clip(ui_engine.db(), cid)
    assert (clip.start_s, clip.end_s) == (START, END)
    assert edits(ui_engine, cid) == {}
    expect(page.get_by_role("button", name="Reset cut")).to_be_disabled()


def test_range_handle_drag_snaps_to_words(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    start, end = (page.get_by_role("slider", name=name) for name in ("Clip start", "Clip end"))
    expect(start).to_have_attribute("aria-valuenow", "40")
    expect(end).to_have_attribute("aria-valuenow", "59.9")

    def drag(handle, i: int) -> None:
        box = word(page, i).bounding_box()
        assert box
        handle.hover()
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2, steps=8)
        page.mouse.up()

    drag(start, 76)  # dropped part way into the word, it takes the whole word in
    expect(start).to_have_attribute("aria-valuenow", "38")
    expect(length(page)).to_have_text("21.9 s")
    drag(end, 130)
    expect(end).to_have_attribute("aria-valuenow", "65.4")
    expect(length(page)).to_have_text("27.4 s")
    start.focus()
    page.keyboard.press("ArrowRight")  # a word at a time from the keyboard
    expect(start).to_have_attribute("aria-valuenow", "38.5")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {}, "range": [38.5, 65.4]}


def test_range_outside_limits_shows_the_sentence(page, console, ui_engine, server, video):
    console.allow(r"status of 422")  # the refusal itself, which Chrome logs
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    word(page, 83).click()
    page.keyboard.press("o")
    expect(length(page)).to_have_text("1.9 s")
    tab(page, "Look")
    neutral(page)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 422
    expect(page.get_by_role("tab", name="Cut")).to_have_attribute("aria-selected", "true")
    expect(panel(page).get_by_role("alert")).to_have_text(TOO_SHORT)
    clip = repo.get_clip(ui_engine.db(), cid)
    assert (clip.start_s, clip.end_s) == (START, END)
    expect(waiting(page)).to_have_text("1 change not rendered yet")  # still there to fix
    word(page, 90).click()
    page.keyboard.press("o")
    expect(panel(page).get_by_role("alert")).to_have_count(0)  # a change clears the sentence


def test_title_counter_and_alternatives(page, console, ui_engine, server, video):
    console.allow(r"status of 422")  # the refusal itself, which Chrome logs
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    tab(page, "Text")
    title = panel(page).get_by_role("textbox", name="Hook title")
    expect(title).to_have_value(TITLE)
    expect(panel(page).get_by_test_id("hook_title-count")).to_have_text("19 of 42")
    others = panel(page).get_by_role("group", name="Other titles").get_by_role("button")
    expect(others).to_have_text(ALTS)
    others.filter(has_text=ALTS[1]).click()
    expect(title).to_have_value(ALTS[1])
    expect(panel(page).get_by_test_id("hook_title-count")).to_have_text("33 of 42")
    expect(others).to_have_text([TITLE, ALTS[0]])  # the AI's own stays one click away
    title.fill("x" * 43)
    expect(panel(page).get_by_test_id("hook_title-count")).to_have_text("43 of 42")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 422
    expect(title).to_have_attribute("aria-invalid", "true")
    expect(panel(page).get_by_role("alert")).to_have_text(TITLE_LONG)
    assert edits(ui_engine, cid) == {}
    title.fill(ALTS[0])
    expect(title).not_to_have_attribute("aria-invalid", "true")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 202
    assert edits(ui_engine, cid) == {"hook_title": ALTS[0]}
    expect(page.get_by_role("heading", level=1)).to_contain_text(ALTS[0])


def test_accent_word_choices_follow_the_title(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    tab(page, "Text")
    accent = panel(page).get_by_role("group", name="Accent word")
    expect(accent.get_by_role("radio")).to_have_count(6)
    for name in ("None", "Hook", "1", "why", "we", "wait"):
        expect(accent.get_by_role("radio", name=name, exact=True)).to_be_attached()
    expect(accent.get_by_role("radio", name="None")).to_be_checked()
    accent.get_by_role("radio", name="wait", exact=True).check()
    panel(page).get_by_role("textbox", name="Hook title").fill("Nobody waits forever, waits")
    expect(accent.get_by_role("radio")).to_have_count(4)  # each word once
    expect(accent.get_by_role("radio", name="None")).to_be_checked()  # "wait" isn't in it now
    accent.get_by_role("radio", name="forever", exact=True).check()
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 202
    assert edits(ui_engine, cid) == {"hook_title": "Nobody waits forever, waits", "accent_word": "forever,"}


def test_fact_card_limits(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    tab(page, "Text")
    add = panel(page).get_by_role("button", name="Add fact card")
    for _ in range(3):
        add.click()
    expect(add).to_be_disabled()
    expect(panel(page)).to_contain_text(MAX_CARDS)

    def card(n: int):
        return panel(page).get_by_role("group", name=f"Fact card {n}")

    expect(card(1).get_by_test_id("fact_cards.0-count")).to_have_text("0 of 70")
    expect(card(1).get_by_role("combobox", name="Sentence").locator("option")).to_have_count(4)  # S9 to S12
    card(1).get_by_role("textbox", name="Text").fill("x" * 70)
    expect(card(1).get_by_test_id("fact_cards.0-count")).to_have_text("70 of 70")
    card(1).get_by_role("combobox", name="Sentence").select_option("S10")
    card(2).get_by_role("textbox", name="Text").fill("Two")
    card(3).get_by_role("button", name="Remove fact card 3").click()
    expect(add).to_be_enabled()
    expect(panel(page)).not_to_contain_text(MAX_CARDS)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.status == 202
    assert edits(ui_engine, cid) == {
        "fact_cards": [{"sid": "S10", "text": "x" * 70}, {"sid": "S9", "text": "Two"}]
    }


def test_caption_fix_is_sent_with_the_render(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    tab(page, "Text")
    fixes = panel(page).get_by_role("group", name="Caption corrections")
    fixes.get_by_role("combobox", name="Word").select_option("82")
    fixes.get_by_role("textbox", name="Shows as").fill("gonna")
    fixes.get_by_role("button", name="Add correction").click()
    expect(fixes.get_by_role("listitem")).to_have_text(["w82 shows as gonnaRemove"])
    expect(panel(page).get_by_role("switch", name="Emoji")).to_be_checked()
    panel(page).get_by_role("switch", name="Emoji").click()
    tab(page, "Cut")
    expect(word(page, 82)).to_have_text("gonna")
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {"word_fixes": {"82": "gonna"}, "emoji": False}}
    assert edits(ui_engine, cid) == {"word_fixes": {"82": "gonna"}, "emoji": False}


def test_look_changes_wait_for_cmd_enter(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    sent = []
    page.on("request", lambda r: sent.append(r.url) if r.method in ("POST", "PATCH", "PUT") else None)
    tab(page, "Look")
    expect(panel(page)).to_contain_text(f"AI's choice: Face crop. {REASON}")
    panel(page).get_by_role("radio", name="Blurred fit").check()
    panel(page).get_by_role("radio", name="Neon").check()
    punch = panel(page).get_by_role("switch", name="Punch-ins")
    expect(punch).to_be_checked()
    punch.click()
    expect(waiting(page)).to_have_text("3 changes not rendered yet")
    tab(page, "Text")
    tab(page, "Look")  # a tab left keeps its changes
    expect(panel(page).get_by_role("radio", name="Blurred fit")).to_be_checked()
    expect(punch).not_to_be_checked()
    expect(panel(page).get_by_role("button", name="New random segment")).to_have_count(0)
    assert sent == [] and edits(ui_engine, cid) == {}
    neutral(page)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {
        "edits": {"layout": "fit", "preset": "neon", "punch_ins": False}
    }
    assert edits(ui_engine, cid) == {"layout": "fit", "preset": "neon", "punch_ins": False}
    assert len(sent) == 1
    expect(waiting(page)).to_have_count(0)
    expect(page.get_by_test_id("clip-status")).to_have_text("In the queue")
    expect(toasts(page)).to_contain_text("Rendering the clip")


def test_cold_open_switch(page, ui_engine, server, video):
    """The Look tab's Cold open switch quotes the line the AI picked and queues a render when turned on; a
    clip with no line picked has it off and greyed out, saying why."""
    picked = PACKAGING.model_copy(update={"hook_title": TITLE, "hook_alts": ALTS, "cold_open_sid": "S10"})
    cid = seed_clip(ui_engine, video, packaging=picked.model_dump(mode="json"))
    open_editor(page, server, cid)
    tab(page, "Look")
    cold = panel(page).get_by_role("switch", name="Cold open")
    line = " ".join(f"w{i}" for i in range(90, 100))
    expect(panel(page)).to_contain_text(f'Opens on "{line}", then a whoosh into the start.')
    expect(cold).to_be_enabled()
    expect(cold).not_to_be_checked()
    cold.click()
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    neutral(page)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {"cold_open": True}}
    assert edits(ui_engine, cid) == {"cold_open": True}
    expect(page.get_by_test_id("clip-status")).to_have_text("In the queue")
    plain = seed_clip(ui_engine, video)
    open_editor(page, server, plain)
    tab(page, "Look")
    off = panel(page).get_by_role("switch", name="Cold open")
    expect(panel(page)).to_contain_text("No line in this clip was picked for a cold open.")
    expect(off).to_be_disabled()
    expect(off).not_to_be_checked()


def test_sound_tab_picks_music_and_level(page, ui_engine, server, video):
    conn = ui_engine.db()
    for item in (
        LibraryItem(
            id="lm1", kind="music", name="Arcade Rush", path="/music/rush.m4a", tags=["hype"], energy=5
        ),
        LibraryItem(
            id="lm2",
            kind="music",
            name="Night Drive",
            path="/music/night.m4a",
            credit="Kavinsky",
            credit_required=True,
            tags=["calm"],
            energy=3,
        ),
    ):
        repo.add_library_item(conn, item)
    late = trends_store.put_shortlist(conn, shortlist(title="Late Train", mood_tags=["calm"]))
    cid = seed_clip(ui_engine, video)  # calm, with an energy of 3, as the test packaging is
    open_editor(page, server, cid)
    tab(page, "Sound")
    expect(panel(page)).not_to_contain_text("in a later version")
    music = panel(page).get_by_role("combobox", name="Music")
    expect(music).to_have_value("")  # Off, as a clip starts
    # the tracks that suit the clip come first, each saying why
    expect(music.locator("option")).to_have_text(
        ["Off", "Best match from your library", "Night Drive, matches its calm mood", "Arcade Rush"]
    )
    expect(panel(page).get_by_role("slider", name="Music level")).to_have_count(0)
    music.select_option("lm2")
    level = panel(page).get_by_role("slider", name="Music level")
    expect(level).to_have_value("-12")
    expect(panel(page).get_by_text("12 dB under the speech")).to_be_visible()
    expect(panel(page).get_by_text(CREDIT_NOTE)).to_be_visible()
    level.fill("-18")
    expect(panel(page).get_by_text("18 dB under the speech")).to_be_visible()
    expect(waiting(page)).to_have_text("2 changes not rendered yet")
    music.select_option("auto")
    expect(panel(page).get_by_text(CREDIT_NOTE)).to_have_count(0)  # which track it is waits for the render
    music.select_option("lm2")

    # the shortlisted sounds that suit it, each opening in your browser, for a post to pick
    trending = panel(page).get_by_role("list", name="Trending sounds for this clip")
    expect(trending.get_by_role("listitem")).to_have_text([re.compile(r"^Late Train.*Matches its calm mood")])
    expect(panel(page).get_by_text("Pick one for a post in the Post dialog.")).to_be_visible()
    with page.expect_response(lambda r: r.url.endswith("/open")) as answer:
        trending.get_by_role("button", name="Open Late Train").click()
    assert answer.value.status == 204
    assert [c for c in ui_engine.run_cmd.calls if c[:1] == ["open"]] == [["open", late.url]]

    with rendered(page, cid) as answer:
        page.get_by_role("button", name="Re-render").click()
    assert answer.value.request.post_data_json == {"edits": {"music": "lm2", "music_db": -18}}
    assert edits(ui_engine, cid) == {"music": "lm2", "music_db": -18}


def test_voiceover_from_a_file(page, ui_engine, server, video, tmp_path):
    """The Sound tab's Voiceover takes a recording file chosen beside Record: it is saved and set, the clip
    queues a render, and its player shows with where it starts, which waits for the render like the other
    edits. Record is never pressed, so Chrome never asks for the microphone."""
    cid = seed_clip(ui_engine, video)
    take = make_test_audio(tmp_path / "take.wav", 2.0, 440)
    open_editor(page, server, cid)
    tab(page, "Sound")
    voice = panel(page).get_by_role("region", name="Voiceover")
    expect(voice.get_by_role("button", name="Record")).to_be_enabled()
    expect(voice.get_by_role("button", name="Choose a file")).to_be_enabled()
    expect(voice.get_by_label("Your voiceover")).to_have_count(0)
    with page.expect_response(lambda r: r.request.method == "POST" and "/voiceover?" in r.url) as answer:
        voice.get_by_label("Voiceover recording file").set_input_files(take)
    assert answer.value.status == 202
    file = edits(ui_engine, cid)["voiceover"]
    assert re.fullmatch(r"[0-9a-f]{10}\.wav", file)
    expect(toasts(page)).to_contain_text("Voiceover saved. The clip renders again with it.")
    expect(page.get_by_test_id("clip-status")).to_have_text("In the queue")
    player = voice.get_by_label("Your voiceover")
    expect(player).to_have_attribute("src", f"/api/clips/{cid}/voiceover/{file}")
    start = voice.get_by_role("spinbutton", name="Starts at")
    expect(start).to_have_value("0")
    expect(start).to_have_attribute("max", "19.9")
    expect(voice).to_contain_text("s into the clip, up to 19.9")
    start.fill("2.5")
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    with rendered(page, cid) as answer:
        page.get_by_role("button", name="Re-render").click()
    assert answer.value.request.post_data_json == {"edits": {"voiceover_at": 2.5}}
    assert edits(ui_engine, cid) == {"voiceover": file, "voiceover_at": 2.5}
    with page.expect_response(lambda r: r.request.method == "DELETE") as answer:
        voice.get_by_role("button", name="Remove").click()
    assert answer.value.status == 202
    expect(player).to_have_count(0)
    assert edits(ui_engine, cid).get("voiceover") is None


def test_post_copy_saves_on_blur_with_utf16_counts(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    credit = views.clip_detail(ui_engine, repo.get_clip(ui_engine.db(), cid))["project"]["credit"]
    open_editor(page, server, cid)
    tab(page, "Post copy")
    caption = panel(page).get_by_role("textbox", name="TikTok caption")
    tags = panel(page).get_by_role("textbox", name="TikTok hashtags")
    count = panel(page).get_by_test_id("tiktok-count")
    expect(caption).to_have_value(PACKAGING.tiktok_caption)
    expect(tags).to_have_value("#deadlines")
    n = units(post_text(PACKAGING.tiktok_caption, ["#deadlines"], credit))
    expect(count).to_have_text(f"{n} of 2200")
    caption.fill(PACKAGING.tiktok_caption + " 🔥")
    expect(count).to_have_text(f"{n + 3} of 2200")  # a space and an emoji, which counts 2
    assert edits(ui_engine, cid) == {}
    with page.expect_response(lambda r: r.request.method == "PATCH") as answer:
        tags.focus()
    assert answer.value.request.post_data_json == {
        "edits": {"tiktok_caption": PACKAGING.tiktok_caption + " 🔥"}
    }
    assert edits(ui_engine, cid) == {"tiktok_caption": PACKAGING.tiktok_caption + " 🔥"}
    expect(panel(page).get_by_test_id("tiktok-saved")).to_have_text("Saved")
    tags.fill("#deadlines #focus")
    with page.expect_response(lambda r: r.request.method == "PATCH"):
        caption.focus()
    assert edits(ui_engine, cid)["tiktok_hashtags"] == ["#deadlines", "#focus"]
    expect(count).to_have_text(
        f"{units(post_text(PACKAGING.tiktok_caption + ' 🔥', ['#deadlines', '#focus'], credit))} of 2200"
    )
    expect(waiting(page)).to_have_count(0)  # post copy isn't in the video, so it needs no render
    with page.expect_response(lambda r: r.request.method == "PATCH") as answer:
        panel(page).get_by_role("button", name="Reset TikTok to the AI's").click()
    assert answer.value.request.post_data_json == {"edits": {"tiktok_caption": None, "tiktok_hashtags": None}}
    assert edits(ui_engine, cid) == {}
    expect(caption).to_have_value(PACKAGING.tiktok_caption)
    expect(panel(page).get_by_role("textbox", name="Instagram caption")).to_have_value(
        PACKAGING.instagram_caption
    )


def test_unsaved_changes_dialog(page, ui_engine, server, video):
    cid = seed_clip(ui_engine, video)
    pid = cid.rsplit("_c", 1)[0]
    open_editor(page, server, cid)
    tab(page, "Look")
    panel(page).get_by_role("switch", name="Punch-ins").click()
    page.get_by_role("link", name="Back to project").click()
    dialog = page.get_by_role("dialog", name=LEAVE)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Keep editing").click()
    expect(dialog).to_have_count(0)
    assert page.url.endswith(f"/clips/{cid}")
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    page.get_by_role("navigation", name="Pages").get_by_role("link", name="Projects").click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Discard changes").click()
    expect(page).to_have_url(f"{server.url}/projects")
    assert edits(ui_engine, cid) == {}
    page.go_back()
    expect(page.get_by_role("heading", level=1)).to_contain_text(TITLE)
    expect(waiting(page)).to_have_count(0)  # discarded, so a new visit starts clean
    page.get_by_role("link", name="Back to project").click()  # nothing waiting, so nothing asks
    expect(page).to_have_url(f"{server.url}/projects/{pid}")


def test_changes_waiting_outlast_a_reload(page, ui_engine, server, video):
    """A window that reloads itself (the engine restarted, so its session cookie is new) keeps the changes
    waiting for a render, and they render as before."""
    cid = seed_clip(ui_engine, video)
    open_editor(page, server, cid)
    tab(page, "Look")
    panel(page).get_by_role("switch", name="Punch-ins").click()
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    page.reload()
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    tab(page, "Look")
    expect(panel(page).get_by_role("switch", name="Punch-ins")).not_to_be_checked()
    neutral(page)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert answer.value.request.post_data_json == {"edits": {"punch_ins": False}}
    expect(waiting(page)).to_have_count(0)
    page.reload()
    expect(page.get_by_role("heading", level=1)).to_contain_text(TITLE)
    expect(waiting(page)).to_have_count(0)  # rendered, so nothing is kept


def test_previous_version_keeps_playing_until_the_new_one_is_ready(page, renders, video):
    engine, live, gate = renders
    cid = seed_clip(engine, video)
    open_editor(page, live, cid)
    player = page.locator("video")
    expect(player).to_have_attribute("src", src(engine, cid, 1))
    neutral(page)
    page.keyboard.press("l")
    page.keyboard.press("l")
    at(page, 2.0)
    tab(page, "Look")
    panel(page).get_by_role("switch", name="Punch-ins").click()
    neutral(page)
    page.keyboard.press("Meta+Enter")
    expect(page.get_by_test_id("clip-status")).to_have_text("Rendering")
    expect(page.get_by_test_id("render-progress")).to_have_text("50%")
    expect(player).to_have_attribute("src", src(engine, cid, 1))
    at(page, 2.0)
    gate.set()
    expect(page.get_by_test_id("clip-status")).to_have_text("Ready")
    expect(player).to_have_attribute("src", src(engine, cid, 2))
    at(page, 2.0)  # the new version picks up where the old one was
    versions = page.get_by_role("combobox", name="Version")
    expect(versions.locator("option")).to_have_text(["Version 2 (current)", "Version 1"])
    versions.select_option(label="Version 1")
    expect(player).to_have_attribute("src", src(engine, cid, 1))


def test_new_random_segment_for_gameplay(page, ui_engine, server, video, tmp_path):
    for n, name in enumerate(("Subway run", "Parkour map")):
        path = tmp_path / f"gameplay{n}.mp4"
        path.write_bytes(b"a gameplay video")
        item = LibraryItem(id=f"lg{n}", kind="gameplay", name=name, path=str(path), duration_s=600.0)
        repo.add_library_item(ui_engine.db(), item)
    cid = seed_clip(ui_engine, video, layout="gameplay", layout_reason="The talk is slow to look at.")
    open_editor(page, server, cid)
    tab(page, "Look")
    expect(panel(page)).to_contain_text("AI's choice: Gameplay split.")
    item = panel(page).get_by_role("combobox", name="Gameplay video")
    expect(item.locator("option")).to_have_text(["Any from the library", "Subway run", "Parkour map"])
    with page.expect_response(lambda r: r.url.endswith(f"/api/clips/{cid}/gameplay/shuffle")) as answer:
        panel(page).get_by_role("button", name="New random segment").click()
    assert answer.value.status == 202
    assert edits(ui_engine, cid) == {"gameplay_seed": 1}
    expect(page.get_by_test_id("clip-status")).to_have_text("In the queue")
    expect(toasts(page)).to_contain_text("Rendering with another stretch of gameplay")
    expect(waiting(page)).to_have_count(0)  # it renders on its own, not with the changes waiting
    item.select_option(label="Parkour map")
    expect(waiting(page)).to_have_text("1 change not rendered yet")
    panel(page).get_by_role("radio", name="Blurred fit").check()
    expect(panel(page).get_by_role("button", name="New random segment")).to_have_count(0)
    with rendered(page, cid) as answer:
        page.keyboard.press("Meta+Enter")
    assert edits(ui_engine, cid) == {"gameplay_seed": 1, "gameplay_item": "lg1", "layout": "fit"}
