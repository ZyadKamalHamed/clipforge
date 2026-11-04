"""Style kits (SPEC 11): an account's own look, laid over the clip's for that account's posts."""

from typing import get_args

import pytest

from clipforge.assets import FONTS
from clipforge.config import Settings
from clipforge.post import store
from clipforge.render import captions
from clipforge.render.captions import caption_sprite, line_for, shown_words
from clipforge.render.styles import FontName, Style, StyleKit, caption_font, style_for, style_key
from clipforge.server.routes.accounts import COLOUR_WRONG, CREDIT_NO_CHANNEL, CREDIT_TOO_LONG, FONT_WRONG
from tests.fakes import fake_engine
from tests.posting import add_account
from tests.server import api_client
from tests.unit.test_captions import page_of


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def test_kit_layers_over_the_clip():
    settings, edits = Settings(caption_preset="bold_pop"), {"preset": "hormozi"}
    style = style_for(settings, edits, StyleKit(preset="neon", accent="#00E5FF"))
    assert (style.preset, style.accent) == ("neon", "#00E5FF")
    assert (
        style_for(settings, edits, StyleKit())
        == style_for(settings, edits)
        == style_for(settings, edits, None)
    )
    assert StyleKit().empty and not StyleKit(gameplay_mode="off").empty
    # A kit's set field wins over the clip's edit; one it leaves out keeps the clip's.
    clip = {"preset": "hormozi", "accent": "#FF0000", "gameplay_mode": "always"}
    worn = style_for(settings, clip, StyleKit(gameplay_mode="off", credit_format="Via {channel}"))
    assert (worn.preset, worn.accent, worn.gameplay_mode, worn.credit_format) == (
        "hormozi",
        "#FF0000",
        "off",
        "Via {channel}",
    )


def test_kit_font_reaches_the_captions(monkeypatch):
    assert set(get_args(FontName)) == set(FONTS)
    style = style_for(Settings(), {}, StyleKit(font="anton"))
    assert caption_font(style) == "anton"
    assert caption_font(Style(preset="beast")) == "bangers"  # without a kit font, the preset's own
    page = page_of(["go", "go", "go"])
    assert line_for(shown_words(page, style.preset), style.preset, caption_font(style)).font == "anton"
    used = []
    real = captions.font

    def spy(name, size):
        f = real(name, size)
        used.append(f.path)
        return f

    monkeypatch.setattr(captions, "font", spy)
    line_for.cache_clear()
    captions._draw.cache_clear()
    sprite = caption_sprite(page, 0.45, style)
    assert sprite.rgba.shape[0] > 0 and used
    assert {str(p).rsplit("/", 1)[-1] for p in used} == {"Anton-Regular.ttf"}
    # A look without a font of its own keeps the key its renders had before kits.
    assert style_key(Style()) == style_key(style_for(Settings(), {}, StyleKit())) == "93aa65101e"
    assert style_key(style) != style_key(Style())


def test_kit_validation(engine):
    client = api_client(engine)
    account = add_account(engine)
    url = f"/api/accounts/{account.id}"
    for kit, field, sentence in [
        ({"credit_format": "Clip by me"}, "credit_format", CREDIT_NO_CHANNEL),
        ({"credit_format": "{channel} " + "x" * 60}, "credit_format", CREDIT_TOO_LONG),
        ({"accent": "red"}, "accent", COLOUR_WRONG),
        ({"hook_fill": "#12345"}, "hook_fill", COLOUR_WRONG),
        ({"font": "comic-sans"}, "font", FONT_WRONG),
    ]:
        refused = client.patch(url, json={"settings": {"style_kit": kit}})
        assert refused.status_code == 422, kit
        assert refused.json()["field"] == f"settings.style_kit.{field}", kit
        assert refused.json()["message"] == sentence, kit
    assert store.get_account(engine.db(), account.id).settings.style_kit.empty

    kit = {
        "preset": "neon",
        "font": "anton",
        "accent": "#00e5ff",
        "credit_format": "  Clip:\u0007 {channel} ",
    }
    saved = client.patch(url, json={"settings": {"style_kit": kit}})
    assert saved.status_code == 200, saved.text
    shown = saved.json()["account"]["settings"]["style_kit"]
    assert (shown["preset"], shown["font"], shown["accent"], shown["credit_format"]) == (
        "neon",
        "anton",
        "#00E5FF",
        "Clip: {channel}",
    )
    assert shown["hook_fill"] is None and shown["gameplay_mode"] is None
    stored = store.get_account(engine.db(), account.id).settings.style_kit
    assert stored == StyleKit(preset="neon", font="anton", accent="#00E5FF", credit_format="Clip: {channel}")
    # Another setting's change keeps the kit; a null kit goes back to the clip's own look.
    assert client.patch(url, json={"settings": {"daily_cap": 3}}).status_code == 200
    assert store.get_account(engine.db(), account.id).settings.style_kit == stored
    assert client.patch(url, json={"settings": {"style_kit": None}}).status_code == 200
    assert store.get_account(engine.db(), account.id).settings.style_kit.empty
    assert client.patch(url, json={"settings": {"style_kit": {"sparkle": True}}}).status_code == 422
