import assert from "node:assert/strict";
import { test } from "node:test";

import type { Check } from "../types.ts";
import { byNeed, checkName, needsFixing } from "./checks.ts";

const check = (name: string, ok: boolean, required = true): Check => ({ name, ok, detail: "", fix: "", required });
const CHECKS = [check("ffmpeg", true), check("speech model", false, false), check("yt-dlp", true), check("claude", false)];

test("byNeed puts what needs fixing first, then what's optional, then what's fine, each kept in order", () => {
  assert.deepEqual(
    byNeed(CHECKS).map((c) => c.name),
    ["claude", "speech model", "ffmpeg", "yt-dlp"],
  );
  assert.deepEqual(CHECKS[0].name, "ffmpeg"); // a new list, the old one as it was
});

test("needsFixing is only the required checks that fail", () => {
  assert.deepEqual(
    needsFixing(CHECKS).map((c) => c.name),
    ["claude"],
  );
  assert.deepEqual(needsFixing([check("ffmpeg", true)]), []);
});

test("checkName says a check the way a person would, or keeps a name it doesn't know", () => {
  assert.equal(checkName("claude"), "Claude Code");
  assert.equal(checkName("js runtime"), "JavaScript runtime");
  assert.equal(checkName("something new"), "something new");
});
