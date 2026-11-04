"""The Library and Settings pages (SPEC 12 and 14.7). Gameplay is added with who made it and its licence and
deleted only once asked, and a starter list video downloads only when you press Download (from a fake
YouTube here); the sound effects play and the fonts draw in their own face. Each setting saves
as it changes, a refused one says why at its field, and the theme changes at once. The Doctor's update
runs a fake here: a UI test never updates yt-dlp for real."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.assets import FONTS_DIR
from clipforge.doctor import Check
from clipforge.library import starter
from clipforge.server.routes import system
from clipforge.server.routes.library import NO_CREDIT, NO_LICENCE
from clipforge.ytdlp_update import UpdateResult
from tests.helpers import make_test_audio
from tests.ui.test_shell import opened, toasts
from tests.unit.test_api_projects import wait_for
from tests.unit.test_clip_jobs import add_clips, project
from tests.unit.test_starter import StarterTube
from tests.unit.test_storage import FREED, settled

NO_GAMEPLAY = "No gameplay yet."
MUTED = "Gameplay plays muted, under the speaker, in clips with the gameplay layout."
NO_FILE = "Choose a video file first."
NO_MUSIC = "No music yet."
MUSIC_FOR = "Add tracks you own or have a licence for."
NO_TRACK = "Choose an audio file first."
MUSIC_LICENCE = "Say what the licence lets you do."
ARTLIST = "Bought from Artlist for social use"
LINK = "https://creativecommons.org/licenses/by/4.0/"
LANGUAGE_WRONG = "The speech language must be Auto or a code like en or es."
SHORTEST_WRONG = "The shortest video to clip must be from 0.5 to 60 minutes."
CLAUDE_FIX = "Install Claude Code, then run claude in Terminal and log in."
UPDATED = UpdateResult(
    True, "2026.10.10", "2026.10.20", "yt-dlp is now 2026.10.20. Restart ClipForge to use it."
)
NOT_UPDATED = UpdateResult(
    False, "2026.10.10", "2026.10.10", "yt-dlp couldn't update. Check the internet, then try again."
)
NOTHING_TO_CLEAN = "Nothing to clean up yet."
SECTIONS = ["AI", "Speech", "Downloads", "Rendering", "Default look", "Storage", "Appearance", "Doctor"]

# Whether a progress bar was ever in the page, so one that comes and goes between two looks is still seen.
WATCH_PROGRESS = """() => {
  window.sawProgress = [];
  new MutationObserver(() => {
    for (const bar of document.querySelectorAll('[role="progressbar"]')) {
      window.sawProgress.push(bar.getAttribute("aria-label"));
    }
  }).observe(document.body, { subtree: true, childList: true, attributes: true });
}"""
BACKGROUND = "() => getComputedStyle(document.body).backgroundColor"
LOADED_FACES = """() => [...document.fonts]
  .filter((f) => f.status === "loaded")
  .map((f) => f.family.replace(/"/g, ""))"""


def open_library(page: Page, server, tab: str = "Gameplay") -> None:
    opened(page, f"{server.url}/library")
    expect(page.get_by_role("heading", name="Library", level=1)).to_be_visible()
    page.get_by_role("tab", name=tab).click()


def open_settings(page: Page, server) -> None:
    opened(page, f"{server.url}/settings")
    expect(page.get_by_role("heading", name="Settings", level=1)).to_be_visible()


def section(page: Page, name: str):
    return page.get_by_role("region", name=name, exact=True)


def adding(page: Page, video=None):
    """The Add gameplay dialog, with ``video`` chosen in it."""
    page.get_by_role("button", name="Add gameplay").click()
    dialog = page.get_by_role("dialog", name="Add gameplay")
    if video is not None:
        with page.expect_file_chooser() as chooser:
            dialog.get_by_role("button", name="Choose video").click()
        chooser.value.set_files(video)
        expect(dialog).to_contain_text(video.name)
    return dialog


def saved(page: Page) -> None:
    expect(toasts(page)).to_contain_text("Saved")


# Library


def test_add_gameplay_with_progress_and_delete(page, ui_engine, server, video):
    open_library(page, server)
    expect(page.get_by_role("tab", name="Gameplay")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_text(NO_GAMEPLAY)).to_be_visible()
    expect(page.get_by_text(MUTED)).to_be_visible()
    page.evaluate(WATCH_PROGRESS)
    dialog = adding(page, video)
    name = dialog.get_by_label("Name")
    expect(name).to_have_value("clip")  # the file's name until one is given
    name.fill("Parkour run")
    dialog.get_by_label("Credit").fill("Zoë Plays")
    dialog.get_by_label("Licence", exact=True).fill("CC BY 4.0")
    dialog.get_by_label("Licence link").fill(LINK)
    dialog.get_by_label("Notes").fill("Minecraft parkour, no commentary")
    dialog.get_by_role("button", name="Add", exact=True).click()
    expect(dialog).to_be_hidden()
    assert any(label and label.startswith("Uploading") for label in page.evaluate("() => window.sawProgress"))
    card = page.get_by_role("article", name="Parkour run")
    expect(card).to_contain_text("0:02")
    expect(card).to_contain_text(" KB")  # its size
    expect(card).to_contain_text("Zoë Plays")
    expect(card.get_by_role("link", name="CC BY 4.0")).to_have_attribute("href", LINK)
    expect(card).to_contain_text("Not used yet")
    expect(card.locator("video")).to_have_js_property("muted", True)
    (item,) = repo.list_library(ui_engine.db())
    assert (item.name, item.credit, item.license, item.license_url) == (
        "Parkour run",
        "Zoë Plays",
        "CC BY 4.0",
        LINK,
    )
    assert item.notes == "Minecraft parkour, no commentary"
    expect(page.get_by_text(NO_GAMEPLAY)).to_have_count(0)

    card.get_by_role("button", name="Delete").click()
    ask = page.get_by_role("dialog", name="Delete this gameplay?")
    expect(ask).to_contain_text("Parkour run")
    ask.get_by_role("button", name="Cancel").click()
    expect(card).to_be_visible()
    assert len(repo.list_library(ui_engine.db())) == 1  # asked, not done
    card.get_by_role("button", name="Delete").click()
    page.get_by_role("dialog", name="Delete this gameplay?").get_by_role("button", name="Delete").click()
    expect(card).to_have_count(0)
    expect(page.get_by_text(NO_GAMEPLAY)).to_be_visible()
    assert repo.list_library(ui_engine.db()) == []


def test_starter_list(page, ui_engine, ui_server, video, console):
    tube = ui_engine.youtube = StarterTube(video)
    entries = {e.id: e for e in starter.entries()}
    open_library(page, ui_server)
    listed = section(page, "Starter list")
    expect(listed).to_contain_text(starter.NOTE)

    def row(entry_id: str):
        return listed.get_by_role("listitem", name=entries[entry_id].title, exact=True)

    mine = row("orbital-minecraft-16m")
    expect(mine).to_contain_text("Minecraft")
    expect(mine.get_by_role("list", name="Limits")).to_have_text(starter.PERSONAL_ONLY)
    expect(row("sybo-subway-9x16").get_by_role("list", name="Limits")).to_have_text(starter.INFORMAL)
    expect(row("orbital-plus-subway")).to_contain_text(starter.FIRST_PART)
    expect(row("orbital-plus-subway")).to_contain_text("1:16:10")
    expect(row("orbital-plus-subway").get_by_role("list", name="Limits")).to_have_count(0)
    expect(row("orbital-minecraft-16m")).not_to_contain_text(starter.FIRST_PART)
    expect(page.get_by_text(NO_GAMEPLAY)).to_be_visible()  # your own files, under the list
    assert tube.calls == []  # nothing downloads until you press Download

    ludo = entries["ludo-surfs-subway"]
    row(ludo.id).get_by_role("button", name=f"Open {ludo.title}").click()
    expect(toasts(page)).to_contain_text(f"Opened {ludo.title} in your browser.")
    assert [c for c in ui_engine.run_cmd.calls if c[:1] == ["open"]] == [["open", starter.url_of(ludo)]]
    assert tube.calls == []

    row(ludo.id).get_by_role("button", name=f"Download {ludo.title}").click()
    expect(row(ludo.id)).to_contain_text("Added", timeout=30_000)
    expect(row(ludo.id).get_by_role("button", name=f"Download {ludo.title}")).to_have_count(0)
    card = page.get_by_role("article", name=ludo.title)
    expect(card).to_contain_text(ludo.credit)
    expect(card).to_contain_text("Not used yet")
    expect(page.get_by_text(NO_GAMEPLAY)).to_have_count(0)
    ((_, url, _, quality),) = tube.called("download_video")
    assert (url, quality) == (starter.url_of(ludo), "best")
    (item,) = repo.list_library(ui_engine.db(), "gameplay")
    assert (item.name, item.tags) == (ludo.title, [f"starter:{ludo.id}"])


def test_add_gameplay_needs_credit_and_licence(page, ui_engine, server, video, console):
    console.allow(r"status of 422")  # the refused licence link, which Chrome logs
    open_library(page, server)
    dialog = adding(page)
    add = dialog.get_by_role("button", name="Add", exact=True)
    add.click()
    expect(dialog).to_contain_text(NO_FILE)
    with page.expect_file_chooser() as chooser:
        dialog.get_by_role("button", name="Choose video").click()
    chooser.value.set_files(video)
    expect(dialog).not_to_contain_text(NO_FILE)
    add.click()
    credit = dialog.get_by_label("Credit")
    expect(dialog).to_contain_text(NO_CREDIT)
    expect(credit).to_have_attribute("aria-invalid", "true")
    expect(credit).to_be_focused()
    credit.fill("Zoë Plays")
    add.click()
    licence = dialog.get_by_label("Licence", exact=True)
    expect(dialog).to_contain_text(NO_LICENCE)
    expect(dialog).not_to_contain_text(NO_CREDIT)
    expect(licence).to_have_attribute("aria-invalid", "true")
    licence.fill("CC BY 4.0")
    dialog.get_by_label("Licence link").fill("ftp://example.com/licence")
    add.click()
    link = dialog.get_by_label("Licence link")
    expect(dialog).to_contain_text("The licence link must start with http:// or https://.")  # the engine's
    expect(link).to_have_attribute("aria-invalid", "true")
    assert repo.list_library(ui_engine.db()) == []
    link.fill("")
    add.click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_role("article", name="clip")).to_be_visible()
    (item,) = repo.list_library(ui_engine.db())
    assert item.license_url is None


def test_music_tab_adds_plays_and_deletes(page, ui_engine, server, tmp_path, console):
    track = make_test_audio(tmp_path / "night drive.m4a", seconds=1.2)
    open_library(page, server, "Music")
    expect(page.get_by_role("tab")).to_have_text(["Gameplay", "Music", "SFX", "Fonts"])
    expect(page.get_by_text(NO_MUSIC)).to_be_visible()
    expect(page.get_by_text(MUSIC_FOR)).to_be_visible()
    page.get_by_role("button", name="Add music").click()
    dialog = page.get_by_role("dialog", name="Add music")
    add = dialog.get_by_role("button", name="Add", exact=True)
    add.click()
    expect(dialog).to_contain_text(NO_TRACK)
    with page.expect_file_chooser() as chooser:
        dialog.get_by_role("button", name="Choose file").click()
    chooser.value.set_files(track)
    expect(dialog).not_to_contain_text(NO_TRACK)
    title = dialog.get_by_label("Title")
    expect(title).to_have_value("night drive")  # the file's name until one is given
    title.fill("Night Drive")
    dialog.get_by_label("Artist").fill("Kai Lune")
    add.click()
    licence = dialog.get_by_label("Licence", exact=True)
    expect(dialog).to_contain_text(MUSIC_LICENCE)
    expect(licence).to_have_attribute("aria-invalid", "true")
    expect(licence).to_be_focused()
    licence.fill(ARTLIST)
    dialog.get_by_label("The licence asks for credit").check()
    dialog.get_by_role("checkbox", name="Hype").check()
    dialog.get_by_role("checkbox", name="Dark").check()
    dialog.get_by_label("Energy").select_option("4")
    add.click()
    expect(dialog).to_be_hidden()
    row = page.get_by_role("listitem", name="Night Drive")
    for text in (
        "Kai Lune",
        ARTLIST,
        "Credit in captions",
        "Hype",
        "Dark",
        "Energy 4",
        "0:01",
        "Not used yet",
    ):
        expect(row).to_contain_text(text)
    expect(page.get_by_text(NO_MUSIC)).to_have_count(0)
    (item,) = repo.list_library(ui_engine.db(), "music")
    assert (item.name, item.credit, item.license, item.credit_required, item.tags, item.energy) == (
        "Night Drive",
        "Kai Lune",
        ARTLIST,
        True,
        ["hype", "dark"],
        4,
    )

    play = row.get_by_role("button", name="Play Night Drive")
    with page.expect_response(lambda r: "/media/library/music/" in r.url) as got:
        play.click()
    assert got.value.ok and got.value.headers["content-type"] == "audio/mp4"
    expect(play).to_have_attribute("aria-pressed", "true")  # playing
    expect(play).to_have_attribute("aria-pressed", "false", timeout=5_000)  # and done

    row.get_by_role("button", name="Edit").click()
    editing = page.get_by_role("dialog", name="Edit Night Drive")
    expect(editing.get_by_label("Licence", exact=True)).to_have_value(ARTLIST)
    expect(editing.get_by_role("checkbox", name="Hype")).to_be_checked()
    editing.get_by_label("Energy").select_option("2")
    editing.get_by_role("checkbox", name="Dark").uncheck()
    editing.get_by_role("button", name="Save").click()
    expect(editing).to_be_hidden()
    expect(row).to_contain_text("Energy 2")
    expect(row).not_to_contain_text("Dark")
    assert repo.get_library_item(ui_engine.db(), item.id).tags == ["hype"]

    (cid,) = add_clips(ui_engine, project(ui_engine))
    repo.update_clip(ui_engine.db(), cid, edits={"music": item.id})
    open_library(page, server, "Music")
    expect(row).to_contain_text("Used in 1 clip")
    row.get_by_role("button", name="Delete").click()
    ask = page.get_by_role("dialog", name="Delete this track?")
    expect(ask).to_contain_text("1 clip uses this track. It will have no music the next time it renders.")
    ask.get_by_role("button", name="Keep it").click()
    expect(row).to_be_visible()
    assert len(repo.list_library(ui_engine.db(), "music")) == 1  # asked, not done
    row.get_by_role("button", name="Delete").click()
    page.get_by_role("dialog", name="Delete this track?").get_by_role("button", name="Delete track").click()
    expect(row).to_have_count(0)
    expect(page.get_by_text(NO_MUSIC)).to_be_visible()
    assert repo.list_library(ui_engine.db(), "music") == []


def test_sfx_play_buttons_load_audio(page, server):
    open_library(page, server, "SFX")
    rows = page.get_by_role("listitem").filter(has=page.get_by_role("button", name="Play"))
    expect(rows).to_have_count(5)
    for kind in ("Whoosh", "Pop", "Hit", "Riser", "Click"):
        expect(page.get_by_role("button", name=f"Play {kind.lower()}")).to_be_visible()
    play = page.get_by_role("button", name="Play whoosh")
    with page.expect_response(lambda r: r.url.endswith("/api/library/sfx/whoosh.wav")) as got:
        play.click()
    assert got.value.status == 200 and got.value.headers["content-type"].startswith("audio/")
    expect(play).to_have_attribute("aria-pressed", "true")  # playing
    expect(play).to_have_attribute("aria-pressed", "false", timeout=5_000)  # and done, 0.45 s later


def test_fonts_tab_draws_bundled_fonts(page, server):
    files = sorted(p.name for p in FONTS_DIR.glob("*.ttf"))
    open_library(page, server, "Fonts")
    samples = page.get_by_test_id("font-sample")
    expect(samples).to_have_count(len(files))
    page.wait_for_function(
        f"() => [...document.fonts].filter((f) => f.status === 'loaded').length >= {len(files)}"
    )
    loaded = page.evaluate(LOADED_FACES)
    drawn = samples.evaluate_all("(els) => els.map((el) => getComputedStyle(el).fontFamily)")
    for file, family in zip(files, drawn, strict=True):
        face = f"ClipForge {file.removesuffix('.ttf')}"
        assert face in loaded and face in family, (file, family)
    bebas = page.get_by_role("listitem").filter(has_text="Bebas Neue")
    expect(bebas).to_contain_text("Regular")
    expect(bebas.get_by_role("link", name="OFL-BebasNeue.txt")).to_have_attribute(
        "href", "/api/fonts/OFL-BebasNeue.txt"
    )


# Settings


def test_settings_save_and_validation_sentence(page, ui_engine, server, console):
    console.allow(r"status of 422")  # the refused language, which Chrome logs
    open_settings(page, server)
    for name in SECTIONS:
        expect(section(page, name)).to_be_visible()
    calls = section(page, "AI").get_by_label("AI calls at once")
    expect(calls).to_have_value("3")
    calls.fill("5")
    calls.press("Enter")
    saved(page)
    assert wait_for(lambda: ui_engine.settings.llm_parallel == 5)
    discover = section(page, "AI").get_by_label("Discover model")
    expect(discover).to_have_attribute("placeholder", "opus")  # the default, until one is set
    discover.fill("sonnet")
    discover.blur()
    assert wait_for(lambda: ui_engine.settings.llm_models == {"claude_cli": {"discover": "sonnet"}})

    language = section(page, "Speech").get_by_label("Speech language")
    language.fill("French")
    language.blur()
    expect(section(page, "Speech")).to_contain_text(LANGUAGE_WRONG)
    expect(language).to_have_attribute("aria-invalid", "true")
    expect(language).to_have_value("French")  # kept to fix, not put back
    assert ui_engine.settings.asr_language == "auto"
    language.fill("es")
    language.press("Enter")
    expect(section(page, "Speech")).not_to_contain_text(LANGUAGE_WRONG)
    assert wait_for(lambda: ui_engine.settings.asr_language == "es")

    downloads = section(page, "Downloads")
    downloads.get_by_role("switch", name="Comments").uncheck()
    assert wait_for(lambda: ui_engine.settings.comments is False)
    channels = downloads.get_by_label("Your channels")
    channels.fill("@me\n\n  @alsome  ")
    channels.blur()
    assert wait_for(lambda: ui_engine.settings.own_channels == ["@me", "@alsome"])
    section(page, "Rendering").get_by_label("Render on their own").select_option("top5")
    assert wait_for(lambda: ui_engine.settings.auto_render == "top5")
    look = section(page, "Default look")
    look.get_by_label("Caption style").select_option("hormozi")
    assert wait_for(lambda: ui_engine.settings.caption_preset == "hormozi")
    look.get_by_role("switch", name="Progress bar").check()
    assert wait_for(lambda: ui_engine.settings.progress_bar is True)

    page.reload()
    expect(section(page, "AI").get_by_label("AI calls at once")).to_have_value("5")
    expect(section(page, "AI").get_by_label("Discover model")).to_have_value("sonnet")
    expect(section(page, "Speech").get_by_label("Speech language")).to_have_value("es")
    expect(section(page, "Downloads").get_by_label("Your channels")).to_have_value("@me\n@alsome")
    expect(section(page, "Downloads").get_by_role("switch", name="Comments")).not_to_be_checked()


def test_settings_skip_lengths_save(page, ui_engine, server, console):
    """SPEC 10.9: the lengths a clipping channel skips are set once here, for every channel."""
    console.allow(r"status of 422")  # the refused length, which Chrome logs
    open_settings(page, server)
    watch = section(page, "Clipping channels")
    expect(watch).to_contain_text("skip videos under 4 minutes and over 4 hours")
    shortest = watch.get_by_label("Shortest video to clip")
    expect(shortest).to_have_value("4")
    shortest.fill("90")
    shortest.press("Enter")
    expect(watch).to_contain_text(SHORTEST_WRONG)
    expect(shortest).to_have_attribute("aria-invalid", "true")
    assert ui_engine.settings.watch.min_minutes == 4.0
    shortest.fill("2.5")
    shortest.press("Enter")
    expect(watch).not_to_contain_text(SHORTEST_WRONG)
    saved(page)
    assert wait_for(lambda: ui_engine.settings.watch.min_minutes == 2.5)
    longest = watch.get_by_label("Longest video to clip")
    longest.fill("6")
    longest.blur()
    assert wait_for(lambda: ui_engine.settings.watch.max_hours == 6.0)
    expect(watch).to_contain_text("skip videos under 2.5 minutes and over 6 hours")
    assert ui_engine.settings.watch.min_minutes == 2.5  # each field sends the other as it is

    page.reload()
    expect(section(page, "Clipping channels").get_by_label("Shortest video to clip")).to_have_value("2.5")
    expect(section(page, "Clipping channels").get_by_label("Longest video to clip")).to_have_value("6")


def test_theme_switch_applies_at_once(page, ui_engine, server):
    open_settings(page, server)
    html = page.locator("html")
    theme = section(page, "Appearance").get_by_role("group", name="Theme")
    expect(html).to_have_attribute("data-theme", "dark")
    dark = page.evaluate(BACKGROUND)
    theme.get_by_role("radio", name="Light").check()
    expect(html).to_have_attribute("data-theme", "light")
    page.wait_for_function(f"() => ({BACKGROUND})() !== {dark!r}")
    light = page.evaluate(BACKGROUND)
    saved(page)
    assert wait_for(lambda: ui_engine.settings.theme == "light")
    page.reload()
    expect(html).to_have_attribute("data-theme", "light")  # the server writes it into the page
    page.emulate_media(color_scheme="dark")
    section(page, "Appearance").get_by_role("group", name="Theme").get_by_role("radio", name="System").check()
    expect(html).to_have_attribute("data-theme", "system")
    page.wait_for_function(f"() => ({BACKGROUND})() === {dark!r}")
    page.emulate_media(color_scheme="light")
    page.wait_for_function(f"() => ({BACKGROUND})() === {light!r}")
    assert wait_for(lambda: ui_engine.settings.theme == "system")


def test_doctor_lists_what_needs_fixing_first(page, server, monkeypatch):
    checks = [
        Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
        Check(
            "speech model", False, "Parakeet is not downloaded yet", "It downloads by itself.", required=False
        ),
        Check("yt-dlp", True, "2026.10.10"),
        Check("claude", False, "not found", CLAUDE_FIX),
    ]
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(checks))
    open_settings(page, server)
    rows = section(page, "Doctor").get_by_role("listitem")
    expect(rows).to_have_count(4)
    firsts = [text.split("\n")[0] for text in rows.all_inner_texts()]
    assert firsts == ["Claude Code", "Speech model", "ffmpeg", "yt-dlp"]


