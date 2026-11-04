// What is running now (SPEC 14.1): a button that counts the active jobs and opens them in a dialog, each
// with its progress and, once it has run long enough to tell, about how long it has left.

import { useQuery } from "@tanstack/react-query";
import { Activity, LoaderCircle } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { etaSeconds } from "../lib/eta.ts";
import { formatDuration } from "../lib/format.ts";
import { activeJobsQuery, live } from "../state.ts";
import type { ActiveJob } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { Dialog } from "./Dialog.tsx";

export function JobsIndicator() {
  const { data } = useQuery(activeJobsQuery);
  const jobs = data?.jobs ?? [];
  const running = jobs.some((job) => job.status === "running");
  const Icon = running ? LoaderCircle : Activity;
  const trigger = (
    <Button variant="ghost">
      <Icon aria-hidden size={16} className={running ? "animate-spin" : undefined} />
      {jobs.length === 0 ? "No jobs" : jobs.length === 1 ? "1 job" : `${jobs.length} jobs`}
    </Button>
  );
  return (
    <Dialog title="Jobs" trigger={trigger}>
      <JobList jobs={jobs} />
    </Dialog>
  );
}

function JobList({ jobs }: { jobs: ActiveJob[] }) {
  useSyncExternalStore(live.subscribe, live.version); // the stream's progress, between fetches of the list
  const now = useNow();
  if (jobs.length === 0) return <p className="text-sm text-muted">Nothing is running.</p>;
  return (
    <ul className="flex flex-col gap-3">
      {jobs.map((job) => (
        <JobRow key={job.id} job={job} now={now} />
      ))}
    </ul>
  );
}

function JobRow({ job, now }: { job: ActiveJob; now: number }) {
  const seen = live.get(job.id);
  const progress = Math.min(1, Math.max(0, seen?.progress ?? job.progress));
  const left = seen ? etaSeconds(seen.samples, now) : null;
  return (
    <li className="flex flex-col gap-1.5 rounded-control border border-line bg-panel-2 p-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="font-medium">{job.label}</span>
        {job.status === "queued" ? (
          <Badge>Waiting</Badge>
        ) : (
          left !== null && <span className="text-muted tabular-nums">About {formatDuration(left)} left</span>
        )}
      </div>
      {job.project_title && (
        <p className="truncate text-xs text-muted">
          <bdi>{job.project_title}</bdi>
          {job.clip_rank !== null && `, clip ${job.clip_rank}`}
        </p>
      )}
      <div
        role="progressbar"
        aria-label={job.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
        className="h-1.5 overflow-hidden rounded-full bg-line"
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${progress * 100}%` }} />
      </div>
      {job.message && (
        <p className="truncate text-xs text-faint" dir="auto">
          {job.message}
        </p>
      )}
    </li>
  );
}

// The time in seconds, once a second while it's shown, so the time left counts down.
function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}
