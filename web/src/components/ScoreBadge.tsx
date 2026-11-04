// A clip's score with its tier: a dot in the tier's colour, and the tier's word, so the colour is never all
// it says. It sits on the clip's picture, so it keeps a dark ground in both themes.

import { TIERS } from "../lib/clips.ts";
import type { Tier } from "../types.ts";
import type { Tone } from "./Badge.tsx";

const DOTS: Record<Tone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
};

export function ScoreBadge({ score, tier }: { score: number | null; tier: Tier | null }) {
  if (score === null && tier === null) return null;
  const word = tier ? TIERS[tier] : null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-black/70 px-2 py-0.5 text-xs font-semibold text-white">
      {word && <span aria-hidden className={`size-1.5 rounded-full ${DOTS[word.tone]}`} />}
      {score !== null && (
        <span className="tabular-nums">
          <span className="sr-only">Score </span>
          {Math.round(score)}
        </span>
      )}
      {word && <span className="font-medium">{word.label}</span>}
    </span>
  );
}
