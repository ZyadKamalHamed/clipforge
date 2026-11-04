import assert from "node:assert/strict";
import { test } from "node:test";

import { formatAgo, formatBytes, formatDuration, utf16Length } from "./format.ts";

test("formatDuration shows minutes and seconds, and hours when there are some", () => {
  assert.equal(formatDuration(65), "1:05");
  assert.equal(formatDuration(3723), "1:02:03");
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(59.6), "1:00");
  assert.equal(formatDuration(-3), "0:00");
});

test("formatBytes uses the largest unit that keeps the number at 1 or more", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(999), "999 B");
  assert.equal(formatBytes(1500), "1.5 KB");
  assert.equal(formatBytes(4.9e9), "4.9 GB");
  assert.equal(formatBytes(12e9), "12 GB");
  assert.equal(formatBytes(2.5e12), "2.5 TB");
});

test("utf16Length counts the way Instagram and TikTok count", () => {
  assert.equal(utf16Length(String.fromCodePoint(0x1f525)), 2);
  assert.equal(utf16Length("abc"), 3);
});

test("formatAgo says how long ago in plain words, from the engine's seconds since 1970", () => {
  const at = (iso: string) => Date.parse(iso) / 1000;
  const now = at("2026-10-22T12:00:00Z");
  assert.equal(formatAgo(at("2026-10-22T11:59:30Z"), now), "just now");
  assert.equal(formatAgo(at("2026-10-22T11:55:00Z"), now), "5 min ago");
  assert.equal(formatAgo(at("2026-10-22T09:00:00Z"), now), "3 h ago");
  assert.equal(formatAgo(at("2026-10-21T11:00:00Z"), now), "yesterday");
  assert.equal(formatAgo(at("2026-10-19T12:00:00Z"), now), "3 days ago");
  assert.equal(formatAgo(at("2026-10-22T12:00:05Z"), now), "just now");
  assert.equal(formatAgo(Number.NaN, now), "");
});
