import pytest

from clipforge import db, repo
from clipforge.config import DEFAULT_MODELS, Settings, changed, model_for
from clipforge.errors import ClipForgeError, LLMError, NetworkError, SourceRefused, WaitUntil


def test_settings_defaults():
    s = Settings()
    assert s.llm_provider == "claude_cli"
    assert s.llm_parallel == 3
    assert s.llm_timeout_s == 180.0
    assert s.asr_engine == "auto"
    assert s.download_quality == "best"
    assert s.full_download_max_gb == 2.5
    assert s.comments is True
    assert s.auto_render == "top10"
    assert s.allow_other_sites is False
    assert s.min_free_gb == 10.0
    assert s.own_channels == []
    assert s.max_source_hours == 6.0
    assert s.on_llm_limit == "wait"
    assert s.live_chat is True


def test_trend_settings_default_on(tmp_path):
    assert Settings().trends.list_ping is True
    old = Settings().model_dump(mode="json")
    del old["trends"]  # a settings file from before 5f
    assert Settings.model_validate(old).trends.list_ping is True
    conn = db.connect(tmp_path / "settings.db")
    db.migrate(conn)
    conn.execute("INSERT INTO settings (key, value_json) VALUES ('comments', 'false')")
    loaded = repo.load_settings(conn)
    assert (loaded.comments, loaded.trends.list_ping) == (False, True)
    assert changed(Settings(), {"trends": {"list_ping": False}}).trends.list_ping is False


def test_model_for_uses_pass_entry_then_star():
    s = Settings()
    assert model_for(s, "claude_cli", "discover") == "opus"
    assert model_for(s, "claude_cli", "package") == "sonnet"
    assert model_for(s, "anthropic", "rerank") == "claude-opus-5-5"
    assert model_for(s, "gemini", "discover") == DEFAULT_MODELS["gemini"]["*"]
    assert model_for(s, "openai_compat", "review") == "qwen3:14b"


def test_model_for_economy_and_overrides():
    economy = Settings(llm_economy=True)
    assert model_for(economy, "claude_cli", "discover") == "sonnet"
    assert model_for(economy, "anthropic", "rerank") == "claude-sonnet-5-5"
    assert model_for(economy, "gemini", "discover") == "gemini-flash-latest"
    custom = Settings(llm_models={"claude_cli": {"discover": "haiku", "*": "sonnet"}})
    assert model_for(custom, "claude_cli", "discover") == "haiku"
    assert model_for(custom, "claude_cli", "rerank") == "sonnet"
    assert model_for(custom, "anthropic", "discover") == "claude-opus-5-5"
    assert model_for(Settings(), "heuristic", "discover") == ""


def test_errors_carry_plain_messages():
    err = ClipForgeError("ytdlp_failed", "YouTube download failed.", details="HTTP 403")
    assert str(err) == "YouTube download failed."
    assert err.code == "ytdlp_failed" and err.details == "HTTP 403" and err.retryable is False
    assert NetworkError("net", "No internet connection.").retryable is True
    assert isinstance(SourceRefused("too_long", "Too long."), ClipForgeError)


def test_llm_error_kinds_have_default_messages():
    err = LLMError(kind="usage_limit", retry_at=123.0)
    assert err.kind == "usage_limit" and err.code == "llm_usage_limit" and err.retry_at == 123.0
    assert str(err) == "Claude's usage limit is reached."
    assert str(LLMError(kind="auth")).startswith("Claude Code isn't logged in.")
    assert str(LLMError(kind="timeout")) == "Claude took too long to answer."
    assert str(LLMError(kind="bad_output", message="Bad JSON.")) == "Bad JSON."


def test_wait_until_holds_time_and_message():
    err = WaitUntil(until=1000.0, message="Waiting for Claude.")
    assert err.until == 1000.0 and str(err) == "Waiting for Claude." and err.code == "waiting"


@pytest.mark.parametrize(
    "change, field",
    [
        ({"min_minutes": 0.4}, "watch.min_minutes"),
        ({"min_minutes": 61}, "watch.min_minutes"),
        ({"max_hours": 0.4}, "watch.max_hours"),
        ({"max_hours": 13}, "watch.max_hours"),
        ({"nonsense": 1}, "watch.nonsense"),
    ],
)
def test_watch_settings_bounds(change, field):
    from clipforge.config import SettingError, changed

    current = Settings()
    assert (current.watch.min_minutes, current.watch.max_hours) == (4, 4)
    with pytest.raises(SettingError) as err:
        changed(current, {"watch": current.watch.model_dump() | change})
    assert err.value.field == field
    assert err.value.message.endswith(".") and "\n" not in err.value.message
    ok = changed(current, {"watch": {"min_minutes": 2, "max_hours": 6}})
    assert (ok.watch.min_minutes, ok.watch.max_hours) == (2, 6)


