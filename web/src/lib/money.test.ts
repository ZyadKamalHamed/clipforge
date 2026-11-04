import assert from "node:assert/strict";
import { test } from "node:test";

import type { MoneyAccount, MoneyPost } from "../types.ts";
import {
  accountRows,
  addAmounts,
  amountText,
  asIfNote,
  barText,
  best,
  byNiche,
  byPlatform,
  CSV_COLUMNS,
  csvName,
  DEFAULT_PERIOD,
  earned,
  earnings,
  filterPosts,
  firstRows,
  inPeriod,
  NO_POST_FILTER,
  periodStart,
  PERIODS,
  seriesSlots,
  sortPosts,
  timeline,
  toCsv,
  totals,
  usd,
} from "./money.ts";

// A local clock would lose an hour across Sydney's clock change; the sums work on calendar days.
process.env.TZ = "Australia/Sydney";

let made = 0;
function post(over: Partial<MoneyPost> = {}): MoneyPost {
  made += 1;
  return {
    post_id: `p${made}`,
    posted_on: "2026-11-01",
    account_id: "a1",
    handle: "@t1",
    platform: "tiktok",
    niche: "gaming",
    campaign_id: "c1",
    campaign: "Beast clips",
    marketplace: "Whop",
    hook: `Clip ${made}`,
    url: null,
    views_2d: null,
    views_7d: null,
    views: null,
    status: "submitted",
    reason: null,
    currency: "USD",
    estimated: null,
    pending: null,
    paid: null,
    rate: 1,
    as_if: false,
    ...over,
  };
}

const account = (
  id: string,
  platform: MoneyAccount["platform"],
  handle: string | null,
  niche: string | null = null,
): MoneyAccount => ({ id, platform, handle, niche });

const ids = (posts: MoneyPost[]) => posts.map((p) => p.post_id);
const isMonday = (day: string) =>
  new Date(`${day}T00:00:00Z`).getUTCDay() === 1;

test("periodStart and buckets at the edges", () => {
  assert.deepEqual(
    PERIODS.map((p) => [p.value, p.label]),
    [
      ["7d", "7 days"],
      ["30d", "30 days"],
      ["month", "This month"],
      ["all", "All time"],
    ],
  );
  assert.equal(DEFAULT_PERIOD, "30d");
  assert.equal(periodStart("month", "2026-11-01", []), "2026-11-01");
  assert.equal(periodStart("month", "2026-11-19", []), "2026-11-01");
  assert.equal(periodStart("7d", "2026-10-06", []), "2026-09-30");
  assert.equal(periodStart("30d", "2026-11-01", []), "2026-10-03");
  assert.equal(periodStart("all", "2026-11-01", []), "2026-11-01");
  const old = [
    post({ posted_on: "2026-10-20" }),
    post({ posted_on: "2026-09-02" }),
  ];
  assert.equal(periodStart("all", "2026-11-01", old), "2026-09-02");

  const edges = [
    post({ post_id: "before", posted_on: "2026-10-26" }),
    post({ post_id: "first", posted_on: "2026-10-27" }),
    post({ post_id: "last", posted_on: "2026-11-01" }),
    post({ post_id: "after", posted_on: "2026-11-02" }),
  ];
  assert.deepEqual(ids(inPeriod(edges, "2026-10-27", "2026-11-01")), [
    "first",
    "last",
  ]);

  const days = timeline([], "2026-09-02", "2026-11-01", "marketplace", []);
  assert.equal(days.step, "day");
  assert.equal(days.bars.length, 61);
  assert.equal(days.bars[0].start, "2026-09-02");
  assert.equal(days.bars[0].label, "2 Sep");
  assert.equal(days.bars[60].start, "2026-11-01");
  assert.ok(days.bars.every((bar) => bar.total === 0));

  const sunday = [post({ posted_on: "2026-11-01", estimated: 4 })];
  const weeks = timeline(sunday, "2026-09-01", "2026-11-01", "marketplace", []);
  assert.equal(weeks.step, "week");
  assert.ok(weeks.bars.every((bar) => isMonday(bar.start)));
  assert.equal(weeks.bars.length, 9);
  assert.equal(weeks.bars[0].start, "2026-08-31");
  assert.equal(weeks.bars[0].label, "Week of 31 Aug");
  assert.equal(weeks.bars[8].start, "2026-10-26");
  assert.equal(weeks.bars[8].total, 4);
});

