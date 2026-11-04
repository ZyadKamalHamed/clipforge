// The Campaigns sheet and a campaign's page (SPEC 14.11 and 10.10): how the rows sort, the triangle on a
// row with something waiting for you today and where each of its lines goes, the totals a currency, money as
// each currency writes it, the six counts from made to paid, the names of a submission's moves, and the
// campaign form's fields as text to edit and back.

import type {
  Banned,
  Campaign,
  CampaignCounts,
  CampaignFields,
  Platform,
  Submission,
  SubmissionStatus,
} from "../types.ts";
import { hashtagText, MONTHS, parseHashtags } from "./plan.ts";

export const EMPTY = "No campaigns yet. Paste a campaign's rules to make one.";
export const ESTIMATES = "Estimates from your entries";
export const CHECK_THIS = "Check this";
export const TICK_FIRST = "Tick I've checked the filled fields first.";
export const AMOUNT_NEEDED = "Enter the amount paid.";
export const VIEWS_NEEDED = "Enter the views as a whole number.";

export type SortKey =
  | "needs"
  | "marketplace"
  | "name"
  | "rate"
  | "budget"
  | "ends"
  | "channels"
  | "progress"
  | "views"
  | "estimated"
  | "pending"
  | "paid";
export type SortDir = "asc" | "desc";
export type Sort = { key: SortKey; dir: SortDir };

const TEXT_KEYS = new Set<SortKey>(["marketplace", "name", "ends", "channels"]); // A to Z (soonest first) to start

// An amount in US$, so rows in different currencies sort by what they are worth.
const inUsd = (row: Campaign, amount: number | null) =>
  amount === null ? null : amount * row.usd_rate;

function sortValue(
  row: Campaign,
  key: Exclude<SortKey, "needs">,
): string | number | null {
  switch (key) {
    case "marketplace":
      return row.marketplace;
    case "name":
      return row.name;
    case "rate":
      return inUsd(row, row.rate_per_1000);
    case "budget":
      return inUsd(row, row.budget_left);
    case "ends":
      return row.ends_on;
    case "channels":
      return row.channels.map((c) => c.name).join(", ") || null;
    case "progress":
      return progressCounts(row).share;
    case "views":
      return row.money.views;
    case "estimated":
      return inUsd(row, row.money.estimated);
    case "pending":
      return inUsd(row, row.money.pending);
    case "paid":
      return inUsd(row, row.money.paid);
  }
}

// The rows in a new list, by ``key``: a row with nothing to sort by last either way, a tie in the order given,
// amounts by what they are worth in US$. By default the rows with a triangle come first, each group by
// estimated earnings, biggest first.
export function sortRows(
  rows: Campaign[],
  key: SortKey = "needs",
  dir: SortDir = "desc",
): Campaign[] {
  if (key === "needs") {
    const byMoney = sortRows(rows, "estimated", "desc");
    const waits = byMoney.filter(hasTriangle);
    const rest = byMoney.filter((row) => !hasTriangle(row));
    return dir === "desc" ? [...waits, ...rest] : [...rest, ...waits];
  }
  const flip = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: sortValue(row, key) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null)
        return a.v === b.v ? a.i - b.i : a.v === null ? 1 : -1;
      const order =
        typeof a.v === "string" && typeof b.v === "string"
          ? a.v.localeCompare(b.v, "en-AU", { sensitivity: "base" })
          : Number(a.v) - Number(b.v);
      return order * flip || a.i - b.i;
    })
    .map(({ row }) => row);
}

// A header pressed: the same one turns its order round; another starts A to Z for text, biggest first for numbers.
export function nextSort(current: Sort, key: SortKey): Sort {
  if (current.key === key)
    return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  return { key, dir: TEXT_KEYS.has(key) ? "asc" : "desc" };
}

const hasTriangle = (row: Pick<Campaign, "todo">) => row.todo.length > 0;

