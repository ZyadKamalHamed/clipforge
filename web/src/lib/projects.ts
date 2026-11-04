// What the Create and Projects pages say about a project: its title, where it is in words, and which
// filter chip and search it falls under.

import type { Tone } from "../components/Badge.tsx";
import type { Project } from "../types.ts";

export type Filter = "all" | "working" | "ready" | "failed";

export const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "working", label: "Working" },
  { value: "ready", label: "Ready" },
  { value: "failed", label: "Failed" },
];

const WORKING = new Set(["queued", "running", "waiting"]);
const STOPPED = new Set(["failed", "refused"]);

export function inFilter(project: Project, filter: Filter): boolean {
  if (filter === "working") return WORKING.has(project.status);
  if (filter === "ready") return project.status === "ready";
  if (filter === "failed") return STOPPED.has(project.status);
  return true;
}

// A search looks in the title the card shows, whatever its case; a blank one matches everything.
export function matchesSearch(project: Project, search: string): boolean {
  const words = search.trim().toLocaleLowerCase();
  return !words || displayTitle(project).toLocaleLowerCase().includes(words);
}

// A project's title, or until its details come, its file's name or its link.
export function displayTitle(project: Project): string {
  if (project.title) return project.title;
  const url = project.source_url;
  if (!url) return "A new project";
  if (project.source_kind !== "file") return url;
  const name = url.slice(url.lastIndexOf("/") + 1);
  try {
    return decodeURIComponent(name);
  } catch {
    return name; // not a name a file:// link would have, so shown as it came
  }
}

export function clipsLabel(n: number): string {
  if (n === 0) return "No clips";
  return n === 1 ? "1 clip" : `${n} clips`;
}

export type Status = { tone: Tone; text: string; working: boolean; detail: string | null };

// Where a project is, in a word or two, and the sentence that goes with it when it is waiting or stopped.
export function statusOf(project: Project): Status {
  switch (project.status) {
    case "running": {
      const step = project.steps.find((s) => s.status === "running");
      return { tone: "accent", text: step?.label ?? "Working", working: true, detail: null };
    }
    case "waiting":
      return { tone: "warn", text: "Waiting", working: true, detail: project.message };
    case "ready":
      return { tone: "ok", text: "Ready", working: false, detail: null };
    case "failed":
      return { tone: "bad", text: "Failed", working: false, detail: project.error };
    case "refused":
      return { tone: "bad", text: "Can't use this video", working: false, detail: project.error };
    default:
      return { tone: "neutral", text: "In the queue", working: true, detail: null };
  }
}