test("totals keeps each currency apart", () => {
  const lines = totals([
    post({ estimated: 12.4, views: 5000 }),
    post({ status: "paid", paid: 5, views: 1000 }),
    post({ status: "approved", estimated: 3.333, pending: 3.333 }),
    post({
      currency: "AUD",
      rate: 0.66,
      status: "paid",
      paid: 10,
      views: 2000,
    }),
    post({ currency: "AUD", rate: 0.66, status: "rejected", views: 300 }),
    post({ currency: "EUR", as_if: true, status: "to_submit" }),
  ]);
  assert.deepEqual(lines, [
    {
      currency: "AUD",
      paid: 10,
      pending: null,
      estimated: null,
      views: 2300,
      posts: 2,
    },
    {
      currency: "EUR",
      paid: 0,
      pending: null,
      estimated: null,
      views: null,
      posts: 1,
    },
    {
      currency: "USD",
      paid: 5,
      pending: 3.33,
      estimated: 15.73,
      views: 6000,
      posts: 3,
    },
  ]);
  assert.deepEqual(totals([]), []);
});

test("timeline converts through the rates and lists the as-if currencies", () => {
  const aud = post({
    currency: "AUD",
    rate: 0.66,
    status: "paid",
    paid: 10,
    marketplace: "Vyro",
  });
  assert.equal(Math.round(usd(aud, "paid") * 100) / 100, 6.6);
  assert.equal(usd(aud, "estimated"), 0);
  assert.equal(earned(aud), usd(aud, "paid"));
  assert.equal(earned(post({ estimated: 2, rate: 0.5 })), 1);

  const posts = [
    aud,
    post({ estimated: 12.4, marketplace: "Whop" }),
    post({
      posted_on: "2026-10-31",
      currency: "EUR",
      as_if: true,
      estimated: 2,
      marketplace: "whop",
    }),
    post({
      posted_on: "2026-10-30",
      status: "rejected",
      marketplace: "Clipify",
    }),
  ];
  const t = timeline(posts, "2026-10-26", "2026-11-01", "marketplace", []);
  assert.equal(t.step, "day");
  assert.equal(t.bars.length, 7);
  assert.deepEqual(t.series, [
    { key: "whop", name: "Whop" },
    { key: "vyro", name: "Vyro" },
  ]);
  const last = t.bars[6];
  assert.equal(last.label, "1 Nov");
  assert.equal(last.total, 19);
  assert.deepEqual(last.parts, [
    { key: "whop", name: "Whop", paid: 0, estimated: 12.4 },
    { key: "vyro", name: "Vyro", paid: 6.6, estimated: 0 },
  ]);
  assert.equal(t.bars[5].total, 2);
  assert.equal(t.bars[4].total, 0);

  assert.equal(
    asIfNote(["EUR"]),
    "No exchange rate for EUR, so it counts as if in US$. Add one in Settings.",
  );
  assert.equal(
    asIfNote(["EUR", "GBP"]),
    "No exchange rate for EUR and GBP, so they count as if in US$. Add them in Settings.",
  );
  assert.equal(
    asIfNote(["EUR", "GBP", "JPY"]),
    "No exchange rate for EUR, GBP and JPY, so they count as if in US$. Add them in Settings.",
  );
  assert.equal(asIfNote([]), null);

  const seven = ["A", "B", "C", "D", "E", "F", "G"].map((m, i) =>
    post({ marketplace: m, estimated: 10 - i }),
  );
  const split = timeline(seven, "2026-11-01", "2026-11-01", "marketplace", []);
  assert.deepEqual(
    split.series.map((s) => s.name),
    ["A", "B", "C", "D", "E", "Others"],
  );
  assert.deepEqual(split.bars[0].parts[5], {
    key: "Others",
    name: "Others",
    paid: 0,
    estimated: 9,
  });
  const called = [...seven, post({ marketplace: "others", estimated: 20 })];
  const both = timeline(called, "2026-11-01", "2026-11-01", "marketplace", []);
  assert.deepEqual(
    both.series.map((s) => [s.key, s.name]),
    [
      ["others", "others"],
      ["a", "A"],
      ["b", "B"],
      ["c", "C"],
      ["d", "D"],
      ["Others", "Others"],
    ],
  );
  assert.equal(both.bars[0].parts[5].estimated, 15);
  const six = timeline(
    seven.slice(0, 6),
    "2026-11-01",
    "2026-11-01",
    "marketplace",
    [],
  );
  assert.deepEqual(
    six.series.map((s) => s.name),
    ["A", "B", "C", "D", "E", "F"],
  );

  const mine = [
    post({ account_id: "a1", estimated: 1 }),
    post({ account_id: "a2", handle: "@i1", estimated: 3 }),
    post({ account_id: "gone", handle: null, estimated: 2 }),
  ];
  const accounts = [
    account("a1", "tiktok", "@t1"),
    account("a2", "instagram", "@i1"),
  ];
  const byAccount = timeline(
    mine,
    "2026-11-01",
    "2026-11-01",
    "account",
    accounts,
  );
  assert.deepEqual(byAccount.series, [
    { key: "a2", name: "@i1" },
    { key: "gone", name: "A removed account" },
    { key: "a1", name: "@t1" },
  ]);
});

