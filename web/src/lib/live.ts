// What each running job has done lately, from the event stream, so the window can show its progress and
// time left (etaSeconds over the samples) between fetches of the jobs list, and which project or clip
// each one is for.

import type { JobEvent } from "../types.ts";

const WINDOW_S = 60; // the time left is fitted to this much of the latest progress
const FINISHED = new Set(["done", "failed", "cancelled"]);

export type LiveJob = JobEvent & { samples: Array<[number, number]> };

export function liveJobs() {
  const jobs = new Map<number, LiveJob>();
  const listeners = new Set<() => void>();
  let version = 0;

  function changed() {
    version += 1;
    for (const listener of listeners) listener();
  }

  return {
    // now is in seconds, as etaSeconds takes it.
    push(job: JobEvent, now: number) {
      if (FINISHED.has(job.status)) {
        if (jobs.delete(job.id)) changed();
        return;
      }
      const seen = jobs.get(job.id);
      // A retry starts again from nothing, so the old samples would only drag the line down.
      const kept = seen && job.progress >= seen.progress ? seen.samples.filter(([t]) => now - t <= WINDOW_S) : [];
      jobs.set(job.id, { ...job, samples: [...kept, [now, job.progress]] });
      changed();
    },
    get: (id: number): LiveJob | undefined => jobs.get(id),
    // The unfinished jobs for a project (its own and its clips') or for one clip, oldest first.
    of: (ref: string): LiveJob[] => [...jobs.values()].filter((j) => j.ref_id === ref || j.project === ref),
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    version: () => version,
  };
}

export type LiveJobs = ReturnType<typeof liveJobs>;
