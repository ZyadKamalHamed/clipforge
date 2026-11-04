import assert from "node:assert/strict";
import { test } from "node:test";

import type { Account } from "../types.ts";
import {
  WINDOW_LINE,
  accountName,
  capText,
  capWarning,
  lastPostText,
  listText,
  methodText,
  parseList,
  parseWindows,
  reconnectFor,
  statusLabel,
  windowLines,
} from "./accounts.ts";

const NOW = 1_793_000_000;

function account(fields: Partial<Account> = {}): Account {
  return {
    id: "a1",
    platform: "tiktok",
    method: "browser",
    label: "",
    handle: "clips_tt",
    display_name: null,
    avatar: null,
    status: "connected",
    status_note: null,
    paused: false,
    ig_mode: null,
    experimental: false,
    token_expires_at: null,
    last_checked_at: null,
    created_at: NOW,
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
    today: { posts: 1, cap: 2, ceiling: 10 },
    last_post: null,
    ...fields,
  };
}

test("statusLabel says each status in words with its tone", () => {
  assert.deepEqual(statusLabel(account()), { text: "Connected", tone: "ok" });
  assert.deepEqual(statusLabel(account({ status: "needs_login" })), {
    text: "Needs login",
    tone: "warn",
  });
  assert.deepEqual(statusLabel(account({ status: "token_expiring" })), {
    text: "Token expiring",
    tone: "warn",
  });
  assert.deepEqual(statusLabel(account({ status: "error" })), {
    text: "Not working",
    tone: "bad",
  });
  // A status this window doesn't know yet reads as one that needs a look, never as connected.
  assert.equal(
    statusLabel({ status: "later" as Account["status"] }).tone,
    "bad",
  );
});

test("capWarning warns above 3 a day on TikTok and 2 on Instagram, and past a route's most", () => {
  const tiktok =
    "More than 3 a day on one TikTok account risks looking like spam.";
  const instagram =
    "More than 2 a day on one Instagram account risks looking like spam.";
  assert.equal(capWarning("tiktok", 3), null);
  assert.equal(capWarning("tiktok", 4), tiktok);
  assert.equal(capWarning("tiktok", "10", 10), tiktok);
  assert.equal(capWarning("instagram", "2"), null);
  assert.equal(capWarning("instagram", " 3 "), instagram);
  assert.equal(
    capWarning("instagram", 3, 1),
    "ClipForge posts at most 1 a day this way, so this account posts 1.",
  );
  assert.equal(capWarning("instagram", 1, 1), null);
  assert.equal(capWarning("tiktok", ""), null);
  assert.equal(capWarning("tiktok", "lots"), null); // the refusal sentence covers it
  assert.equal(capWarning("tiktok", null), null);
});

test("capText puts today's posts against the cap", () => {
  assert.equal(capText(account()), "1 of 2 today");
  assert.equal(
    capText(account({ today: { posts: 0, cap: 1, ceiling: 10 } })),
    "0 of 1 today",
  );
  assert.equal(
    capText(account({ today: { posts: 3, cap: 2, ceiling: 10 } })),
    "3 of 2 today",
  );
});

test("an account is named by its handle, else its label, else its platform", () => {
  assert.equal(accountName(account()), "@clips_tt");
  assert.equal(
    accountName(account({ handle: null, label: "Gaming" })),
    "Gaming",
  );
  assert.equal(accountName(account({ handle: null })), "New TikTok account");
  assert.equal(
    accountName(account({ handle: null, platform: "instagram" })),
    "New Instagram account",
  );
});

test("how each account posts, in words", () => {
  assert.equal(methodText(account()), "Posts in Chrome");
  assert.equal(
    methodText(account({ method: "api", ig_mode: "facebook" })),
    "Meta's API, through Facebook",
  );
  assert.equal(
    methodText(account({ method: "api", ig_mode: "instagram" })),
    "Meta's API, Instagram login",
  );
});

test("the last post says how long ago it went", () => {
  assert.equal(lastPostText(account(), NOW), "No posts yet");
  const went = {
    id: "s1",
    clip_title: "The deadline",
    url: null,
    posted_at: NOW - 7200,
  };
  assert.equal(
    lastPostText(account({ last_post: went }), NOW),
    "Last post 2 h ago",
  );
  assert.equal(
    lastPostText(account({ last_post: { ...went, posted_at: null } }), NOW),
    "Last post",
  );
});

test("each account reconnects its own way", () => {
  assert.deepEqual(reconnectFor(account({ status: "needs_login" })), {
    kind: "login",
    label: "Log in",
  });
  assert.deepEqual(reconnectFor(account()), {
    kind: "login",
    label: "Log in again",
  });
  const api = { method: "api", status: "token_expiring" } as const;
  assert.deepEqual(reconnectFor(account({ ...api, ig_mode: "instagram" })), {
    kind: "token",
    label: "Reconnect",
  });
  assert.deepEqual(reconnectFor(account({ ...api, ig_mode: "facebook" })), {
    kind: "facebook",
    label: "Reconnect",
  });
});

test("lists are typed with commas or one a line, each word once", () => {
  assert.deepEqual(parseList(" Gaming, streamers\nGAMING,, \n"), [
    "Gaming",
    "streamers",
  ]);
  assert.deepEqual(parseList(""), []);
  assert.equal(listText(["gaming", "streamers"]), "gaming, streamers");
  assert.deepEqual(parseList(listText(["a", "b"])), ["a", "b"]);
});

test("posting windows are typed one a line and read back the same", () => {
  assert.deepEqual(parseWindows("7:00 to 09:00\n 18:30-22:00 \n"), {
    windows: [
      { start: "07:00", end: "09:00" },
      { start: "18:30", end: "22:00" },
    ],
  });
  assert.deepEqual(parseWindows("  \n"), { windows: null });
  assert.deepEqual(parseWindows("07:00 to 09:00\nmornings"), {
    error: WINDOW_LINE,
  });
  // Whether a window ends after it starts is the engine's to say.
  assert.deepEqual(parseWindows("09:00 to 07:00"), {
    windows: [{ start: "09:00", end: "07:00" }],
  });
  const windows = [{ start: "07:00", end: "09:00" }];
  assert.equal(windowLines(windows), "07:00 to 09:00");
  assert.equal(windowLines(null), "");
  assert.deepEqual(parseWindows(windowLines(windows)), { windows });
});