test("accountRows lists every account", () => {
  const accounts = [
    account("a1", "tiktok", "@t1", "gaming"),
    account("a2", "instagram", "@i1"),
    account("a3", "tiktok", "@idle"),
    account("a4", "instagram", "@Abc"),
  ];
  const paid = post({ views_7d: 300, views: 400, status: "paid", paid: 5 });
  const posts = [
    post({ views_7d: 100, views: 150, estimated: 2 }),
    paid,
    post({ views_7d: null, views: null }),
    post({
      account_id: "a2",
      handle: "@i1",
      platform: "instagram",
      currency: "AUD",
      rate: 0.66,
      status: "approved",
      estimated: 1,
      pending: 1,
      views: 50,
      views_7d: 50,
    }),
  ];
  const rows = accountRows(accounts, posts);
  assert.deepEqual(
    rows.map((r) => r.account.id),
    ["a1", "a2", "a4", "a3"],
  );
  const [t1, i1, abc] = rows;
  assert.equal(t1.posts, 3);
  assert.equal(t1.views, 550);
  assert.equal(t1.median7, 200);
  assert.deepEqual(t1.estimated, [{ currency: "USD", amount: 2 }]);
  assert.deepEqual(t1.pending, []);
  assert.deepEqual(t1.paid, [{ currency: "USD", amount: 5 }]);
  assert.equal(t1.best, paid);
  assert.deepEqual(i1.pending, [{ currency: "AUD", amount: 1 }]);
  assert.equal(i1.median7, 50);
  assert.equal(abc.posts, 0);
  assert.equal(abc.views, null);
  assert.equal(abc.median7, null);
  assert.deepEqual(abc.estimated, []);
  assert.equal(abc.best, null);

  const three = accountRows(
    [accounts[0]],
    [100, 500, 300].map((v) => post({ views_7d: v })),
  );
  assert.equal(three[0].median7, 300);

  assert.equal(amountText([]), "None");
  assert.equal(
    amountText([
      { currency: "USD", amount: 5 },
      { currency: "AUD", amount: 3 },
    ]),
    "US$5.00 + A$3.00",
  );
});

test("best picks by views and by earned", () => {
  const posts = [
    post({ post_id: "A", posted_on: "2026-10-01", views: 900, estimated: 1 }),
    post({
      post_id: "B",
      posted_on: "2026-10-02",
      views: 5000,
      estimated: 0.5,
    }),
    post({
      post_id: "C",
      posted_on: "2026-10-03",
      views: 5000,
      status: "rejected",
    }),
    post({ post_id: "D", posted_on: "2026-10-04", status: "paid", paid: 8 }),
    post({
      post_id: "E",
      posted_on: "2026-10-05",
      views: 10,
      currency: "AUD",
      rate: 0.66,
      status: "paid",
      paid: 10,
    }),
    post({ post_id: "F", posted_on: "2026-09-30", views: 2000, estimated: 1 }),
  ];
  assert.deepEqual(ids(best(posts, "views")), ["C", "B", "F", "A", "E"]);
  assert.deepEqual(ids(best(posts, "views", 2)), ["C", "B"]);
  assert.deepEqual(ids(best(posts, "earned")), ["D", "E", "F", "A", "B"]);
  assert.deepEqual(best([], "earned"), []);
});