// How many rows have a triangle: the rail's count beside Campaigns.
export function triangleCount(rows: Pick<Campaign, "todo">[]): number {
  return rows.filter(hasTriangle).length;
}

// The triangle's name, read out: "Needs you: 3 things", a thing a line.
export function triangleName(n: number): string {
  return `Needs you: ${n} ${n === 1 ? "thing" : "things"}`;
}

// The rail's Campaigns link, read out with how many campaigns have a triangle.
export function campaignsLabel(n: number): string {
  if (n <= 0) return "Campaigns";
  return `Campaigns, ${n} ${n === 1 ? "needs" : "need"} you`;
}

export type TriangleLine = {
  title: string;
  label: string;
  to: string;
  join?: true;
}; // join: Open and I've joined

// Each thing a campaign waits on, in today's list's order, with its one button and where it goes: a block of
// Today by its anchor, the page its action is on, or the campaign's own page, where views and decisions go in.
export function triangleLines(
  row: Pick<Campaign, "id" | "todo">,
): TriangleLine[] {
  const page = `/campaigns/${encodeURIComponent(row.id)}`;
  const where: Record<
    Exclude<Campaign["todo"][number]["kind"], "briefs" | "source" | "path">,
    { label: string; to: string }
  > = {
    drafts: { label: "Review drafts", to: "/#review" },
    needs_you: { label: "Open the Queue", to: "/queue" },
    login: { label: "Open Accounts", to: "/accounts" },
    insights: { label: "Open Accounts", to: "/accounts" },
    submit: { label: "Submit", to: "/#submit" },
    views: { label: "Enter views", to: page },
    decide: { label: "Record", to: page },
    payout: { label: "Withdraw", to: "/#money" },
    ends: { label: "Look", to: page },
    exported: { label: "Put up", to: "/#post" },
    join: { label: "Open", to: page },
  };
  // briefs, sources and the path's steps belong to no campaign, so they never reach a campaign's row
  return row.todo.flatMap((t) =>
    t.kind === "briefs" || t.kind === "source" || t.kind === "path"
      ? []
      : [
          {
            title: t.title,
            ...where[t.kind],
            ...(t.kind === "join" ? { join: true as const } : {}),
          },
        ],
  );
}

export type CurrencyTotal = {
  currency: string;
  views: number | null;
  estimated: number | null;
  pending: number | null;
  paid: number;
};

// The sheet's search and filters (14.11): none of them is kept in the address.
export type StateFilter = "all" | "active" | "ending" | "ended";
export type SheetFilter = {
  search: string;
  marketplace: string | null;
  state: StateFilter;
  needsMe: boolean;
};
export const NO_FILTER: SheetFilter = {
  search: "",
  marketplace: null,
  state: "all",
  needsMe: false,
};

// Whether any filter is on, so the sheet can offer Clear filters.
export function filtering(filter: SheetFilter): boolean {
  return (
    filter.search.trim() !== "" ||
    filter.marketplace !== null ||
    filter.state !== "all" ||
    filter.needsMe
  );
}

// Text as the searches compare it: full-width letters as plain ones, case ignored.
export const fold = (text: string) =>
  text.normalize("NFKC").toLocaleLowerCase("en-AU");

// The marketplaces the rows name, once each whatever its case, in the spelling seen first, A to Z.
export function marketplaceNames(rows: Campaign[]): string[] {
  const names = new Map<string, string>();
  for (const row of rows) {
    const key = fold(row.marketplace);
    if (!names.has(key)) names.set(key, row.marketplace);
  }
  return [...names.values()].sort((a, b) =>
    a.localeCompare(b, "en-AU", { sensitivity: "base" }),
  );
}

