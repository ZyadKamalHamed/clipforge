import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProjectOptions } from "../types.ts";
import {
  CAPTION_PRESETS,
  CLIP_COUNTS,
  CONTENT_TYPES,
  LAYOUTS,
  LENGTHS,
  PROMPT_MAX,
  QUALITIES,
  optionsSummary,
} from "./options.ts";

const DEFAULTS: ProjectOptions = {
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

test("every choice the engine has comes with its words, in the engine's order", () => {
  assert.deepEqual(
    LENGTHS.map((l) => [l.value, l.label, l.range]),
    [
      ["auto", "Auto", "15 to 75 s"],
      ["short", "Short", "15 to 35 s"],
      ["standard", "Standard", "30 to 60 s"],
      ["long", "Long", "60 to 90 s"],
    ],
  );
  assert.deepEqual(
    CONTENT_TYPES.map((c) => c.label),
    ["Auto", "Podcast", "Solo", "Stream", "Other"],
  );
  assert.deepEqual(LAYOUTS, {
    auto: "AI decides",
    face: "Face crop",
    stacked: "Stacked speakers",
    gameplay: "Gameplay split",
    stream: "Facecam and game",
    fit: "Blurred fit",
    motion: "Follow motion",
  });
  assert.deepEqual(Object.keys(CAPTION_PRESETS), [
    "bold_pop",
    "hormozi",
    "beast",
    "pill",
    "clean",
    "one_word",
    "neon",
  ]);
  assert.equal(CAPTION_PRESETS.one_word, "One word");
  assert.deepEqual(QUALITIES, {
    best: "Best",
    balanced: "Balanced",
    fast: "Fast",
  });
  assert.deepEqual(
    [CLIP_COUNTS[0], CLIP_COUNTS.at(-1), CLIP_COUNTS.length],
    [1, 30, 30],
  );
  assert.equal(PROMPT_MAX, 300);
});

test("optionsSummary names only what was changed from the defaults", () => {
  assert.equal(optionsSummary(DEFAULTS), "");
  const all: ProjectOptions = {
    ...DEFAULTS,
    length: "short",
    clip_count: 5,
    content_type: "podcast",
    prompt: "the money advice",
    caption_preset: "hormozi",
    layout: "stacked",
    quality: "fast",
    credit: false,
  };
  assert.equal(
    optionsSummary(all),
    'Short (15 to 35 s), 5 clips, Podcast, Hormozi captions, Stacked speakers, Fast quality, Credit off, About "the money advice"',
  );
  assert.equal(
    optionsSummary({ ...DEFAULTS, clip_count: 1, credit: true }),
    "1 clip, Credit on",
  );
  assert.equal(optionsSummary({ ...DEFAULTS, prompt: "   " }), "");
});

test("optionsSummary of Post as it is leaves out what only cutting clips uses", () => {
  const whole: ProjectOptions = {
    ...DEFAULTS,
    as_is: true,
    length: "short",
    clip_count: 5,
    layout: "stacked",
  };
  assert.equal(optionsSummary(whole), "Post as it is");
  assert.equal(
    optionsSummary({ ...whole, quality: "best", credit: false }),
    "Post as it is, Best quality, Credit off",
  );
});
