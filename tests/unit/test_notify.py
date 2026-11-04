"""Notifications on this Mac (SPEC 18, 21): the helper inside ClipForge.app when it was built, else osascript,
the untrusted text only ever an argument and the click only ever opening ClipForge's own pages; Telegram
alerts when they are turned on. Events go only once the Today switch for them is on (it is off by default);
the daily notification goes on its own switch. No command really runs: ``run`` records them."""

import pytest

from clipforge.config import Settings
from clipforge.notify import HELPER, Notifier, notify_for
from clipforge.post.errors import Fatal, NeedsUser
from clipforge.post.models import PublishResult
from clipforge.watch import drafts
from tests.fakes import Clock, FakeCommands, FakePublisher, fake_engine
from tests.posting import PACK, add_account, add_clip, at, custom, run
from tests.watching import add_channel, add_check, watched


def settings(*, events=True, daily=True, **posting):
    s = Settings()
    today = s.today.model_copy(update={"event_notifications": events, "daily_notification": daily})
    return s.model_copy(update={"posting": s.posting.model_copy(update=posting), "today": today})


def notifier(tmp_path, *, helper=True, events=True, daily=True, **posting):
    apps = tmp_path / "Applications"
    if helper:
        (apps / "ClipForge.app" / "Contents" / "Helpers" / HELPER).mkdir(parents=True)
    cmds = FakeCommands()
    s = settings(events=events, daily=daily, **posting)
    return Notifier(tmp_path, s, port=8723, run=cmds, app_dir=apps), cmds


def test_notify_uses_the_helper_with_arguments(tmp_path):
    n, cmds = notifier(tmp_path)
    n.send("A post needs you", 'Say "hi"; do shell script "x"', "/queue")
    helper = tmp_path / "Applications" / "ClipForge.app" / "Contents" / "Helpers" / HELPER
    assert cmds.calls == [
        [
            "open",
            "-g",
            "-n",
            "-a",
            str(helper),
            "--args",
            "--title",
            "A post needs you",
            "--body",
            'Say "hi"; do shell script "x"',
            "--open",
            "http://127.0.0.1:8723/queue",
        ]
    ]


def test_notify_falls_back_to_osascript(tmp_path):
    n, cmds = notifier(tmp_path, helper=False)
    n.send("Posted", 'A "clip" is on @mine.', "/queue")
    assert cmds.calls == [
        [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            "Posted",
            'A "clip" is on @mine.',
        ]
    ]


@pytest.mark.parametrize(
    "path",
    ["//evil.example/x", "/queue?x=1", "/queue#top", "/a b", "queue", "/../etc", "/qé", "/queue\n", ""],
)
def test_notify_refuses_other_urls(tmp_path, path):
    n, cmds = notifier(tmp_path, telegram_alerts=True, telegram_target="123456789")
    n.send("Posted", "A clip is on @mine.", path)
    assert cmds.calls == []


def test_notify_cleans_and_shortens_text(tmp_path):
    n, cmds = notifier(tmp_path)
    n.send("Ready\x1b[31m\n" + "t" * 100, "line\x07one\r\n\u202e" + "b" * 300, "/projects/p1")
    (call,) = cmds.calls
    title, body = call[call.index("--title") + 1], call[call.index("--body") + 1]
    assert len(title) == 80 and len(body) == 200
    assert title.startswith("Ready [31m t") and body.startswith("line one b")
    assert not any(ord(ch) < 32 or ord(ch) == 127 or ch == "\u202e" for ch in title + body)
    assert title.endswith("t…") and body.endswith("b…")

    n.send("Posted", "  short  ", "/queue")
    assert cmds.calls[-1][-3] == "short"


def test_notify_events_off_sends_nothing(tmp_path):
    n, cmds = notifier(tmp_path, events=False, telegram_alerts=True, telegram_target="123456789")
    n.send("Posted", "A clip is on @mine.", "/queue")
    assert cmds.calls == []


