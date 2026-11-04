import assert from "node:assert/strict";
import { test } from "node:test";

import type { Brief, BriefSource, Suggestion } from "../types.ts";
import {
  adviceFacts,
  budgetText,
  checkAgain,
  filterBriefs,
  filterOptions,
  fitText,
  LOOK_MAX,
  makeLine,
  platformText,
  rateText,
  readerLine,
  resultLine,
  tagHint,
  worthALook,
  worthText,
} from "./briefs.ts";

function brief(fields: Partial<Brief> = {}): Brief {
  return {
    id: "br1",
    source_id: "bs_scout",
    external_id: "https://contentrewards.com/c/1",
    url: "https://contentrewards.com/c/1",
    marketplace: "Whop",
    brand: null,
    title: "Clip the stream",
    niche: null,
    language: null,
    currency: "USD",
    rate_per_1000: null,
    rates: {},
    min_views: null,
    max_payout: null,
    caps: {},
    budget_total: null,
    budget_spent: null,
    budget_left: null,
    budget_read_at: null,
    ends_on: null,
    platforms: ["tiktok", "instagram"],
    footage: { kind: null, links: [] },
    text: "",
    fields: {},
    notes: {},
    status: "new",
    campaign_id: null,
    first_seen_at: 0,
    last_seen_at: 0,
    worth: { amount: null, currency: "USD", guess: false, account_id: null },
    fits: [],
    openable: true,
    link_id: "brief:br1",
    reposting: false,
    ...fields,
  };
}

test("rateText gives the one rate, else the highest a platform pays, else No rate", () => {
  assert.equal(
    rateText(brief({ rate_per_1000: 1 })),
    "US$1.00 per 1,000 views",
  );
  assert.equal(
    rateText(
      brief({ rate_per_1000: 1.5, rates: { tiktok: 1.5, instagram: 1 } }),
    ),
    "up to US$1.50 per 1,000 views",
  );
  assert.equal(
    rateText(brief({ rate_per_1000: 1, rates: { tiktok: 1 } })),
    "US$1.00 per 1,000 views",
  );
  assert.equal(
    rateText(
      brief({
        rate_per_1000: 2,
        rates: { instagram: 0.5 },
        platforms: ["tiktok", "instagram"],
      }),
    ),
    "up to US$2.00 per 1,000 views",
  );
  assert.equal(
    rateText(
      brief({
        rate_per_1000: 3,
        rates: { instagram: 0.5 },
        platforms: ["tiktok"],
      }),
    ),
    "US$3.00 per 1,000 views", // only the platforms it pays on count
  );
  assert.equal(
    rateText(brief({ currency: "AUD", rate_per_1000: 0.8 })),
    "A$0.80 per 1,000 views",
  );
  assert.equal(rateText(brief()), "No rate");
});

test("budgetText gives what is left in whole money where it is whole, and the Sydney day it was read", () => {
  const read = Date.UTC(2026, 9, 26, 14, 0) / 1000; // 1:00 am on 27 Oct in Sydney
  assert.equal(
    budgetText(brief({ budget_left: 812, budget_read_at: read })),
    "US$812 left, read 27 Oct",
  );
  assert.equal(budgetText(brief({ budget_left: 812.5 })), "US$812.50 left");
  assert.equal(
    budgetText(brief({ budget_left: 1200, currency: "EUR" })),
    "1,200 EUR left",
  );
  assert.equal(budgetText(brief({ budget_left: 0 })), "US$0 left");
  assert.equal(budgetText(brief({ budget_read_at: read })), "Budget not shown");
});

test("worthText says what it could earn a day, and when that is a guess", () => {
  assert.equal(
    worthText({ amount: 4.5, currency: "USD", guess: false, account_id: "a1" }),
    "about US$4.50 a day",
  );
  assert.equal(
    worthText({ amount: 1, currency: "USD", guess: true, account_id: null }),
    "about US$1.00 a day, a guess",
  );
  assert.equal(
    worthText({ amount: 2, currency: "AUD", guess: false, account_id: null }),
    "about A$2.00 a day",
  );
  assert.equal(
    worthText({
      amount: null,
      currency: "USD",
      guess: false,
      account_id: null,
    }),
    "No rate",
  );
});

