import assert from "node:assert/strict";
import { test } from "node:test";

import type { Campaign, CampaignCounts, CampaignFields } from "../types.ts";
import {
  amountValue,
  campaignsLabel,
  dayLabel,
  estimateText,
  filledMark,
  filterRows,
  formBody,
  formState,
  hiddenColumns,
  keepHidden,
  marketplaceNames,
  moveLabel,
  money,
  nextSort,
  NO_FILTER,
  progressCounts,
  sortRows,
  totalsByCurrency,
  triangleCount,
  triangleLines,
  triangleName,
} from "./campaigns.ts";

const NONE: CampaignCounts = {
  made: 0,
  approved: 0,
  live: 0,
  submitted: 0,
  accepted: 0,
  paid: 0,
};

type Row = {
  id: string;
  name?: string;
  marketplace?: string;
  currency?: string;
  usd_rate?: number;
  rate?: number | null;
  budget?: number | null;
  views?: number | null;
  estimated?: number | null;
  pending?: number | null;
  paid?: number;
  counts?: Partial<CampaignCounts>;
  todo?: Campaign["todo"];
  state?: Campaign["state"];
  channels?: string[];
};

function row(r: Row): Campaign {
  return {
    id: r.id,
    name: r.name ?? r.id,
    marketplace: r.marketplace ?? "Whop",
    currency: r.currency ?? "USD",
    usd_rate: r.usd_rate ?? 1,
    rate_per_1000: r.rate ?? null,
    budget_left: r.budget ?? null,
    ends_on: null,
    channels: (r.channels ?? []).map((name, i) => ({ id: `ch${i}`, name })),
    state: r.state ?? "active",
    money: {
      counts: { ...NONE, ...r.counts },
      views: r.views ?? null,
      estimated: r.estimated ?? null,
      pending: r.pending ?? null,
      paid: r.paid ?? 0,
    },
    todo: r.todo ?? [],
  } as unknown as Campaign; // the helpers read only these
}

const ids = (rows: Campaign[]) => rows.map((r) => r.id);

test("sorts by estimate by default", () => {
  const rows = [
    row({ id: "low", estimated: 2 }),
    row({ id: "none", estimated: null }),
    row({ id: "high", estimated: 40 }),
    row({ id: "mid", estimated: 12.5 }),
  ];
  assert.deepEqual(ids(sortRows(rows)), ["high", "mid", "low", "none"]);
  assert.deepEqual(ids(sortRows(rows, "estimated", "asc")), [
    "low",
    "mid",
    "high",
    "none",
  ]); // no rate stays last
  assert.deepEqual(ids(rows), ["low", "none", "high", "mid"]); // the rows given are left as they were
  const named = [
    row({ id: "b", name: "beast" }),
    row({ id: "a", name: "Alpha" }),
    row({ id: "c", name: "Cats" }),
  ];
  assert.deepEqual(ids(sortRows(named, "name", "asc")), ["a", "b", "c"]); // whatever the case
  const tied = [
    row({ id: "first", estimated: 5 }),
    row({ id: "second", estimated: 5 }),
  ];
  assert.deepEqual(ids(sortRows(tied)), ["first", "second"]); // a tie keeps the order given (newest first)
  assert.deepEqual(nextSort({ key: "estimated", dir: "desc" }, "estimated"), {
    key: "estimated",
    dir: "asc",
  });
  assert.deepEqual(nextSort({ key: "estimated", dir: "desc" }, "name"), {
    key: "name",
    dir: "asc",
  });
  assert.deepEqual(nextSort({ key: "name", dir: "asc" }, "views"), {
    key: "views",
    dir: "desc",
  }); // biggest first
});

test("sorts amounts in US$ across currencies", () => {
  // US$80 against A$100, which is US$66 at 0.66.
  const rows = [
    row({
      id: "aud",
      currency: "AUD",
      usd_rate: 0.66,
      rate: 1.5,
      budget: 1000,
      estimated: 100,
      pending: 100,
      paid: 100,
    }),
    row({
      id: "usd",
      rate: 1.2,
      budget: 800,
      estimated: 80,
      pending: 80,
      paid: 80,
    }),
  ];
  assert.deepEqual(ids(sortRows(rows)), ["usd", "aud"]);
  for (const key of ["rate", "budget", "estimated", "pending", "paid"] as const)
    assert.deepEqual(ids(sortRows(rows, key, "desc")), ["usd", "aud"], key);
  // Views and the rest have no currency.
  const viewed = [
    row({ id: "aud", currency: "AUD", usd_rate: 0.66, views: 100 }),
    row({ id: "usd", views: 80 }),
  ];
  assert.deepEqual(ids(sortRows(viewed, "views", "desc")), ["aud", "usd"]);
});

