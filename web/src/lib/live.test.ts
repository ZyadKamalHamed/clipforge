import assert from "node:assert/strict";
import { test } from "node:test";

import type { JobEvent } from "../types.ts";
import { liveJobs } from "./live.ts";

const job = (progress: number, status = "running"): JobEvent => ({
  id: 7,
  kind: "asr.transcribe",
  ref_id: "p1",
  project: "p1",
  status,
  progress,
  message: null,
});

test("a job's samples cover its last 60 s", () => {
  const live = liveJobs();
  live.push(job(0.1), 1000);
  live.push(job(0.3), 1030);
  live.push(job(0.5), 1061);
  assert.deepEqual(live.get(7)?.samples, [
    [1030, 0.3],
    [1061, 0.5],
  ]);
  assert.equal(live.get(7)?.progress, 0.5);
});

test("progress going backwards, as a retry does, starts the samples again", () => {
  const live = liveJobs();
  live.push(job(0.6), 1000);
  live.push(job(0.2), 1010);
  assert.deepEqual(live.get(7)?.samples, [[1010, 0.2]]);
});

test("a finished job is forgotten, and listeners hear every change", () => {
  const live = liveJobs();
  let heard = 0;
  const stop = live.subscribe(() => void (heard += 1));
  live.push(job(0.4), 1000);
  const before = live.version();
  live.push(job(1, "done"), 1005);
  assert.equal(live.get(7), undefined);
  assert.notEqual(live.version(), before);
  stop();
  live.push(job(0.1), 1010);
  assert.equal(heard, 2);
});

test("a job is known by what it is for, so a project's jobs and a clip's can be listed", () => {
  const live = liveJobs();
  const render = (id: number, ref: string, project: string, status: string, progress: number): JobEvent => ({
    id,
    kind: "clip.render",
    ref_id: ref,
    project,
    status,
    progress,
    message: null,
  });
  live.push(job(0.4), 1000);
  live.push(render(8, "p1_c0", "p1", "running", 0.5), 1000);
  live.push(render(9, "p2_c0", "p2", "queued", 0), 1000);
  assert.deepEqual(
    live.of("p1").map((j) => j.id),
    [7, 8],
  );
  assert.deepEqual(
    live.of("p1_c0").map((j) => [j.kind, j.status, j.progress]),
    [["clip.render", "running", 0.5]],
  );
  live.push(render(8, "p1_c0", "p1", "done", 1), 1001);
  assert.deepEqual(live.of("p1_c0"), []);
});
