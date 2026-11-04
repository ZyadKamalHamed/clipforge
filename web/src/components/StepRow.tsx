// A project's steps in a row (SPEC 14.3): each one's state as an icon, and as a word for screen readers,
// the running one's share done and, once it can be guessed, about how long it has left.

import { Check, Circle, Clock, LoaderCircle, type LucideIcon, X } from "lucide-react";

import { formatDuration } from "../lib/format.ts";
import type { Step, StepStatus } from "../types.ts";

const STATES: Record<StepStatus, { word: string; icon: LucideIcon; tone: string }> = {
  done: { word: "Done", icon: Check, tone: "text-ok" },
  running: { word: "Running", icon: LoaderCircle, tone: "text-accent motion-safe:animate-spin" },
  waiting: { word: "Waiting", icon: Clock, tone: "text-warn" },
  failed: { word: "Failed", icon: X, tone: "text-bad" },
  todo: { word: "To do", icon: Circle, tone: "text-faint" },
};

export function StepRow({ steps, eta }: { steps: Step[]; eta: number | null }) {
  const timed = steps.findIndex((step) => step.status === "running");
  return (
    <ol aria-label="Steps" className="flex flex-wrap gap-2">
      {steps.map((step, k) => {
        const state = STATES[step.status] ?? STATES.todo;
        const Icon = state.icon;
        const percent = Math.round(Math.min(1, Math.max(0, step.progress)) * 100);
        return (
          <li
            key={step.key}
            className={
              "flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs " +
              (step.status === "todo" ? "text-muted" : "text-fg")
            }
          >
            <Icon aria-hidden size={14} className={state.tone} />
            <span>{step.label}</span>
            <span className="sr-only">, {state.word}</span>
            {step.status === "running" && <span className="text-muted tabular-nums">{percent}%</span>}
            {k === timed && eta !== null && <span className="text-muted">About {formatDuration(eta)} left</span>}
          </li>
        );
      })}
    </ol>
  );
}