test("totals never mix currencies", () => {
  const rows = [
    row({
      id: "u1",
      currency: "USD",
      views: 1000,
      estimated: 1.5,
      pending: 1,
      paid: 2,
    }),
    row({
      id: "a1",
      currency: "AUD",
      views: 500,
      estimated: 3,
      pending: null,
      paid: 0,
    }),
    row({
      id: "u2",
      currency: "USD",
      views: null,
      estimated: 0.1,
      pending: 0.2,
      paid: 0.1,
    }),
    row({
      id: "e1",
      currency: "EUR",
      views: null,
      estimated: null,
      pending: null,
      paid: 0,
    }),
  ];
  assert.deepEqual(totalsByCurrency(rows), [
    { currency: "AUD", views: 500, estimated: 3, pending: null, paid: 0 },
    { currency: "EUR", views: null, estimated: null, pending: null, paid: 0 },
    { currency: "USD", views: 1000, estimated: 1.6, pending: 1.2, paid: 2.1 },
  ]);
  assert.deepEqual(totalsByCurrency([]), []);
});

test("money formats", () => {
  assert.equal(money(12.4, "USD"), "US$12.40");
  assert.equal(money(3, "AUD"), "A$3.00");
  assert.equal(money(12.4, "EUR"), "12.40 EUR");
  assert.equal(money(1234.5, "USD"), "US$1,234.50");
  assert.equal(money(0, "USD"), "US$0.00");
});

test("a submission with no views yet says so, not that the rate is missing", () => {
  assert.equal(
    estimateText({ estimate: null, views: null }, "USD"),
    "No views yet",
  );
  assert.equal(estimateText({ estimate: null, views: 5000 }, "USD"), "No rate");
  assert.equal(estimateText({ estimate: 12.4, views: 5000 }, "AUD"), "A$12.40");
  assert.equal(estimateText({ estimate: 0, views: 300 }, "USD"), "US$0.00"); // under the minimum views
});

test("filled marks clear when edited", () => {
  const filled = ["name", "rate_per_1000", "required.hashtags"];
  assert.equal(filledMark("name", filled, new Set()), "Check this");
  assert.equal(
    filledMark("required.hashtags", filled, new Set()),
    "Check this",
  );
  assert.equal(filledMark("name", filled, new Set(["name"])), null);
  assert.equal(
    filledMark("rate_per_1000", filled, new Set(["name"])),
    "Check this",
  );
  assert.equal(filledMark("marketplace", filled, new Set()), null); // Claude didn't fill it
});

test("progress counts each step and how far the clips got", () => {
  const p = progressCounts(
    row({
      id: "x",
      counts: {
        made: 4,
        approved: 4,
        live: 2,
        submitted: 2,
        accepted: 1,
        paid: 1,
      },
    }),
  );
  assert.deepEqual(
    p.parts.map((part) => part.n),
    [4, 4, 2, 2, 1, 1],
  );
  assert.equal(
    p.sentence,
    "4 made, 4 approved by you, 2 live, 2 submitted, 1 accepted, 1 paid",
  );
  assert.equal(p.share, 10 / 20);
  assert.equal(progressCounts(row({ id: "y" })).share, 0); // nothing made yet
});

test("moveLabel says which way a submission goes", () => {
  assert.equal(moveLabel("to_submit", "submitted"), "Mark submitted");
  assert.equal(moveLabel("submitted", "approved"), "Approved");
  assert.equal(moveLabel("submitted", "rejected"), "Rejected");
  assert.equal(moveLabel("approved", "paid"), "Paid");
  assert.equal(moveLabel("submitted", "to_submit"), "Back to To submit");
  assert.equal(moveLabel("approved", "submitted"), "Back to Submitted");
  assert.equal(moveLabel("paid", "approved"), "Back to Approved");
  assert.equal(moveLabel("rejected", "approved"), "Approved");
});

