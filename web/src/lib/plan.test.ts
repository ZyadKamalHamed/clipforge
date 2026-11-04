import assert from "node:assert/strict";
import { test } from "node:test";

import type { Account, PlanWarning } from "../types.ts";
import {
  CAPTION_LIMIT,
  captionUnits,
  dayThenTime,
  firstLine,
  groupAccounts,
  hashtagText,
  isOn,
  localInput,
  parseHashtags,
  parseLocal,
  sortWarnings,
  POST_DEFAULTS,
  setTimeZone,
  switchesFor,
  switchesOff,
  switchOn,
  timeZone,
  trialLine,
  warningTone,
  whyNot,
} from "./plan.ts";

function account(id: string, fields: Partial<Account> = {}): Account {
  return {
    id,
    platform: "tiktok",
    method: "browser",
    label: "",
    handle: id,
    display_name: null,
    avatar: null,
    status: "connected",
    status_note: null,
    paused: false,
    ig_mode: null,
    experimental: false,
    token_expires_at: null,
    last_checked_at: null,
    created_at: 0,
    settings: {
      niche_tags: [],
      assisted: false,
      hashtags: [],
      cta: "",
      daily_cap: null,
      windows: null,
      pair_id: null,
      creator_rewards: false,
      style_kit: {
        preset: null,
        font: null,
        accent: null,
        hook_fill: null,
        hook_text: null,
        credit_format: null,
        gameplay_mode: null,
      },
    },
    today: { posts: 0, cap: 3, ceiling: 10 },
    last_post: null,
    ...fields,
  };
}

function warning(
  code: string,
  blocking: boolean,
  key: string | null = null,
): PlanWarning {
  return { code, sentence: code, key, blocking, move_to: null };
}

// Seconds since 1970 for a UTC time, as the engine sends its times.
const utc = (y: number, m: number, d: number, h: number, min = 0) =>
  Date.UTC(y, m - 1, d, h, min) / 1000;

test("groupAccounts puts TikTok first, then Instagram, each in the order the engine lists them", () => {
  const groups = groupAccounts([
    account("ig1", { platform: "instagram", method: "api" }),
    account("tt1"),
    account("ig2", { platform: "instagram", method: "api" }),
    account("tt2"),
  ]);
  assert.deepEqual(
    groups.map((g) => [g.platform, g.accounts.map((a) => a.id)]),
    [
      ["tiktok", ["tt1", "tt2"]],
      ["instagram", ["ig1", "ig2"]],
    ],
  );
  // A platform with no accounts has no group.
  assert.deepEqual(
    groupAccounts([account("ig1", { platform: "instagram" })]).map(
      (g) => g.platform,
    ),
    ["instagram"],
  );
  assert.deepEqual(groupAccounts([]), []);
});

test("whyNot says why an account that needs a login can't be picked, and nothing for the rest", () => {
  assert.equal(whyNot(account("tt1")), null);
  assert.equal(whyNot(account("tt1", { status: "token_expiring" })), null);
  assert.equal(whyNot(account("tt1", { paused: true })), null);
  assert.equal(
    whyNot(account("tt1", { status: "needs_login" })),
    "Press Log in to post here.",
  );
  assert.equal(
    whyNot(
      account("ig1", {
        platform: "instagram",
        method: "api",
        status: "needs_login",
      }),
    ),
    "Log in again on the Accounts page to post here.",
  );
  assert.equal(
    whyNot(account("tt1", { status: "error" })),
    "Check it on the Accounts page to post here.",
  );
});

test("sortWarnings puts the ones that stop posting first and keeps the order within each", () => {
  const sorted = sortWarnings([
    warning("duplicate", false),
    warning("cap", true),
    warning("light_edits", false),
    warning("caption_too_long", true),
  ]);
  assert.deepEqual(
    sorted.map((w) => w.code),
    ["cap", "caption_too_long", "duplicate", "light_edits"],
  );
  assert.deepEqual(sortWarnings([]), []);
});

test("warningTone: red stops posting, muted only says what ClipForge does first, amber the rest", () => {
  assert.equal(warningTone(warning("cap", true)), "text-bad");
  assert.equal(warningTone(warning("renders_variant", false)), "text-muted");
  assert.equal(warningTone(warning("duplicate", false)), "text-warn");
});