// The rows the filters keep, in the order given. The search looks in the name, the marketplace and the
// clipping channels' names; Active keeps the rows that are ending too; a marketplace no row has any more
// counts as all of them; Needs me keeps the rows with something on today's list.
export function filterRows(rows: Campaign[], filter: SheetFilter): Campaign[] {
  const words = fold(filter.search.trim());
  const market = filter.marketplace === null ? null : fold(filter.marketplace);
  const known =
    market !== null && rows.some((row) => fold(row.marketplace) === market);
  return rows.filter((row) => {
    const texts = [
      row.name,
      row.marketplace,
      ...row.channels.map((c) => c.name),
    ];
    if (words && !texts.some((text) => fold(text).includes(words)))
      return false;
    if (known && fold(row.marketplace) !== market) return false;
    if (filter.state === "active" && row.state === "ended") return false;
    if (
      (filter.state === "ending" || filter.state === "ended") &&
      row.state !== filter.state
    )
      return false;
    return !filter.needsMe || row.todo.length > 0;
  });
}

// The columns you can hide, in the sheet's order: the triangle and the name always show.
export const HIDEABLE: SortKey[] = [
  "marketplace",
  "rate",
  "budget",
  "ends",
  "channels",
  "progress",
  "views",
  "estimated",
  "pending",
  "paid",
];
export const COLUMNS_KEY = "clipforge.campaigns.hidden";

// The columns you hid last time, or none when the browser's storage is blocked, empty or not ours.
export function hiddenColumns(
  storage: Pick<Storage, "getItem"> | null,
): Set<SortKey> {
  try {
    const kept: unknown = JSON.parse(storage?.getItem(COLUMNS_KEY) ?? "[]");
    return new Set(
      Array.isArray(kept) ? HIDEABLE.filter((key) => kept.includes(key)) : [],
    );
  } catch {
    return new Set();
  }
}

// Keeps the hidden columns for next time; a full or blocked storage keeps them for this visit only.
export function keepHidden(
  storage: Pick<Storage, "setItem"> | null,
  hidden: ReadonlySet<SortKey>,
): void {
  try {
    storage?.setItem(
      COLUMNS_KEY,
      JSON.stringify(HIDEABLE.filter((key) => hidden.has(key))),
    );
  } catch {
    // nothing to do: the menu still shows what you chose
  }
}

const cents = (n: number) => Math.round(n * 100) / 100;
const plus = (total: number | null, n: number | null) =>
  n === null ? total : (total ?? 0) + n;

// The sheet's totals, a line a currency (by its code), never added across currencies.
export function totalsByCurrency(rows: Campaign[]): CurrencyTotal[] {
  const lines = new Map<string, CurrencyTotal>();
  for (const row of rows) {
    const line = lines.get(row.currency) ?? {
      currency: row.currency,
      views: null,
      estimated: null,
      pending: null,
      paid: 0,
    };
    line.views = plus(line.views, row.money.views);
    line.estimated = plus(line.estimated, row.money.estimated);
    line.pending = plus(line.pending, row.money.pending);
    line.paid += row.money.paid;
    lines.set(row.currency, line);
  }
  return [...lines.values()]
    .sort((a, b) => a.currency.localeCompare(b.currency))
    .map((line) => ({
      ...line,
      estimated: line.estimated === null ? null : cents(line.estimated),
      pending: line.pending === null ? null : cents(line.pending),
      paid: cents(line.paid),
    }));
}

const PREFIX: Record<string, string> = { USD: "US$", AUD: "A$" };

