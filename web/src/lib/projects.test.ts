import assert from "node:assert/strict";
import { test } from "node:test";

import type { Project, ProjectOptions, Step } from "../types.ts";
import {
  FILTERS,
  clipsLabel,
  displayTitle,
  inFilter,
  matchesSearch,
  statusOf,
} from "./projects.ts";

const OPTIONS: ProjectOptions = {
  length: "auto",
  clip_count: null,
  content_type: "auto",
  prompt: null,
  quality: null,
  credit: null,
  render: true,
  provider: null,
  layout: "auto",
  caption_preset: null,
  as_is: false,
};

function project(fields: Partial<Project> = {}): Project {
  return {
    id: "p1",
    title: "Inside the Mind of a Master Procrastinator",
    channel: "TED",
    channel_url: null,
    source_url: "https://www.youtube.com/watch?v=arj7oStGLkU",
    source_kind: "youtube",
    duration_s: 844,
    language: "en",
    rights_basis: "not_cleared",
    status: "ready",
    stage: null,
    progress: 1,
    message: null,
    error: null,
    error_code: null,
    error_details: null,
    options: OPTIONS,
    created_at: 1_793_000_000,
    updated_at: 1_793_000_000,
    thumb: null,
    clip_count: 6,
    rendered_count: 4,
    rerun_drops: 4,
    source_on_disk: true,
    steps: [],
    campaign_id: null,
    ...fields,
  };
}

const step = (key: string, label: string, status: Step["status"]): Step => ({
  key,
  label,
  status,
  progress: 0,
});

test("the filter chips are All, Working, Ready and Failed, in that order", () => {
  assert.deepEqual(
    FILTERS.map((f) => f.label),
    ["All", "Working", "Ready", "Failed"],
  );
});

test("each status sits under the chip that says it", () => {
  const under = (status: string) =>
    FILTERS.filter((f) => inFilter(project({ status }), f.value)).map(
      (f) => f.label,
    );
  assert.deepEqual(under("queued"), ["All", "Working"]);
  assert.deepEqual(under("running"), ["All", "Working"]);
  assert.deepEqual(under("waiting"), ["All", "Working"]);
  assert.deepEqual(under("ready"), ["All", "Ready"]);
  assert.deepEqual(under("failed"), ["All", "Failed"]);
  assert.deepEqual(under("refused"), ["All", "Failed"]);
});

test("a search matches the title whatever its case, and a blank one matches everything", () => {
  const p = project();
  assert.equal(matchesSearch(p, "master"), true);
  assert.equal(matchesSearch(p, "  MIND of "), true);
  assert.equal(matchesSearch(p, ""), true);
  assert.equal(matchesSearch(p, "   "), true);
  assert.equal(matchesSearch(p, "podcast"), false);
  assert.equal(
    matchesSearch(
      project({
        title: null,
        source_kind: "file",
        source_url: "file:///x/Team%20talk.mp4",
      }),
      "team",
    ),
    true,
  );
});

test("a project without its title yet goes by its file's name or its link", () => {
  assert.equal(
    displayTitle(project()),
    "Inside the Mind of a Master Procrastinator",
  );
  assert.equal(
    displayTitle(
      project({
        title: null,
        source_kind: "file",
        source_url: "file:///Users/z/Movies/team%20talk.mp4",
      }),
    ),
    "team talk.mp4",
  );
  assert.equal(
    displayTitle(
      project({
        title: null,
        source_kind: "file",
        source_url: "file:///x/100%25.mp4",
      }),
    ),
    "100%.mp4",
  );
  assert.equal(
    displayTitle(
      project({
        title: null,
        source_kind: "file",
        source_url: "file:///x/bad%E0.mp4",
      }),
    ),
    "bad%E0.mp4",
  );
  assert.equal(
    displayTitle(project({ title: null })),
    "https://www.youtube.com/watch?v=arj7oStGLkU",
  );
  assert.equal(
    displayTitle(project({ title: null, source_url: null })),
    "A new project",
  );
  assert.equal(
    displayTitle(project({ title: "" })),
    "https://www.youtube.com/watch?v=arj7oStGLkU",
  );
});

test("clipsLabel counts clips in words", () => {
  assert.equal(clipsLabel(0), "No clips");
  assert.equal(clipsLabel(1), "1 clip");
  assert.equal(clipsLabel(6), "6 clips");
});

test("statusOf says where a project is, and what went wrong when it stopped", () => {
  const running = project({
    status: "running",
    progress: 0.4,
    steps: [
      step("metadata", "Metadata", "done"),
      step("transcribe", "Transcribe", "running"),
    ],
  });
  assert.deepEqual(statusOf(running), {
    tone: "accent",
    text: "Transcribe",
    working: true,
    detail: null,
  });
  assert.deepEqual(statusOf(project({ status: "running" })), {
    tone: "accent",
    text: "Working",
    working: true,
    detail: null,
  });
  assert.deepEqual(statusOf(project({ status: "queued" })), {
    tone: "neutral",
    text: "In the queue",
    working: true,
    detail: null,
  });
  const waiting = project({
    status: "waiting",
    message: "Claude's limit resets at 3 pm.",
  });
  assert.deepEqual(statusOf(waiting), {
    tone: "warn",
    text: "Waiting",
    working: true,
    detail: "Claude's limit resets at 3 pm.",
  });
  assert.deepEqual(statusOf(project()), {
    tone: "ok",
    text: "Ready",
    working: false,
    detail: null,
  });
  const failed = project({
    status: "failed",
    error: "The download stopped. Try again.",
  });
  assert.deepEqual(statusOf(failed), {
    tone: "bad",
    text: "Failed",
    working: false,
    detail: "The download stopped. Try again.",
  });
  const refused = project({
    status: "refused",
    error: "This video is over 4 hours long.",
  });
  assert.deepEqual(statusOf(refused), {
    tone: "bad",
    text: "Can't use this video",
    working: false,
    detail: "This video is over 4 hours long.",
  });
});
