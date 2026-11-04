"""Trial reels (SPEC 10.2): an Instagram Reel shown at first only to people who don't follow the account,
through Meta's API in either mode. ``trial_params`` names how it graduates, and a trial post sends no
``share_to_feed``. Off the API (Instagram through Chrome, or TikTok) the plan refuses one."""

import json
from datetime import timedelta

import pytest

from clipforge.post.models import PlanRequest, PostEdit, PostOptions
from clipforge.post.plan import TRIAL_API_ONLY, build_plan
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import FakeGraph
from tests.posting import add_account, add_clip, confirm, run
from tests.unit.test_instagram_api import NOW, ig_account
from tests.unit.test_public_media import BASE, mode_b, through_app, with_base


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def engine(clock, graph):
    eng = fake_engine(now=clock)
    eng.graph_transport = graph.transport()
    eng.public_transport = through_app(eng, [])
    with_base(eng, BASE)
    yield eng
    eng.close()


def posted(engine, clock, graph, mode: str, options: PostOptions) -> dict[str, str]:
    """The container form Meta got for a fresh clip posted now with ``options`` to a new account linked in
    ``mode``, 20 minutes after the last one (a new account and 20 minutes apart, so no cap or gap gets in
    the way)."""
    clock.set(clock() + timedelta(minutes=20))
    account = ig_account(engine) if mode == "a" else mode_b(engine)
    clip = add_clip(engine)
    key = f"{clip.id}:{account.id}"
    _, (p,) = confirm(
        engine,
        [clip],
        [account],
        timing="custom",
        custom_times={key: clock().timestamp()},
        edits={key: PostEdit(options=options)},
    )
    _, after = run(engine, p.id)
    assert after.status == "posted"
    return graph.form(graph.of("container")[-1])


@pytest.mark.parametrize("mode", ["a", "b"])
def test_trial_params_in_both_modes(engine, clock, graph, mode):
    trial = posted(engine, clock, graph, mode, PostOptions(trial_reel=True))
    assert trial["trial_params"] == json.dumps({"graduation_strategy": "MANUAL"})
    assert "share_to_feed" not in trial
    auto = posted(engine, clock, graph, mode, PostOptions(trial_reel=True, trial_graduation="auto"))
    assert json.loads(auto["trial_params"]) == {"graduation_strategy": "SS_PERFORMANCE"}
    plain = posted(engine, clock, graph, mode, PostOptions())
    assert "trial_params" not in plain and plain["share_to_feed"] == "true"


def test_trial_reel_refused_off_the_api(engine):
    assert TRIAL_API_ONLY == "Trial reels go up only through Meta's API, so this account can't post one."
    clip = add_clip(engine)
    chrome = add_account(engine, "instagram", method="browser")
    tiktok = add_account(engine, "tiktok")
    api = ig_account(engine)
    edits = {
        f"{clip.id}:{a.id}": PostEdit(options=PostOptions(trial_reel=True)) for a in (chrome, tiktok, api)
    }
    request = PlanRequest(clip_ids=[clip.id], account_ids=[chrome.id, tiktok.id, api.id], edits=edits)
    plan = build_plan(engine, request, plan_id="plan1", now=NOW)
    refused = {w.key for w in plan.warnings if w.code == "trial_route"}
    assert refused == {f"{clip.id}:{chrome.id}", f"{clip.id}:{tiktok.id}"}
    assert all(w.blocking and w.sentence == TRIAL_API_ONLY for w in plan.warnings if w.code == "trial_route")
    assert plan.blocked
