import assert from "node:assert/strict";
import { beforeEach, mock, test } from "node:test";

import { type EventsClient, connectEvents } from "./events.ts";
import type { JobEvent } from "./types.ts";

class FakeSource {
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  heard = new Map<string, Array<(e: { data: string }) => void>>();
  constructor(url: string) {
    this.url = url;
  }
  addEventListener(topic: string, fn: (e: { data: string }) => void) {
    this.heard.set(topic, [...(this.heard.get(topic) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(topic: string, data: unknown) {
    for (const fn of this.heard.get(topic) ?? [])
      fn({ data: JSON.stringify(data) });
  }
}

let sources: FakeSource[] = [];
let calls: string[] = [];
let cache = new Map<string, unknown>();
let jobs: JobEvent[] = [];
let probes = 0;

const client = {
  invalidateQueries: async (filters?: { queryKey?: unknown[] }) => {
    calls.push(
      filters?.queryKey
        ? `invalidate ${JSON.stringify(filters.queryKey)}`
        : "invalidate all",
    );
  },
  setQueryData: (key: unknown[], update: (old: unknown) => unknown) => {
    cache.set(JSON.stringify(key), update(cache.get(JSON.stringify(key))));
  },
  removeQueries: (filters: { queryKey: unknown[] }) => {
    cache.delete(JSON.stringify(filters.queryKey));
    calls.push(`remove ${JSON.stringify(filters.queryKey)}`);
  },
} as unknown as EventsClient;

function connect() {
  return connectEvents(client, (job) => void jobs.push(job), {
    open: (url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
    probe: () => void (probes += 1),
  });
}

const last = () => sources[sources.length - 1];
const job = (status: string, progress: number): JobEvent => ({
  id: 3,
  kind: "asr.transcribe",
  ref_id: "p1",
  project: "p1",
  status,
  progress,
  message: null,
});

beforeEach(() => {
  sources = [];
  calls = [];
  cache = new Map();
  jobs = [];
  probes = 0;
  mock.timers.reset();
  mock.timers.enable({ apis: ["setTimeout"] });
});

test("a project event merges into the cached project and refreshes the list", () => {
  const stop = connect();
  assert.equal(last().url, "/api/events");
  cache.set('["project","p1"]', { id: "p1", title: "Talk", status: "running" });
  last().emit("project", { id: "p1", status: "ready", progress: 1 });
  assert.deepEqual(cache.get('["project","p1"]'), {
    id: "p1",
    title: "Talk",
    status: "ready",
    progress: 1,
  });
  assert.deepEqual(calls, ['invalidate ["projects"]']);
  stop();
});

test("a project that isn't cached stays uncached, and a deleted one leaves the cache", () => {
  const stop = connect();
  last().emit("project", { id: "p2", status: "ready" });
  assert.equal(cache.get('["project","p2"]'), undefined);
  cache.set('["project","p1"]', { id: "p1" });
  last().emit("project", { id: "p1", status: "deleted" });
  assert.equal(cache.has('["project","p1"]'), false);
  assert.ok(calls.includes('remove ["project","p1"]'));
  stop();
});

test("clip events refresh that project's clips, the clip itself and the drafts, which show its render", () => {
  const stop = connect();
  last().emit("clip", { id: "c1", project_id: "p1", status: "rendered" });
  last().emit("clips", { project_id: "p2" });
  assert.deepEqual(calls, [
    'invalidate ["clips","p1"]',
    'invalidate ["clip","c1"]',
    'invalidate ["drafts"]',
    'invalidate ["clips","p2"]',
  ]);
  stop();
});

test("a clipping channel's change and the drafts' count both refresh the drafts and the clipping channels", () => {
  const stop = connect();
  last().emit("clip_channel", { id: "cc1" });
  mock.timers.tick(300);
  last().emit("drafts", { count: 2 });
  assert.deepEqual(calls, [
    'invalidate ["clip-channels"]',
    'invalidate ["drafts"]',
    'invalidate ["today"]',
    'invalidate ["clip-channels"]',
    'invalidate ["drafts"]',
    'invalidate ["today"]',
  ]);
  stop();
});

test("an account's change and a post's refresh the accounts and the posts, and a post's the drafts it was", () => {
  const stop = connect();
  last().emit("account", { id: "a1", status: "connected", paused: true });
  mock.timers.tick(300);
  last().emit("post", { id: "s1", status: "uploading", account_id: "a1" });
  assert.deepEqual(calls, [
    'invalidate ["accounts"]',
    'invalidate ["posts"]',
    'invalidate ["today"]',
    'invalidate ["accounts"]',
    'invalidate ["posts"]',
    'invalidate ["drafts"]',
    'invalidate ["today"]',
  ]);
  stop();
});

test("a post that went up also refreshes the clips, whose cards say where they were posted", () => {
  const stop = connect();
  last().emit("post", { id: "s1", status: "posted", account_id: "a1" });
  assert.deepEqual(calls, [
    'invalidate ["accounts"]',
    'invalidate ["posts"]',
    'invalidate ["drafts"]',
    'invalidate ["clips"]',
    'invalidate ["clip"]',
    'invalidate ["today"]',
  ]);
  stop();
});

test("todo refreshes today and campaigns", () => {
  const stop = connect();
  last().emit("todo", { day: "2026-11-03" });
  assert.deepEqual(calls, ['invalidate ["today"]', 'invalidate ["campaigns"]']);
  stop();
});

test("a brief's change refreshes the briefs, their sources, the advice and Today", () => {
  const stop = connect();
  last().emit("brief", { ids: ["br1"], status: "hidden" });
  assert.deepEqual(calls, [
    'invalidate ["briefs"]',
    'invalidate ["brief-sources"]',
    'invalidate ["advice"]',
    'invalidate ["today"]',
  ]);
  stop();
});

test("a change to the trends refreshes the Trends page and the sounds suggested for clips", () => {
  const stop = connect();
  last().emit("trends", { shortlist: "snd_1" });
  assert.deepEqual(calls, ['invalidate ["trends"]']);
  stop();
});

test("a campaign's change and a submission's refresh the sheet, that campaign's page and Today", () => {
  const stop = connect();
  last().emit("campaign", { id: "cp1" });
  mock.timers.tick(300);
  last().emit("submission", { id: "sb1", campaign_id: "cp2" });
  assert.deepEqual(calls, [
    'invalidate ["campaigns"]',
    'invalidate ["campaign","cp1"]',
    'invalidate ["today"]',
    'invalidate ["campaigns"]',
    'invalidate ["campaign","cp2"]',
    'invalidate ["today"]',
  ]);
  stop();
});

test("every job event feeds the window, and a project's own job refreshes it as it goes", () => {
  const stop = connect();
  last().emit("job", job("running", 0.1));
  last().emit("job", job("running", 0.2));
  mock.timers.tick(300);
  last().emit("job", job("done", 1));
  mock.timers.tick(300);
  assert.deepEqual(
    jobs.map((j) => j.progress),
    [0.1, 0.2, 1],
  );
  assert.deepEqual(calls, [
    'invalidate ["jobs"]',
    'invalidate ["project","p1"]',
    'invalidate ["project","p1"]',
    'invalidate ["jobs"]',
    'invalidate ["project","p1"]',
  ]);
  stop();
});

test("a clip's job refreshes the lists only when it changes status, as its card follows its progress", () => {
  const stop = connect();
  const render = (status: string, progress: number): JobEvent => ({
    id: 4,
    kind: "clip.render",
    ref_id: "p1_c0",
    project: "p1",
    status,
    progress,
    message: null,
  });
  last().emit("job", render("running", 0.1));
  mock.timers.tick(300);
  last().emit("job", render("running", 0.5));
  mock.timers.tick(300);
  last().emit("job", render("done", 1));
  assert.deepEqual(calls, [
    'invalidate ["jobs"]',
    'invalidate ["project","p1"]',
    'invalidate ["jobs"]',
    'invalidate ["project","p1"]',
  ]);
  stop();
});

test("a starter download refreshes the starter list as it changes status, and the gameplay once it's done", () => {
  const stop = connect();
  const download = (status: string, progress: number): JobEvent => ({
    id: 5,
    kind: "library.starter",
    ref_id: "ludo-surfs-subway",
    project: null,
    status,
    progress,
    message: null,
  });
  last().emit("job", download("running", 0.1));
  mock.timers.tick(300);
  last().emit("job", download("running", 0.5));
  mock.timers.tick(300);
  last().emit("job", download("done", 1));
  assert.deepEqual(calls, [
    'invalidate ["jobs"]',
    'invalidate ["library","starter"]',
    'invalidate ["jobs"]',
    'invalidate ["library","starter"]',
    'invalidate ["library","gameplay"]',
  ]);
  stop();
});

test("invalidations are throttled to one per key per 250 ms, with one more at the end", () => {
  const stop = connect();
  for (let i = 0; i < 5; i++) last().emit("clips", { project_id: "p1" });
  assert.equal(calls.length, 1);
  mock.timers.tick(250);
  assert.equal(calls.length, 2);
  mock.timers.tick(250);
  assert.equal(calls.length, 2);
  last().emit("clips", { project_id: "p1" });
  assert.equal(calls.length, 3);
  stop();
});

test("a resync refreshes everything, and a line that isn't JSON is skipped", () => {
  const stop = connect();
  last().emit("resync", {});
  for (const fn of last().heard.get("clip") ?? []) fn({ data: "{not json" });
  assert.deepEqual(calls, ["invalidate all"]);
  stop();
});

test("a closed stream probes, connects again after 2 s and refreshes everything once back", () => {
  const stop = connect();
  const first = last();
  first.onopen?.();
  assert.deepEqual(calls, []);
  first.onerror?.();
  assert.equal(first.closed, true);
  assert.equal(probes, 1);
  mock.timers.tick(1999);
  assert.equal(sources.length, 1);
  mock.timers.tick(1);
  assert.equal(sources.length, 2);
  last().onopen?.();
  assert.deepEqual(calls, ["invalidate all"]);
  stop();
});

test("stopping closes the stream and never connects again", () => {
  const stop = connect();
  const first = last();
  stop();
  assert.equal(first.closed, true);
  first.onerror?.();
  mock.timers.tick(5000);
  assert.equal(sources.length, 1);
  assert.equal(probes, 0);
});
