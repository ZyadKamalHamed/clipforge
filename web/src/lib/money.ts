// The Money page's sums (SPEC 14.12), worked from GET /api/money's rows: the period, the totals a line a
// currency, the timeline and the other comparisons in US$ through the rates in Settings, the accounts, the
// best posts, the posts sheet's filters and sort, and Export CSV. Dates are calendar days ("2026-11-03" on
// the posting time zone's clock), worked through UTC dates so this browser's own clock never moves them.

import type {
  MoneyAccount,
  MoneyPost,
  Platform,
  SubmissionStatus,
} from "../types.ts";
import { PLATFORMS } from "./accounts.ts";
import {
  count,
  dayLabel,
  fold,
  money,
  NAMES,
  type SortDir,
} from "./campaigns.ts";

export type Period = "7d" | "30d" | "month" | "all";
export const PERIODS: { value: Period; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "month", label: "This month" },
  { value: "all", label: "All time" },
];
export const DEFAULT_PERIOD: Period = "30d";

const DAY_MS = 86_400_000;
const dateOf = (day: string) => new Date(`${day}T00:00:00Z`);
const dayOf = (date: Date) => date.toISOString().slice(0, 10);
const addDays = (day: string, n: number) =>
  dayOf(new Date(dateOf(day).getTime() + n * DAY_MS));
const daysBetween = (from: string, to: string) =>
  Math.round((dateOf(to).getTime() - dateOf(from).getTime()) / DAY_MS);
const mondayOf = (day: string) =>
  addDays(day, -((dateOf(day).getUTCDay() + 6) % 7));

// The period's first day: today and the 6 or 29 before it, the 1st of this month, or the first post's day.
export function periodStart(
  period: Period,
  today: string,
  posts: MoneyPost[],
): string {
  if (period === "7d") return addDays(today, -6);
  if (period === "30d") return addDays(today, -29);
  if (period === "month") return `${today.slice(0, 8)}01`;
  return posts.reduce(
    (first, p) => (p.posted_on < first ? p.posted_on : first),
    today,
  );
}

export function inPeriod(
  posts: MoneyPost[],
  start: string,
  today: string,
): MoneyPost[] {
  return posts.filter((p) => p.posted_on >= start && p.posted_on <= today);
}

export type Amount = { currency: string; amount: number };
export type CurrencyLine = {
  currency: string;
  paid: number;
  pending: number | null;
  estimated: number | null;
  views: number | null;
  posts: number;
};

const cents = (n: number) => Math.round(n * 100) / 100;
const plus = (total: number | null, n: number | null) =>
  n === null ? total : (total ?? 0) + n;
const byName = (a: string, b: string) =>
  a.localeCompare(b, "en-AU", { sensitivity: "base" });

// The period's totals, a line a currency (by its code), never added across currencies.
export function totals(posts: MoneyPost[]): CurrencyLine[] {
  const lines = new Map<string, CurrencyLine>();
  for (const p of posts) {
    const line = lines.get(p.currency) ?? {
      currency: p.currency,
      paid: 0,
      pending: null,
      estimated: null,
      views: null,
      posts: 0,
    };
    line.paid += p.paid ?? 0;
    line.pending = plus(line.pending, p.pending);
    line.estimated = plus(line.estimated, p.estimated);
    line.views = plus(line.views, p.views);
    line.posts += 1;
    lines.set(p.currency, line);
  }
  return [...lines.values()]
    .sort((a, b) => a.currency.localeCompare(b.currency))
    .map((line) => ({
      ...line,
      paid: cents(line.paid),
      pending: line.pending === null ? null : cents(line.pending),
      estimated: line.estimated === null ? null : cents(line.estimated),
    }));
}

// An amount in US$ through Settings' rate for its currency (1 when it has none, said by asIfNote).
export function usd(
  post: MoneyPost,
  field: "estimated" | "pending" | "paid",
): number {
  return (post[field] ?? 0) * post.rate;
}

// What a post brought in, in US$: what you were paid, or else ClipForge's estimate.
export function earned(post: MoneyPost): number {
  return post.paid !== null ? usd(post, "paid") : usd(post, "estimated");
}

