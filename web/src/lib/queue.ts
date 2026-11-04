// The Queue's helpers (SPEC 14.6): the posts in the order shown, the filters by status, account and
// platform, the buttons each post offers from where it is (the engine's own rules, clipforge/post/
// service.py), and each status in words.

import type { Tone } from "../components/Badge.tsx";
import type { Platform, PostStatus, QueuedPost } from "../types.ts";
import { timeZone } from "./plan.ts";

export type QueueFilters = { status: string; account: string; platform: Platform | "all" };

export const ALL: QueueFilters = { status: "all", account: "all", platform: "all" };

// The status filter's choices, each with the statuses it shows ("all" shows every one).
export const STATUS_FILTERS: { value: string; label: string; statuses: PostStatus[] }[] = [
  { value: "all", label: "Every status", statuses: [] },
  // as the Needs you section, with the posts whose window waits for you
  { value: "needs_you", label: "Needs you", statuses: ["needs_you", "failed"] },
  { value: "scheduled", label: "Scheduled", statuses: ["scheduled", "rendering", "scheduled_on_tiktok"] },
  { value: "posting", label: "Posting now", statuses: ["uploading", "processing"] },
  { value: "posted", label: "Posted", statuses: ["posted"] },
  { value: "failed", label: "Failed", statuses: ["failed"] },
  { value: "done", label: "Cancelled or exported", statuses: ["cancelled", "exported"] },
];

const STILL_TO_GO = new Set<PostStatus>(["scheduled", "rendering", "uploading", "processing", "scheduled_on_tiktok"]);
const MOVABLE = new Set<PostStatus>(["scheduled", "needs_you", "failed"]);
const CANCELLABLE = new Set<PostStatus>(["scheduled", "rendering", "scheduled_on_tiktok", "needs_you", "failed"]);
const SOUNDLESS = new Set<PostStatus>(["scheduled", "needs_you", "failed"]); // edited, or tried again without it

export type Action =
  | "front"
  | "login"
  | "publish_anyway"
  | "retry"
  | "mark_posted"
  | "edit"
  | "without_sound"
  | "reschedule"
  | "export"
  | "cancel"
  | "log";

const STATUS: Record<PostStatus, { text: string; tone: Tone }> = {
  needs_you: { text: "Needs you", tone: "warn" },
  scheduled: { text: "Scheduled", tone: "neutral" },
  rendering: { text: "Rendering", tone: "info" },
  uploading: { text: "Uploading", tone: "info" },
  processing: { text: "Processing", tone: "info" },
  scheduled_on_tiktok: { text: "Scheduled on TikTok", tone: "neutral" },
  posted: { text: "Posted", tone: "ok" },
  failed: { text: "Failed", tone: "bad" },
  cancelled: { text: "Cancelled", tone: "neutral" },
  exported: { text: "Exported", tone: "neutral" },
};

export function statusText(status: PostStatus): { text: string; tone: Tone } {
  return STATUS[status];
}

// A post's badge: its status, unless its Chrome window waits for you.
export function statusOf(post: Pick<QueuedPost, "status" | "waiting">): { text: string; tone: Tone } {
  return post.waiting ? { text: "Waiting for you", tone: "warn" } : statusText(post.status);
}

export type QueueGroup = "needs_you" | "next" | "done";

// The Queue's three sections: what needs you (a failed post too, until you retry, reschedule, export or
// cancel it, and one whose Chrome window waits for your click), what goes next, and what is over.
export function queueGroup(post: Pick<QueuedPost, "status"> & Partial<Pick<QueuedPost, "waiting">>): QueueGroup {
  if (post.status === "needs_you" || post.status === "failed" || post.waiting) return "needs_you";
  return STILL_TO_GO.has(post.status) ? "next" : "done";
}

function rank(post: QueuedPost): number {
  if (post.status === "needs_you" || post.waiting) return 0;
  if (post.status === "failed") return 1;
  return STILL_TO_GO.has(post.status) ? 2 : 3;
}

// What needs you first (soonest first, then the failed ones, newest first), then what goes next (soonest
// first), then what is over (newest first), so the Queue stays useful however many posts it has behind it.
// Posts at the same time keep the order given.
export function queueOrder(posts: QueuedPost[]): QueuedPost[] {
  const when = (p: QueuedPost) => p.posted_at ?? p.scheduled_at;
  return [...posts].sort((a, b) => {
    const group = rank(a) - rank(b);
    if (group !== 0) return group;
    return rank(a) === 1 || rank(a) === 3 ? when(b) - when(a) : when(a) - when(b);
  });
}