test("dayThenTime says today, tomorrow, the weekday this week, else the date, in Sydney time", () => {
  // 7:00 am on Tue 3 Nov 2026 in Sydney is still the 2nd in UTC.
  const now = utc(2026, 11, 2, 20);
  assert.equal(dayThenTime(utc(2026, 11, 3, 8, 40), now), "Today 7:40 pm");
  assert.equal(dayThenTime(utc(2026, 11, 2, 21, 5), now), "Today 8:05 am");
  assert.equal(dayThenTime(utc(2026, 11, 3, 13), now), "Tomorrow 12:00 am");
  assert.equal(dayThenTime(utc(2026, 11, 5, 1, 15), now), "Thu 12:15 pm");
  assert.equal(dayThenTime(utc(2026, 11, 8, 22), now), "Mon 9:00 am");
  assert.equal(dayThenTime(utc(2026, 11, 9, 22), now), "Tue 10 Nov, 9:00 am");
  // A time gone by says its date.
  assert.equal(dayThenTime(utc(2026, 11, 1, 22), now), "Mon 2 Nov, 9:00 am");
  // September is Sep, as the engine's sentences say it.
  assert.equal(dayThenTime(utc(2026, 9, 14, 23), now), "Tue 15 Sep, 9:00 am");
});

test("dayThenTime follows the clocks going forward and back", () => {
  // Daylight saving starts at 2 am on Sun 4 Oct 2026: an hour of real time later, the clock reads two later.
  const before = utc(2026, 10, 3, 10); // 8:00 pm Sat, standard time
  assert.equal(
    dayThenTime(utc(2026, 10, 3, 15, 30), before),
    "Tomorrow 1:30 am",
  );
  assert.equal(
    dayThenTime(utc(2026, 10, 3, 16, 30), before),
    "Tomorrow 3:30 am",
  );
  assert.equal(dayThenTime(utc(2026, 10, 3, 22), before), "Tomorrow 9:00 am");
  // It ends at 3 am on Sun 4 Apr 2027, when 2:30 am comes round twice.
  const autumn = utc(2027, 4, 3, 9); // 8:00 pm Sat, daylight time
  assert.equal(dayThenTime(utc(2027, 4, 3, 15, 30), autumn), "Tomorrow 2:30 am");
  assert.equal(dayThenTime(utc(2027, 4, 3, 16, 30), autumn), "Tomorrow 2:30 am");
  assert.equal(dayThenTime(utc(2027, 4, 3, 23), autumn), "Tomorrow 9:00 am");
});

test("localInput and parseLocal carry a time to and from a date and time field in Sydney time", () => {
  assert.equal(localInput(utc(2026, 11, 3, 8, 40)), "2026-11-03T19:40");
  assert.equal(localInput(utc(2026, 10, 3, 22)), "2026-10-04T09:00");
  assert.equal(parseLocal("2026-11-03T19:40"), utc(2026, 11, 3, 8, 40));
  assert.equal(parseLocal("2026-10-04T09:00"), utc(2026, 10, 3, 22));
  assert.equal(parseLocal("2026-10-03T20:00"), utc(2026, 10, 3, 10));
  // The hour the clocks skip doesn't exist, so it is read as the hour after.
  assert.equal(parseLocal("2026-10-04T02:30"), utc(2026, 10, 3, 16, 30));
  assert.equal(parseLocal(""), null);
  assert.equal(parseLocal("soon"), null);
});

test("the times follow the posting time zone, and one the browser doesn't know keeps Sydney's", () => {
  try {
    setTimeZone("Asia/Bangkok");
    assert.equal(timeZone(), "Asia/Bangkok");
    // 7:40 pm on Tue 3 Nov in Sydney is 3:40 pm in Bangkok (UTC+7).
    assert.equal(dayThenTime(utc(2026, 11, 3, 8, 40), utc(2026, 11, 3, 1)), "Today 3:40 pm");
    assert.equal(localInput(utc(2026, 11, 3, 8, 40)), "2026-11-03T15:40");
    assert.equal(parseLocal("2026-11-03T15:40"), utc(2026, 11, 3, 8, 40));
    setTimeZone("Not/A_Zone");
    assert.equal(timeZone(), "Australia/Sydney");
    assert.equal(localInput(utc(2026, 11, 3, 8, 40)), "2026-11-03T19:40");
  } finally {
    setTimeZone("Australia/Sydney");
  }
});

test("captionUnits counts the caption with its hashtags as the apps count them", () => {
  assert.equal(captionUnits("Hello", []), 5);
  assert.equal(
    captionUnits("Hello", ["deadlines", "money"]),
    "Hello\n\n#deadlines #money".length,
  );
  assert.equal(captionUnits("", ["a"]), 2);
  assert.equal(captionUnits("🔥", []), 2);
  assert.deepEqual(CAPTION_LIMIT, { tiktok: 4000, instagram: 2200 });
});