export type Split = "marketplace" | "account";
export type Series = { key: string; name: string };
export type Part = Series & { paid: number; estimated: number };
export type Bar = {
  start: string;
  label: string;
  total: number;
  parts: Part[];
};
// ranked: every key that earned in the period, the biggest earner first, before the Others cut.
export type Timeline = {
  step: "day" | "week";
  bars: Bar[];
  series: Series[];
  ranked: string[];
};

const NAMED_SERIES = 5;
// Folded names and account ids are lower case, so this key never meets a marketplace called "others".
const OTHERS: Series = { key: "Others", name: "Others" };
export const REMOVED = "A removed account";

// The period's earnings in US$, a bar a day, or a week from Monday once the period is over 60 days long,
// with every bar in the range there even when empty. Each bar is split by marketplace or account: the 5
// that earned the most keep their names and the rest share Others, unless there are only 6.
export function timeline(
  posts: MoneyPost[],
  start: string,
  today: string,
  split: Split,
  accounts: MoneyAccount[],
): Timeline {
  const step = daysBetween(start, today) > 60 ? "week" : "day";
  const barOf = (day: string) => (step === "day" ? day : mondayOf(day));
  const handles = new Map(accounts.map((a) => [a.id, a.handle]));
  const keyOf = (p: MoneyPost) =>
    split === "marketplace" ? fold(p.marketplace) : p.account_id;
  const nameOf = (p: MoneyPost) =>
    split === "marketplace"
      ? p.marketplace
      : (handles.get(p.account_id) ?? p.handle ?? REMOVED);

  const counted = inPeriod(posts, start, today).filter((p) => earned(p) > 0);
  const worth = new Map<string, Series & { earned: number }>();
  for (const p of counted) {
    const key = keyOf(p);
    const line = worth.get(key) ?? { key, name: nameOf(p), earned: 0 };
    line.earned += earned(p);
    worth.set(key, line);
  }
  const ranked = [...worth.values()].sort(
    (a, b) => b.earned - a.earned || byName(a.name, b.name),
  );
  const named =
    ranked.length <= NAMED_SERIES + 1 ? ranked : ranked.slice(0, NAMED_SERIES);
  const series: Series[] = named.map(({ key, name }) => ({ key, name }));
  if (named.length < ranked.length) series.push(OTHERS);
  const kept = new Set(series.map((s) => s.key));

  const bars: Bar[] = [];
  const raw = new Map<
    string,
    { bar: Bar; paid: number[]; estimated: number[] }
  >();
  for (
    let day = barOf(start);
    day <= today;
    day = addDays(day, step === "day" ? 1 : 7)
  ) {
    const label = step === "day" ? dayLabel(day) : `Week of ${dayLabel(day)}`;
    const bar: Bar = { start: day, label, total: 0, parts: [] };
    bars.push(bar);
    raw.set(day, {
      bar,
      paid: series.map(() => 0),
      estimated: series.map(() => 0),
    });
  }
  for (const p of counted) {
    const sums = raw.get(barOf(p.posted_on));
    if (!sums) continue;
    const key = kept.has(keyOf(p)) ? keyOf(p) : OTHERS.key;
    const i = series.findIndex((s) => s.key === key);
    if (p.paid !== null) sums.paid[i] += usd(p, "paid");
    else sums.estimated[i] += usd(p, "estimated");
  }
  for (const { bar, paid, estimated } of raw.values()) {
    bar.parts = series.map((s, i) => ({
      ...s,
      paid: cents(paid[i]),
      estimated: cents(estimated[i]),
    }));
    bar.total = cents(
      paid.reduce((a, n) => a + n, 0) + estimated.reduce((a, n) => a + n, 0),
    );
  }
  return { step, bars, series, ranked: ranked.map((s) => s.key) };
}

// "US$12.40 estimated" and "US$5.00 paid", leaving out what is nothing.
export function earnings(estimated: number, paid: number): string[] {
  const said: string[] = [];
  if (cents(estimated)) said.push(`${money(estimated, "USD")} estimated`);
  if (cents(paid)) said.push(`${money(paid, "USD")} paid`);
  return said;
}