def test_daily_goes_even_with_events_off(tmp_path):
    n, cmds = notifier(tmp_path, events=False)
    n.daily("Your 15 minutes is ready: 6 drafts, 4 to submit, 1 payout")
    helper = tmp_path / "Applications" / "ClipForge.app" / "Contents" / "Helpers" / HELPER
    assert cmds.calls == [
        [
            "open",
            "-g",
            "-n",
            "-a",
            str(helper),
            "--args",
            "--title",
            "ClipForge",
            "--body",
            "Your 15 minutes is ready: 6 drafts, 4 to submit, 1 payout",
            "--open",
            "http://127.0.0.1:8723/",
        ]
    ]
    n, cmds = notifier(tmp_path / "told", events=False, telegram_alerts=True, telegram_target="@my_chat")
    n.daily("Your 15 minutes is ready: 6 drafts")
    assert cmds.calls[1][-1] == "ClipForge: Your 15 minutes is ready: 6 drafts"  # Telegram carries it too


def test_daily_off_sends_nothing(tmp_path):
    n, cmds = notifier(tmp_path, daily=False, telegram_alerts=True, telegram_target="123456789")
    n.daily("Your 15 minutes is ready: 6 drafts")
    assert cmds.calls == []


def test_telegram_alerts_text_only_and_off_by_default(tmp_path):
    assert Settings().posting.telegram_alerts is False
    n, cmds = notifier(tmp_path / "off", helper=False, telegram_target="123456789")
    n.send("Posted", "A clip is on @mine.", "/queue")
    assert [c[0] for c in cmds.calls] == ["osascript"]

    n, cmds = notifier(tmp_path / "on", helper=False, telegram_alerts=True, telegram_target="@my_chat")
    n.send("Posted", "A clip\x1b is on @mine.", "/queue")
    assert cmds.calls[1] == [
        "openclaw",
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        "@my_chat",
        "--message",
        "Posted: A clip is on @mine.",
    ]
    assert not any("127.0.0.1" in arg for arg in cmds.calls[1])

    n, cmds = notifier(tmp_path / "nowhere", helper=False, telegram_alerts=True)  # no chat to send to
    n.send("Posted", "A clip is on @mine.", "/queue")
    assert [c[0] for c in cmds.calls] == ["osascript"]


def test_a_command_that_wont_start_is_let_go(tmp_path):
    def missing(args):
        raise FileNotFoundError(args[0])

    n = Notifier(
        tmp_path,
        settings(telegram_alerts=True, telegram_target="1"),
        port=8723,
        run=missing,
        app_dir=tmp_path,
    )
    n.send("Posted", "A clip is on @mine.", "/queue")  # neither osascript nor openclaw: nothing raised


# The four moments, from the events the engine publishes


@pytest.fixture
def engine(tmp_path):
    eng = fake_engine(
        now=Clock(at(7, 10)),
        publishers={"tiktok:browser": FakePublisher()},
        today={"event_notifications": True},
    )
    eng.notifier.app_dir = tmp_path / "Applications"  # no helper there: osascript, recorded
    yield eng
    eng.close()


def shown(engine):
    return [c[-2:] for c in engine.run_cmd.calls if c[0] == "osascript"]


