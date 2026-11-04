// The engine's events (one stream for the whole window) kept in step with the cached data: a project's
// change merges into its cached copy, a clip's refreshes its project's clips, a job's feeds the jobs
// indicator (and its project, as its steps move, or the starter list and gameplay for a starter video's
// download), an account's or a post's refreshes the accounts, a clipping channel's or the drafts' count
// refreshes Today's drafts, a campaign's or a submission's
// refreshes the Campaigns sheet and that campaign's page, a brief's refreshes the Briefs tab, its
// sources and the accounts worth adding, and a change to the trends refreshes the Trends page and the sounds
// suggested for each clip. Each of those but a project's, a clip's, a job's and the trends' also refreshes Today's list and board, and the list's own change refreshes Today and the sheet,
// whose rows carry their share of it. After a gap (a reconnect, or a resync when the window fell behind)
// everything is fetched again.

import type { QueryClient, QueryKey } from "@tanstack/react-query";

import { api, key } from "./api.ts";
import type { JobEvent, Project } from "./types.ts";

export type EventsClient = Pick<
  QueryClient,
  "invalidateQueries" | "setQueryData" | "removeQueries"
>;

type Source = {
  onopen: (() => void) | null;
  onerror: (() => void) | null;
  addEventListener(
    topic: string,
    listener: (e: { data: string }) => void,
  ): void;
  close(): void;
};

export type EventsDeps = { open: (url: string) => Source; probe: () => void };

const THROTTLE_MS = 250; // one refresh per key per this long, and one more after it if more came
const RECONNECT_MS = 2000;
const FINISHED = new Set(["done", "failed", "cancelled"]);
const STARTER = "library.starter"; // a starter gameplay video's download

const DEFAULTS: EventsDeps = {
  open: (url) => new EventSource(url) as unknown as Source,
  // A 401 here reloads the window (api does that), so a restarted engine hands it a new cookie.
  probe: () => void api("/api/jobs?active=1").catch(() => undefined),
};

type ProjectEvent = Partial<Project> & { id: string };
type ClipEvent = { id: string; project_id: string };

export function connectEvents(
  client: EventsClient,
  onJob: (job: JobEvent) => void,
  deps: EventsDeps = DEFAULTS,
): () => void {
  const cooling = new Map<string, boolean>(); // a key refreshed lately -> whether it's wanted again
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const statuses = new Map<number, string>(); // each unfinished job's last status
  let source: Source | null = null;
  let broken = false;
  let stopped = false;

  function later(run: () => void, ms: number) {
    const timer = setTimeout(() => {
      timers.delete(timer);
      run();
    }, ms);
    timers.add(timer);
  }

  function refresh(queryKey?: QueryKey) {
    const id = JSON.stringify(queryKey ?? null);
    if (cooling.has(id)) {
      cooling.set(id, true);
      return;
    }
    void (queryKey
      ? client.invalidateQueries({ queryKey })
      : client.invalidateQueries());
    cooling.set(id, false);
    later(() => {
      const again = cooling.get(id);
      cooling.delete(id);
      if (again) refresh(queryKey);
    }, THROTTLE_MS);
  }

  const handlers: Record<string, (data: never) => void> = {
    project(p: ProjectEvent) {
      if (p.status === "deleted")
        client.removeQueries({ queryKey: key.project(p.id) });
      else
        client.setQueryData<Project>(key.project(p.id), (old) =>
          old ? { ...old, ...p } : old,
        );
      refresh(key.projects);
    },
    clip(c: ClipEvent) {
      refresh(key.clips(c.project_id));
      refresh(key.clip(c.id));
      refresh(key.drafts); // a draft shows its clip's current render
    },
    clips(c: { project_id: string }) {
      refresh(key.clips(c.project_id));
    },
    job(job: JobEvent) {
      onJob(job);
      const changed = statuses.get(job.id) !== job.status;
      if (FINISHED.has(job.status)) statuses.delete(job.id);
      else statuses.set(job.id, job.status);
      if (changed) refresh(key.jobs);
      // A project's own job moves its steps along, so the project follows its every step; a clip's job
      // only when it starts or stops, as the clip's card follows its progress from the live jobs.
      if (job.project && (changed || job.ref_id === job.project))
        refresh(key.project(job.project));
      // A starter download's row says where it stands, and once done its video is among your gameplay.
      if (job.kind === STARTER && changed) {
        refresh(key.starter);
        if (job.status === "done") refresh(key.gameplay);
      }
    },
    // An account's card counts today's posts, so a post's change refreshes the accounts as its own does,
    // and a post that went up the clip cards too, which say where each clip was posted.
    account() {
      refresh(key.accounts);
      refresh(key.posts); // a paused account's posts say they wait
      refresh(key.today);
    },
    post(p: { status: string }) {
      refresh(key.accounts);
      refresh(key.posts);
      refresh(key.drafts); // a draft approved or discarded is a post's change too
      if (p.status === "posted") {
        refresh(key.allClips);
        refresh(key.anyClip);
      }
      refresh(key.today);
    },
    // A clipping channel's card counts its drafts, and Today says when the next check runs.
    clip_channel() {
      refresh(key.channels);
      refresh(key.drafts);
      refresh(key.today);
    },
    drafts() {
      refresh(key.channels);
      refresh(key.drafts);
      refresh(key.today);
    },
    // The sheet's rows carry each campaign's counts and money, so a submission's move changes them too.
    campaign(c: { id: string }) {
      refresh(key.campaigns);
      refresh(key.campaign(c.id));
      refresh(key.today);
    },
    submission(s: { campaign_id: string }) {
      refresh(key.campaigns);
      refresh(key.campaign(s.campaign_id));
      refresh(key.today);
    },
    todo() {
      refresh(key.today);
      refresh(key.campaigns);
    },
    // A source's read, a brief hidden, seen or used, or the advice hidden: the source says how its read went.
    brief() {
      refresh(key.briefs);
      refresh(key.briefSources);
      refresh(key.advice);
      refresh(key.today);
    },
    // A sound shortlisted, changed or removed, a hashtag set's change, a list read or the lists seen.
    trends() {
      refresh(key.trends); // the clips' suggestions are under it too
    },
    resync() {
      refresh();
    },
  };

  function connect() {
    const s = deps.open("/api/events");
    source = s;
    s.onopen = () => {
      if (broken) refresh(); // anything could have changed while the stream was down
      broken = false;
    };
    s.onerror = () => {
      // The browser's own retry would give up for good on a 401, so the window does its own.
      s.close();
      if (stopped) return;
      broken = true;
      deps.probe();
      later(connect, RECONNECT_MS);
    };
    for (const [topic, handle] of Object.entries(handlers)) {
      s.addEventListener(topic, (e) => {
        let data: unknown;
        try {
          data = JSON.parse(e.data);
        } catch {
          return; // a line the window can't read is skipped, not fatal
        }
        if (data && typeof data === "object") handle(data as never);
      });
    }
  }

  connect();
  return () => {
    stopped = true;
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
    source?.close();
  };
}
