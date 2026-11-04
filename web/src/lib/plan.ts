// The Post dialog's helpers (SPEC 14.5): the accounts by platform and which can't be picked, the plan's
// warnings with the ones that stop Confirm and post first, a post's caption counted as the apps count it,
// and its time in words and in a date and time field, in the posting time zone. Times are seconds since
// 1970, as the engine sends them.

import type { Account, Platform, PlanWarning, PostOptions } from "../types.ts";
import { PLATFORMS } from "./accounts.ts";

export const CAPTION_LIMIT: Record<Platform, number> = {
  tiktok: 4000,
  instagram: 2200,
};

// A post's switches before anyone changes them (clipforge/post/models.py PostOptions).
export const POST_DEFAULTS: PostOptions = {
  visibility: "everyone",
  comments: true,
  duet: true,
  stitch: true,
  disclosure: false,
  share_to_feed: true,
  trial_reel: false,
  trial_graduation: "manual",
  assisted: false,
};

export const VISIBILITY: { value: PostOptions["visibility"]; label: string }[] =
  [
    { value: "everyone", label: "Everyone" },
    { value: "friends", label: "Friends" },
    { value: "only_me", label: "Only me" },
  ];

type Switch =
  | "comments"
  | "duet"
  | "stitch"
  | "disclosure"
  | "share_to_feed"
  | "trial_reel"
  | "trial_graduation"; // on: Instagram shares a trial reel with followers when it does well

type SwitchRow = { key: Switch; label: string; hint?: string };

const TIKTOK: SwitchRow[] = [
  { key: "comments", label: "Comments" },
  { key: "duet", label: "Duet" },
  { key: "stitch", label: "Stitch" },
  {
    key: "disclosure",
    label: "Content disclosure",
    hint: "Turn on when the post promotes a brand, a product or a service.",
  },
];
const SHARE: SwitchRow = {
  key: "share_to_feed",
  label: "Share to feed",
  hint: "Shows the reel on your profile grid as well as in Reels.",
};
const TRIAL: SwitchRow = {
  key: "trial_reel",
  label: "Trial reel",
  hint: "Shown only to people who don't follow this account at first.",
};
const GRADUATE: SwitchRow = {
  key: "trial_graduation",
  label: "Share with followers if it does well",
  hint: "Off: you share it from Instagram when you choose.",
};

// A platform's on and off switches, in the order the form shows them. A trial reel goes only through Meta's
// API, and while one is on it takes Share to feed's place: Instagram shares it with followers later, or not.
export function switchesFor(
  platform: Platform,
  method: Account["method"],
  options: PostOptions,
): SwitchRow[] {
  if (platform === "tiktok") return TIKTOK;
  if (method !== "api") return [SHARE];
  return options.trial_reel ? [TRIAL, GRADUATE] : [TRIAL, SHARE];
}

export function isOn(options: PostOptions, key: Switch): boolean {
  return key === "trial_graduation"
    ? options.trial_graduation === "auto"
    : options[key];
}

export function switchOn(key: Switch, on: boolean): Partial<PostOptions> {
  return key === "trial_graduation"
    ? { trial_graduation: on ? "auto" : "manual" }
    : { [key]: on };
}

// What a post's line in Details says about its switches: the ones that differ from its platform's block.
export function switchesOff(
  platform: Platform,
  options: PostOptions,
  block: PostOptions,
): string[] {
  const out: string[] = [];
  if (platform === "tiktok" && options.visibility !== block.visibility) {
    out.push(
      VISIBILITY.find((v) => v.value === options.visibility)?.label ??
        options.visibility,
    );
  }
  const rows = platform === "tiktok" ? TIKTOK : [TRIAL, SHARE, GRADUATE]; // every switch the post could differ in
  for (const { key, label } of rows) {
    if (isOn(options, key) !== isOn(block, key))
      out.push(`${label} ${isOn(options, key) ? "on" : "off"}`);
  }
  return out;
}

// What a trial reel's row on Review says, since its warnings column has nothing to say about it.
export function trialLine(
  platform: Platform,
  options: PostOptions,
): string | null {
  if (platform !== "instagram" || !options.trial_reel) return null;
  const later =
    options.trial_graduation === "auto"
      ? "Instagram shares it with followers if it does well."
      : "You share it with followers from Instagram.";
  return `Trial reel: people who don't follow this account see it first. ${later}`;
}

export function firstLine(text: string): string {
  return (
    text
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean) ?? ""
  );
}

const SYDNEY = "Australia/Sydney";
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];
const DAY_MS = 86_400_000;

export function groupAccounts(
  accounts: Account[],
): { platform: Platform; accounts: Account[] }[] {
  return (Object.keys(PLATFORMS) as Platform[])
    .map((platform) => ({
      platform,
      accounts: accounts.filter((a) => a.platform === platform),
    }))
    .filter((group) => group.accounts.length > 0);
}

// Why an account can't be picked to post to, or null when it can.
export function whyNot(
  account: Pick<Account, "status" | "method">,
): string | null {
  if (account.status === "needs_login" && account.method === "browser")
    return "Press Log in to post here.";
  if (account.status === "needs_login")
    return "Log in again on the Accounts page to post here.";
  if (account.status === "error")
    return "Check it on the Accounts page to post here.";
  return null;
}