// What a bar says to a screen reader and in its tooltip: the day's figures, then each part's, like
// "3 Nov: US$12.40 estimated, US$5.00 paid; Vyro US$10.00 estimated".
export function barText(bar: Bar): string {
  const estimated = bar.parts.reduce((sum, p) => sum + p.estimated, 0);
  const paid = bar.parts.reduce((sum, p) => sum + p.paid, 0);
  const overall = earnings(estimated, paid);
  if (overall.length === 0) return `${bar.label}: nothing earned`;
  const parts = bar.parts
    .map((p) => ({ name: p.name, said: earnings(p.estimated, p.paid) }))
    .filter((p) => p.said.length > 0)
    .map((p) => `${p.name} ${p.said.join(" and ")}`);
  return `${bar.label}: ${overall.join(", ")}; ${parts.join(", ")}`;
}

export const SLOTS = 6;

// The colour slot each series shown takes, so a colour follows its marketplace or account and not the
// period: its place in the whole history (ranked) round the six slots. When two shown share one, the
// bigger earner of all time keeps it and the other takes the next free one. Others gets none, as it is
// drawn grey.
export function seriesSlots(
  shown: Series[],
  ranked: string[],
): Record<string, number> {
  const place = new Map(ranked.map((key, i) => [key, i]));
  const placeOf = (s: Series) => place.get(s.key) ?? ranked.length;
  const named = shown
    .filter((s) => s.key !== OTHERS.key)
    .sort((a, b) => placeOf(a) - placeOf(b));
  const slots: Record<string, number> = {};
  const taken = new Set<number>();
  for (const s of named) {
    let slot = placeOf(s) % SLOTS;
    while (taken.has(slot) && taken.size < SLOTS) slot = (slot + 1) % SLOTS;
    slots[s.key] = slot;
    taken.add(slot);
  }
  return slots;
}

export type AccountRow = {
  account: MoneyAccount;
  posts: number;
  views: number | null;
  median7: number | null;
  estimated: Amount[];
  pending: Amount[];
  paid: Amount[];
  best: MoneyPost | null;
};

function amounts(
  posts: MoneyPost[],
  field: "estimated" | "pending" | "paid",
): Amount[] {
  const sums = new Map<string, number>();
  for (const p of posts) {
    const n = p[field];
    if (n !== null) sums.set(p.currency, (sums.get(p.currency) ?? 0) + n);
  }
  return [...sums]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, amount]) => ({ currency, amount: cents(amount) }));
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

const handleOf = (a: MoneyAccount) => a.handle ?? "";

// Every account, the ones with posts in the period first by what they earned (US$), then the rest by handle.
// An account the rows name that the list lacks is added from its posts.
export function accountRows(
  accounts: MoneyAccount[],
  posts: MoneyPost[],
): AccountRow[] {
  const all = new Map(accounts.map((a) => [a.id, a]));
  const mine = new Map<string, MoneyPost[]>();
  for (const p of posts) {
    if (!all.has(p.account_id))
      all.set(p.account_id, {
        id: p.account_id,
        platform: p.platform,
        handle: p.handle,
        niche: p.niche,
      });
    mine.set(p.account_id, [...(mine.get(p.account_id) ?? []), p]);
  }
  const worth = (a: MoneyAccount) =>
    (mine.get(a.id) ?? []).reduce((sum, p) => sum + earned(p), 0);
  const order = [...all.values()].sort((a, b) => {
    const had = Number(mine.has(b.id)) - Number(mine.has(a.id));
    return had || worth(b) - worth(a) || byName(handleOf(a), handleOf(b));
  });
  return order.map((account) => {
    const list = mine.get(account.id) ?? [];
    return {
      account,
      posts: list.length,
      views: list.reduce<number | null>((sum, p) => plus(sum, p.views), null),
      median7: median(
        list.flatMap((p) => (p.views_7d === null ? [] : [p.views_7d])),
      ),
      estimated: amounts(list, "estimated"),
      pending: amounts(list, "pending"),
      paid: amounts(list, "paid"),
      best: best(list, "earned", 1)[0] ?? best(list, "views", 1)[0] ?? null,
    };
  });
}

// Several lists of amounts added up a currency at a time, by its code.
export function addAmounts(lists: Amount[][]): Amount[] {
  const sums = new Map<string, number>();
  for (const a of lists.flat())
    sums.set(a.currency, (sums.get(a.currency) ?? 0) + a.amount);
  return [...sums]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, amount]) => ({ currency, amount: cents(amount) }));
}

