import assert from "node:assert/strict";
import { test } from "node:test";

import type { TrendRead, TrendSound } from "../types.ts";
import { ANY, matches, modeBLine, readLine, showsPlatform, soundType, splitTags, tagLine } from "./trends.ts";

const NOW = 1_793_000_000;

function sound(fields: Partial<TrendSound>): TrendSound {
  return {
    id: "snd_1",
    platform: "tiktok",
    source: "shortlist",
    external_id: "7234567890123456789",
    title: "Late Train",
    artist: null,
    url: "https://www.tiktok.com/music/Late-Train-7234567890123456789",
    region: null,
    mood_tags: [],
    energy: null,
    audio_type: null,
    duration_s: null,
    rank: null,
    account_id: null,
    fetched_at: null,
    created_at: NOW,
    stale: false,
    link_id: "sound:snd_1",
    ...fields,
  };
}

function read(fields: Partial<TrendRead>): TrendRead {
  return { id: "ig:a1", read_at: null, newest_at: null, seen_at: null, result: null, error: null, ...fields };
}

test("with no filters every sound shows", () => {
  assert.equal(matches(sound({}), ANY), true);
  assert.equal(matches(sound({ stale: true }), ANY), true);
  assert.equal(showsPlatform(ANY, "tiktok"), true);
  assert.equal(showsPlatform(ANY, "instagram"), true);
});

test("the platform filter keeps one platform's section", () => {
  const tiktok = { ...ANY, platform: "tiktok" as const };
  assert.equal(showsPlatform(tiktok, "tiktok"), true);
  assert.equal(showsPlatform(tiktok, "instagram"), false);
});

test("mood, region and stale each narrow the rows", () => {
  const calm = sound({ mood_tags: ["calm", "dark"], region: "AU" });
  assert.equal(matches(calm, { ...ANY, mood: "calm" }), true);
  assert.equal(matches(calm, { ...ANY, mood: "hype" }), false);
  assert.equal(matches(calm, { ...ANY, region: "AU" }), true);
  assert.equal(matches(calm, { ...ANY, region: "US" }), false);
  assert.equal(matches(sound({ stale: true }), { ...ANY, hideStale: true }), false);
  assert.equal(matches(sound({}), { ...ANY, hideStale: true }), true);
  // Instagram says neither a sound's mood nor its region, so those filters hide its lists
  const listed = sound({ platform: "instagram", source: "ig_trending", audio_type: "music" });
  assert.equal(matches(listed, { ...ANY, mood: "calm" }), false);
  assert.equal(matches(listed, { ...ANY, region: "AU" }), false);
});

test("a listed sound's type in words", () => {
  assert.equal(soundType(sound({ audio_type: "music" })), "Music");
  assert.equal(soundType(sound({ audio_type: "original_sound" })), "Original sound");
  assert.equal(soundType(sound({})), null);
});

test("hashtags typed with spaces, commas or a # each become one tag", () => {
  assert.deepEqual(splitTags("#gaming  clips,fyp\n#Minecraft"), ["#gaming", "clips", "fyp", "#Minecraft"]);
  assert.deepEqual(splitTags("   "), []);
  assert.equal(tagLine(["gaming", "clips"]), "#gaming #clips");
  assert.equal(tagLine([]), "");
});

test("the line naming accounts Instagram gives no list", () => {
  assert.equal(
    modeBLine(["drama.clips", null, "gaming.clips"]),
    "Instagram doesn't offer trending sounds to accounts linked through Instagram Login: @drama.clips, @gaming.clips.",
  );
  assert.equal(modeBLine([]), null);
  assert.equal(modeBLine([null]), null);
});

test("when an account's list was last read, or why it couldn't be", () => {
  assert.deepEqual(readLine(null, NOW), { text: "Not read yet. ClipForge reads it each morning.", bad: false });
  assert.deepEqual(readLine(read({ read_at: NOW - 7200, result: "ok" }), NOW), { text: "Read 2 h ago.", bad: false });
  const failed = "Instagram's trending sounds couldn't be read. ClipForge tries again tomorrow.";
  assert.deepEqual(readLine(read({ read_at: NOW - 120, result: "error", error: failed }), NOW), {
    text: `${failed} Last tried 2 min ago.`,
    bad: true,
  });
});
