import assert from "node:assert/strict";
import { test } from "node:test";

import type { Draft, DraftGroup, PlanWarning, RuleCheck } from "../types.ts";
import {
  approveProblem,
  blockedHint,
  countLabel,
  draftTitle,
  groupName,
  groupTick,
  nextCheckLine,
  ticked,
  tickedLabel,
  todayLabel,
  handTicks,
  listedWarnings,
  withCaption,
  withTicks,
} from "./drafts.ts";

const at = (day: number, hour: number, minute = 0) => Date.UTC(2026, 10, day, hour - 11, minute) / 1000; // Sydney, AEDT
const NOW = at(3, 7, 10); // 7:10 am on Tue 3 Nov 2026

function draft(id: string): Draft {
  return { id } as Draft; // the helpers only read the id
}

function group(name: string | null, ...videos: string[][]): DraftGroup {
  return {
    channel: name === null ? null : { id: `cc_${name}`, name },
    videos: videos.map((ids, n) => ({
      project: { id: `p${n}`, title: "A talk", channel: "TED", thumb: null, published_at: null, rights_basis: "own" },
      drafts: ids.map(draft),
    })),
  };
}

const GROUPS = [group("Podcasts", ["a", "b"], ["c"]), group("Gaming", ["d"])];

test("ticked gives every draft not unticked, in the order shown", () => {
  assert.deepEqual(
    ticked(GROUPS, new Set()).map((d) => d.id),
    ["a", "b", "c", "d"],
  );
  assert.deepEqual(
    ticked(GROUPS, new Set(["b", "d"])).map((d) => d.id),
    ["a", "c"],
  );
  assert.deepEqual(ticked([], new Set(["a"])), []);
});

test("a group's tick is all, some or none of its drafts", () => {
  const [podcasts] = GROUPS;
  assert.equal(groupTick(podcasts, new Set()), "all");
  assert.equal(groupTick(podcasts, new Set(["c", "d"])), "some");
  assert.equal(groupTick(podcasts, new Set(["a", "b", "c"])), "none");
});

test("countLabel says how many drafts", () => {
  assert.equal(countLabel(0), "No drafts");
  assert.equal(countLabel(1), "1 draft");
  assert.equal(countLabel(6), "6 drafts");
});

test("tickedLabel says how many are ticked, once any are not", () => {
  assert.equal(tickedLabel(3, 3), "3 drafts");
  assert.equal(tickedLabel(2, 3), "2 of 3 drafts ticked");
  assert.equal(tickedLabel(0, 1), "0 of 1 draft ticked");
});

test("the rail's Today link is read out with the drafts waiting", () => {
  assert.equal(todayLabel(0), "Today");
  assert.equal(todayLabel(1), "Today, 1 draft to review");
  assert.equal(todayLabel(3), "Today, 3 drafts to review");
});

test("Approve and post takes 1 to 100 drafts", () => {
  assert.equal(approveProblem(0), "Tick the drafts to approve first.");
  assert.equal(approveProblem(1), null);
  assert.equal(approveProblem(100), null);
  assert.equal(approveProblem(101), "Approve up to 100 at a time.");
});

test("a group from a removed clipping channel still has a name", () => {
  assert.equal(groupName(GROUPS[0]), "Podcasts");
  assert.equal(groupName(group(null, ["e"])), "From removed clipping channels");
});

test("the next check is said in Sydney time, with its clipping channel", () => {
  const line = (day: number, hour: number) => nextCheckLine({ at: at(day, hour), name: "Podcasts" }, NOW, 1);
  assert.equal(line(3, 18), "The next check is at 6:00 pm today (Podcasts).");
  assert.equal(line(4, 6), "The next check is at 6:00 am tomorrow (Podcasts).");
  assert.equal(line(6, 6), "The next check is at 6:00 am on Fri (Podcasts).");
  assert.equal(line(12, 6), "The next check is at 6:00 am on Thu 12 Nov (Podcasts).");
  assert.equal(nextCheckLine(null, NOW, 2), "No check is coming up: each clipping channel is paused or has nothing to watch.");
});

