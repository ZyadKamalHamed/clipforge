// What the path on Today says (SPEC 14.10): the level you are on, how many steps are done, where each step
// stands, the page each step's work is on and the account to make first.

import type {
  PathAccount,
  PathLevel,
  PathOn,
  PathStep,
  PathView,
} from "../types.ts";
import { money } from "./campaigns.ts";

const LEVELS = 6;

// "Level 2 of 6: Your first account", or that the path is walked.
export function levelLine(path: PathOn): string {
  if (path.level === null) return "Path done: you are earning";
  const level = path.levels.find((lv) => lv.n === path.level);
  return `Level ${path.level} of ${LEVELS}: ${level?.name ?? ""}`;
}

// "4 of 22 steps done".
export function progressLine(path: PathOn): string {
  return `${path.done} of ${path.total} steps done`;
}

// Where a step stands, in a few words.
export function stateLabel(step: PathStep): string {
  switch (step.state) {
    case "done":
      return step.done_how === "hand" ? "Ticked" : "Done in ClipForge";
    case "todo":
    case "listed":
      return "On your list";
    case "waiting":
      return "Comes onto your list when it's due";
    case "locked":
      return "Locked";
  }
}

const ON_LIST = new Set(["todo", "listed"]);

// Whether a step shows what it asks and its page: on the level you are on while it's still to do, and on any
// level once it's on your list.
export function workShown(step: PathStep, current: boolean): boolean {
  if (ON_LIST.has(step.state)) return true;
  return current && step.state !== "done" && step.state !== "locked";
}

// Whether a level is open: the one you are on, and any with a step on your list.
export function levelOpen(level: PathLevel, current: boolean): boolean {
  return current || level.steps.some((s) => ON_LIST.has(s.state));
}

const PAGES: Record<string, string> = {
  "/accounts": "Open Accounts",
  "/settings": "Open Settings",
  "/campaigns?tab=briefs": "Open Briefs",
  "/campaigns": "Open Campaigns",
  "/create": "Open Create",
  "/projects": "Open Projects",
  "/channels": "Open Channels",
  "/trends": "Open Trends",
};

// A step's button: the page its work is on, named.
export function stepLink(to: string): { label: string; to: string } {
  return { label: PAGES[to] ?? "Open", to };
}

// Each step's page by its id, for the To do list's buttons; none while the path is off.
export function pathLinks(path: PathView): Record<string, string | null> {
  if (!path.on) return {};
  return Object.fromEntries(
    path.levels.flatMap((lv) => lv.steps.map((s) => [s.id, s.to])),
  );
}

// The account to make, a line each: its name ideas, bio, picture, niche tags and what it could earn a day,
// those it has.
export function accountLines(account: PathAccount): [string, string][] {
  const lines: ([string, string] | null)[] = [
    ["Name ideas", account.names.join(", ")],
    ["Bio", account.bio],
    ["Profile picture", account.picture],
    account.tags.length ? ["Niche tags", account.tags.join(", ")] : null,
    account.per_day > 0
      ? [
          "Could earn",
          `about ${money(account.per_day, account.currency)} a day (a guess)`,
        ]
      : null,
  ];
  return lines.filter((line): line is [string, string] => line !== null);
}
