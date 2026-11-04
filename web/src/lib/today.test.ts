import assert from "node:assert/strict";
import { test } from "node:test";

import type { Guideline, TodoItem, TodoKind } from "../types.ts";
import {
  accountLine,
  budgetLine,
  estimateLabel,
  gainLine,
  itemAction,
  payLine,
  putUpLine,
  ruleLines,
  scheduledLine,
  submitAction,
  valueLabel,
} from "./today.ts";

function item(kind: TodoKind, fields: Partial<TodoItem> = {}): TodoItem {
  return {
    id: "td1",
    kind,
    ref: "c1",
    title: "An item",
    detail: null,
    estimate_s: 20,
    value: 4.5,
    currency: "USD",
    as_if_usd: false,
    guess: false,
    optional: false,
    due: false,
    carried_over: false,
    done: false,
    done_how: null,
    campaigns: {},
    ...fields,
  };
}

test("budgetLine says the minutes left out of 15 and what the rest needs", () => {
  assert.deepEqual(budgetLine(0, 240), {
    big: "15 of 15 minutes left",
    small: "The rest of the list needs about 4 minutes.",
  });
  assert.deepEqual(budgetLine(600, 30), {
    big: "5 of 15 minutes left",
    small: "The rest of the list needs under a minute.",
  });
  assert.deepEqual(budgetLine(960, 120), {
    big: "0 of 15 minutes left",
    small: "You're past 15 minutes; the optional items can wait.",
  });
  assert.equal(budgetLine(61, 0).big, "13 of 15 minutes left"); // a minute begun is a minute used
  assert.equal(budgetLine(61, 0).small, "Nothing left on the list.");
  assert.equal(
    budgetLine(0, 70).small,
    "The rest of the list needs about 1 minute.",
  );
});

test("valueLabel names what the item's money is", () => {
  for (const kind of ["drafts", "submit", "views", "exported"] as const) {
    assert.equal(valueLabel(item(kind)), "Could earn US$4.50", kind);
  }
  for (const kind of ["decide", "ends", "payout"] as const) {
    assert.equal(
      valueLabel(item(kind, { value: 62.4 })),
      "US$62.40 at stake",
      kind,
    );
  }
  for (const kind of ["needs_you", "login"] as const) {
    assert.equal(
      valueLabel(item(kind, { value: 3 })),
      "Unblocks US$3.00",
      kind,
    );
  }
  assert.equal(
    valueLabel(item("drafts", { guess: true })),
    "Could earn US$4.50 (a guess)",
  );
  assert.equal(
    valueLabel(item("drafts", { currency: "EUR", as_if_usd: true })),
    "Could earn 4.50 EUR (as if US$)",
  );
  assert.equal(
    valueLabel(
      item("submit", { currency: "AUD", guess: true, as_if_usd: true }),
    ),
    "Could earn A$4.50 (a guess, as if US$)",
  );
  assert.equal(valueLabel(item("drafts", { value: 0 })), "");
  assert.equal(valueLabel(item("insights", { value: 0 })), "");
});

test("estimateLabel says seconds, then minutes", () => {
  assert.equal(estimateLabel(40), "about 40 s");
  assert.equal(estimateLabel(59), "about 59 s");
  assert.equal(estimateLabel(60), "about 1 min");
  assert.equal(estimateLabel(150), "about 3 min");
});

test("itemAction goes where each kind's action is done, and join has its own buttons", () => {
  const actions: Record<
    Exclude<TodoKind, "join" | "path">,
    [string, string]
  > = {
    drafts: ["Review", "#review"],
    submit: ["Submit", "#submit"],
    views: ["Enter views", "#submit"],
    decide: ["Record", "#submit"],
    ends: ["Look", "/campaigns/c1"],
    payout: ["Withdraw", "#money"],
    exported: ["Put up", "#post"],
    needs_you: ["Open the Queue", "/queue"],
    login: ["Open Accounts", "/accounts"],
    insights: ["Open Accounts", "/accounts"],
    briefs: ["Look", "#briefs"],
    source: ["Open Settings", "/settings"],
  };
  for (const [kind, [label, to]] of Object.entries(actions)) {
    assert.deepEqual(
      itemAction(
        item(kind as TodoKind, kind === "source" ? { ref: "bs_scout" } : {}),
      ),
      { label, to },
      kind,
    );
  }
  assert.equal(itemAction(item("join")), null);
  assert.deepEqual(itemAction(item("source", { ref: "bs_vyro" })), {
    label: "Paste briefs",
    to: "#paste-bs_vyro",
  });
  assert.equal(
    itemAction(item("ends", { ref: "c 2/x" }))?.to,
    "/campaigns/c%202%2Fx",
  );
});

test("itemAction sends a step of the path to its page, else to the path on Today", () => {
  const links = { link_account: "/accounts", notify: null };
  assert.deepEqual(itemAction(item("path", { ref: "link_account" }), links), {
    label: "Open Accounts",
    to: "/accounts",
  });
  assert.deepEqual(itemAction(item("path", { ref: "notify" }), links), {
    label: "Start",
    to: "#path",
  });
  assert.deepEqual(itemAction(item("path", { ref: "gone" })), {
    label: "Start",
    to: "#path",
  });
});

