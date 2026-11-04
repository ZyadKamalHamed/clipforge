import assert from "node:assert/strict";
import { test } from "node:test";

import type { QueuedPost } from "../types.ts";
import { setTimeZone } from "./plan.ts";
import {
  actionsFor,
  ALL,
  cancelText,
  filterPosts,
  heldLine,
  logTime,
  queueGroup,
  queueLabel,
  queueOrder,
  statusOf,
  statusText,
} from "./queue.ts";

function post(id: string, fields: Partial<QueuedPost> = {}): QueuedPost {
  return {
    id,
    batch_id: "b1",
    clip_id: "c1",
    clip_title: "The deadline",
    account_id: "a1",
    account_name: "@one",
    platform: "tiktok",
    method: "browser",
    caption: "Words",
    hashtags: [],
    options: { visibility: "everyone", comments: true, duet: true, stitch: true, disclosure: false, share_to_feed: true, trial_reel: false, trial_graduation: "manual", assisted: false },
    scheduled_at: 1000,
    status: "scheduled",
    attempts: 0,
    url: null,
    error: null,
    error_code: null,
    details: null,
    note: null,
    actions: [],
    retry_helps: true,
    on_tiktok: false,
    waiting: null,
    screenshot: null,
    posted_at: null,
    created_at: 1,
    updated_at: 1,
    ...fields,
  };
}

const ids = (posts: QueuedPost[]) => posts.map((p) => p.id);

test("queueOrder puts what needs you first, failed after Needs you, then the next to go, then the newest finished", () => {
  const posts = [
    post("posted-old", { status: "posted", scheduled_at: 100, posted_at: 110 }),
    post("later", { scheduled_at: 3000 }),
    post("needs-later", { status: "needs_you", scheduled_at: 2500 }),
    post("failed-new", { status: "failed", scheduled_at: 900 }),
    post("soon", { status: "rendering", scheduled_at: 2000 }),
    post("needs-soon", { status: "needs_you", scheduled_at: 1500 }),
    post("posted-new", { status: "posted", scheduled_at: 200, posted_at: 950 }),
    post("uploading", { status: "uploading", scheduled_at: 1200 }),
  ];
  assert.deepEqual(ids(queueOrder(posts)), [
    "needs-soon",
    "needs-later",
    "failed-new",
    "uploading",
    "soon",
    "later",
    "posted-new",
    "posted-old",
  ]);
});

test("queueGroup pins failed posts with the ones that need you, until you deal with them", () => {
  const group = (status: QueuedPost["status"]) => queueGroup(post("p", { status }));
  for (const status of ["needs_you", "failed"] as const) assert.equal(group(status), "needs_you");
  for (const status of ["scheduled", "rendering", "uploading", "processing"] as const) assert.equal(group(status), "next");
  for (const status of ["posted", "cancelled", "exported"] as const) assert.equal(group(status), "done");
});

test("a post whose window waits for you sits with the ones that need you, found by that filter, with its window", () => {
  const waiting = "ClipForge has filled this in. Check it and press Post. It waits for you in its Chrome window for 10 minutes.";
  const posts = [
    post("next", { status: "scheduled", scheduled_at: 900 }),
    post("failed", { status: "failed", scheduled_at: 800 }),
    post("posting", { status: "uploading", scheduled_at: 700 }),
    post("waits", { status: "processing", scheduled_at: 1200, waiting }),
  ];
  assert.equal(queueGroup(posts[3]), "needs_you");
  assert.equal(queueGroup(posts[2]), "next");
  assert.deepEqual(ids(queueOrder(posts)), ["waits", "failed", "posting", "next"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "needs_you" })), ["failed", "waits"]);
  assert.ok(actionsFor(posts[3]).includes("front"));
  assert.ok(!actionsFor(post("p", { status: "processing" })).includes("front"));
});

test("queueOrder keeps posts at the same time in the order given and leaves its input alone", () => {
  const posts = [post("b"), post("a"), post("c", { scheduled_at: 999 })];
  assert.deepEqual(ids(queueOrder(posts)), ["c", "b", "a"]);
  assert.deepEqual(ids(posts), ["b", "a", "c"]);
});