// "US$5.00 + A$3.00", each currency on its own, or "None".
export function amountText(list: Amount[]): string {
  return list.map((a) => money(a.amount, a.currency)).join(" + ") || "None";
}

// The n posts with the most views (counted ones only) or that earned the most (in US$, more than nothing);
// a tie goes to more views, then the newer post.
export function best(
  posts: MoneyPost[],
  by: "views" | "earned",
  n = 5,
): MoneyPost[] {
  const score = (p: MoneyPost) => (by === "views" ? (p.views ?? 0) : earned(p));
  return posts
    .filter((p) => (by === "views" ? p.views !== null : earned(p) > 0))
    .sort(
      (a, b) =>
        score(b) - score(a) ||
        (b.views ?? -1) - (a.views ?? -1) ||
        b.posted_on.localeCompare(a.posted_on),
    )
    .slice(0, n);
}

export type Group = {
  name: string;
  posts: number;
  views: number | null;
  estimated: number;
  paid: number;
};
export const NO_NICHE = "No niche";

function groups(
  posts: MoneyPost[],
  keyOf: (p: MoneyPost) => string,
  nameOf: (p: MoneyPost) => string,
): Group[] {
  const lines = new Map<string, Group>();
  for (const p of posts) {
    const key = keyOf(p);
    const line = lines.get(key) ?? {
      name: nameOf(p),
      posts: 0,
      views: null,
      estimated: 0,
      paid: 0,
    };
    line.posts += 1;
    line.views = plus(line.views, p.views);
    line.estimated += usd(p, "estimated");
    line.paid += usd(p, "paid");
    lines.set(key, line);
  }
  return [...lines.values()]
    .sort(
      (a, b) =>
        b.estimated + b.paid - (a.estimated + a.paid) || byName(a.name, b.name),
    )
    .map((line) => ({
      ...line,
      estimated: cents(line.estimated),
      paid: cents(line.paid),
    }));
}

// Posts, views, estimated and paid in US$ by platform, and by each account's first niche tag.
export function byPlatform(posts: MoneyPost[]): Group[] {
  return groups(
    posts,
    (p) => p.platform,
    (p) => PLATFORMS[p.platform],
  );
}

export function byNiche(posts: MoneyPost[]): Group[] {
  return groups(
    posts,
    (p) => (p.niche ? fold(p.niche) : ""),
    (p) => p.niche || NO_NICHE,
  );
}

export type PostFilter = {
  from: string | null;
  to: string | null;
  account: string | null;
  platform: Platform | null;
  campaign: string | null;
  marketplace: string | null;
  status: SubmissionStatus | null;
  search: string;
};
export const NO_POST_FILTER: PostFilter = {
  from: null,
  to: null,
  account: null,
  platform: null,
  campaign: null,
  marketplace: null,
  status: null,
  search: "",
};

// The posts the filters keep, in the order given; the search looks in the hook, the campaign, the
// marketplace and the handle, as the Campaigns sheet's does. An empty date field is no limit.
export function filterPosts(posts: MoneyPost[], f: PostFilter): MoneyPost[] {
  const words = fold(f.search.trim());
  const market = f.marketplace === null ? null : fold(f.marketplace);
  return posts.filter((p) => {
    if (f.from && p.posted_on < f.from) return false;
    if (f.to && p.posted_on > f.to) return false;
    if (f.account !== null && p.account_id !== f.account) return false;
    if (f.platform !== null && p.platform !== f.platform) return false;
    if (f.campaign !== null && p.campaign_id !== f.campaign) return false;
    if (market !== null && fold(p.marketplace) !== market) return false;
    if (f.status !== null && p.status !== f.status) return false;
    const texts = [p.hook, p.campaign, p.marketplace, p.handle ?? ""];
    return !words || texts.some((text) => fold(text).includes(words));
  });
}

export type PostKey =
  | "posted"
  | "account"
  | "platform"
  | "campaign"
  | "hook"
  | "views_2d"
  | "views_7d"
  | "views"
  | "status"
  | "estimated"
  | "paid";

const STEPS = Object.keys(NAMES) as SubmissionStatus[]; // the order a submission moves through