test("valueLabel says a step opens what the account could earn a day", () => {
  assert.equal(
    valueLabel(item("path", { value: 5, guess: true })),
    "Opens about US$5.00 a day (a guess)",
  );
  assert.equal(valueLabel(item("path", { value: 0 })), "");
});

test("submitAction copies the live link and opens the marketplace's page, as far as it has them", () => {
  const live = "https://www.tiktok.com/@t0/video/1";
  assert.deepEqual(
    submitAction({ url: live, submit_link: "c1:submit", marketplace: "Whop" }),
    {
      label: "Copy link and open Whop",
      copy: live,
      open: "c1:submit",
      done: "Link copied. Paste it on Whop, then mark it submitted.",
    },
  );
  assert.deepEqual(
    submitAction({ url: live, submit_link: null, marketplace: "Vyro" }),
    {
      label: "Copy link",
      copy: live,
      open: null,
      done: "Link copied. Paste it on Vyro, then mark it submitted.",
    },
  );
  assert.deepEqual(
    submitAction({ url: null, submit_link: "c1:submit", marketplace: "Whop" }),
    {
      label: "Open Whop",
      copy: null,
      open: "c1:submit",
      done: "Paste the post's link on Whop, then mark it submitted.",
    },
  );
  assert.equal(
    submitAction({ url: null, submit_link: null, marketplace: "Whop" }),
    null,
  );
});

test("putUpLine says whether the caption is copied and where the clip is", () => {
  assert.equal(
    putUpLine(true, true),
    "Caption copied. The clip is shown in Finder.",
  );
  assert.equal(
    putUpLine(false, true),
    "Caption copied. The clip isn't in its export folder any more.",
  );
  assert.equal(
    putUpLine(true, false),
    "Copy the caption below with Cmd+C. The clip is shown in Finder.",
  );
});

test("scheduledLine says when TikTok posts it, in Sydney time", () => {
  assert.equal(
    scheduledLine(Date.UTC(2026, 10, 3, 7, 5) / 1000),
    "Scheduled on TikTok for Tue 3 Nov, 6:05 pm",
  );
  assert.equal(
    scheduledLine(Date.UTC(2026, 11, 31, 13, 0) / 1000),
    "Scheduled on TikTok for Fri 1 Jan, 12:00 am",
  );
});

test("gainLine says what a post gained in a day", () => {
  assert.equal(gainLine(1200), "+1,200 views in a day");
  assert.equal(gainLine(1), "+1 view in a day");
  assert.equal(gainLine(0), "No new views in a day");
});

const RULES: Guideline = {
  id: "c1",
  name: "Beast clips",
  marketplace: "Whop",
  currency: "USD",
  rate: 1.5,
  minimum: 1000,
  cap: 50,
  hashtags: ["a", "b"],
  mentions: ["@x"],
  text: [],
  banned: ["music", "gameplay"],
  platforms: ["tiktok", "instagram"],
  ends_on: "2026-11-30",
  budget_left: 800,
  budget_entered_on: "2026-10-27",
  page_link: "c1:page",
};

test("payLine says the rate, the least views and the most a clip, those it has", () => {
  assert.equal(
    payLine(RULES),
    "US$1.50 per 1,000 views, at least 1,000 views, up to US$50.00 a clip",
  );
  assert.equal(
    payLine({ ...RULES, rate: null }),
    "At least 1,000 views, up to US$50.00 a clip",
  );
  assert.equal(
    payLine({ ...RULES, rate: null, minimum: null, cap: null }),
    "No rate entered",
  );
});

test("ruleLines gives the rest of a campaign's card, a line each, those it has", () => {
  assert.deepEqual(ruleLines(RULES), [
    "Tags: #a #b",
    "Mentions: @x",
    "Not allowed: gameplay split, music",
    "TikTok and Instagram",
    "Ends 30 Nov",
    "US$800.00 left on 27 Oct",
  ]);
  const bare = {
    ...RULES,
    hashtags: [],
    mentions: [],
    banned: [],
    ends_on: null,
    budget_entered_on: null,
  };
  assert.deepEqual(
    ruleLines({ ...bare, text: ["Link in bio"], platforms: ["tiktok"] }),
    ["Must say: Link in bio", "TikTok only", "US$800.00 left"],
  );
  assert.deepEqual(
    ruleLines({ ...bare, platforms: [], budget_left: null }),
    [],
  );
});

test("accountLine puts an account's posts today against its cap", () => {
  const today = { posts: 1, cap: 2, ceiling: 4 };
  assert.equal(
    accountLine({ id: "a1", handle: "@t0", platform: "tiktok", today }),
    "@t0 on TikTok: 1 of 2",
  );
  assert.equal(
    accountLine({ id: "a2", handle: null, platform: "instagram", today }),
    "An account on Instagram: 1 of 2",
  );
});