export function filterPosts(posts: QueuedPost[], filters: QueueFilters): QueuedPost[] {
  const statuses = STATUS_FILTERS.find((f) => f.value === filters.status)?.statuses ?? [];
  const mine = (p: QueuedPost) => (filters.status === "needs_you" ? queueGroup(p) === "needs_you" : false);
  return posts.filter(
    (p) =>
      (statuses.length === 0 || statuses.includes(p.status) || mine(p)) &&
      (filters.account === "all" || p.account_id === filters.account) &&
      (filters.platform === "all" || p.platform === filters.platform),
  );
}

// The buttons a post offers, in the order shown: what its sentence asks for first. The window comes to the
// front only while a browser post uploads or its window waits for you; Log in and Publish anyway only
// where the engine offered them, and Mark as posted after Retry, since such a post most often never went up;
// an exported post offers Mark as posted once you have put it up yourself.
// A failed post a retry can't change leads with Export and leaves Retry and Reschedule out: at any time it
// fails the same way. A post with an Instagram sound can go without it (Instagram may turn the sound down),
// first of all on one turned down. One TikTok Studio holds is never uploaded again, so it offers only Mark
// as posted (once it needs you), Cancel and its log.
export function actionsFor(
  post: Pick<QueuedPost, "status" | "method" | "actions" | "retry_helps" | "on_tiktok"> &
    Partial<Pick<QueuedPost, "options" | "waiting">>,
): Action[] {
  const { status } = post;
  const out: Action[] = [];
  if (post.on_tiktok && (status === "scheduled_on_tiktok" || status === "needs_you")) {
    if (status === "needs_you" && post.actions.includes("login")) out.push("login");
    if (status === "needs_you") out.push("mark_posted");
    return [...out, "cancel", "log"];
  }
  const stuck = status === "failed" && !post.retry_helps;
  if (post.method === "browser" && (status === "uploading" || post.waiting)) out.push("front");
  if (status === "needs_you" && post.actions.includes("login")) out.push("login");
  if (status === "needs_you" && post.actions.includes("publish_anyway")) out.push("publish_anyway");
  if (status === "needs_you" || (status === "failed" && !stuck)) out.push("retry");
  if (status === "needs_you" || status === "exported") out.push("mark_posted");
  if (status === "scheduled") out.push("edit");
  if (post.options?.sound?.source === "ig_trending" && SOUNDLESS.has(status)) out.push("without_sound");
  if (stuck) out.push("export");
  else if (MOVABLE.has(status)) out.push("reschedule", "export");
  if (CANCELLABLE.has(status)) out.push("cancel");
  out.push("log");
  return out;
}

// The line under a post TikTok Studio holds: ClipForge can't take it back from there.
export function heldLine(post: Pick<QueuedPost, "status">): string | null {
  return post.status === "scheduled_on_tiktok"
    ? "Cancelling or moving it means deleting it in TikTok Studio by hand."
    : null;
}

// What Cancel asks before it stops a post.
export function cancelText(post: Pick<QueuedPost, "on_tiktok">): string {
  return post.on_tiktok
    ? "This post is already scheduled on TikTok. Cancelling here only stops ClipForge following it, so delete it " +
        "in TikTok Studio too."
    : "It won't go up. You can post the clip again from its project.";
}

let clock: { zone: string; format: Intl.DateTimeFormat } | null = null;

// A log step's moment (seconds since 1970) in the posting time zone to the second: "5:50:07 am".
export function logTime(at: number): string {
  const zone = timeZone();
  if (clock?.zone !== zone) {
    clock = {
      zone,
      format: new Intl.DateTimeFormat("en-AU", {
        timeZone: zone,
        hourCycle: "h23",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      }),
    };
  }
  const parts = Object.fromEntries(
    clock.format.formatToParts(new Date(Math.floor(at) * 1000)).map((p) => [p.type, p.value]),
  );
  const hour = Number(parts.hour);
  return `${hour % 12 || 12}:${parts.minute}:${parts.second} ${hour < 12 ? "am" : "pm"}`;
}

// The rail's Queue link, read out with how many posts need you.
export function queueLabel(needsYou: number): string {
  if (needsYou <= 0) return "Queue";
  return `Queue, ${needsYou} ${needsYou === 1 ? "needs" : "need"} you`;
}
