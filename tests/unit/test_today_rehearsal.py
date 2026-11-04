"""The rehearsal (SPEC 14.10, phase 5c): a typical day and a heavy one, seeded as they happen (posts put up
and submitted on the days before, this morning's checks making their drafts) and built by the morning
through the real needs and list. A typical day fits the 15 minutes with nothing set aside, the most valuable
item first; a heavy one sets the cheapest items that aren't due aside and lowers tomorrow's drafts cap."""

from math import ceil

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.post import store
from clipforge.today import day, morning
from clipforge.today import store as today_store
from clipforge.today.day import BUDGET_S
from clipforge.today.models import Timing
from clipforge.watch import drafts
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, add_clip, at, custom, linked_clip, posted
from tests.watching import add_channel, add_check, watched

TODAY = "2026-11-03"


@pytest.fixture
def clock():
    return Clock(at(8, 0, day=1))  # two days before, in the first posting window


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock, publishers={"tiktok:browser": FakePublisher()})
    yield eng
    eng.close()


def live(engine, campaign, rank: int, views: int | None = None):
    """A post of a clip of ``campaign`` live now on an account of its own, with ``views`` counted by hand."""
    p = posted(engine, linked_clip(engine, campaign, rank), add_account(engine, "tiktok"))
    if views is not None:
        tracker.add_views(engine, p.id, views)
    return p


def move(engine, post, *statuses: str) -> None:
    sub = campaigns.submission_of_post(engine.db(), post.id)
    for status in statuses:
        tracker.move_submission(engine, sub.id, status)


def drafted(engine, name: str, n: int, campaign=None):
    """A clipping channel whose TikTok account takes ``n`` a day, this morning's check making a draft of
    each of its ``n`` clips, the video linked to ``campaign``."""
    channel = add_channel(engine, [add_account(engine, "tiktok", name.lower(), daily_cap=n)], name)
    pid, _ = watched(engine, channel, add_check(engine, channel), [90 - k for k in range(n)])
    drafts.settle(engine, pid)
    if campaign is not None:
        repo.update_project(engine.db(), pid, campaign_id=campaign.id, rights_basis="clipping_programme")
    return channel


def stuck(engine) -> None:
    """A post with no campaign that needs you."""
    p = custom(engine, add_clip(engine, 99), add_account(engine), at(19))
    store.update_post(engine.db(), p.id, status="needs_you")


def counts(items) -> list[tuple[str, str, int]]:
    """Each item's kind, ref and how many drafts or posts it holds, in the list's order."""
    return [(i.kind, i.ref, len(i.ids)) for i in items]


def sent(engine) -> list[str]:
    return [arg for call in engine.run_cmd.calls for arg in call if arg.startswith("Your 15 minutes")]


def test_a_typical_day_fits_15_minutes(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0)
    drama = add_campaign(engine, name="Drama", marketplace="Vyro", rate_per_1000=3.0)
    counted = [live(engine, beast, n) for n in (1, 2, 3)]  # two days ago: their 2-day counts are due today
    clock.set(at(9, 0, day=1))
    move(engine, counted[0], "submitted")
    move(engine, counted[1], "submitted")  # a day on, Whop should have decided
    clock.set(at(8, 0, day=2))
    for n in (4, 5):
        move(engine, live(engine, beast, n, views=10_000), "submitted", "approved")  # US$40 to withdraw
    sending = [live(engine, drama, n) for n in (6, 7, 8, 9)]
    clock.set(at(12, 0, day=2))
    move(engine, counted[2], "submitted")  # under a day ago, so not yet to decide
    clock.set(at(7, 0, day=3))
    podcasts = drafted(engine, "Podcasts", 4, beast)
    gaming = drafted(engine, "Gaming", 4)
    stuck(engine)
    clock.set(at(7, 30))

    built = morning.build(engine, TODAY)
    items = day.refresh(engine)
    assert counts(items) == [  # by what each could earn, then the quicker first
        ("payout", "Whop:USD", 1),  # US$40 approved
        ("submit", drama.id, len(sending)),  # a guessed US$3 each
        ("drafts", podcasts.id, 4),  # a guessed US$2 each
        ("views", "tiktok", 3),
        ("decide", "whop", 2),
        ("needs_you", "all", 1),  # no campaign, so nothing, and quicker than the Gaming drafts
        ("drafts", gaming.id, 4),
    ]
    assert built.planned_s == 30 + 60 + 80 + 30 + 20 + 60 + 80 <= BUDGET_S  # the default estimates
    assert not any(i.optional for i in items)
    assert day.left_s(items) == built.planned_s
    assert sent(engine) == [
        "Your 15 minutes is ready: 8 drafts, 4 to submit, 3 views, 2 decisions, 1 payout, 1 post needs you"
    ]
    assert today_store.get_day(engine.db(), "2026-11-04") is None  # no cap set, and none to set


def test_a_heavy_day_marks_optional_and_lowers_the_cap(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0)
    small = add_campaign(engine, name="Small", rate_per_1000=0.1)
    drama = add_campaign(engine, name="Drama", marketplace="Vyro", rate_per_1000=3.0)
    for n, finished in enumerate((at(7, 50, day=1), at(7, 45, day=2), at(7, 40, day=2))):
        units = 4 + n  # your own last three mornings: 45 s a draft
        today_store.add_timing(
            engine.db(),
            Timing(
                id=repo.new_id("tm"),
                kind="drafts",
                started_at=finished.timestamp() - 45 * units,
                finished_at=finished.timestamp(),
                seconds=45.0 * units,
                units=units,
            ),
        )
    clock.set(at(8, 0, day=2))
    for n in range(10):
        live(engine, drama, n + 1)
    clock.set(at(7, 0, day=3))
    podcasts = drafted(engine, "Podcasts", 10, beast)
    gaming = drafted(engine, "Gaming", 10, small)
    stuck(engine)
    clock.set(at(7, 30))

    built = morning.build(engine, TODAY)
    items = day.refresh(engine)
    assert counts(items) == [
        ("submit", drama.id, 10),
        ("drafts", podcasts.id, 10),
        ("drafts", gaming.id, 10),  # US$1 in all
        ("needs_you", "all", 1),
    ]
    planned = 10 * 15 + 10 * 45 + 10 * 45 + 60
    assert built.planned_s == planned > BUDGET_S
    optional = {(i.kind, i.ref) for i in items if i.optional}
    assert optional == {("drafts", gaming.id)}  # the cheapest that isn't due; the post that needs you is due
    assert day.left_s(items) == planned - 450 <= BUDGET_S
    over = ceil((planned - BUDGET_S) / 45)
    assert today_store.get_day(engine.db(), "2026-11-04").drafts_cap == 20 - over == 15
    assert sent(engine) == ["Your 15 minutes is ready: 10 drafts, 10 to submit, 1 post needs you"]