test("byPlatform and byNiche total in US$", () => {
  const posts = [
    post({ views: 100, estimated: 10 }),
    post({ currency: "AUD", rate: 0.66, status: "paid", paid: 10 }),
    post({ platform: "instagram", niche: null, views: 50, estimated: 2 }),
    post({ platform: "instagram", niche: "Gaming", views: 10, estimated: 1 }),
  ];
  assert.deepEqual(byPlatform(posts), [
    { name: "TikTok", posts: 2, views: 100, estimated: 10, paid: 6.6 },
    { name: "Instagram", posts: 2, views: 60, estimated: 3, paid: 0 },
  ]);
  assert.deepEqual(byNiche(posts), [
    { name: "gaming", posts: 3, views: 110, estimated: 11, paid: 6.6 },
    { name: "No niche", posts: 1, views: 50, estimated: 2, paid: 0 },
  ]);
  assert.deepEqual(byNiche([post({ niche: "" })])[0].name, "No niche");
});

test("filterPosts by every filter and search", () => {
  const posts = [
    post({
      post_id: "p1",
      posted_on: "2026-10-20",
      hook: "Wait for it",
    }),
    post({
      post_id: "p2",
      posted_on: "2026-10-25",
      account_id: "a2",
      handle: "@i1",
      platform: "instagram",
      campaign_id: "c2",
      campaign: "Drama nights",
      marketplace: "vyro",
      status: "rejected",
      hook: "The twist",
    }),
    post({
      post_id: "p3",
      posted_on: "2026-11-01",
      campaign_id: "c2",
      campaign: "Drama nights",
      marketplace: "Vyro",
      status: "paid",
      hook: "\uFF37\uFF4F\uFF57 moment",
    }),
  ];
  const keep = (f: Partial<typeof NO_POST_FILTER>) =>
    ids(filterPosts(posts, { ...NO_POST_FILTER, ...f }));
  assert.deepEqual(keep({}), ["p1", "p2", "p3"]);
  assert.deepEqual(keep({ from: "2026-10-25" }), ["p2", "p3"]);
  assert.deepEqual(keep({ to: "2026-10-25" }), ["p1", "p2"]);
  assert.deepEqual(keep({ from: "", to: "" }), ["p1", "p2", "p3"]);
  assert.deepEqual(keep({ account: "a1" }), ["p1", "p3"]);
  assert.deepEqual(keep({ platform: "instagram" }), ["p2"]);
  assert.deepEqual(keep({ campaign: "c2" }), ["p2", "p3"]);
  assert.deepEqual(keep({ marketplace: "VYRO" }), ["p2", "p3"]);
  assert.deepEqual(keep({ status: "rejected" }), ["p2"]);
  assert.deepEqual(keep({ search: "TWIST" }), ["p2"]);
  assert.deepEqual(keep({ search: "beast" }), ["p1"]);
  assert.deepEqual(keep({ search: "@I1" }), ["p2"]);
  assert.deepEqual(keep({ search: "wow" }), ["p3"]);
  assert.deepEqual(keep({ search: "   " }), ["p1", "p2", "p3"]);
  assert.deepEqual(keep({ platform: "tiktok", marketplace: "vyro" }), ["p3"]);
  assert.deepEqual(keep({ search: "nothing like it" }), []);
});

test("sortPosts puts nulls last", () => {
  const posts = [
    post({ post_id: "a", posted_on: "2026-10-01", views: 10 }),
    post({ post_id: "b", posted_on: "2026-10-03", views: null }),
    post({ post_id: "c", posted_on: "2026-10-02", views: 30 }),
    post({ post_id: "d", posted_on: "2026-10-04", views: 10 }),
  ];
  assert.deepEqual(ids(sortPosts(posts, "views", "desc")), [
    "c",
    "a",
    "d",
    "b",
  ]);
  assert.deepEqual(ids(sortPosts(posts, "views", "asc")), ["a", "d", "c", "b"]);
  assert.deepEqual(ids(sortPosts(posts)), ["d", "b", "c", "a"]);
  assert.deepEqual(ids(posts), ["a", "b", "c", "d"]);

  const money = [
    post({ post_id: "aud", currency: "AUD", rate: 0.5, estimated: 10 }),
    post({ post_id: "usd", estimated: 6 }),
    post({ post_id: "none" }),
  ];
  assert.deepEqual(ids(sortPosts(money, "estimated", "desc")), [
    "usd",
    "aud",
    "none",
  ]);
  assert.deepEqual(ids(sortPosts(money, "estimated", "asc")), [
    "aud",
    "usd",
    "none",
  ]);

  const named = [
    post({ post_id: "x", handle: "@zed", status: "paid" }),
    post({ post_id: "y", handle: null, status: "to_submit" }),
    post({ post_id: "z", handle: "@Amy", status: "approved" }),
  ];
  assert.deepEqual(ids(sortPosts(named, "account", "asc")), ["z", "x", "y"]);
  assert.deepEqual(ids(sortPosts(named, "status", "asc")), ["y", "z", "x"]);
});