test("fitText names the accounts that fit, the one its worth is through first", () => {
  assert.equal(fitText(brief()), "Fits no account yet");
  const one = {
    id: "a1",
    platform: "tiktok" as const,
    handle: "realme",
    label: "",
  };
  const two = {
    id: "a2",
    platform: "instagram" as const,
    handle: null,
    label: "Gaming IG",
  };
  assert.equal(fitText(brief({ fits: [one] })), "Fits @realme");
  assert.equal(fitText(brief({ fits: [one, two] })), "Fits @realme, Gaming IG");
  const through = { amount: 3, currency: "USD", guess: true, account_id: "a2" };
  assert.equal(
    fitText(brief({ fits: [one, two], worth: through })),
    "Fits Gaming IG, @realme",
  );
});

test("filterBriefs keeps the briefs of the niche and the marketplace picked, null for any", () => {
  const list = [
    brief({ id: "b1", niche: "Gaming", marketplace: "Whop" }),
    brief({ id: "b2", niche: "Streamers", marketplace: "Vyro" }),
    brief({ id: "b3", niche: "Gaming", marketplace: "Vyro" }),
    brief({ id: "b4", niche: null, marketplace: "Whop" }),
  ];
  const ids = (picked: Brief[]) => picked.map((b) => b.id);
  assert.deepEqual(
    ids(filterBriefs(list, { niche: null, marketplace: null })),
    ["b1", "b2", "b3", "b4"],
  );
  assert.deepEqual(
    ids(filterBriefs(list, { niche: "Gaming", marketplace: null })),
    ["b1", "b3"],
  );
  assert.deepEqual(
    ids(filterBriefs(list, { niche: null, marketplace: "Vyro" })),
    ["b2", "b3"],
  );
  assert.deepEqual(
    ids(filterBriefs(list, { niche: "Gaming", marketplace: "Vyro" })),
    ["b3"],
  );
  assert.deepEqual(
    ids(filterBriefs(list, { niche: "Music", marketplace: null })),
    [],
  );
});

test("filterOptions lists the niches and marketplaces the briefs have, once each and in order", () => {
  const list = [
    brief({ niche: "Streamers", marketplace: "Whop" }),
    brief({ niche: "Gaming", marketplace: "Vyro" }),
    brief({ niche: null, marketplace: "Whop" }),
    brief({ niche: "Gaming", marketplace: "Whop" }),
  ];
  assert.deepEqual(filterOptions(list), {
    niches: ["Gaming", "Streamers"],
    marketplaces: ["Vyro", "Whop"],
  });
});

test("platformText names the platforms a brief pays on", () => {
  assert.equal(platformText(["tiktok"]), "TikTok only");
  assert.equal(platformText(["instagram"]), "Instagram only");
  assert.equal(platformText(["tiktok", "instagram"]), "TikTok and Instagram");
});

test("worthALook keeps the new briefs that fit an account or a suggested niche, the first 5 as sent", () => {
  const fits = [
    { id: "a1", platform: "tiktok" as const, handle: "me", label: "" },
  ];
  const list = [
    brief({ id: "b1", fits }),
    brief({ id: "b2", niche: "Gaming" }),
    brief({ id: "b3", niche: "Music" }),
    brief({ id: "b4", fits, status: "seen" }),
    ...[5, 6, 7, 8, 9].map((n) => brief({ id: `b${n}`, fits })),
  ];
  assert.deepEqual(
    worthALook(list, ["Gaming"]).map((b) => b.id),
    ["b1", "b2", "b5", "b6", "b7"],
  );
  assert.equal(LOOK_MAX, 5);
  assert.deepEqual(worthALook([brief({ niche: "Music" })], []), []);
});

function suggestion(fields: Partial<Suggestion> = {}): Suggestion {
  return {
    niche: "Reality TV and drama",
    language: "en",
    label: "Drama",
    count: 1,
    budget_left: 500,
    per_day: 4,
    as_if: false,
    guess: false,
    best: [],
    make: ["tiktok", "instagram"],
    names: ["Drama TV Clips", "Drama Clips Daily", "Drama Clip Vault"],
    bio: "Clips of the best drama. A fan page.",
    picture: "A bold D.",
    channel: { name: "Drama clips", sources: [], account_ids: [] },
    plan: "Up to 3 posts a day.",
    ...fields,
  };
}

