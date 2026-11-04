import assert from "node:assert/strict";
import { test } from "node:test";

import type { Account, SoundSuggestion, TrendSound } from "../types.ts";
import {
  lengthText,
  levelText,
  pickable,
  postSound,
  soundLabel,
  soundValue,
  trackLabel,
} from "./sounds.ts";

function account(fields: Partial<Account>): Account {
  return {
    id: "a1",
    platform: "tiktok",
    method: "browser",
    label: "",
    handle: "clips",
    display_name: null,
    avatar: null,
    status: "connected",
    status_note: null,
    paused: false,
    ig_mode: null,
    experimental: false,
    token_expires_at: null,
    last_checked_at: null,
    created_at: 1_793_000_000,
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
    today: { posts: 0, cap: 2, ceiling: 10 },
    last_post: null,
    ...fields,
  };
}

const LISTED: TrendSound = {
  id: "snd_1",
  platform: "instagram",
  source: "ig_trending",
  external_id: "m1",
  title: "Song m1",
  artist: "The Band",
  url: "https://www.instagram.com/reels/audio/m1/",
  region: null,
  mood_tags: [],
  energy: null,
  audio_type: "music",
  duration_s: 30,
  rank: 1,
  account_id: "a2",
  fetched_at: 1_793_000_000,
  created_at: 1_793_000_000,
  stale: false,
  link_id: "sound:snd_1",
};

test("levelText says how far under", () => {
  assert.equal(levelText(-12), "12 dB under the speech");
  assert.equal(levelText(-24), "24 dB under the speech");
  assert.equal(levelText(-6), "6 dB under the speech");
  assert.equal(levelText(-18.5), "18.5 dB under the speech");
  assert.equal(levelText(-12.04), "12 dB under the speech");
});

test("pickable follows the route", () => {
  assert.equal(pickable(account({})), "tiktok");
  assert.equal(
    pickable(
      account({ platform: "instagram", method: "api", ig_mode: "facebook" }),
    ),
    "instagram",
  );
  // Instagram adds a sound only to an account linked through Facebook
  assert.equal(
    pickable(
      account({ platform: "instagram", method: "api", ig_mode: "instagram" }),
    ),
    null,
  );
  assert.equal(
    pickable(account({ platform: "instagram", method: "browser" })),
    null,
  );
});

test("soundLabel names the sound and who made it", () => {
  assert.equal(soundLabel(LISTED), "Song m1 by The Band");
  assert.equal(soundLabel({ title: "Late Train", artist: null }), "Late Train");
});

test("a track that suits the clip says why after its name", () => {
  assert.equal(
    trackLabel("Night Drive", "Matches its calm mood"),
    "Night Drive, matches its calm mood",
  );
  assert.equal(trackLabel("Arcade Rush", null), "Arcade Rush");
});

test("a picked sound keeps where it is from, its id there and its level", () => {
  assert.equal(soundValue(LISTED), "ig_trending:m1");
  assert.deepEqual(postSound(LISTED), {
    source: "ig_trending",
    external_id: "m1",
    title: "Song m1",
    artist: "The Band",
    url: "https://www.instagram.com/reels/audio/m1/",
    volume: 15,
  });
  const suggested: SoundSuggestion = {
    kind: "shortlist",
    id: "snd_2",
    title: "Late Train",
    artist: null,
    why: "Matches its calm mood",
    stale: true,
    score: 3,
    external_id: "7234567890123456789",
    url: "https://www.tiktok.com/music/Late-Train-7234567890123456789",
    link_id: "sound:snd_2",
  };
  assert.equal(soundValue(suggested), "shortlist:7234567890123456789");
  assert.deepEqual(postSound(suggested, 40), {
    source: "shortlist",
    external_id: "7234567890123456789",
    title: "Late Train",
    artist: null,
    url: "https://www.tiktok.com/music/Late-Train-7234567890123456789",
    volume: 40,
  });
  assert.equal(soundValue(postSound(suggested)), soundValue(suggested));
});

test("a sound effect's length is in tenths of a second, and a click is under one", () => {
  assert.equal(lengthText(0.015), "under 0.1 s");
  assert.equal(lengthText(0.12), "0.1 s");
  assert.equal(lengthText(1.2), "1.2 s");
});