def test_events_notify_ready_needs_you_failed_and_posted(engine):
    clip = add_clip(engine)
    engine.update_project(clip.project_id, status="ready")  # never seen before: an older project's change
    engine.update_project(clip.project_id, status="running")
    engine.update_project(clip.project_id, status="ready")
    engine.update_project(clip.project_id, status="ready")  # said once
    assert shown(engine) == [["Clips are ready", "1 clip from A talk."]]
    assert notify_for("project", {"id": clip.project_id, "status": "ready"}, engine) == (
        "Clips are ready",
        "1 clip from A talk.",
        f"/projects/{clip.project_id}",
    )

    engine.publishers["tiktok:browser"].outcomes = [
        PublishResult(external_id="1", url="https://www.tiktok.com/@mine/video/1"),
        NeedsUser("TikTok Studio wants you."),
        Fatal("TikTok said no.", "refused"),
    ]
    for handle, minute in (("mine", 10), ("two", 30), ("three", 50)):  # none waits on another's spacing
        account = add_account(engine, "tiktok", handle)
        p = custom(engine, add_clip(engine), account, at(7, minute))
        engine.now.set(at(7, minute))
        run(engine, p.id)
    assert shown(engine)[1:] == [
        ["Posted", f"{PACK['hook_title']} is on @mine."],
        ["A post needs you", "TikTok Studio wants you."],
        ["A post failed", "TikTok said no."],
    ]
    assert notify_for("post", {"id": "nothing", "status": "scheduled"}, engine) is None

    engine.update_settings(settings(events=False))
    engine.update_project(clip.project_id, status="running")
    engine.update_project(clip.project_id, status="ready")
    assert len(shown(engine)) == 4


def test_events_stay_quiet_by_default(tmp_path):
    """SPEC 18: only the daily notification is on until you turn the others on."""
    eng = fake_engine(now=Clock(at(7, 10)), publishers={"tiktok:browser": FakePublisher()})
    try:
        eng.notifier.app_dir = tmp_path / "Applications"
        assert eng.settings.today.event_notifications is False
        eng.publishers["tiktok:browser"].outcomes = [NeedsUser("TikTok Studio wants you.")]
        p = custom(eng, add_clip(eng), add_account(eng, "tiktok", "mine"), at(7, 10))
        _, after = run(eng, p.id)
        assert after.status == "needs_you"
        assert [c for c in eng.run_cmd.calls if c[0] in ("open", "osascript", "openclaw")] == []
    finally:
        eng.close()


def test_a_post_marked_posted_by_hand_tells_nothing(engine):
    clip = add_clip(engine)
    account = add_account(engine, "tiktok", "mine")
    engine.publishers["tiktok:browser"].outcomes = [NeedsUser("TikTok Studio wants you.")]
    p = custom(engine, clip, account, at(7, 10))
    run(engine, p.id)
    engine.posting.mark_posted(p.id, "https://www.tiktok.com/@mine/video/2")
    assert [t for t, _ in shown(engine)] == ["A post needs you"]  # marked by hand: nothing to tell


def test_a_post_tiktok_studio_put_up_tells_you_it_posted(engine):
    """TikTok Studio held it and put it up at its slot; ClipForge read its link, so it was sent, not marked
    by hand (SPEC 10.3)."""
    engine.now.set(at(9))
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "mine", assisted=True), at(18))
    tiktok = engine.publishers["tiktok:browser"]
    tiktok.outcomes = [PublishResult(external_id=None, url=None, scheduled=True)]
    run(engine, p.id)
    engine.now.set(at(18, 10))
    tiktok.links = [PublishResult(external_id="1", url="https://www.tiktok.com/@mine/video/1")]
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert shown(engine) == [["Posted", f"{PACK['hook_title']} is on @mine."]]


def test_drafted_notification(engine):
    ch = add_channel(
        engine, [add_account(engine, "tiktok", "mine"), add_account(engine, "instagram", "gram")]
    )
    check = add_check(engine, ch)
    pid, _ = watched(engine, ch, check, [90, 60], status="running")
    engine.update_project(pid, status="running")
    engine.update_project(pid, status="ready")  # a clipping channel's project: its drafts say it
    assert shown(engine) == []
    drafts.settle(engine, pid)
    assert shown(engine) == [["Drafts are ready", "3 drafts from Podcasts."]]  # TikTok's cap is 2
    drafts.settle(engine, pid)
    assert len(shown(engine)) == 1
    assert notify_for("watch", {"id": check.id, "status": "drafted", "drafts": 1, "name": "Gym"}, engine) == (
        "Drafts are ready",
        "1 draft from Gym.",
        "/",
    )
    assert (
        notify_for("watch", {"id": check.id, "status": "drafted", "drafts": 0, "name": "Gym"}, engine) is None
    )
