import assert from "node:assert/strict";
import { test } from "node:test";

import { empty, extendTo, selectAll, selectTier, toggle } from "./selection.ts";

const ORDER = ["a", "b", "c", "d", "e"];
const ids = (s: { ids: ReadonlySet<string> }) => [...s.ids].sort();

test("toggle adds and removes one clip and remembers it as the anchor", () => {
  const one = toggle(empty(), "b");
  assert.deepEqual(ids(one), ["b"]);
  assert.equal(one.anchor, "b");
  assert.deepEqual(ids(toggle(one, "b")), []);
});

test("Shift extends from the anchor over the shown order, either way", () => {
  assert.deepEqual(ids(extendTo(toggle(empty(), "b"), "d", ORDER)), ["b", "c", "d"]);
  assert.deepEqual(ids(extendTo(toggle(empty(), "d"), "a", ORDER)), ["a", "b", "c", "d"]);
  assert.deepEqual(ids(extendTo(empty(), "c", ORDER)), ["c"]);
});

test("select all takes every shown clip, and a tier takes that tier's", () => {
  assert.deepEqual(ids(selectAll(ORDER)), ORDER);
  const clips = [
    { id: "a", tier: "top" },
    { id: "b", tier: "good" },
    { id: "c", tier: "top" },
  ];
  assert.deepEqual(ids(selectTier(clips, "top")), ["a", "c"]);
});
