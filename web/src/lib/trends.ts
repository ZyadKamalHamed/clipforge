// The Trends page (SPEC 13): the filters over its sounds, a listed sound's type in words, hashtags typed
// in a box split into tags and shown again with their #, the line naming the accounts Instagram gives no
// list, and when an account's list was last read or why it couldn't be.

import type { Platform, TrendRead, TrendSound } from "../types.ts";
import { formatAgo } from "./format.ts";

export type SoundFilters = {
  platform: "" | Platform; // "" for both
  mood: string; // "" for any
  region: "" | "AU" | "US"; // "" for any
  hideStale: boolean;
};

export const ANY: SoundFilters = { platform: "", mood: "", region: "", hideStale: false };
export const NO_MATCH = "No sounds match the filters.";

// Whether a platform's section shows at all.
export function showsPlatform(filters: SoundFilters, platform: Platform): boolean {
  return filters.platform === "" || filters.platform === platform;
}

// Whether a sound's row shows. Instagram says neither a sound's mood nor its region, so a mood or a region
// picked hides its lists.
export function matches(sound: TrendSound, filters: SoundFilters): boolean {
  if (filters.mood && !sound.mood_tags.includes(filters.mood)) return false;
  if (filters.region && sound.region !== filters.region) return false;
  return !(filters.hideStale && sound.stale);
}

export function soundType(sound: TrendSound): string | null {
  if (sound.audio_type === "music") return "Music";
  if (sound.audio_type === "original_sound") return "Original sound";
  return null;
}

// What was typed in the Hashtags box, one tag per word; the engine takes off each #.
export function splitTags(text: string): string[] {
  return text.split(/[\s,]+/).filter(Boolean);
}

export function tagLine(tags: string[]): string {
  return tags.map((tag) => `#${tag}`).join(" ");
}

export function modeBLine(handles: (string | null)[]): string | null {
  const named = handles.filter((h): h is string => Boolean(h)).map((h) => `@${h}`);
  if (named.length === 0) return null;
  return `Instagram doesn't offer trending sounds to accounts linked through Instagram Login: ${named.join(", ")}.`;
}

export function readLine(read: TrendRead | null, now: number): { text: string; bad: boolean } {
  if (read === null || read.read_at === null) return { text: "Not read yet. ClipForge reads it each morning.", bad: false };
  const ago = formatAgo(read.read_at, now);
  if (read.result === "ok") return { text: `Read ${ago}.`, bad: false };
  return { text: `${read.error ?? "It couldn't be read."} Last tried ${ago}.`, bad: true };
}
