// What the Channels page says about a clipping channel (SPEC 14.9 and 10.9): the first problem with its
// form, its check time, when it checks next and when Check now opens again.

import type { ClipChannel, RightsBasis } from "../types.ts";
import { timeThenDay, wallClock } from "./plan.ts";

export const NAME_MAX = 60; // clipforge/watch/models.py
export const HASHTAGS_MAX = 10;
export const BACKFILL_MAX = 5;
export const NAME_NEEDED = "Give the clipping channel a name.";
export const NAME_LONG = `Keep the name to ${NAME_MAX} characters.`;
export const CHECK_TIME = "Give the check time as hours and minutes, like 06:00.";
export const TOO_MANY_TAGS = `Give at most ${HASHTAGS_MAX} hashtags.`;
export const PAUSED_LINE = "No checks while it is paused.";
export const NO_SOURCE_LINE = "No checks until it watches a YouTube channel.";

export const RIGHTS: { value: RightsBasis; label: string }[] = [
  { value: "not_cleared", label: "Not cleared yet" },
  { value: "permission", label: "I have permission" },
  { value: "clipping_programme", label: "A clipping programme" },
];

export const ON_LIMIT: { value: ClipChannel["on_limit"]; label: string }[] = [
  { value: "wait", label: "Wait for the reset" },
  { value: "economy", label: "Economy models" },
  { value: "basic", label: "Basic mode" },
];

const CLOCK = /^([01][0-9]|2[0-3]):([0-5][0-9])$/;

export type ChannelForm = { name: string; checkTime: string; hashtags: string[] };

export function validChannel(form: ChannelForm): string | null {
  const name = form.name.trim();
  if (!name) return NAME_NEEDED;
  if (name.length > NAME_MAX) return NAME_LONG;
  if (!CLOCK.test(form.checkTime)) return CHECK_TIME;
  if (form.hashtags.length > HASHTAGS_MAX) return TOO_MANY_TAGS;
  return null;
}

export function timeLabel(hhmm: string): string {
  const [, hours, minutes] = CLOCK.exec(hhmm) ?? [];
  if (hours === undefined) return hhmm;
  const hour = Number(hours);
  return `${hour % 12 || 12}:${minutes} ${hour < 12 ? "am" : "pm"}`;
}

export function nextCheckLabel(channel: Pick<ClipChannel, "paused" | "next_check_at">, now: number): string {
  if (channel.paused) return PAUSED_LINE;
  if (channel.next_check_at === null) return NO_SOURCE_LINE;
  if (channel.next_check_at <= now + 60) return "A check is due now.";
  return `Next check at ${timeThenDay(channel.next_check_at, now)}.`;
}

export function checkNowLabel(after: number | null, now: number): string | null {
  if (after === null) return null;
  const when = timeThenDay(after, now);
  return `Check now opens at ${when.endsWith(" today") ? wallClock(after) : when}.`;
}

// What Delete says goes with the clipping channel.
export function deleteLine(drafts: number): string {
  const stay = "Its projects and the posts you approved stay.";
  if (drafts === 0) return `It has no drafts. ${stay}`;
  return `${drafts === 1 ? "Its draft is" : `Its ${drafts} drafts are`} discarded. ${stay}`;
}

// The links typed in, one a line: each once, blank lines left out.
export function linkLines(text: string): string[] {
  return [...new Set(text.split("\n").map((line) => line.trim()).filter(Boolean))];
}