test("dayLabel and amountValue", () => {
  assert.equal(dayLabel("2026-10-03"), "3 Oct");
  assert.equal(dayLabel("2026-12-25"), "25 Dec");
  assert.equal(dayLabel(null), "");
  assert.equal(amountValue(""), null);
  assert.equal(amountValue("  "), null);
  assert.equal(amountValue("1.5"), 1.5);
  assert.equal(amountValue("1,000"), 1000);
  assert.equal(amountValue("abc"), "abc"); // sent as it is, so the engine says what the field needs
});

test("the form keeps every field it is given", () => {
  const fields: CampaignFields = {
    name: "Beast clips",
    marketplace: "Whop",
    campaign_url: null,
    submit_url: "https://whop.com/beast/submit",
    currency: "USD",
    rate_per_1000: 1.5,
    min_views: 1000,
    max_payout: null,
    platforms: ["tiktok"],
    footage: { videos: ["arj7oStGLkU"], files: true },
    footage_links: [
      "https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA/videos",
    ],
    required: {
      hashtags: ["MrBeast"],
      mentions: ["@mrbeast"],
      text: ["Link in bio"],
    },
    banned: ["gameplay", "music"],
    hand_rules: ["No swearing", "Keep the intro"],
    min_s: 15,
    max_s: 60,
    ends_on: "2026-11-30",
    budget_left: 5000,
    notes: "Paid weekly",
    rules_text: "The rules",
  };
  const state = formState(fields);
  assert.equal(state.text.rate_per_1000, "1.5");
  assert.equal(state.text["required.hashtags"], "#MrBeast");
  assert.equal(state.text.hand_rules, "No swearing\nKeep the intro");
  assert.equal(state.text.max_payout, "");
  assert.deepEqual(formBody(state, "The rules"), fields);
  const typed = {
    ...state,
    text: {
      ...state.text,
      campaign_url: "  ",
      min_views: "lots",
      hand_rules: "\n One \n\n",
    },
  };
  const body = formBody(typed, "The rules");
  assert.equal(body.campaign_url, null);
  assert.equal(body.min_views, "lots");
  assert.deepEqual(body.hand_rules, ["One"]);
});

test("rows with a triangle sort first", () => {
  const waits = [
    { kind: "submit", title: "Submit 1 live post", count: 1 },
  ] as Campaign["todo"];
  const rows = [
    row({ id: "rich", estimated: 90 }),
    row({ id: "small", estimated: 3, todo: waits }),
    row({ id: "none", estimated: null, todo: waits }),
    row({ id: "big", estimated: 40, todo: waits }),
    row({ id: "mid", estimated: 12 }),
  ];
  assert.deepEqual(ids(sortRows(rows)), [
    "big",
    "small",
    "none",
    "rich",
    "mid",
  ]); // then by estimate, none last
  assert.deepEqual(ids(sortRows(rows, "needs", "asc")), [
    "rich",
    "mid",
    "big",
    "small",
    "none",
  ]);
  assert.deepEqual(nextSort({ key: "estimated", dir: "desc" }, "needs"), {
    key: "needs",
    dir: "desc",
  });
});

test("triangleCount counts the rows with something waiting", () => {
  const waits = [
    { kind: "drafts", title: "Approve 2 drafts", count: 2 },
  ] as Campaign["todo"];
  assert.equal(
    triangleCount([
      row({ id: "a", todo: waits }),
      row({ id: "b" }),
      row({ id: "c", todo: waits }),
    ]),
    2,
  );
  assert.equal(triangleCount([row({ id: "b" })]), 0);
  assert.equal(triangleName(3), "Needs you: 3 things");
  assert.equal(triangleName(1), "Needs you: 1 thing");
  assert.equal(campaignsLabel(0), "Campaigns");
  assert.equal(campaignsLabel(1), "Campaigns, 1 needs you");
  assert.equal(campaignsLabel(2), "Campaigns, 2 need you");
});

