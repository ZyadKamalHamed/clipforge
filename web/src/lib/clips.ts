// What the Project page says about a clip: its title, its tier and status in words, the order and filter
// it shows under, and where its download comes from.

import type { Tone } from "../components/Badge.tsx";
import type { Clip, Tier } from "../types.ts";
import { formatDuration } from "./format.ts";

export const TIERS: Record<Tier, { label: string; tone: Tone }> = {
  post: { label: "Post", tone: "ok" },
  test: { label: "Test", tone: "info" },
  weak: { label: "Weak", tone: "warn" },
  no: { label: "Don't post", tone: "bad" },
};

export type TierFilter = "all" | Tier;

export const TIER_FILTERS: { value: TierFilter; label: string }[] = [
  { value: "all", label: "All" },
  ...(Object.keys(TIERS) as Tier[]).map((tier) => ({
    value: tier,
    label: TIERS[tier].label,
  })),
];

export function inTier(clip: Clip, filter: TierFilter): boolean {
  return filter === "all" || clip.tier === filter;
}

export type Sort = "score" | "time";

// Best first goes by score, then by the brain's rank; a clip without either goes last.
export function sortClips(clips: Clip[], sort: Sort): Clip[] {
  const last = (n: number | null, flip: number) =>
    n === null ? Infinity : n * flip;
  return [...clips].sort((a, b) =>
    sort === "time"
      ? a.start_s - b.start_s
      : last(a.final_score, -1) - last(b.final_score, -1) ||
        last(a.rank, 1) - last(b.rank, 1),
  );
}

// A clip's hook title, or until it has one, where it starts.
export function clipTitle(clip: Clip): string {
  return clip.hook_title || `The moment at ${formatDuration(clip.start_s)}`;
}

export function timeRange(clip: Clip): string {
  return `${formatDuration(clip.start_s)} to ${formatDuration(clip.end_s)}`;
}

const STATUSES: Record<Clip["status"], { text: string; tone: Tone }> = {
  idea: { text: "Idea", tone: "neutral" },
  packaged: { text: "Idea", tone: "neutral" },
  queued: { text: "In the queue", tone: "neutral" },
  analyzing: { text: "Getting ready", tone: "accent" },
  rendering: { text: "Rendering", tone: "accent" },
  rendered: { text: "Ready", tone: "ok" },
  failed: { text: "Failed", tone: "bad" },
};

// A clip posted as it is is the video itself, so it never renders (clipforge/app.py fixed).
export function canRender(clip: Clip): boolean {
  return !clip.as_is;
}

export function clipStatus(clip: Clip): { text: string; tone: Tone } {
  return clip.discarded
    ? { text: "Discarded", tone: "neutral" }
    : STATUSES[clip.status];
}

export const DOWNLOAD_MAX = 40;

// One clip downloads as its own file, named as its card is; several come as one zip. Only clips with a
// render count, and the server takes at most 40 at once.
export function download(
  clips: Clip[],
): { href: string } | { problem: string } {
  const ready = clips.filter((clip) => clip.render?.video);
  if (!ready.length)
    return { problem: "None of those clips has a render to download yet." };
  if (ready.length > DOWNLOAD_MAX)
    return { problem: `Download at most ${DOWNLOAD_MAX} clips at a time.` };
  if (ready.length === 1) {
    const [clip] = ready;
    const name = `${clip.rank ?? ""} ${clipTitle(clip)}`.trim();
    return {
      href: `${clip.render?.video}?download=${encodeURIComponent(name)}`,
    };
  }
  return {
    href: `/api/clips/download?ids=${ready.map((clip) => clip.id).join(",")}`,
  };
}
