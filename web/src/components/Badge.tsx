// A short status word with a coloured dot: the word stays in the text colour, so it reads in both themes.

import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad" | "info";

const DOTS: Record<Tone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-panel-2 px-2 py-0.5 text-xs font-medium text-fg">
      <span aria-hidden className={`size-1.5 rounded-full ${DOTS[tone]}`} />
      {children}
    </span>
  );
}
