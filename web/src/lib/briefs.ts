// The Briefs tab (SPEC 10.11): a brief's rate, its budget and the day that was read, what it could earn a
// day, the accounts it fits, and the filters by niche and marketplace. Money is in the brief's currency.

import type {
  Brief,
  BriefSource,
  BriefWorth,
  Platform,
  Suggestion,
} from "../types.ts";
import { accountName, PLATFORMS } from "./accounts.ts";
import { money } from "./campaigns.ts";
import { formatAgo } from "./format.ts";
import { dayOnly, timeThenDay } from "./plan.ts";

export const NO_BRIEFS =
  "No briefs yet. Campaign Scout's file is read each morning, and you can paste a campaign's rules below.";
export const NONE_MATCH = "No briefs match these filters.";
export const SCOUT = "bs_scout"; // Campaign Scout's file, the one source ClipForge reads with no site behind it
export const NO_NEW = "No new briefs to look at.";
export const LOOK_MAX = 5; // the new briefs Today shows; the rest are on the Briefs tab

export type BriefFilter = { niche: string | null; marketplace: string | null }; // null for any

// Whole money without its cents: "US$812", else "US$812.50".
function whole(amount: number, currency: string): string {
  const shown = money(amount, currency);
  return Number.isInteger(amount) ? shown.replace(".00", "") : shown;
}

// The rate on each platform it pays on (the platform's own, else the brief's): the one rate when they
// agree, else the highest of them.
export function rateText(
  b: Pick<Brief, "rate_per_1000" | "rates" | "platforms" | "currency">,
): string {
  const rates = b.platforms
    .map((p) => b.rates[p] ?? b.rate_per_1000)
    .filter((r): r is number => r !== null);
  if (rates.length === 0) return "No rate";
  const most = Math.max(...rates);
  const one =
    rates.length === b.platforms.length && rates.every((r) => r === most);
  return `${one ? "" : "up to "}${money(most, b.currency)} per 1,000 views`;
}

export function budgetText(
  b: Pick<Brief, "budget_left" | "budget_read_at" | "currency">,
): string {
  if (b.budget_left === null) return "Budget not shown";
  const left = `${whole(b.budget_left, b.currency)} left`;
  return b.budget_read_at === null
    ? left
    : `${left}, read ${dayOnly(b.budget_read_at)}`;
}

export function worthText(w: BriefWorth): string {
  if (w.amount === null) return "No rate";
  return `about ${money(w.amount, w.currency)} a day${w.guess ? ", a guess" : ""}`;
}

// The accounts that fit, the one the worth is through first.
export function fitText(b: Pick<Brief, "fits" | "worth">): string {
  if (b.fits.length === 0) return "Fits no account yet";
  const first = b.fits.filter((a) => a.id === b.worth.account_id);
  const rest = b.fits.filter((a) => a.id !== b.worth.account_id);
  return `Fits ${[...first, ...rest].map(accountName).join(", ")}`;
}

export function platformText(platforms: Platform[]): string {
  if (platforms.length === 1) return `${PLATFORMS[platforms[0]]} only`;
  return platforms.map((p) => PLATFORMS[p]).join(" and ");
}

export function filterBriefs(list: Brief[], filter: BriefFilter): Brief[] {
  return list.filter(
    (b) =>
      (filter.niche === null || b.niche === filter.niche) &&
      (filter.marketplace === null || b.marketplace === filter.marketplace),
  );
}

// The niches and marketplaces the briefs have, for the filters: once each, in order.
export function filterOptions(list: Brief[]): {
  niches: string[];
  marketplaces: string[];
} {
  const sorted = (words: (string | null)[]) =>
    [...new Set(words.filter((w): w is string => !!w))].sort((a, b) =>
      a.localeCompare(b),
    );
  return {
    niches: sorted(list.map((b) => b.niche)),
    marketplaces: sorted(list.map((b) => b.marketplace)),
  };
}

// Today's new briefs worth a look (SPEC 14.10): the new ones that fit an account or a niche the advice
// suggests, in the engine's order, richest first, the first 5.
export function worthALook(list: Brief[], niches: string[]): Brief[] {
  return list
    .filter(
      (b) =>
        b.status === "new" &&
        (b.fits.length > 0 || (b.niche !== null && niches.includes(b.niche))),
    )
    .slice(0, LOOK_MAX);
}

// Why a niche is worth an account: its live briefs, their budget left and what it could earn a day, all in
// US$, and whether that is a guess or some of it was taken as if it were US$.
export function adviceFacts(
  s: Pick<Suggestion, "count" | "budget_left" | "per_day" | "guess" | "as_if">,
): string {
  const parts = [
    `${s.count} live brief${s.count === 1 ? "" : "s"}`,
    s.budget_left === null ? null : `${whole(s.budget_left, "USD")} left`,
    `about ${money(s.per_day, "USD")} a day`,
  ].filter(Boolean);
  const notes = [
    s.guess ? "a guess" : null,
    s.as_if ? "as if US$" : null,
  ].filter(Boolean);
  return parts.join(", ") + (notes.length ? ` (${notes.join(", ")})` : "");
}

// "Make a TikTok and an Instagram account", or null when you have an account for the niche already.
export function makeLine(make: Platform[]): string | null {
  if (make.length === 0) return null;
  const named = make.map(
    (p) => `${/^[AEIOU]/.test(PLATFORMS[p]) ? "an" : "a"} ${PLATFORMS[p]}`,
  );
  return `Make ${named.join(" and ")} account`;
}

// What Set it up waits on: an account tagged with the niche, by its first two tags.
export function tagHint(tags: string[]): string {
  const named = tags.length ? tags.slice(0, 2).join(" or ") : "this niche";
  return `Tag an account with ${named} on Accounts to set it up.`;
}

export function readerLine(
  s: Pick<BriefSource, "reader" | "list_url">,
): string {
  if (s.reader === "campaign_scout")
    return "Read from Campaign Scout's file on this Mac.";
  if (s.reader === "paste")
    return "Paste only: you paste its briefs on the Briefs tab.";
  return `Read from its public list page, ${s.list_url}.`;
}

const SCOUT_PROBLEMS: Record<string, string> = {
  missing: "Campaign Scout's file is missing.",
  reshaped: "Campaign Scout's file has changed shape.",
};

// When a source was last read and, when that didn't go through, why; null for one you paste from.
export function resultLine(
  s: Pick<BriefSource, "reader" | "last_run_at" | "last_result" | "error">,
  now: number,
): string | null {
  if (s.reader === "paste") return null;
  if (s.last_run_at === null) return "Not read yet.";
  const ago = formatAgo(s.last_run_at, now);
  if (s.last_result === "ok") return `Read ${ago}.`;
  const why =
    s.reader === "campaign_scout" ? SCOUT_PROBLEMS[s.error ?? ""] : s.error;
  return why ? `Couldn't be read ${ago}. ${why}` : `Couldn't be read ${ago}.`;
}

// When Check now opens again: in whole minutes rounded up within the hour, else the time and day (after a
// block it waits for the next morning); null when it is open now.
export function checkAgain(after: number | null, now: number): string | null {
  if (after === null || after <= now) return null;
  if (after - now > 3600)
    return `Check now opens again at ${timeThenDay(after, now)}.`;
  return `Check now opens again in ${Math.ceil((after - now) / 60)} min.`;
}
