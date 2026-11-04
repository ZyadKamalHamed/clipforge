// What the Today page says (SPEC 14.10 items 1, 2 and 4 to 8): the minutes left out of 15, what each item on
// the list could earn, protect or unblock, about how long it takes and where its action is done, what putting
// a post up by hand and submitting a live one copy, open and say, each campaign's rules and each account's
// posts today in short, and what yesterday's best posts gained.

import type {
  AccountToday,
  Guideline,
  SubmitEntry,
  TodoItem,
  TodoKind,
} from "../types.ts";
import { PLATFORMS } from "./accounts.ts";
import { SCOUT } from "./briefs.ts";
import { BANNED, count, dayLabel, money } from "./campaigns.ts";
import { stepLink } from "./path.ts";
import { dayAndTime, hashtagText } from "./plan.ts";

const BUDGET_MIN = 15;

// "12 of 15 minutes left" by the time your timed items took (a minute begun counts), and what the rest of
// the list needs.
export function budgetLine(
  used_s: number,
  left_s: number,
): { big: string; small: string } {
  const big = `${Math.max(0, BUDGET_MIN - Math.ceil(used_s / 60))} of ${BUDGET_MIN} minutes left`;
  if (used_s >= BUDGET_MIN * 60)
    return {
      big,
      small: "You're past 15 minutes; the optional items can wait.",
    };
  if (left_s <= 0) return { big, small: "Nothing left on the list." };
  if (left_s < 60)
    return { big, small: "The rest of the list needs under a minute." };
  const n = Math.round(left_s / 60);
  return {
    big,
    small: `The rest of the list needs about ${n} ${n === 1 ? "minute" : "minutes"}.`,
  };
}

const AT_STAKE: TodoKind[] = ["decide", "ends", "payout"];
const UNBLOCKS: TodoKind[] = ["needs_you", "login"];

// "Could earn US$4.50", "US$62.40 at stake", "Unblocks US$3.00" or, for a step of the path, "Opens about
// US$5.00 a day", by what the item's money is, and whether it is a guess or was compared as if it were US$;
// empty when it has none.
export function valueLabel(item: TodoItem): string {
  if (item.value <= 0) return "";
  const amount = money(item.value, item.currency);
  const text = AT_STAKE.includes(item.kind)
    ? `${amount} at stake`
    : UNBLOCKS.includes(item.kind)
      ? `Unblocks ${amount}`
      : item.kind === "path"
        ? `Opens about ${amount} a day`
        : `Could earn ${amount}`;
  const notes = [
    item.guess ? "a guess" : null,
    item.as_if_usd ? "as if US$" : null,
  ].filter(Boolean);
  return notes.length ? `${text} (${notes.join(", ")})` : text;
}

// "about 40 s" under a minute, "about 3 min" from one on.
export function estimateLabel(seconds: number): string {
  return seconds < 60
    ? `about ${Math.round(seconds)} s`
    : `about ${Math.round(seconds / 60)} min`;
}

// The item's one button and where it goes: a block of this page by its anchor, or the page its action is on.
// A join has none: its row draws Open and I've joined itself. A step of the path goes to its page from
// ``links`` (the path's steps by id), else to the path on this page.
export function itemAction(
  item: TodoItem,
  links: Record<string, string | null> = {},
): { label: string; to: string } | null {
  switch (item.kind) {
    case "drafts":
      return { label: "Review", to: "#review" };
    case "submit":
      return { label: "Submit", to: "#submit" };
    case "views":
      return { label: "Enter views", to: "#submit" };
    case "decide":
      return { label: "Record", to: "#submit" };
    case "ends":
      return {
        label: "Look",
        to: `/campaigns/${encodeURIComponent(item.ref)}`,
      };
    case "payout":
      return { label: "Withdraw", to: "#money" };
    case "exported":
      return { label: "Put up", to: "#post" };
    case "needs_you":
      return { label: "Open the Queue", to: "/queue" };
    case "login":
    case "insights":
      return { label: "Open Accounts", to: "/accounts" };
    case "briefs":
      return { label: "Look", to: "#briefs" };
    case "source":
      // Campaign Scout's file is fixed on Settings; a site that couldn't be read is pasted from on Today
      return item.ref === SCOUT
        ? { label: "Open Settings", to: "/settings" }
        : { label: "Paste briefs", to: `#paste-${item.ref}` };
    case "join":
      return null; // Open and I've joined, drawn by the row itself
    case "path": {
      const to = links[item.ref];
      return to ? stepLink(to) : { label: "Start", to: "#path" };
    }
  }
}