test("adviceFacts says its live briefs, their budget left and what it could earn a day, in US$", () => {
  assert.equal(
    adviceFacts(suggestion()),
    "1 live brief, US$500 left, about US$4.00 a day",
  );
  assert.equal(
    adviceFacts(suggestion({ count: 3, budget_left: 812.5, per_day: 12.25 })),
    "3 live briefs, US$812.50 left, about US$12.25 a day",
  );
  assert.equal(
    adviceFacts(suggestion({ budget_left: null, guess: true, as_if: true })),
    "1 live brief, about US$4.00 a day (a guess, as if US$)",
  );
});

test("makeLine says which accounts to make, or nothing when you have them", () => {
  assert.equal(
    makeLine(["tiktok", "instagram"]),
    "Make a TikTok and an Instagram account",
  );
  assert.equal(makeLine(["instagram"]), "Make an Instagram account");
  assert.equal(makeLine(["tiktok"]), "Make a TikTok account");
  assert.equal(makeLine([]), null);
});

test("tagHint names the niche's first two tags to put on an account", () => {
  assert.equal(
    tagHint(["reality", "drama", "reality tv"]),
    "Tag an account with reality or drama on Accounts to set it up.",
  );
  assert.equal(
    tagHint(["gaming"]),
    "Tag an account with gaming on Accounts to set it up.",
  );
  assert.equal(
    tagHint([]),
    "Tag an account with this niche on Accounts to set it up.",
  );
});

function source(fields: Partial<BriefSource> = {}): BriefSource {
  return {
    id: "bs1",
    name: "Clip Market",
    marketplace: "Clip Market",
    list_url: "https://clipmarket.example/campaigns",
    reader: "general",
    enabled: true,
    terms_checked_on: "2026-10-29",
    terms_note: null,
    last_run_at: null,
    last_result: null,
    error: null,
    created_at: 0,
    updated_at: 0,
    check_now_after: null,
    reading: false,
    paste: false,
    ...fields,
  };
}

test("readerLine says how each source is read", () => {
  assert.equal(
    readerLine(source({ reader: "campaign_scout" })),
    "Read from Campaign Scout's file on this Mac.",
  );
  assert.equal(
    readerLine(source({ reader: "paste" })),
    "Paste only: you paste its briefs on the Briefs tab.",
  );
  assert.equal(
    readerLine(source()),
    "Read from its public list page, https://clipmarket.example/campaigns.",
  );
});

test("resultLine says when a source was last read and what went wrong", () => {
  const now = 10_000;
  assert.equal(resultLine(source(), now), "Not read yet.");
  assert.equal(
    resultLine(source({ last_run_at: now - 120, last_result: "ok" }), now),
    "Read 2 min ago.",
  );
  assert.equal(
    resultLine(
      source({
        last_run_at: now - 7200,
        last_result: "blocked",
        error: "The site said no.",
      }),
      now,
    ),
    "Couldn't be read 2 h ago. The site said no.",
  );
  assert.equal(
    resultLine(
      source({
        reader: "campaign_scout",
        last_run_at: now,
        last_result: "changed",
        error: "missing",
      }),
      now,
    ),
    "Couldn't be read just now. Campaign Scout's file is missing.",
  );
  assert.equal(
    resultLine(
      source({
        reader: "campaign_scout",
        last_run_at: now,
        last_result: "changed",
        error: "reshaped",
      }),
      now,
    ),
    "Couldn't be read just now. Campaign Scout's file has changed shape.",
  );
  assert.equal(
    resultLine(source({ last_run_at: now, last_result: "error" }), now),
    "Couldn't be read just now.",
  );
  assert.equal(
    resultLine(
      source({ reader: "paste", last_run_at: now, last_result: "ok" }),
      now,
    ),
    null,
  );
});

test("checkAgain says when Check now opens again, in whole minutes up", () => {
  assert.equal(checkAgain(null, 1000), null);
  assert.equal(checkAgain(1000, 1000), null);
  assert.equal(checkAgain(1061, 1000), "Check now opens again in 2 min.");
  assert.equal(checkAgain(1060, 1000), "Check now opens again in 1 min.");
  assert.equal(checkAgain(1001, 1000), "Check now opens again in 1 min.");
  // after a site turned ClipForge away, it waits for the next morning's read
  assert.equal(
    checkAgain(1793732400, 1793671200),
    "Check now opens again at 6:00 am tomorrow.",
  );
  assert.equal(
    checkAgain(1000 + 3600, 1000),
    "Check now opens again in 60 min.",
  );
});
