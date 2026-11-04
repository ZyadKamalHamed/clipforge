import assert from "node:assert/strict";
import { test } from "node:test";

import { etaSeconds } from "./eta.ts";

test("etaSeconds waits for 15 s of progress before it guesses", () => {
  assert.equal(etaSeconds([], 100), null);
  assert.equal(etaSeconds([[0, 0], [10, 0.1]], 10), null);
});

test("a straight line finishes where the line says", () => {
  assert.equal(etaSeconds([[0, 0], [10, 0.1], [20, 0.2]], 20), 80);
  assert.equal(etaSeconds([[0, 0], [10, 0.1], [20, 0.2]], 30), 70);
});

test("the guess is never negative", () => {
  assert.equal(etaSeconds([[0, 0], [20, 1]], 20), 0);
  assert.equal(etaSeconds([[0, 0.5], [20, 1.2]], 20), 0);
  assert.equal(etaSeconds([[0, 0], [20, 0.5]], 500), 0);
});

test("a stalled run has no guess", () => {
  assert.equal(etaSeconds([[0, 0.4], [30, 0.4]], 30), null);
  assert.equal(etaSeconds([[0, 0.6], [30, 0.4]], 30), null);
});