test("with no clipping channels, the line says where to make one", () => {
  assert.equal(nextCheckLine(null, NOW, 0), "Make a clipping channel on the Channels page to get drafts here.");
});

test("a draft is called by its clip's hook title, else the clip's title", () => {
  assert.equal(draftTitle({ hook_title: "Why we wait", clip_title: "Clip 1" } as Draft), "Why we wait");
  assert.equal(draftTitle({ hook_title: null, clip_title: "Clip 1" } as Draft), "Clip 1");
});

test("withCaption puts a saved caption on its draft and leaves the rest as they were", () => {
  const body = { groups: GROUPS, count: 4, next_check: null };
  const changed = withCaption(body, "c", "New words");
  assert.equal(changed.groups[0].videos[1].drafts[0].caption, "New words");
  assert.equal(changed.groups[0].videos[0], body.groups[0].videos[0]);
  assert.equal(changed.groups[1], body.groups[1]);
  assert.equal(body.groups[0].videos[1].drafts[0].caption, undefined); // the old answer is left alone
});

const rule = (key: string, ok: boolean): RuleCheck => ({
  key,
  label: key,
  kind: key.startsWith("hand:") ? "hand" : "auto",
  ok,
  sentence: ok ? "" : "Fix it.",
});
const RULES = [rule("hashtag:0", false), rule("hand:0", false), rule("hand:1", true)];

test("handTicks gives every hand rule ticked after one is ticked or unticked, never an auto one", () => {
  assert.deepEqual(handTicks(RULES, "hand:0", true), ["hand:0", "hand:1"]);
  assert.deepEqual(handTicks(RULES, "hand:1", false), []);
});

test("withTicks marks the ticked hand rules done and leaves the auto ones as ClipForge found them", () => {
  const ruled = { ...draft("c"), rules: RULES };
  const body = { groups: [{ ...GROUPS[0], videos: [{ ...GROUPS[0].videos[1], drafts: [ruled] }] }], count: 1, next_check: null };
  const changed = withTicks(body, "c", ["hand:0"]).groups[0].videos[0].drafts[0];
  assert.deepEqual(
    changed.rules.map((r) => [r.key, r.ok]),
    [["hashtag:0", false], ["hand:0", true], ["hand:1", false]],
  );
  assert.equal(changed.rules[1].sentence, ""); // nothing left to say once it is ticked
  assert.equal(RULES[1].ok, false); // the old answer is left alone
});

test("listedWarnings leaves out the rule warnings only while the rules list shows them", () => {
  const warn = (code: string): PlanWarning => ({ code, sentence: code, key: null, blocking: true, move_to: null });
  const warnings = [warn("rule_unchecked"), warn("slot_taken"), warn("rule_broken")];
  assert.deepEqual(
    listedWarnings({ ...draft("a"), warnings, rules: RULES }).map((w) => w.code),
    ["slot_taken"],
  );
  assert.deepEqual(
    listedWarnings({ ...draft("a"), warnings, rules: [] }).map((w) => w.code),
    ["rule_unchecked", "slot_taken", "rule_broken"],
  );
});

test("blockedHint names what holds the Post dialog back: red warnings, rules to tick, or both", () => {
  const warn = (code: string, blocking = true): PlanWarning => ({ code, sentence: code, key: "k", blocking, move_to: null });
  assert.equal(blockedHint([warn("rule_unchecked"), warn("slot_taken", false)]), "Tick the rules you checked to post.");
  assert.equal(blockedHint([warn("rule_broken")]), "Fix the warnings in red to post."); // a broken rule shows red
  assert.equal(blockedHint([warn("over_cap")]), "Fix the warnings in red to post.");
  assert.equal(
    blockedHint([warn("over_cap"), warn("rule_unchecked")]),
    "Fix the warnings in red and tick the rules you checked to post.",
  );
});
