// What an account's card says (SPEC 11): its name, how it posts, how it is, today's posts against its
// cap and its last post; and what the account's Settings send, from the lines and lists typed there.

import type { Tone } from "../components/Badge.tsx";
import type { Account, Platform, PostingWindow } from "../types.ts";
import { formatAgo } from "./format.ts";

export const PLATFORMS: Record<Platform, string> = { tiktok: "TikTok", instagram: "Instagram" };

const STATUSES: Record<Account["status"], { text: string; tone: Tone }> = {
  connected: { text: "Connected", tone: "ok" },
  needs_login: { text: "Needs login", tone: "warn" },
  token_expiring: { text: "Token expiring", tone: "warn" },
  error: { text: "Not working", tone: "bad" },
};

export function statusLabel(account: Pick<Account, "status">): { text: string; tone: Tone } {
  return STATUSES[account.status] ?? STATUSES.error;
}

// An account's login job in a word: it waits for Chrome's lane, behind a post or a check, before its window
// opens for you.
export function loginWord(job: { status: string }): string {
  return job.status === "running" ? "Logging in" : "Waiting for Chrome";
}

export function capText(account: Pick<Account, "today">): string {
  return `${account.today.posts} of ${account.today.cap} today`;
}

const SPAMMY: Record<Platform, number> = { tiktok: 3, instagram: 2 }; // SPEC 5 item 8: a warning above these

// What a posts-a-day number risks, said beside it: more than the route posts at all, or more than looks
// right to the platform. A number that isn't whole gets its refusal sentence instead.
export function capWarning(platform: Platform, cap: number | string | null, ceiling?: number): string | null {
  const typed = typeof cap === "string" ? cap.trim() : cap;
  if (typed === null || typed === "" || !/^\d+$/.test(String(typed))) return null;
  const n = Number(typed);
  if (ceiling !== undefined && n > ceiling) {
    return `ClipForge posts at most ${ceiling} a day this way, so this account posts ${ceiling}.`;
  }
  if (n > SPAMMY[platform]) {
    return `More than ${SPAMMY[platform]} a day on one ${PLATFORMS[platform]} account risks looking like spam.`;
  }
  return null;
}

// The account as the window names it: its handle, else its label, as the engine's sentences do.
export function accountName(account: Pick<Account, "handle" | "label" | "platform">): string {
  if (account.handle) return `@${account.handle}`;
  return account.label || `New ${PLATFORMS[account.platform]} account`;
}

export function methodText(account: Pick<Account, "method" | "ig_mode">): string {
  if (account.method === "browser") return "Posts in Chrome";
  return account.ig_mode === "instagram" ? "Meta's API, Instagram login" : "Meta's API, through Facebook";
}

export function lastPostText(account: Pick<Account, "last_post">, now: number): string {
  const at = account.last_post?.posted_at;
  if (!account.last_post) return "No posts yet";
  return at ? `Last post ${formatAgo(at, now)}` : "Last post";
}

// How the card gets an account going again: its Chrome login, a new key pasted for it, or its Facebook
// Pages linked again (a key from Facebook covers every account on them).
export type Reconnect = { kind: "login" | "token" | "facebook"; label: string };

export function reconnectFor(account: Pick<Account, "method" | "ig_mode" | "status">): Reconnect {
  if (account.method === "browser") {
    return { kind: "login", label: account.status === "needs_login" ? "Log in" : "Log in again" };
  }
  return { kind: account.ig_mode === "instagram" ? "token" : "facebook", label: "Reconnect" };
}

// Words in a list, from text typed with commas or one a line: trimmed, and each kept once (the first
// spelling of it, whatever its case).
export function parseList(text: string): string[] {
  const words = new Map<string, string>();
  for (const part of text.split(/[,\n]/)) {
    const word = part.trim();
    if (word && !words.has(word.toLowerCase())) words.set(word.toLowerCase(), word);
  }
  return [...words.values()];
}

export function listText(words: string[]): string {
  return words.join(", ");
}

export const WINDOW_LINE = "Write each window like 07:00 to 09:00, one a line.";

const LINE = /^(\d{1,2}):(\d{2})\s*(?:to|-)\s*(\d{1,2}):(\d{2})$/i;

export function windowLines(windows: PostingWindow[] | null): string {
  return (windows ?? []).map((w) => `${w.start} to ${w.end}`).join("\n");
}

// The windows typed, one a line, or the line that can't be read. No lines means the defaults in Settings
// (null). Whether each one ends after it starts is the engine's to say, with its own sentence.
export function parseWindows(text: string): { windows: PostingWindow[] | null } | { error: string } {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return { windows: null };
  const windows: PostingWindow[] = [];
  for (const line of lines) {
    const m = LINE.exec(line);
    if (!m) return { error: WINDOW_LINE };
    const [, h1, m1, h2, m2] = m;
    windows.push({ start: `${h1.padStart(2, "0")}:${m1}`, end: `${h2.padStart(2, "0")}:${m2}` });
  }
  return { windows };
}
