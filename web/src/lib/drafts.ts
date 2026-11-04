// What Today's Clips to review says (SPEC 14.9 and 14.10 item 3): which drafts are ticked, how a group's
// tick shows, the counts, and when the next check runs. Every draft starts ticked; the page keeps the ones
// unticked, so a draft that arrives later starts ticked too. A campaign draft's rules (10.10) tick by hand
// here too, and its rule warnings show in its rule list rather than twice.

import type { Draft, DraftGroup, DraftsBody, NextCheck, PlanWarning, RuleCheck } from "../types.ts";
import { timeThenDay } from "./plan.ts";

export const APPROVE_MAX = 100; // the most drafts one plan takes, as the server checks
export const NONE_TICKED = "Tick the drafts to approve first.";
export const TOO_MANY = `Approve up to ${APPROVE_MAX} at a time.`;
export const NO_CHECK = "No check is coming up: each clipping channel is paused or has nothing to watch.";
export const NO_CHANNELS = "Make a clipping channel on the Channels page to get drafts here.";
export const HAND_UNTICKED = "Check this yourself, then tick it."; // as rules.checks_for says it
const RULE_CODES = new Set(["rule_unchecked", "rule_broken"]);

export function groupDrafts(group: DraftGroup): Draft[] {
  return group.videos.flatMap((video) => video.drafts);
}

// The drafts still ticked, in the order the page shows them.
export function ticked(groups: DraftGroup[], unticked: Set<string>): Draft[] {
  return groups.flatMap(groupDrafts).filter((draft) => !unticked.has(draft.id));
}

export function groupTick(group: DraftGroup, unticked: Set<string>): "all" | "some" | "none" {
  const drafts = groupDrafts(group);
  const off = drafts.filter((draft) => unticked.has(draft.id)).length;
  if (off === 0) return "all";
  return off === drafts.length ? "none" : "some";
}

export function countLabel(n: number): string {
  if (n === 0) return "No drafts";
  return n === 1 ? "1 draft" : `${n} drafts`;
}

// "3 drafts" while all are ticked, else "2 of 3 drafts ticked".
export function tickedLabel(n: number, total: number): string {
  return n === total ? countLabel(total) : `${n} of ${countLabel(total)} ticked`;
}

// The rail's Today link as a screen reader says it.
export function todayLabel(n: number): string {
  return n === 0 ? "Today" : `Today, ${countLabel(n)} to review`;
}

// Why Approve and post can't take this many ticked drafts, or null when it can.
export function approveProblem(n: number): string | null {
  if (n === 0) return NONE_TICKED;
  return n > APPROVE_MAX ? TOO_MANY : null;
}

export function draftTitle(draft: Pick<Draft, "hook_title" | "clip_title">): string {
  return draft.hook_title || draft.clip_title;
}

// The answer with one draft changed; only the group and video that hold it are new.
function withDraft(body: DraftsBody, id: string, change: (draft: Draft) => Draft): DraftsBody {
  const has = (drafts: Draft[]) => drafts.some((draft) => draft.id === id);
  return {
    ...body,
    groups: body.groups.map((group) =>
      group.videos.some((video) => has(video.drafts))
        ? {
            ...group,
            videos: group.videos.map((video) =>
              has(video.drafts) ? { ...video, drafts: video.drafts.map((d) => (d.id === id ? change(d) : d)) } : video,
            ),
          }
        : group,
    ),
  };
}

// The answer with one draft's caption as it was saved.
export function withCaption(body: DraftsBody, id: string, caption: string): DraftsBody {
  return withDraft(body, id, (d) => ({ ...d, caption }));
}

// Every hand rule ticked once ``key`` is ticked (``on``) or unticked: what the draft saves, whole.
export function handTicks(rules: RuleCheck[], key: string, on: boolean): string[] {
  return rules.filter((r) => r.kind === "hand" && (r.key === key ? on : r.ok)).map((r) => r.key);
}

// The rules with the hand ones ticked as ``ticks`` says; the auto ones stay as ClipForge found them.
export function tickedRules(rules: RuleCheck[], ticks: string[]): RuleCheck[] {
  const done = new Set(ticks);
  return rules.map((r) => (r.kind === "hand" ? { ...r, ok: done.has(r.key), sentence: done.has(r.key) ? "" : HAND_UNTICKED } : r));
}

// The answer with one draft's hand rules ticked as saved.
export function withTicks(body: DraftsBody, id: string, ticks: string[]): DraftsBody {
  return withDraft(body, id, (d) => ({ ...d, rules: tickedRules(d.rules, ticks) }));
}

// The warnings a draft card, or a post in the Post dialog's Review, lists: a campaign's rules say their own
// problems in its rules list.
export function listedWarnings(post: { warnings: PlanWarning[]; rules: RuleCheck[] }): PlanWarning[] {
  if (post.rules.length === 0) return post.warnings;
  return post.warnings.filter((w) => !RULE_CODES.has(w.code));
}

// What the Post dialog's footer says holds Confirm back: a rule you check yourself shows amber, not red.
export function blockedHint(warnings: PlanWarning[]): string {
  const blocking = warnings.filter((w) => w.blocking);
  const tick = blocking.some((w) => w.code === "rule_unchecked");
  const red = blocking.some((w) => w.code !== "rule_unchecked");
  if (tick && red) return "Fix the warnings in red and tick the rules you checked to post.";
  return tick ? "Tick the rules you checked to post." : "Fix the warnings in red to post.";
}

export function groupName(group: DraftGroup): string {
  return group.channel?.name ?? "From removed clipping channels";
}

export function nextCheckLine(next: NextCheck | null, now: number, channels: number): string {
  if (next === null) return channels === 0 ? NO_CHANNELS : NO_CHECK;
  return `The next check is at ${timeThenDay(next.at, now)} (${next.name}).`;
}