@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        ({"Whop": {"withdraw_url": "ftp://whop.com/payouts"}}, "Withdraw link"),
        ({"Whop": {"min_payout": -5}}, "minimum payout"),
        ({"": {"min_payout": 5}}, "name"),
        ({"W" * 41: {"min_payout": 5}}, "name"),
        ({f"M{n}": {} for n in range(21)}, "20 marketplaces"),
    ],
)
def test_marketplace_settings_bounds(change, sentence):
    from clipforge.config import SettingError, changed

    current = Settings()
    assert current.marketplaces == {}
    with pytest.raises(SettingError) as err:
        changed(current, {"marketplaces": change})
    assert err.value.field.startswith("marketplaces")
    assert sentence in err.value.message
    assert err.value.message.endswith(".") and "\n" not in err.value.message
    ok = changed(
        current, {"marketplaces": {"Whop": {"withdraw_url": "https://whop.com/payouts", "min_payout": 50}}}
    )
    assert ok.marketplaces["Whop"].withdraw_url == "https://whop.com/payouts"
    assert ok.marketplaces["Whop"].min_payout == 50


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"notify_at": "7:30"}, "today.notify_at"),
        ({"notify_at": "24:00"}, "today.notify_at"),
        ({"starting_views": 0}, "today.starting_views"),
        ({"starting_views": 10_000_001}, "today.starting_views"),
        ({"rates": {"usd": 1.0}}, "today.rates"),
        ({"rates": {"AUD": 0}}, "today.rates"),
        ({"rates": {"AUD": True}}, "today.rates"),
        ({"rates": {f"A{chr(65 + n // 26)}{chr(65 + n % 26)}": 1.0 for n in range(21)}}, "today.rates"),
        ({"nonsense": 1}, "today.nonsense"),
    ],
)
def test_today_settings_bounds(change, field):
    from clipforge.config import SettingError, changed

    current = Settings()
    t = current.today
    assert (t.notify_at, t.daily_notification, t.event_notifications, t.starting_views, t.rates) == (
        "07:30",
        True,
        False,
        1000,
        {},
    )
    with pytest.raises(SettingError) as err:
        changed(current, {"today": t.model_dump() | change})
    assert err.value.field == field
    assert err.value.message.endswith(".") and "\n" not in err.value.message
    ok = changed(current, {"today": {"notify_at": "06:45", "starting_views": 2500, "rates": {"AUD": 0.65}}})
    assert (ok.today.notify_at, ok.today.starting_views, ok.today.rates) == ("06:45", 2500, {"AUD": 0.65})


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"read_at": "6:00"}, "briefs.read_at"),
        ({"read_per_morning": 1001}, "briefs.read_per_morning"),
        ({"read_per_morning": -1}, "briefs.read_per_morning"),
        ({"niches": [{"name": f"Niche {n}", "tags": ["x"]} for n in range(31)]}, "briefs.niches"),
        ({"niches": [{"name": "Gaming", "tags": ["a"]}, {"name": "gaming", "tags": ["b"]}]}, "briefs.niches"),
        ({"niches": [{"name": "Gaming", "tags": []}]}, "briefs.niches"),
        ({"niches": [{"name": "", "tags": ["a"]}]}, "briefs.niches"),
        ({"niches": [{"name": "Gaming", "language": "EN!", "tags": ["a"]}]}, "briefs.niches"),
        ({"nonsense": 1}, "briefs.nonsense"),
    ],
)
def test_brief_settings_bounds(change, field):
    from clipforge.config import SettingError, changed

    current = Settings()
    b = current.briefs
    assert (b.read_at, b.read_per_morning) == ("06:00", 200)
    assert [n.name for n in b.niches] == [
        "Reality TV and drama",
        "Gaming",
        "Streamers",
        "Podcasts and interviews",
        "Sports",
        "Comedy and entertainment",
        "Music",
        "Finance and business",
        "Tech",
        "Lifestyle and fitness",
        "Education",
        "News and politics",
    ]
    assert all(n.language == "en" and n.tags and n.tags == [t.lower() for t in n.tags] for n in b.niches)
    assert "fortnite" in b.niches[1].tags and "twitch" in b.niches[2].tags
    with pytest.raises(SettingError) as err:
        changed(current, {"briefs": b.model_dump() | change})
    assert err.value.field == field
    assert err.value.message.endswith(".") and "\n" not in err.value.message
    ok = changed(
        current,
        {
            "briefs": {
                "read_at": "05:30",
                "niches": [{"name": " Cooking ", "tags": ["Food", "food", " recipes "]}],
            }
        },
    )
    assert ok.briefs.read_at == "05:30"
    assert [(n.name, n.language, n.tags) for n in ok.briefs.niches] == [
        ("Cooking", "en", ["food", "recipes"])
    ]