test("hashtags are written with # and spaces, and read back without them", () => {
  assert.equal(hashtagText(["deadlines", "money"]), "#deadlines #money");
  assert.equal(hashtagText([]), "");
  assert.deepEqual(parseHashtags("#deadlines #money"), ["deadlines", "money"]);
  assert.deepEqual(parseHashtags(" deadlines,  ##money\n#fyp "), [
    "deadlines",
    "money",
    "fyp",
  ]);
  assert.deepEqual(parseHashtags("  "), []);
});

test("switchesOff names a post's switches that differ from its platform's block, that platform's only", () => {
  assert.deepEqual(switchesOff("tiktok", POST_DEFAULTS, POST_DEFAULTS), []);
  const block = {
    ...POST_DEFAULTS,
    comments: false,
    visibility: "only_me" as const,
  };
  assert.deepEqual(
    switchesOff(
      "tiktok",
      { ...block, comments: true, disclosure: true },
      block,
    ),
    ["Comments on", "Content disclosure on"],
  );
  assert.deepEqual(
    switchesOff("tiktok", { ...block, visibility: "friends" }, block),
    ["Friends"],
  );
  // Instagram's posts have one switch; TikTok's never show on them, nor whether ClipForge stops before Post.
  assert.deepEqual(
    switchesOff(
      "instagram",
      { ...POST_DEFAULTS, duet: false, assisted: true },
      POST_DEFAULTS,
    ),
    [],
  );
  assert.deepEqual(
    switchesOff(
      "instagram",
      { ...POST_DEFAULTS, share_to_feed: false },
      POST_DEFAULTS,
    ),
    ["Share to feed off"],
  );
});

test("trialLine says on Review that a post is a trial reel, and who shares it with followers", () => {
  assert.equal(trialLine("tiktok", { ...POST_DEFAULTS, trial_reel: true }), null);
  assert.equal(trialLine("instagram", POST_DEFAULTS), null);
  assert.equal(
    trialLine("instagram", { ...POST_DEFAULTS, trial_reel: true }),
    "Trial reel: people who don't follow this account see it first. You share it with followers from Instagram.",
  );
  assert.equal(
    trialLine("instagram", {
      ...POST_DEFAULTS,
      trial_reel: true,
      trial_graduation: "auto",
    }),
    "Trial reel: people who don't follow this account see it first. Instagram shares it with followers if it does well.",
  );
});

test("POST_DEFAULTS are the engine's PostOptions defaults", () => {
  assert.deepEqual(POST_DEFAULTS, {
    visibility: "everyone",
    comments: true,
    duet: true,
    stitch: true,
    disclosure: false,
    share_to_feed: true,
    trial_reel: false,
    trial_graduation: "manual",
    assisted: false,
  });
});

test("switchesFor offers a trial reel only through Meta's API, and hides Share to feed while it is on", () => {
  const keys = (
    platform: "tiktok" | "instagram",
    method: "api" | "browser",
    trial = false,
  ) =>
    switchesFor(platform, method, { ...POST_DEFAULTS, trial_reel: trial }).map(
      (s) => s.key,
    );
  assert.deepEqual(keys("instagram", "api"), ["trial_reel", "share_to_feed"]);
  assert.deepEqual(keys("instagram", "browser"), ["share_to_feed"]);
  assert.deepEqual(keys("instagram", "api", true), [
    "trial_reel",
    "trial_graduation",
  ]);
  assert.deepEqual(keys("tiktok", "browser", true), [
    "comments",
    "duet",
    "stitch",
    "disclosure",
  ]);
  const [trial, graduate] = switchesFor("instagram", "api", {
    ...POST_DEFAULTS,
    trial_reel: true,
  });
  assert.equal(trial.label, "Trial reel");
  assert.equal(
    trial.hint,
    "Shown only to people who don't follow this account at first.",
  );
  assert.equal(graduate.label, "Share with followers if it does well");
  assert.equal(
    graduate.hint,
    "Off: you share it from Instagram when you choose.",
  );
  assert.equal(
    isOn({ ...POST_DEFAULTS, trial_graduation: "auto" }, "trial_graduation"),
    true,
  );
  assert.equal(isOn(POST_DEFAULTS, "trial_graduation"), false);
  assert.deepEqual(switchOn("trial_graduation", true), {
    trial_graduation: "auto",
  });
  assert.deepEqual(switchOn("share_to_feed", false), { share_to_feed: false });
  // a post's line names a trial reel that differs from its block
  assert.deepEqual(
    switchesOff(
      "instagram",
      { ...POST_DEFAULTS, trial_reel: true },
      POST_DEFAULTS,
    ),
    ["Trial reel on"],
  );
});

test("firstLine is a caption's first line with words in it", () => {
  assert.equal(firstLine("\n  Hook first.\nThen more."), "Hook first.");
  assert.equal(firstLine(""), "");
});
