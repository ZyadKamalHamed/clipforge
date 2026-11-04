"""Settings' Brief sources and Niches (SPEC 10.11, phase 5d): each source with its switch, how it is read, its
terms note and its last result, Check now only for a source ClipForge reads, and Add a site, which takes a
site only once you've ticked that its terms allow reading it; and the niches, one a line, as the posting
windows are written. No Check now is pressed, so nothing is read and nothing leaves 127.0.0.1."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from clipforge.briefs import store
from clipforge.server.routes.briefs import TERMS, TERMS_NOTE
from tests.ui.test_library_settings import open_settings, section
from tests.ui.test_shell import toasts
from tests.unit.test_api_projects import wait_for

LIST = "https://clipmarket.example/campaigns"
TICK = "I've read this site's terms and they allow automated reading"
NICHE_LINE = "Write each niche like Gaming (en): gaming, games, one a line."


def test_brief_sources_settings(page, server, ui_engine, console):
    console.allow(r"status of 422")  # the site refused without the tick, which Chrome logs
    open_settings(page, server)
    sources = section(page, "Brief sources")
    seeded = store.sources(ui_engine.db())
    assert len(seeded) == 3
    for source in seeded:
        row = sources.get_by_role("article", name=source.name, exact=True)
        expect(row).to_contain_text(source.terms_note)
        expect(row.get_by_role("switch", name=f"Use {source.name}")).to_be_checked()
    scout = sources.get_by_role("article", name="Campaign Scout (HeyClicky)", exact=True)
    expect(scout).to_contain_text("Read from Campaign Scout's file on this Mac")
    expect(scout).to_contain_text("Not read yet.")
    expect(scout.get_by_role("button", name="Check now")).to_be_enabled()
    vyro = sources.get_by_role("article", name="Vyro", exact=True)
    expect(vyro).to_contain_text("Paste only")
    expect(vyro.get_by_role("button", name="Check now")).to_have_count(0)  # its terms forbid reading it

    form = sources.get_by_role("form", name="Add a site")
    form.get_by_label("Name", exact=True).fill("Clip Market")
    form.get_by_label("Public list page").fill(LIST)
    form.get_by_role("button", name="Add the site").click()
    tick = form.get_by_role("checkbox", name=TICK)
    expect(tick).to_have_accessible_description(re.compile(re.escape(TERMS)))
    assert len(store.sources(ui_engine.db())) == 3
    tick.check()
    form.get_by_role("button", name="Add the site").click()
    row = sources.get_by_role("article", name="Clip Market", exact=True)
    expect(row).to_contain_text(TERMS_NOTE)
    expect(row).to_contain_text(LIST)
    expect(row.get_by_role("button", name="Check now")).to_be_enabled()
    expect(form.get_by_text(TERMS)).to_have_count(0)
    expect(form.get_by_label("Name", exact=True)).to_have_value("")  # ready for the next site
    expect(tick).not_to_be_checked()
    (added,) = [s for s in store.sources(ui_engine.db()) if s.name == "Clip Market"]
    assert (added.reader, added.enabled, added.list_url) == ("general", True, LIST)

    form.get_by_label("Name", exact=True).fill("Clip Market again")
    form.get_by_label("Public list page").fill(LIST + "/more")
    tick.check()
    form.get_by_role("button", name="Add the site").click()
    link = form.get_by_label("Public list page")
    expect(link).to_have_accessible_description(re.compile("Clip Market already reads that site."))

    row.get_by_role("switch", name="Use Clip Market").uncheck()
    expect(row.get_by_role("switch", name="Use Clip Market")).not_to_be_checked()
    assert wait_for(lambda: store.get_source(ui_engine.db(), added.id).enabled is False)
    page.reload()
    expect(section(page, "Brief sources").get_by_role("switch", name="Use Clip Market")).not_to_be_checked()


def test_niches_save(page, server, ui_engine):
    open_settings(page, server)
    niches = section(page, "Niches").get_by_label("Niches and their tags")
    expect(niches).to_have_value(re.compile(r"^Reality TV and drama \(en\): reality, drama, reality tv"))
    expect(niches).to_have_value(re.compile(r"\nGaming \(en\): gaming, games, gamer, fortnite"))
    lines = niches.input_value().split("\n")
    assert len(lines) == len(ui_engine.settings.briefs.niches)

    niches.fill("\n".join([*lines, "Anime: Anime, manga"]))
    niches.blur()
    expect(toasts(page)).to_contain_text("Saved")
    assert wait_for(lambda: ui_engine.settings.briefs.niches[-1].name == "Anime")
    anime = ui_engine.settings.briefs.niches[-1]
    assert (anime.language, anime.tags) == ("en", ["anime", "manga"])
    expect(niches).to_have_value(re.compile(r"\nAnime \(en\): anime, manga$"))  # tidied as it saved

    page.reload()
    niches = section(page, "Niches").get_by_label("Niches and their tags")
    expect(niches).to_have_value(re.compile(r"\nAnime \(en\): anime, manga$"))

    niches.fill(niches.input_value() + "\nno tags here")
    niches.blur()
    expect(section(page, "Niches").get_by_text(NICHE_LINE)).to_be_visible()
    assert len(ui_engine.settings.briefs.niches) == len(lines) + 1  # nothing was sent