// "US$12.40", "A$3.00", or "12.40 EUR" for a currency without its own sign here.
export function money(amount: number, currency: string): string {
  const n = amount.toLocaleString("en-AU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const prefix = PREFIX[currency];
  return prefix ? `${prefix}${n}` : `${n} ${currency}`;
}

// A submission's estimate: none until its views are entered, and none without the campaign's rate.
export function estimateText(
  s: Pick<Submission, "estimate" | "views">,
  currency: string,
): string {
  if (s.estimate !== null) return money(s.estimate, currency);
  return s.views === null ? "No views yet" : "No rate";
}

export function count(n: number): string {
  return n.toLocaleString("en-AU");
}

export const PROGRESS: { key: keyof CampaignCounts; label: string }[] = [
  { key: "made", label: "made" },
  { key: "approved", label: "approved by you" },
  { key: "live", label: "live" },
  { key: "submitted", label: "submitted" },
  { key: "accepted", label: "accepted" },
  { key: "paid", label: "paid" },
];

export type Progress = {
  parts: { key: keyof CampaignCounts; label: string; n: number }[];
  share: number; // how far the clips made got along the five steps after made, 0 to 1
  sentence: string;
};

export function progressCounts(row: Campaign): Progress {
  const counts = row.money.counts;
  const parts = PROGRESS.map((p) => ({ ...p, n: counts[p.key] }));
  const along = parts.slice(1).reduce((sum, p) => sum + p.n, 0);
  const share = counts.made > 0 ? Math.min(1, along / (5 * counts.made)) : 0;
  return {
    parts,
    share,
    sentence: parts.map((p) => `${p.n} ${p.label}`).join(", "),
  };
}

// Whether a field Claude filled still needs your look: until you edit it.
export function filledMark(
  field: string,
  filled: string[],
  touched: Set<string>,
): string | null {
  return filled.includes(field) && !touched.has(field) ? CHECK_THIS : null;
}

export const NAMES: Record<SubmissionStatus, string> = {
  to_submit: "To submit",
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  paid: "Paid",
};

// The moves that undo one pressed by mistake (tracker.MOVES), said as going back.
const BACK = new Set([
  "submitted>to_submit",
  "approved>submitted",
  "rejected>submitted",
  "paid>approved",
]);

export function moveLabel(
  from: SubmissionStatus,
  to: SubmissionStatus,
): string {
  if (BACK.has(`${from}>${to}`)) return `Back to ${NAMES[to]}`;
  return to === "submitted" ? "Mark submitted" : NAMES[to];
}

// "3 Oct" for "2026-10-03", read as the day it names wherever the window is.
export function dayLabel(day: string | null): string {
  if (!day) return "";
  const [, month, date] = day.split("-").map(Number);
  return `${date} ${MONTHS[month - 1]}`;
}

const NUMBER = /^-?(\d+\.?\d*|\.\d+)$/;

// A number typed with or without its commas; null for nothing, and anything else as typed, so the engine
// says what the field needs.
export function amountValue(text: string): number | string | null {
  const bare = text.replace(/[,\s]/g, "");
  if (!bare) return null;
  return NUMBER.test(bare) ? Number(bare) : text.trim();
}

// A view count typed with or without its commas, or null when it isn't a whole number.
export function viewsValue(text: string): number | null {
  const bare = text.replace(/[,\s]/g, "");
  return /^\d+$/.test(bare) ? Number(bare) : null;
}

export const BANNED: { value: Banned; label: string }[] = [
  { value: "gameplay", label: "Gameplay split" },
  { value: "music", label: "Music" },
  { value: "sfx", label: "Sound effects" },
  { value: "emoji", label: "Emoji" },
  { value: "hook", label: "Hook title" },
  { value: "cards", label: "Fact cards" },
  { value: "progress_bar", label: "Progress bar" },
  { value: "punch_ins", label: "Punch-ins" },
  { value: "credit_line", label: "On-screen credit line" },
];

export const TEXT_FIELDS = [
  "name",
  "marketplace",
  "campaign_url",
  "submit_url",
  "currency",
  "rate_per_1000",
  "min_views",
  "max_payout",
  "budget_left",
  "ends_on",
  "footage_links",
  "footage.videos",
  "required.hashtags",
  "required.mentions",
  "required.text",
  "hand_rules",
  "min_s",
  "max_s",
  "notes",
] as const;
export type TextKey = (typeof TEXT_FIELDS)[number];

export type FormState = {
  text: Record<TextKey, string>;
  platforms: Platform[];
  banned: Banned[];
  files: boolean;
};

const shown = (value: number | string | null) =>
  value === null ? "" : String(value);

// The form's fields as text to edit: a list a line (hashtags and mentions on one line), a number as typed.
export function formState(fields: CampaignFields): FormState {
  return {
    text: {
      name: fields.name,
      marketplace: fields.marketplace,
      campaign_url: fields.campaign_url ?? "",
      submit_url: fields.submit_url ?? "",
      currency: fields.currency,
      rate_per_1000: shown(fields.rate_per_1000),
      min_views: shown(fields.min_views),
      max_payout: shown(fields.max_payout),
      budget_left: shown(fields.budget_left),
      ends_on: fields.ends_on ?? "",
      footage_links: fields.footage_links.join("\n"),
      "footage.videos": fields.footage.videos.join("\n"),
      "required.hashtags": hashtagText(fields.required.hashtags),
      "required.mentions": fields.required.mentions.join(" "),
      "required.text": fields.required.text.join("\n"),
      hand_rules: fields.hand_rules.join("\n"),
      min_s: shown(fields.min_s),
      max_s: shown(fields.max_s),
      notes: fields.notes,
    },
    platforms: fields.platforms,
    banned: fields.banned,
    files: fields.footage.files,
  };
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
const orNull = (text: string) => text.trim() || null;

// What Save sends: the fields back from their text, with the rules as pasted.
export function formBody(state: FormState, rulesText: string): CampaignFields {
  const t = state.text;
  return {
    name: t.name.trim(),
    marketplace: t.marketplace.trim(),
    campaign_url: orNull(t.campaign_url),
    submit_url: orNull(t.submit_url),
    currency: t.currency.trim().toUpperCase(),
    rate_per_1000: amountValue(t.rate_per_1000),
    min_views: amountValue(t.min_views),
    max_payout: amountValue(t.max_payout),
    platforms: state.platforms,
    footage: { videos: lines(t["footage.videos"]), files: state.files },
    footage_links: lines(t.footage_links),
    required: {
      hashtags: parseHashtags(t["required.hashtags"]),
      mentions: t["required.mentions"].split(/[\s,]+/).filter(Boolean),
      text: lines(t["required.text"]),
    },
    banned: state.banned,
    hand_rules: lines(t.hand_rules),
    min_s: amountValue(t.min_s),
    max_s: amountValue(t.max_s),
    ends_on: orNull(t.ends_on),
    budget_left: amountValue(t.budget_left),
    notes: t.notes.trim(),
    rules_text: rulesText,
  };
}

// A saved campaign as the form's fields, for Edit: its footage channels as the links they were found by.
export function fieldsOf(c: Campaign): CampaignFields {
  return {
    name: c.name,
    marketplace: c.marketplace,
    campaign_url: c.campaign_url,
    submit_url: c.submit_url,
    currency: c.currency,
    rate_per_1000: c.rate_per_1000,
    min_views: c.min_views,
    max_payout: c.max_payout,
    platforms: c.platforms,
    footage: { videos: c.footage.videos, files: c.footage.files },
    footage_links: c.footage.channels.map((ch) => ch.url),
    required: c.required,
    banned: c.banned,
    hand_rules: c.hand_rules,
    min_s: c.min_s,
    max_s: c.max_s,
    ends_on: c.ends_on,
    budget_left: c.budget_left,
    notes: c.notes,
    rules_text: c.rules_text,
  };
}

// A new campaign's form before anything is read into it (parse.blank).
export function blankFields(): CampaignFields {
  return {
    name: "",
    marketplace: "",
    campaign_url: null,
    submit_url: null,
    currency: "USD",
    rate_per_1000: null,
    min_views: null,
    max_payout: null,
    platforms: ["tiktok", "instagram"],
    footage: { videos: [], files: false },
    footage_links: [],
    required: { hashtags: [], mentions: [], text: [] },
    banned: [],
    hand_rules: [],
    min_s: null,
    max_s: null,
    ends_on: null,
    budget_left: null,
    notes: "",
    rules_text: "",
  };
}