// A live post's one button on Submit: the link it copies and the stored link id it opens, as far as it has
// them, and what to do next; null when it has neither.
export function submitAction(
  entry: Pick<SubmitEntry, "url" | "submit_link" | "marketplace">,
): {
  label: string;
  copy: string | null;
  open: string | null;
  done: string;
} | null {
  const { url, submit_link: open, marketplace } = entry;
  if (url && open) {
    return {
      label: `Copy link and open ${marketplace}`,
      copy: url,
      open,
      done: copiedLine(marketplace),
    };
  }
  if (url)
    return {
      label: "Copy link",
      copy: url,
      open: null,
      done: copiedLine(marketplace),
    };
  if (open) {
    return {
      label: `Open ${marketplace}`,
      copy: null,
      open,
      done: `Paste the post's link on ${marketplace}, then mark it submitted.`,
    };
  }
  return null;
}

const copiedLine = (marketplace: string) =>
  `Link copied. Paste it on ${marketplace}, then mark it submitted.`;

// What Open in TikTok (or Instagram) says once the upload page is open.
export function putUpLine(hasFile: boolean, copied: boolean): string {
  const caption = copied
    ? "Caption copied."
    : "Copy the caption below with Cmd+C.";
  return `${caption} ${hasFile ? "The clip is shown in Finder." : "The clip isn't in its export folder any more."}`;
}

// A post TikTok Studio holds to put up at its slot.
export function scheduledLine(at: number): string {
  return `Scheduled on TikTok for ${dayAndTime(at)}`;
}

// What a post gained by its newest count: "+1,200 views in a day".
export function gainLine(gained: number): string {
  if (gained <= 0) return "No new views in a day";
  return `+${count(gained)} ${gained === 1 ? "view" : "views"} in a day`;
}

// What a campaign pays, in short: its rate, the least views a clip needs and the most it earns, those it has.
export function payLine(
  g: Pick<Guideline, "rate" | "minimum" | "cap" | "currency">,
): string {
  const line = [
    g.rate === null ? null : `${money(g.rate, g.currency)} per 1,000 views`,
    g.minimum === null ? null : `at least ${count(g.minimum)} views`,
    g.cap === null ? null : `up to ${money(g.cap, g.currency)} a clip`,
  ]
    .filter(Boolean)
    .join(", ");
  return line ? line[0].toUpperCase() + line.slice(1) : "No rate entered";
}

// The rest of a campaign's card, a line each, those it has: what a caption must carry, what a clip mustn't
// have, where it goes, when it ends and its budget left as you last entered it.
export function ruleLines(g: Guideline): string[] {
  const banned = BANNED.filter((b) => g.banned.includes(b.value)).map((b) =>
    b.label.toLowerCase(),
  );
  const budget =
    g.budget_left === null
      ? null
      : `${money(g.budget_left, g.currency)} left${g.budget_entered_on ? ` on ${dayLabel(g.budget_entered_on)}` : ""}`;
  const lines = [
    g.hashtags.length ? `Tags: ${hashtagText(g.hashtags)}` : null,
    g.mentions.length ? `Mentions: ${g.mentions.join(" ")}` : null,
    g.text.length ? `Must say: ${g.text.join("; ")}` : null,
    banned.length ? `Not allowed: ${banned.join(", ")}` : null,
    g.platforms.length === 1 ? `${PLATFORMS[g.platforms[0]]} only` : null,
    g.platforms.length > 1
      ? g.platforms.map((p) => PLATFORMS[p]).join(" and ")
      : null,
    g.ends_on ? `Ends ${dayLabel(g.ends_on)}` : null,
    budget,
  ];
  return lines.filter((line): line is string => line !== null);
}

// An account's posts today against its cap: "@t0 on TikTok: 1 of 2".
export function accountLine(a: AccountToday): string {
  return `${a.handle || "An account"} on ${PLATFORMS[a.platform]}: ${a.today.posts} of ${a.today.cap}`;
}
