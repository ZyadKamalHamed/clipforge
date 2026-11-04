// Sounds for a clip and its posts (SPEC 13): the music bed's level under the speech, how a sound is named,
// and which posts can take one. Instagram adds a trending sound to an account linked through Facebook, and
// you add a shortlisted one yourself in TikTok's window; every other route takes none.

import type { Account, PostSound, SoundSuggestion, TrendSound } from "../types.ts";

export const MUSIC_DB = -12; // clipforge/edits.py: the bed sits 12 dB under the speech unless you move it
export const MUSIC_DB_MIN = -24;
export const MUSIC_DB_MAX = -6;
export const SOUND_LEVEL = 15; // Instagram's audio_volume when you leave it, out of 100

export function levelText(db: number): string {
  return `${Number(Math.abs(db).toFixed(1))} dB under the speech`;
}

// A sound effect's length in tenths of a second; the click, at 15 ms, would read as 0.0 s.
export function lengthText(seconds: number): string {
  return seconds < 0.05 ? "under 0.1 s" : `${seconds.toFixed(1)} s`;
}

export function soundLabel(sound: { title: string; artist: string | null }): string {
  return sound.artist ? `${sound.title} by ${sound.artist}` : sound.title;
}

// A track's name with why it suits the clip, when it does.
export function trackLabel(name: string, why: string | null): string {
  return why ? `${name}, ${why.charAt(0).toLowerCase()}${why.slice(1)}` : name;
}

// Which list an account's posts pick a sound from, or null when its route can't take one.
export function pickable(account: Account): "tiktok" | "instagram" | null {
  if (account.platform === "tiktok") return "tiktok";
  return account.method === "api" && account.ig_mode === "facebook" ? "instagram" : null;
}

type Picked = TrendSound | SoundSuggestion | PostSound;

// What tells two sounds apart: where each is from and its id there.
export function soundValue(sound: Picked): string {
  const source = "source" in sound ? sound.source : sound.kind;
  return `${source}:${sound.external_id}`;
}

export function postSound(sound: TrendSound | SoundSuggestion, volume = SOUND_LEVEL): PostSound {
  return {
    source: "source" in sound ? sound.source : (sound.kind as PostSound["source"]),
    external_id: sound.external_id ?? "",
    title: sound.title,
    artist: sound.artist,
    url: sound.url,
    volume,
  };
}
