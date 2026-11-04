import assert from "node:assert/strict";
import { test } from "node:test";

import type { PathOn, PathStep } from "../types.ts";
import {
  accountLines,
  levelLine,
  levelOpen,
  pathLinks,
  progressLine,
  stateLabel,
  stepLink,
  workShown,
} from "./path.ts";

function step(id: string, fields: Partial<PathStep> = {}): PathStep {
  return {
    id,
    title: `Step ${id}`,
    detail: "What to do.",
    state: "locked",
    done_how: null,
    to: null,
    ...fields,
  };
}

function path(fields: Partial<PathOn> = {}): PathOn {
  return {
    on: true,
    level: 2,
    done: 4,
    total: 22,
    levels: [
      {
        n: 1,
        name: "Set up",
        goal: "ClipForge can reach you",
        steps: [step("notify", { state: "done", done_how: "hand" })],
      },
      {
        n: 2,
        name: "Your first account",
        goal: "A TikTok account ClipForge can post to",
        steps: [
          step("link_account", { state: "todo", to: "/accounts" }),
          step("check_account"),
        ],
      },
    ],
    metrics: { accounts: 1, posts: 0, earned: 0, currency: "USD" },
    account: {
      broad: true,
      niche: null,
      names: ["Late Night Clips", "Clips After Dark"],
      bio: "The best moments, every day.",
      picture: "a bold two letter monogram",
      tags: ["gaming", "music"],
      per_day: 5,
      currency: "USD",
    },
    ...fields,
  };
}

test("levelLine names the level you are on out of six, and says when the path is walked", () => {
  assert.equal(levelLine(path()), "Level 2 of 6: Your first account");
  assert.equal(levelLine(path({ level: null })), "Path done: you are earning");
});

test("progressLine counts the steps done", () => {
  assert.equal(progressLine(path()), "4 of 22 steps done");
  assert.equal(progressLine(path({ done: 1 })), "1 of 22 steps done");
});

test("stateLabel says where each step stands", () => {
  assert.equal(
    stateLabel(step("a", { state: "done", done_how: "hand" })),
    "Ticked",
  );
  assert.equal(
    stateLabel(step("a", { state: "done", done_how: "app" })),
    "Done in ClipForge",
  );
  assert.equal(stateLabel(step("a", { state: "todo" })), "On your list");
  assert.equal(stateLabel(step("a", { state: "listed" })), "On your list");
  assert.equal(
    stateLabel(step("a", { state: "waiting" })),
    "Comes onto your list when it's due",
  );
  assert.equal(stateLabel(step("a", { state: "locked" })), "Locked");
});

test("stepLink names the page a step's work is on", () => {
  assert.deepEqual(stepLink("/accounts"), {
    label: "Open Accounts",
    to: "/accounts",
  });
  assert.deepEqual(stepLink("/campaigns?tab=briefs"), {
    label: "Open Briefs",
    to: "/campaigns?tab=briefs",
  });
  assert.deepEqual(stepLink("/campaigns"), {
    label: "Open Campaigns",
    to: "/campaigns",
  });
  assert.deepEqual(stepLink("/somewhere"), { label: "Open", to: "/somewhere" });
});

test("pathLinks maps each step to its page, null for one done by hand where you are", () => {
  assert.deepEqual(pathLinks(path()), {
    notify: null,
    link_account: "/accounts",
    check_account: null,
  });
  assert.deepEqual(pathLinks({ on: false }), {});
});

test("accountLines spell out the account to make", () => {
  assert.deepEqual(accountLines(path().account), [
    ["Name ideas", "Late Night Clips, Clips After Dark"],
    ["Bio", "The best moments, every day."],
    ["Profile picture", "a bold two letter monogram"],
    ["Niche tags", "gaming, music"],
    ["Could earn", "about US$5.00 a day (a guess)"],
  ]);
  const quiet = { ...path().account, per_day: 0, tags: [] };
  assert.deepEqual(
    accountLines(quiet).map(([name]) => name),
    ["Name ideas", "Bio", "Profile picture"],
  );
});

test("workShown shows what a step asks on the level you are on, and anywhere once it's on your list", () => {
  assert.equal(workShown(step("a", { state: "todo" }), true), true);
  assert.equal(workShown(step("a", { state: "waiting" }), true), true);
  assert.equal(workShown(step("a", { state: "done" }), true), false);
  assert.equal(workShown(step("a", { state: "locked" }), true), false);
  assert.equal(workShown(step("a", { state: "todo" }), false), true);
  assert.equal(workShown(step("a", { state: "listed" }), false), true);
  assert.equal(workShown(step("a", { state: "waiting" }), false), false);
});

test("levelOpen opens the level you are on and any level with a step on your list", () => {
  const [first, second] = path().levels;
  assert.equal(levelOpen(second, true), true);
  assert.equal(levelOpen(first, false), false);
  assert.equal(levelOpen(second, false), true); // link_account is on your list
  const later = {
    ...second,
    steps: [step("check_account", { state: "waiting" })],
  };
  assert.equal(levelOpen(later, false), false);
});
