import httpx
import pytest

from clipforge.config import PostingSettings, SettingError, Settings, Window, changed


def test_posting_settings_defaults_match_call_8():
    p = Settings().posting
    assert p.paused is False
    assert p.timezone == "Australia/Sydney"
    assert [(w.start, w.end) for w in p.windows] == [
        ("07:00", "09:00"),
        ("12:00", "13:00"),
        ("18:00", "22:00"),
    ]
    assert (p.tiktok_daily_cap, p.instagram_daily_cap) == (2, 1)
    assert (p.tiktok_spacing_h, p.instagram_spacing_h) == (3.0, 6.0)
    assert (p.platform_gap_min, p.jitter_min, p.away_min) == (15, 10, 10)
    assert p.graph_version == "v25.0"
    assert p.public_base_url is None
    assert (p.tiktok_schedule, p.telegram_alerts, p.telegram_target) == (True, False, None)


@pytest.mark.parametrize(
    "change, field",
    [
        ({"windows": [{"start": "09:00", "end": "08:00"}]}, "posting.windows"),
        ({"windows": [{"start": "7am", "end": "08:00"}]}, "posting.windows"),
        ({"public_base_url": "http://zyad.example.ts.net"}, "posting.public_base_url"),
        ({"graph_version": "v25"}, "posting.graph_version"),
        ({"tiktok_daily_cap": 11}, "posting.tiktok_daily_cap"),
        ({"instagram_daily_cap": 0}, "posting.instagram_daily_cap"),
        ({"nonsense": 1}, "posting.nonsense"),
    ],
)
def test_posting_settings_validate(change, field):
    current = Settings()
    with pytest.raises(SettingError) as err:
        changed(current, {"posting": current.posting.model_dump() | change})
    assert err.value.field == field
    assert err.value.message.endswith(".")
    assert "\n" not in err.value.message


def test_posting_settings_accept_good_values():
    current = Settings()
    new = changed(
        current,
        {
            "posting": current.posting.model_dump()
            | {"public_base_url": "https://mac.tail.ts.net", "windows": []}
        },
    )
    assert new.posting.public_base_url == "https://mac.tail.ts.net"
    assert isinstance(PostingSettings().windows[0], Window)


def test_guard_blocks_platform_hosts():
    for url in ("https://graph.facebook.com/v25.0/me", "https://www.tiktok.com/", "https://example.com/"):
        with pytest.raises(AssertionError, match="left this Mac"):
            httpx.get(url)
