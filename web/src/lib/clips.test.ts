import assert from "node:assert/strict";
import { test } from "node:test";

import type { Clip, Render } from "../types.ts";
import {
  canRender,
  clipStatus,
  clipTitle,
  download,
  inTier,
  sortClips,
  TIER_FILTERS,
  TIERS,
  timeRange,
} from "./clips.ts";

const render = (
  video: string | null = "/media/projects/p1/clips/p1_c0/v1/clip.mp4",
): Render => ({
  id: 1,
  version: 1,
  poster: null,
  thumb: null,
  video,
  duration_s: 35.4,
  size_bytes: 1000,
  qa_ok: true,
});

const clip = (over: Partial<Clip> = {}): Clip => ({
  id: "p1_c0",
  project_id: "p1",
  rank: 1,
  start_s: 83,
  end_s: 118.4,
  duration_s: 35.4,
  final_score: 80,
  tier: "post",
  passes_gates: true,
  archetype: "story",
  reason: "A setup and its payoff.",
  hook_title: "The night I stopped procrastinating",
  layout: "face",
  layout_reason: null,
  flags: [],
  notes: [],
  status: "packaged",
  error: null,
  render: null,
  progress: null,
  edited: false,
  stale: false,
  discarded: false,
  posted_to: [],
  as_is: false,
  ...over,
});

test("a clip posted as it is never renders, so nothing offers to", () => {
  assert.equal(canRender(clip()), true);
  assert.equal(canRender(clip({ as_is: true, status: "rendered" })), false);
  assert.equal(canRender(clip({ as_is: true, status: "failed" })), false);
});

test("a clip is called by its hook title, or until it has one by where it starts", () => {
  assert.equal(clipTitle(clip()), "The night I stopped procrastinating");
  assert.equal(clipTitle(clip({ hook_title: null })), "The moment at 1:23");
  assert.equal(timeRange(clip()), "1:23 to 1:58");
});

test("each tier has its word and colour, and the filter chips cover every tier", () => {
  assert.deepEqual(TIERS.post, { label: "Post", tone: "ok" });
  assert.deepEqual(TIERS.test, { label: "Test", tone: "info" });
  assert.deepEqual(TIERS.weak, { label: "Weak", tone: "warn" });
  assert.deepEqual(TIERS.no, { label: "Don't post", tone: "bad" });
  assert.deepEqual(
    TIER_FILTERS.map((f) => f.label),
    ["All", "Post", "Test", "Weak", "Don't post"],
  );
  assert.equal(inTier(clip({ tier: "test" }), "test"), true);
  assert.equal(inTier(clip({ tier: "test" }), "post"), false);
  assert.equal(inTier(clip({ tier: null }), "all"), true);
  assert.equal(inTier(clip({ tier: null }), "weak"), false);
});

test("best first goes by score then rank, and in video order by where each starts", () => {
  const a = clip({ id: "a", rank: 2, final_score: 70, start_s: 300 });
  const b = clip({ id: "b", rank: 1, final_score: 90, start_s: 600 });
  const c = clip({ id: "c", rank: 3, final_score: 70, start_s: 10 });
  const d = clip({ id: "d", rank: null, final_score: null, start_s: 5 });
  assert.deepEqual(
    sortClips([d, c, a, b], "score").map((x) => x.id),
    ["b", "a", "c", "d"],
  );
  assert.deepEqual(
    sortClips([a, b, c, d], "time").map((x) => x.id),
    ["d", "c", "a", "b"],
  );
});

test("each status says where the clip is in a word or two", () => {
  const said = (over: Partial<Clip>) => clipStatus(clip(over));
  assert.deepEqual(said({ status: "idea" }), { text: "Idea", tone: "neutral" });
  assert.deepEqual(said({ status: "packaged" }), {
    text: "Idea",
    tone: "neutral",
  });
  assert.deepEqual(said({ status: "queued" }), {
    text: "In the queue",
    tone: "neutral",
  });
  assert.deepEqual(said({ status: "analyzing" }), {
    text: "Getting ready",
    tone: "accent",
  });
  assert.deepEqual(said({ status: "rendering" }), {
    text: "Rendering",
    tone: "accent",
  });
  assert.deepEqual(said({ status: "rendered", render: render() }), {
    text: "Ready",
    tone: "ok",
  });
  assert.deepEqual(said({ status: "failed" }), { text: "Failed", tone: "bad" });
  assert.deepEqual(said({ status: "rendered", discarded: true }), {
    text: "Discarded",
    tone: "neutral",
  });
});

test("one clip downloads as its own file and several as one zip, of those with a render", () => {
  const one = clip({ rank: 3, render: render() });
  const two = clip({
    id: "p1_c1",
    rank: 4,
    render: render("/media/projects/p1/clips/p1_c1/v2/clip.mp4"),
  });
  const idea = clip({ id: "p1_c2" });
  const single = {
    href: "/media/projects/p1/clips/p1_c0/v1/clip.mp4?download=3%20The%20night%20I%20stopped%20procrastinating",
  };
  assert.deepEqual(download([one]), single);
  assert.deepEqual(download([one, idea]), single);
  assert.deepEqual(download([one, two, idea]), {
    href: "/api/clips/download?ids=p1_c0,p1_c1",
  });
});

test("a download says why not when no clip has a render, or there are over 40", () => {
  assert.deepEqual(download([clip()]), {
    problem: "None of those clips has a render to download yet.",
  });
  assert.deepEqual(download([]), {
    problem: "None of those clips has a render to download yet.",
  });
  const many = Array.from({ length: 41 }, (_, k) =>
    clip({ id: `p1_c${k}`, render: render() }),
  );
  assert.deepEqual(download(many), {
    problem: "Download at most 40 clips at a time.",
  });
  assert.ok("href" in download(many.slice(0, 40)));
});