test("firstRows keeps 500 and says how many more", () => {
  const many = Array.from({ length: 3000 }, (_, i) =>
    post({ post_id: `m${i}` }),
  );
  const shown = firstRows(many);
  assert.equal(shown.rows.length, 500);
  assert.equal(shown.rows[0], many[0]);
  assert.equal(shown.rows[499], many[499]);
  assert.equal(shown.more, 2500);
  assert.equal(
    shown.note,
    "Showing the first 500 of 3,000 posts. Export CSV saves all 3,000.",
  );
  const all = firstRows(many.slice(0, 500));
  assert.equal(all.rows.length, 500);
  assert.equal(all.more, 0);
  assert.equal(all.note, null);
});

test("toCsv quotes every cell that needs it and defuses formulas", () => {
  const posts = [
    post({
      hook: '=HYPERLINK("http://x","y")',
      campaign:
        "\u062D\u0645\u0644\u0629 \u0627\u0644\u0645\u0642\u0627\u0637\u0639",
      views_2d: 4000,
      views_7d: 12000,
      views: 12000,
      estimated: 24,
    }),
    post({
      posted_on: "2026-10-30",
      handle: "@i1",
      platform: "instagram",
      hook: 'Wait, "what"\nhappened',
      campaign: "-minus",
      marketplace: "+plus",
      status: "rejected",
      reason: "\tTabbed",
      currency: "AUD",
    }),
    post({
      posted_on: "2026-10-29",
      handle: null,
      hook: "\rcarriage",
      campaign: "@once",
      status: "paid",
      paid: 30,
    }),
  ];
  assert.deepEqual(
    CSV_COLUMNS.map((c) => c.label),
    [
      "Posted",
      "Account",
      "Platform",
      "Campaign",
      "Marketplace",
      "Hook",
      "Views at 2 days",
      "Views at 7 days",
      "Views",
      "Status",
      "Reason",
      "Currency",
      "Estimated",
      "Paid",
    ],
  );
  const csv = toCsv(posts);
  assert.equal(csv[0], "\uFEFF");
  assert.equal(
    csv,
    "\uFEFF" +
      "Posted,Account,Platform,Campaign,Marketplace,Hook,Views at 2 days,Views at 7 days,Views,Status,Reason,Currency,Estimated,Paid\r\n" +
      "2026-11-01,'@t1,TikTok,\u062D\u0645\u0644\u0629 \u0627\u0644\u0645\u0642\u0627\u0637\u0639,Whop," +
      '"\'=HYPERLINK(""http://x"",""y"")",4000,12000,12000,Submitted,,USD,24.00,\r\n' +
      "2026-10-30,'@i1,Instagram,'-minus,'+plus," +
      '"Wait, ""what""\nhappened",,,,Rejected,\'\tTabbed,AUD,,\r\n' +
      "2026-10-29,,TikTok,'@once,Whop," +
      '"\'\rcarriage",,,,Paid,,USD,,30.00\r\n',
  );
  assert.equal(toCsv([]).split("\r\n").length, 2);
  assert.equal(csvName("2026-10-29"), "clipforge-money-2026-10-29.csv");
});