test("filterPosts narrows by status group, account and platform together", () => {
  const posts = [
    post("t1", { status: "scheduled" }),
    post("t2", { status: "rendering", account_id: "a2" }),
    post("t3", { status: "uploading" }),
    post("i1", { status: "posted", platform: "instagram", account_id: "a3" }),
    post("i2", { status: "cancelled", platform: "instagram", account_id: "a3" }),
    post("n1", { status: "needs_you" }),
    post("e1", { status: "exported" }),
    post("f1", { status: "failed" }),
  ];
  assert.deepEqual(ids(filterPosts(posts, ALL)), ids(posts));
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "scheduled" })), ["t1", "t2"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "posting" })), ["t3"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "done" })), ["i2", "e1"]);
  // "Needs you" shows what the Queue's Needs you section shows, the failed posts too.
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "needs_you" })), ["n1", "f1"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "failed" })), ["f1"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, platform: "instagram" })), ["i1", "i2"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, account: "a2" })), ["t2"]);
  assert.deepEqual(ids(filterPosts(posts, { status: "scheduled", account: "a1", platform: "tiktok" })), ["t1"]);
  assert.deepEqual(ids(filterPosts(posts, { ...ALL, status: "needs_you", platform: "instagram" })), []);
});

test("actionsFor offers only what the engine allows from each status", () => {
  const only = (fields: Partial<QueuedPost>) => actionsFor(post("p", fields));
  assert.deepEqual(only({ status: "scheduled" }), ["edit", "reschedule", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "rendering" }), ["cancel", "log"]);
  assert.deepEqual(only({ status: "uploading", method: "browser" }), ["front", "log"]);
  assert.deepEqual(only({ status: "uploading", method: "api" }), ["log"]);
  assert.deepEqual(only({ status: "processing" }), ["log"]);
  assert.deepEqual(only({ status: "needs_you" }), ["retry", "mark_posted", "reschedule", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "failed" }), ["retry", "reschedule", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "failed", retry_helps: false }), ["export", "cancel", "log"]); // a new time fails too
  for (const status of ["posted", "cancelled"] as const) assert.deepEqual(only({ status }), ["log"]);
  assert.deepEqual(only({ status: "exported" }), ["mark_posted", "log"]); // once you have posted it yourself
});

test("actionsFor offers Post without the sound for a post with an Instagram sound, first on one turned down", () => {
  const sound = { source: "ig_trending", external_id: "a1", title: "Song a1", artist: null, url: null, volume: 15 } as const;
  const options = { ...post("p").options, sound };
  const only = (fields: Partial<QueuedPost>) => actionsFor(post("p", { platform: "instagram", method: "api", options, ...fields }));
  assert.deepEqual(only({ status: "failed", retry_helps: false }), ["without_sound", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "failed" }), ["retry", "without_sound", "reschedule", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "scheduled" }), ["edit", "without_sound", "reschedule", "export", "cancel", "log"]);
  assert.deepEqual(only({ status: "needs_you" }), [
    "retry",
    "mark_posted",
    "without_sound",
    "reschedule",
    "export",
    "cancel",
    "log",
  ]);
  for (const status of ["rendering", "uploading", "posted", "exported"] as const) {
    assert.ok(!only({ status }).includes("without_sound"), status);
  }
  // A TikTok sound is one you add yourself in its window, so leaving it out is yours to do there
  const shortlisted = { ...options, sound: { ...sound, source: "shortlist" as const } };
  assert.ok(!actionsFor(post("p", { status: "failed", options: shortlisted })).includes("without_sound"));
});

test("actionsFor adds Publish anyway only where the engine offered it on a post that needs you", () => {
  assert.deepEqual(actionsFor(post("p", { status: "needs_you", actions: ["publish_anyway", "cancel"] })), [
    "publish_anyway",
    "retry",
    "mark_posted",
    "reschedule",
    "export",
    "cancel",
    "log",
  ]);
  assert.ok(!actionsFor(post("p", { status: "failed", actions: ["publish_anyway"] })).includes("publish_anyway"));
});