def test_doctor_shows_checks_and_update_result(page, ui_engine, server, monkeypatch):
    checks = [
        Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
        Check("yt-dlp", True, "2026.10.10"),
        Check("claude", False, "not found", CLAUDE_FIX),
        Check(
            "speech model", False, "Parakeet is not downloaded yet", "It downloads by itself.", required=False
        ),
    ]
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(checks))
    results = [UPDATED, NOT_UPDATED]
    server.app.state.update_ytdlp = lambda: results.pop(0)
    open_settings(page, server)
    doctor = section(page, "Doctor")
    rows = doctor.get_by_role("listitem")
    expect(rows).to_have_count(4)
    claude = rows.filter(has_text="Claude Code")
    expect(claude).to_contain_text("not found")
    expect(claude).to_contain_text(CLAUDE_FIX)
    expect(claude).to_contain_text("Needs fixing")
    expect(rows.filter(has_text="Speech model")).to_contain_text("Optional")
    expect(rows.filter(has_text="ffmpeg")).to_contain_text("OK")

    checks[2] = Check("claude", True, "2.1.0 (Claude Code) at /usr/local/bin/claude")
    doctor.get_by_role("button", name="Run again").click()
    expect(claude).to_contain_text("2.1.0 (Claude Code)")
    expect(claude).not_to_contain_text("Needs fixing")

    ytdlp = rows.filter(has_text="yt-dlp")
    ytdlp.get_by_role("button", name="Update yt-dlp").click()
    expect(ytdlp).to_contain_text(UPDATED.message)
    ytdlp.get_by_role("button", name="Update yt-dlp").click()
    expect(ytdlp).to_contain_text(NOT_UPDATED.message)
    assert results == []  # each press ran the fake once


def test_storage_shows_usage_and_cleans_up(page, ui_engine, server):
    settled(ui_engine)
    open_settings(page, server)
    storage = section(page, "Storage")
    for kind in ("Source videos", "Work files", "Renders", "Library", "Logs"):
        expect(storage).to_contain_text(kind)
    expect(storage).to_contain_text("free of")
    days = storage.get_by_label("Delete source videos after")
    expect(days).to_have_value("14")
    storage.get_by_role("button", name="Clean up now").click()
    ask = page.get_by_role("dialog", name="Clean up now?")
    expect(ask).to_contain_text("2 KB")  # FREED, named before anything goes
    assert FREED == 2000
    ask.get_by_role("button", name="Clean up", exact=True).click()
    expect(toasts(page)).to_contain_text("Freed 2 KB")
    expect(storage.get_by_role("button", name="Clean up now")).to_be_disabled()
    expect(storage).to_contain_text(NOTHING_TO_CLEAN)
    days.fill("30")
    days.press("Enter")
    saved(page)
    assert wait_for(lambda: ui_engine.settings.delete_sources_after_days == 30)