test("triangleLines gives each line its one button and where it goes", () => {
  const todo = (
    [
      "drafts",
      "needs_you",
      "login",
      "insights",
      "submit",
      "views",
      "decide",
      "payout",
      "ends",
      "exported",
      "join",
    ] as const
  ).map((kind) => ({ kind, title: `the ${kind}`, count: 1 }));
  const lines = triangleLines({ id: "cp 1", todo });
  const page = "/campaigns/cp%201";
  assert.deepEqual(
    lines.map((l) => [l.title, l.label, l.to]),
    [
      ["the drafts", "Review drafts", "/#review"],
      ["the needs_you", "Open the Queue", "/queue"],
      ["the login", "Open Accounts", "/accounts"],
      ["the insights", "Open Accounts", "/accounts"],
      ["the submit", "Submit", "/#submit"],
      ["the views", "Enter views", page],
      ["the decide", "Record", page],
      ["the payout", "Withdraw", "/#money"],
      ["the ends", "Look", page],
      ["the exported", "Put up", "/#post"],
      ["the join", "Open", page],
    ],
  );
  assert.deepEqual(
    lines.filter((l) => l.join).map((l) => l.title),
    ["the join"],
  );
});

const waiting = [{ id: "t1" }] as unknown as Campaign["todo"];

test("filterRows searches name, marketplace and channels", () => {
  const rows = [
    row({ id: "a", name: "Beast clips", marketplace: "Whop" }),
    row({
      id: "b",
      name: "Podcasts",
      marketplace: "Vyro",
      channels: ["MrBeast daily"],
    }),
    row({ id: "c", name: "Gaming", marketplace: "Vyro" }),
  ];
  assert.deepEqual(ids(filterRows(rows, { ...NO_FILTER, search: "beast" })), [
    "a",
    "b",
  ]);
  assert.deepEqual(ids(filterRows(rows, { ...NO_FILTER, search: " VYRO " })), [
    "b",
    "c",
  ]);
  assert.deepEqual(ids(filterRows(rows, { ...NO_FILTER, search: "  " })), [
    "a",
    "b",
    "c",
  ]);
});

test("filterRows by state", () => {
  const rows = [
    row({ id: "on", state: "active" }),
    row({ id: "soon", state: "ending" }),
    row({ id: "over", state: "ended" }),
  ];
  const by = (state: "active" | "ending" | "ended") =>
    ids(filterRows(rows, { ...NO_FILTER, state }));
  assert.deepEqual(by("active"), ["on", "soon"]);
  assert.deepEqual(by("ending"), ["soon"]);
  assert.deepEqual(by("ended"), ["over"]);
});

test("filterRows by marketplace folds case and treats a gone one as all", () => {
  const rows = [
    row({ id: "w", marketplace: "Whop" }),
    row({ id: "v", marketplace: "Vyro" }),
  ];
  assert.deepEqual(
    ids(filterRows(rows, { ...NO_FILTER, marketplace: "whop" })),
    ["w"],
  );
  assert.deepEqual(
    ids(filterRows(rows, { ...NO_FILTER, marketplace: "Clipify" })),
    ["w", "v"],
  );
});

test("filterRows keeps rows that need you", () => {
  const rows = [row({ id: "calm" }), row({ id: "waits", todo: waiting })];
  assert.deepEqual(ids(filterRows(rows, { ...NO_FILTER, needsMe: true })), [
    "waits",
  ]);
});

test("marketplaceNames folds case", () => {
  const rows = [
    row({ id: "1", marketplace: "vyro" }),
    row({ id: "2", marketplace: "Whop" }),
    row({ id: "3", marketplace: "Vyro" }),
  ];
  assert.deepEqual(marketplaceNames(rows), ["vyro", "Whop"]);
});

test("hiddenColumns survives bad storage", () => {
  const holding = (value: string | null) => ({ getItem: () => value });
  const throwing = {
    getItem: (): string | null => {
      throw new Error("blocked");
    },
  };
  for (const storage of [
    throwing,
    holding("not json"),
    holding("{}"),
    holding(null),
    null,
  ])
    assert.deepEqual([...hiddenColumns(storage)], []);
  assert.deepEqual(
    [...hiddenColumns(holding('["views","bogus","name"]'))],
    ["views"],
  );
});

test("keepHidden writes in sheet order", () => {
  const written: string[] = [];
  keepHidden(
    { setItem: (_key: string, value: string) => written.push(value) },
    new Set(["paid", "views"]),
  );
  assert.deepEqual(written, ['["views","paid"]']);
  const throwing = {
    setItem: () => {
      throw new Error("full");
    },
  };
  assert.doesNotThrow(() => keepHidden(throwing, new Set(["views"])));
});