test("actionsFor leads a post waiting on a login with Log in, then Retry, then Mark as posted", () => {
  assert.deepEqual(actionsFor(post("p", { status: "needs_you", actions: ["login"] })), [
    "login",
    "retry",
    "mark_posted",
    "reschedule",
    "export",
    "cancel",
    "log",
  ]);
  assert.ok(!actionsFor(post("p", { status: "scheduled", actions: ["login"] })).includes("login"));
});

test("a post TikTok Studio holds says so, offers only Cancel and its log, and goes with the scheduled ones", () => {
  const held = post("p", { status: "scheduled_on_tiktok", on_tiktok: true });
  assert.deepEqual(statusText("scheduled_on_tiktok"), { text: "Scheduled on TikTok", tone: "neutral" });
  assert.deepEqual(actionsFor(held), ["cancel", "log"]);
  assert.equal(queueGroup(held), "next");
  assert.deepEqual(ids(filterPosts([held, post("q", { status: "posted" })], { ...ALL, status: "scheduled" })), ["p"]);
  assert.equal(heldLine(held), "Cancelling or moving it means deleting it in TikTok Studio by hand.");
  assert.equal(heldLine(post("q")), null);
  assert.equal(
    cancelText(held),
    "This post is already scheduled on TikTok. Cancelling here only stops ClipForge following it, so delete it in TikTok Studio too.",
  );
  assert.equal(cancelText(post("q")), "It won't go up. You can post the clip again from its project.");
});

test("a post TikTok Studio holds that needs you offers Mark as posted, never Retry, Reschedule or Export", () => {
  // uploading it again would put it up twice
  const waiting = post("p", { status: "needs_you", on_tiktok: true });
  assert.deepEqual(actionsFor(waiting), ["mark_posted", "cancel", "log"]);
  assert.deepEqual(actionsFor({ ...waiting, actions: ["login"] }), ["login", "mark_posted", "cancel", "log"]);
  assert.equal(heldLine(waiting), null); // its sentence says what to do
  assert.match(cancelText(waiting), /^This post is already scheduled on TikTok\./);
});

test("statusOf says a post whose window waits for you is waiting for you, and any other its status", () => {
  const waiting = "ClipForge has filled this in. Check it and press Post.";
  assert.deepEqual(statusOf(post("w", { status: "uploading", waiting })), { text: "Waiting for you", tone: "warn" });
  assert.deepEqual(statusOf(post("u", { status: "uploading" })), statusText("uploading"));
});

test("statusText says each status in words with its colour", () => {
  assert.deepEqual(statusText("needs_you"), { text: "Needs you", tone: "warn" });
  assert.deepEqual(statusText("posted"), { text: "Posted", tone: "ok" });
  assert.deepEqual(statusText("failed"), { text: "Failed", tone: "bad" });
  assert.deepEqual(statusText("uploading"), { text: "Uploading", tone: "info" });
  assert.deepEqual(statusText("scheduled"), { text: "Scheduled", tone: "neutral" });
});

test("logTime says a step's moment in Sydney to the second", () => {
  // 5:50:07 am on Tue 3 Nov 2026 in Sydney (daylight time, UTC+11) is 6:50:07 pm on 2 Nov in UTC.
  assert.equal(logTime(Date.UTC(2026, 10, 2, 18, 50, 7) / 1000), "5:50:07 am");
  assert.equal(logTime(Date.UTC(2026, 10, 3, 1, 0, 0) / 1000), "12:00:00 pm");
  assert.equal(logTime(Date.UTC(2026, 10, 2, 13, 5, 9.6) / 1000), "12:05:09 am");
});

test("logTime follows the posting time zone", () => {
  try {
    setTimeZone("Asia/Bangkok");
    assert.equal(logTime(Date.UTC(2026, 10, 2, 18, 50, 7) / 1000), "1:50:07 am");
  } finally {
    setTimeZone("Australia/Sydney");
  }
});

test("queueLabel names the rail's link with how many posts need you", () => {
  assert.equal(queueLabel(0), "Queue");
  assert.equal(queueLabel(1), "Queue, 1 needs you");
  assert.equal(queueLabel(2), "Queue, 2 need you");
});