function postValue(p: MoneyPost, key: PostKey): string | number | null {
  switch (key) {
    case "posted":
      return p.posted_on;
    case "account":
      return p.handle;
    case "platform":
      return PLATFORMS[p.platform];
    case "campaign":
      return p.campaign;
    case "hook":
      return p.hook;
    case "status":
      return STEPS.indexOf(p.status);
    case "estimated":
    case "paid":
      return p[key] === null ? null : usd(p, key);
    default:
      return p[key];
  }
}

// A new list sorted by one column, amounts compared in US$, a post without the value last either way, ties
// in the order given; newest posted first unless told otherwise.
export function sortPosts(
  posts: MoneyPost[],
  key: PostKey = "posted",
  dir: SortDir = "desc",
): MoneyPost[] {
  const sign = dir === "asc" ? 1 : -1;
  return posts
    .map((post, i) => ({ post, i, value: postValue(post, key) }))
    .sort((a, b) => {
      if (a.value === null || b.value === null)
        return a.value === b.value ? a.i - b.i : a.value === null ? 1 : -1;
      const order =
        typeof a.value === "string"
          ? byName(a.value, String(b.value))
          : a.value - Number(b.value);
      return order * sign || a.i - b.i;
    })
    .map(({ post }) => post);
}

export const ROWS_SHOWN = 500;

// The first 500 rows for the page, and a note saying Export CSV still saves every one.
export function firstRows<T>(rows: T[]): {
  rows: T[];
  more: number;
  note: string | null;
} {
  const more = Math.max(0, rows.length - ROWS_SHOWN);
  const all = count(rows.length);
  return {
    rows: rows.slice(0, ROWS_SHOWN),
    more,
    note: more
      ? `Showing the first ${count(ROWS_SHOWN)} of ${all} posts. Export CSV saves all ${all}.`
      : null,
  };
}

type Cell = string | number | null;
export const CSV_COLUMNS: {
  label: string;
  value: (p: MoneyPost) => Cell;
  cents?: true;
}[] = [
  { label: "Posted", value: (p) => p.posted_on },
  { label: "Account", value: (p) => p.handle },
  { label: "Platform", value: (p) => PLATFORMS[p.platform] },
  { label: "Campaign", value: (p) => p.campaign },
  { label: "Marketplace", value: (p) => p.marketplace },
  { label: "Hook", value: (p) => p.hook },
  { label: "Views at 2 days", value: (p) => p.views_2d },
  { label: "Views at 7 days", value: (p) => p.views_7d },
  { label: "Views", value: (p) => p.views },
  { label: "Status", value: (p) => NAMES[p.status] },
  { label: "Reason", value: (p) => p.reason },
  { label: "Currency", value: (p) => p.currency },
  { label: "Estimated", value: (p) => p.estimated, cents: true },
  { label: "Paid", value: (p) => p.paid, cents: true },
];

const FORMULA = /^[=+\-@\t\r]/; // a spreadsheet would run text starting with these
const QUOTED = /[",\r\n]/;

function csvCell(value: Cell, twoPlaces = false): string {
  if (value === null) return "";
  if (typeof value === "number")
    return twoPlaces ? value.toFixed(2) : String(value);
  const text = FORMULA.test(value) ? `'${value}` : value;
  return QUOTED.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

// The rows as a CSV file: a byte order mark first so a spreadsheet reads it as UTF-8, a header, CRLF after
// every row, quotes where a cell needs them, and text a spreadsheet would run as a formula made plain text.
export function toCsv(posts: MoneyPost[]): string {
  const rows = [
    CSV_COLUMNS.map((c) => csvCell(c.label)),
    ...posts.map((p) => CSV_COLUMNS.map((c) => csvCell(c.value(p), c.cents))),
  ];
  return `\uFEFF${rows.map((row) => `${row.join(",")}\r\n`).join("")}`;
}

export function csvName(today: string): string {
  return `clipforge-money-${today}.csv`;
}

// What the page says when a currency has no rate in Settings, so its amounts count as if in US$.
export function asIfNote(currencies: string[]): string | null {
  if (currencies.length === 0) return null;
  if (currencies.length === 1)
    return `No exchange rate for ${currencies[0]}, so it counts as if in US$. Add one in Settings.`;
  const names = `${currencies.slice(0, -1).join(", ")} and ${currencies[currencies.length - 1]}`;
  return `No exchange rate for ${names}, so they count as if in US$. Add them in Settings.`;
}
