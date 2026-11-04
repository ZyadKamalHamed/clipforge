import assert from "node:assert/strict";
import { test } from "node:test";

import { looksLikeVideoLink } from "./links.ts";

const ID = "dQw4w9WgXcQ";

test("YouTube video links look like video links", () => {
  for (const url of [
    `https://www.youtube.com/watch?v=${ID}`,
    `https://youtu.be/${ID}`,
    `youtube.com/shorts/${ID}`,
    `https://m.youtube.com/watch?feature=share&v=${ID}&t=30`,
    `https://www.youtube.com/live/${ID}`,
    `  https://youtu.be/${ID}?si=abc  `,
  ]) {
    assert.ok(looksLikeVideoLink(url, false), url);
  }
});

test("other text does not", () => {
  for (const text of ["", "hello", "https://www.youtube.com/watch?v=short", "https://vimeo.com/123", "mailto:a@b.co"]) {
    assert.equal(looksLikeVideoLink(text, false), false, text);
  }
});

test("other sites count only when they are allowed, and only over http or https", () => {
  assert.ok(looksLikeVideoLink("https://vimeo.com/123", true));
  assert.equal(looksLikeVideoLink("ftp://example.com/a.mp4", true), false);
  assert.equal(looksLikeVideoLink("mailto:a@b.co", true), false);
  assert.equal(looksLikeVideoLink("https://localhost/a", true), false);
});