test("barText reads a bar's figures, leaving out what is nothing", () => {
  const bar = {
    start: "2026-11-03",
    label: "3 Nov",
    total: 27.4,
    parts: [
      { key: "whop", name: "Whop", estimated: 12.4, paid: 5 },
      { key: "vyro", name: "Vyro", estimated: 10, paid: 0 },
      { key: "clipr", name: "Clipr", estimated: 0, paid: 0 },
    ],
  };
  assert.equal(
    barText(bar),
    "3 Nov: US$22.40 estimated, US$5.00 paid; Whop US$12.40 estimated and US$5.00 paid, Vyro US$10.00 estimated",
  );
  const paidOnly = {
    ...bar,
    parts: [{ key: "whop", name: "Whop", estimated: 0, paid: 3 }],
  };
  assert.equal(barText(paidOnly), "3 Nov: US$3.00 paid; Whop US$3.00 paid");
  assert.equal(
    barText({ ...bar, total: 0, parts: [] }),
    "3 Nov: nothing earned",
  );
  assert.equal(
    barText({
      ...bar,
      parts: [
        { key: "a", name: "A", estimated: 0.1, paid: 0 },
        { key: "b", name: "B", estimated: 0.2, paid: 0 },
      ],
    }),
    "3 Nov: US$0.30 estimated; A US$0.10 estimated, B US$0.20 estimated",
  );
  assert.deepEqual(earnings(0, 0), []);
  assert.deepEqual(earnings(1.5, 2), ["US$1.50 estimated", "US$2.00 paid"]);
});

test("seriesSlots gives every series its slot from its place in the whole history", () => {
  const s = (key: string) => ({ key, name: key });
  const ranked = ["a", "b", "c", "d", "e", "f", "g", "h"];
  // The period ranks them differently and drops some: each keeps the slot its all-time place gives it,
  // counting round the six again after the sixth.
  assert.deepEqual(seriesSlots([s("c"), s("a")], ranked), { c: 2, a: 0 });
  assert.deepEqual(seriesSlots([s("g"), s("f"), s("h")], ranked), {
    g: 0,
    f: 5,
    h: 1,
  });
  // Two shown that share a slot: the bigger earner of all time keeps it, the other takes the next free one.
  assert.deepEqual(seriesSlots([s("g"), s("b"), s("a")], ranked), {
    a: 0,
    b: 1,
    g: 2,
  });
  // Others is never given a slot; it is drawn grey.
  assert.deepEqual(
    seriesSlots([s("b"), { key: "Others", name: "Others" }], ranked),
    { b: 1 },
  );
  assert.deepEqual(seriesSlots([], ranked), {});
});

test("a series keeps its colour in every period, outside the top five of all time too", () => {
  const earner = (id: string, posted_on: string, estimated: number) =>
    post({ account_id: id, handle: `@${id}`, posted_on, estimated });
  const posts = [
    ...[1, 2, 3, 4, 5].map((n) => earner(`m${n}`, "2026-08-03", 100 - n)),
    earner("m6", "2026-10-31", 9),
    earner("m7", "2026-10-30", 8),
    earner("m8", "2026-10-15", 7),
    ...[9, 10, 11, 12].map((n) => earner(`m${n}`, "2026-10-10", 10 - n / 2)),
  ];
  const ever = timeline(posts, "2026-08-03", "2026-11-01", "account", []);
  assert.deepEqual(ever.ranked, [
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "m6",
    "m7",
    "m8",
    "m9",
    "m10",
    "m11",
    "m12",
  ]);
  const slotsIn = (start: string) =>
    seriesSlots(
      timeline(posts, start, "2026-11-01", "account", []).series,
      ever.ranked,
    );
  const week = slotsIn("2026-10-26");
  const month = slotsIn("2026-10-03");
  const all = slotsIn("2026-08-03");
  assert.deepEqual(week, { m6: 5, m7: 0 });
  assert.deepEqual(month, { m6: 5, m7: 0, m8: 1, m9: 2, m10: 3 });
  assert.deepEqual(all, { m1: 0, m2: 1, m3: 2, m4: 3, m5: 4 });
});

test("addAmounts adds lists by currency", () => {
  assert.deepEqual(
    addAmounts([
      [
        { currency: "USD", amount: 8.1 },
        { currency: "AUD", amount: 1 },
      ],
      [],
      [{ currency: "USD", amount: 0.2 }],
    ]),
    [
      { currency: "AUD", amount: 1 },
      { currency: "USD", amount: 8.3 },
    ],
  );
  assert.deepEqual(addAmounts([]), []);
});