export function sortWarnings(warnings: PlanWarning[]): PlanWarning[] {
  return [
    ...warnings.filter((w) => w.blocking),
    ...warnings.filter((w) => !w.blocking),
  ];
}

// Notes that only say what ClipForge does before a post goes up, so nothing to fix.
const NOTES = new Set(["renders_variant"]);

// A warning's colour: red when it stops posting, muted for a note, else amber.
export function warningTone(w: PlanWarning): string {
  if (w.blocking) return "text-bad";
  return NOTES.has(w.code) ? "text-muted" : "text-warn";
}

// The caption as it posts, the hashtags a blank line below, in UTF-16 units as TikTok and Instagram count.
export function captionUnits(caption: string, hashtags: string[]): number {
  return [caption, hashtagText(hashtags)].filter(Boolean).join("\n\n").length;
}

export function hashtagText(hashtags: string[]): string {
  return hashtags.map((tag) => `#${tag}`).join(" ");
}

export function parseHashtags(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((word) => word.replace(/^#+/, ""))
    .filter(Boolean);
}

type Wall = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
};

function wallIn(zone: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-AU", {
    timeZone: zone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  });
}

let zone = SYDNEY;
let WALL = wallIn(SYDNEY);

// The posting time zone (Settings), which the engine keeps its windows and caps in, so every time the
// window shows is in it too. A zone the browser doesn't know keeps Sydney's.
export function setTimeZone(name: string): void {
  if (name === zone) return;
  try {
    WALL = wallIn(name);
    zone = name;
  } catch {
    WALL = wallIn(SYDNEY);
    zone = SYDNEY;
  }
}

export function timeZone(): string {
  return zone;
}

function wall(at: number): Wall {
  const parts = Object.fromEntries(
    WALL.formatToParts(new Date(at * 1000)).map((p) => [p.type, p.value]),
  );
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
  };
}

const dayNumber = (w: Wall) => Date.UTC(w.year, w.month - 1, w.day) / DAY_MS;
const pad = (n: number) => String(n).padStart(2, "0");

function clock(w: Wall): string {
  return `${w.hour % 12 || 12}:${pad(w.minute)} ${w.hour < 12 ? "am" : "pm"}`;
}

// The time on the zone's clock alone: "7:20 am".
export function wallClock(at: number): string {
  return clock(wall(at));
}

// "Today 7:40 pm", "Tomorrow 9:00 am", "Thu 12:15 pm" this week, else "Tue 10 Nov, 9:00 am".
export function dayThenTime(at: number, now: number): string {
  const then = wall(at);
  const days = dayNumber(then) - dayNumber(wall(now));
  const weekday = DAYS[new Date(dayNumber(then) * DAY_MS).getUTCDay()];
  if (days === 0) return `Today ${clock(then)}`;
  if (days === 1) return `Tomorrow ${clock(then)}`;
  if (days > 1 && days < 7) return `${weekday} ${clock(then)}`;
  return dayAndTime(at);
}

// The day and time in full, however near: "Tue 3 Nov, 6:05 pm".
export function dayAndTime(at: number): string {
  const then = wall(at);
  const weekday = DAYS[new Date(dayNumber(then) * DAY_MS).getUTCDay()];
  return `${weekday} ${then.day} ${MONTHS[then.month - 1]}, ${clock(then)}`;
}

// The same in a sentence, the time first: "6:00 am today", "6:00 am tomorrow", "6:00 am on Fri" this week,
// else "6:00 am on Thu 12 Nov".
export function timeThenDay(at: number, now: number): string {
  const then = wall(at);
  const days = dayNumber(then) - dayNumber(wall(now));
  const weekday = DAYS[new Date(dayNumber(then) * DAY_MS).getUTCDay()];
  if (days === 0) return `${clock(then)} today`;
  if (days === 1) return `${clock(then)} tomorrow`;
  if (days > 1 && days < 7) return `${clock(then)} on ${weekday}`;
  return `${clock(then)} on ${weekday} ${then.day} ${MONTHS[then.month - 1]}`;
}

// The day alone, on the zone's calendar: "27 Oct".
export function dayOnly(at: number): string {
  const then = wall(at);
  return `${then.day} ${MONTHS[then.month - 1]}`;
}

// The time as a date and time field shows it in the zone: "2026-11-03T19:40".
export function localInput(at: number): string {
  const w = wall(at);
  return `${w.year}-${pad(w.month)}-${pad(w.day)}T${pad(w.hour)}:${pad(w.minute)}`;
}

// How far ahead of UTC the zone's clock is at a moment, in milliseconds.
function offset(ms: number): number {
  const w = wall(ms / 1000);
  return (
    Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute) -
    Math.floor(ms / 60_000) * 60_000
  );
}

// A date and time field's value read in the zone. In the hour the clocks skip, it is the hour after.
export function parseLocal(text: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const asUtc = Date.UTC(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
  );
  if (!Number.isFinite(asUtc)) return null;
  const first = offset(asUtc);
  const second = offset(asUtc - first);
  return (asUtc - (first === second ? first : second)) / 1000;
}
