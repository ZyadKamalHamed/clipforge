"""SPEC 19's last row on a big library (Phase 6 Task 4): 11 projects, one of them with 40 clips, 12 accounts,
200 posts in the Queue, 300 campaigns and 300 briefs. Each page opens, then one control on it is pressed,
and the press must settle in under 100 ms by two clocks: Chrome's own, from the press to the next frame drawn
(Event Timing, the measure behind Chrome's INP), and the time until the page shows what the press asked for,
checked every frame. Then the 40 clip gallery scrolls from top to bottom with no task over 50 ms. A press
or scroll over its limit is timed again from a fresh page, up to three times, since other work on this Mac
can take a frame; one that misses every time fails. The numbers go to the test's folder, or to
CLIPFORGE_PERF when it is set. Nothing runs and nothing leaves 127.0.0.1: the engine's jobs never start
here."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import Page, TimeoutError, expect

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Metric, Submission
from clipforge.post import store
from clipforge.post.models import Post, PostOptions
from tests.briefing import add_brief
from tests.posting import DAY, add_account, add_campaign
from tests.ui.seed import seed_project
from tests.ui.test_shell import opened

pytestmark = pytest.mark.slow

LIMIT_MS = 100.0  # SPEC 19: UI interactions under 100 ms
LONG_MS = 50.0  # a task this long or longer drops frames; Chrome reports nothing shorter as a long task
TRIES = 3  # other work on this Mac can take a frame, so a press over the limit is timed again, fresh
PROJECTS = 11
CLIPS = 40
DISCARDED = (12, 27)
RULES = "Clips run 30 to 60 s. Credit the channel in the caption."
MARKETPLACES = ["Whop", "Vyro", "Content Rewards"]
NICHES = ["Gaming", "Podcasts", "Streamers"]
CAMPAIGNS = 300
BRIEFS = 300
# 200 posts: most still to go, a third gone up over the last four months, the rest needing you or let go.
POSTS = [("scheduled", 120), ("posted", 60), ("failed", 10), ("needs_you", 5), ("cancelled", 5)]
FAILED = "TikTok logged you out. Press Log in, then Retry once you're in."

# Every event Chrome times over 16 ms and every long task, kept from the page's first moment.
WATCH = """
window.__events = []; window.__long = [];
new PerformanceObserver((list) => window.__events.push(...list.getEntries().map(
  (e) => ({ name: e.name, start: e.startTime, duration: e.duration })))
).observe({ type: "event", durationThreshold: 16, buffered: true });
new PerformanceObserver((list) => window.__long.push(...list.getEntries().map(
  (e) => ({ start: e.startTime, duration: e.duration })))
).observe({ type: "longtask", buffered: true });
"""
# Ready for one press: what the page shows now is kept as __before, the press's first event marks __t0, and
# each frame after it checks whether the page shows what was asked for, marking __done the first time it
# does. False when the page already shows it, which would time nothing.
ARM = """() => {
  const w = window;
  w.__before = (BEFORE); w.__t0 = null; w.__done = null;
  const holds = () => (DONE);
  if (holds()) return false;
  const kinds = ["pointerdown", "keydown", "input", "change"];
  const mark = () => { if (w.__t0 === null) w.__t0 = performance.now(); };
  kinds.forEach((k) => addEventListener(k, mark, true));
  const tick = () => {
    if (w.__t0 !== null && holds()) {
      w.__done = performance.now();
      kinds.forEach((k) => removeEventListener(k, mark, true));
    } else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return true;
}"""
DONE = "() => window.__done !== null"
# Chrome hands its timings to the observers after the frame is drawn, so they are read a moment later.
LATER = "() => new Promise((done) => setTimeout(done, 150))"
TIMED = """() => {
  const w = window, t0 = w.__t0, end = w.__done;
  const events = w.__events.filter((e) => e.start >= t0 - 20 && e.start <= end);
  const long = w.__long.filter((e) => e.start + e.duration >= t0 && e.start <= end);
  return {
    settle: end - t0,
    event: Math.max(0, ...events.map((e) => e.duration)),
    long: Math.max(0, ...long.map((e) => e.duration)),
  };
}"""
ARTICLES = 'document.querySelectorAll("main article").length'
ROWS = 'document.querySelectorAll("main table tbody tr").length'
DIALOG = '!!document.querySelector("[role=dialog]")'
SELECTED = 'document.querySelector("main [role=tab][aria-selected=true]")?.textContent.trim()'
TEXT = 'document.querySelector("main").innerText'
HIDE_STALE = """[...document.querySelectorAll("main [role=switch]")]
  .find((i) => i.labels[0]?.textContent === "Hide stale").checked"""
OPTIONS = """[...document.querySelectorAll("main legend")]
  .some((l) => l.textContent === "Clip length" && !l.closest("[hidden]"))"""
DRAWN = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
SCROLL = """() => { const m = document.querySelector("main");
  return { top: m.scrollTop, end: m.scrollHeight - m.clientHeight }; }"""
TO_TOP = '() => { document.querySelector("main").scrollTop = 0; }'
NOW = "() => performance.now()"
THEME = "document.documentElement.dataset.theme"
LONG_SINCE = "(t0) => window.__long.filter((e) => e.start >= t0).map((e) => e.duration)"


@dataclass
class Press:
    """One control on one page: the page's address, what shows once it has loaded, the press, what the page
    shows before it (kept as __before) and what it shows once the press is done."""

    name: str
    path: str
    ready: str
    press: Callable[[Page], None]
    done: str
    before: str = "null"


def seed(engine, video) -> dict:
    """The big library. Returns the ids the presses open."""
    for k in range(PROJECTS - 1):
        seed_project(engine, title=f"Talk {k + 1}", clips=6, rendered=2, video=video)
    big = seed_project(engine, title="The big library", clips=CLIPS, rendered=10, video=video)
    for k in DISCARDED:
        engine.discard_clip(f"{big}_c{k}")
    accounts = [add_account(engine, "tiktok", f"tt_{k}") for k in range(6)]
    accounts += [add_account(engine, "instagram", f"ig_{k}") for k in range(6)]
    made = []
    for k in range(CAMPAIGNS):
        made.append(
            add_campaign(
                engine,
                name=f"Campaign {k + 1:03d}",
                marketplace=MARKETPLACES[k % len(MARKETPLACES)],
                rate_per_1000=0.5 + k % 7,
                budget_left=100.0 * (k % 13),
                rules_text=RULES if k == 0 else "",
            )
        )
    add_posts(engine, accounts, [f"{big}_c{k}" for k in range(10)], made[:20])
    for k in range(BRIEFS):
        add_brief(
            engine,
            title=f"Brief {k + 1:03d}",
            marketplace=MARKETPLACES[k % len(MARKETPLACES)],
            niche=NICHES[k % len(NICHES)],
            rate_per_1000=0.5 + k % 5,
        )
    engine.update_settings(engine.settings.model_copy(update={"theme": "dark"}))
    return {"project": big, "clip": f"{big}_c0", "campaign": made[0].id}


def add_posts(engine, accounts, clips, linked) -> None:
    """``POSTS`` in one batch across the accounts: the ones still to go from the next half hour on, the
    others gone by. Each one that went up has a count of its views and is submitted to one of ``linked``,
    every third one paid."""
    conn = engine.db()
    batch = store.add_batch(conn, repo.new_id("plan"), "spread", "smart", {})
    now = time.time()
    posts, metrics, sent = [], [], []
    statuses = [status for status, n in POSTS for _ in range(n)]
    for k, status in enumerate(statuses):
        account = accounts[k % len(accounts)]
        ahead = status == "scheduled"
        when = now + (k + 1) * 1800 if ahead else now - (k + 1) * 2 * DAY / 3
        post = Post(
            id=repo.new_id("po"),
            batch_id=batch,
            clip_id=clips[k % len(clips)],
            clip_title=f"Clip {k + 1}",
            render_id=None,
            account_id=account.id,
            platform=account.platform,
            method=account.method,
            caption=f"Clip {k + 1} #clip",
            hashtags=["clip"],
            options=PostOptions(),
            scheduled_at=when,
            status=status,
            error=FAILED if status in ("failed", "needs_you") else None,
            posted_at=when if status == "posted" else None,
            video_key=f"v{k}",
            start_s=0.0,
            end_s=30.0,
            created_at=now,
            updated_at=now,
        )
        posts.append(post)
        if status == "posted":
            metrics.append(
                Metric(post_id=post.id, captured_at=when + DAY, source="hand", views=1000 * (k + 1))
            )
            paid = 5.0 * (k % 9) if k % 3 == 0 else None
            sent.append(
                Submission(
                    id=repo.new_id("sb"),
                    post_id=post.id,
                    campaign_id=linked[k % len(linked)].id,
                    status="approved" if paid is not None else "submitted",
                    paid_amount=paid,
                    paid_on=date.fromtimestamp(when + 3 * DAY).isoformat() if paid is not None else None,
                    created_at=now,
                    updated_at=now,
                )
            )
    store.add_posts(conn, posts)
    for m in metrics:
        campaigns.add_metric(conn, m)
    for made in sent:
        campaigns.add_submission(conn, made)


def rail(page: Page):
    return page.get_by_role("navigation", name="Pages")


def other_theme(page: Page) -> None:
    """Light from dark, dark from light: the choice is saved, so a second try starts from the first's."""
    name = "Dark" if page.evaluate(f"() => {THEME}") == "light" else "Light"
    page.get_by_role("radio", name=name).check()


def presses(ids: dict) -> list[Press]:
    return [
        Press(
            "Today, to Projects in the rail",
            "/",
            f"{TEXT}.length > 0",
            lambda page: rail(page).get_by_role("link", name="Projects", exact=True).click(),
            f'location.pathname === "/projects" && {ARTICLES} === {PROJECTS}',
        ),
        Press(
            "Create, Options",
            "/create",
            '!!document.querySelector("main")',
            lambda page: page.get_by_role("button", name="Options").click(),
            OPTIONS,
        ),
        Press(
            "Projects, search",
            "/projects",
            f"{ARTICLES} === {PROJECTS}",
            lambda page: page.get_by_label("Search projects").fill("big"),
            f"{ARTICLES} === 1",
        ),
        Press(
            "Project of 40 clips, Show discarded",
            f"/projects/{ids['project']}",
            f"{ARTICLES} === {CLIPS - len(DISCARDED)}",
            lambda page: page.get_by_label("Show discarded").check(),
            f"{ARTICLES} === {CLIPS}",
        ),
        Press(
            "Editor, Text tab",
            f"/clips/{ids['clip']}",
            f'{SELECTED} === "Cut"',
            lambda page: page.get_by_role("tab", name="Text", exact=True).click(),
            f'{SELECTED} === "Text"',
        ),
        Press(
            "Queue of 200, Status",
            "/queue",
            f"{ARTICLES} > 100",
            lambda page: page.get_by_label("Status").select_option("failed"),
            f'location.search.includes("failed") && {ARTICLES} > 0 && {ARTICLES} < window.__before',
            ARTICLES,
        ),
        Press(
            "Channels, New clipping channel",
            "/channels",
            '!!document.querySelector("main h1")',
            lambda page: page.get_by_role("button", name="New clipping channel").click(),
            DIALOG,
        ),
        Press(
            "Campaigns of 300, Marketplace",
            "/campaigns",
            f"{ROWS} === {CAMPAIGNS}",
            lambda page: page.get_by_label("Marketplace").select_option("Vyro"),
            f"{ROWS} === {CAMPAIGNS // len(MARKETPLACES)}",
        ),
        Press(
            "Briefs of 300, Niche",
            "/campaigns?tab=briefs",
            f"{ARTICLES} > 0",
            lambda page: page.get_by_label("Niche").select_option("Gaming"),
            f"{ARTICLES} > 0 && {ARTICLES} < window.__before",
            ARTICLES,
        ),
        Press(
            "A campaign, the rules as pasted",
            f"/campaigns/{ids['campaign']}",
            '!!document.querySelector("main details")',
            lambda page: page.get_by_text("The rules as pasted").click(),
            'document.querySelector("main details").open',
        ),
        Press(
            "Money, All time",
            "/money",
            '!!document.querySelector("main input[name=period]")',
            lambda page: page.get_by_role("radio", name="All time").check(),
            f"{TEXT} !== window.__before",
            TEXT,
        ),
        Press(
            "Accounts of 12, Add account",
            "/accounts",
            f"{ARTICLES} > 0",
            lambda page: page.get_by_role("button", name="Add account").click(),
            DIALOG,
        ),
        Press(
            "Library, SFX tab",
            "/library",
            f'{SELECTED} === "Gameplay"',
            lambda page: page.get_by_role("tab", name="SFX").click(),
            f'{SELECTED} === "SFX"',
        ),
        Press(
            "Trends, Hide stale",
            "/trends",
            '!!document.querySelector("main [role=switch]")',
            lambda page: page.get_by_role("switch", name="Hide stale").click(),
            f"({HIDE_STALE}) !== window.__before",
            HIDE_STALE,
        ),
        Press(
            "Settings, the other theme",
            "/settings",
            '!!document.querySelector("main input[name=theme]")',
            other_theme,
            f"{THEME} !== window.__before",
            THEME,
        ),
    ]


def timed(page: Page, url: str, press: Press) -> dict:
    """The page opened, then the press timed: its settle time, Chrome's longest event and longest task."""
    opened(page, url + press.path)
    try:
        page.wait_for_function(f"() => {press.ready}", timeout=10_000)
    except TimeoutError as e:
        raise AssertionError(f"{press.name}: the page never showed {press.ready}") from e
    page.evaluate(DRAWN)
    armed = ARM.replace("BEFORE", press.before).replace("DONE", press.done)
    assert page.evaluate(armed), f"{press.name}: the page already shows what the press asks for"
    press.press(page)
    page.wait_for_function(DONE, polling="raf")
    page.evaluate(LATER)
    return {k: round(v, 1) for k, v in page.evaluate(TIMED).items()}


def scrolled(page: Page) -> list[float]:
    """The gallery wheeled from top to bottom a step a frame: every long task on the way."""
    page.evaluate(TO_TOP)
    page.evaluate(DRAWN)
    box = page.get_by_role("main").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    t0 = page.evaluate(NOW)
    for _ in range(60):
        page.mouse.wheel(0, 240)
        page.evaluate(DRAWN)
        at = page.evaluate(SCROLL)
        if at["top"] >= at["end"] - 1:
            break
    else:
        raise AssertionError(f"the gallery didn't reach its end: {at}")
    page.evaluate(LATER)
    return page.evaluate(LONG_SINCE, t0)


def test_big_library_stays_fast(page, ui_engine, server, video, tmp_path):
    ids = seed(ui_engine, video)
    page.add_init_script(WATCH)
    times = {}
    for press in presses(ids):
        for _ in range(TRIES):
            took = timed(page, server.url, press)
            if took["settle"] < LIMIT_MS and took["event"] < LIMIT_MS:
                break
        times[press.name] = took
        if press.path.startswith("/projects/"):
            expect(page.get_by_role("article")).to_have_count(CLIPS)
            for _ in range(TRIES):
                janky = max(scrolled(page), default=0.0)
                if janky < LONG_MS:
                    break
            times["Project of 40 clips, gallery scroll"] = {"long": janky}

    out = Path(os.environ.get("CLIPFORGE_PERF") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "big-library.json").write_text(json.dumps(times, indent=2))
    slow = {
        name: t
        for name, t in times.items()
        if t.get("settle", 0) >= LIMIT_MS or t.get("event", 0) >= LIMIT_MS
    }
    assert not slow, f"presses over {LIMIT_MS:.0f} ms: {slow}"
    janky = times["Project of 40 clips, gallery scroll"]["long"]
    assert janky < LONG_MS, f"the gallery's scroll had a {janky:.0f} ms task"
