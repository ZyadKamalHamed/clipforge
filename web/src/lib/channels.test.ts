import assert from "node:assert/strict";
import { test } from "node:test";

import type { ClipChannel } from "../types.ts";
import {
  checkNowLabel,
  deleteLine,
  linkLines,
  nextCheckLabel,
  timeLabel,
  validChannel,
} from "./channels.ts";

const at = (day: number, hour: number, minute = 0) => Date.UTC(2026, 10, day, hour - 11, minute) / 1000; // Sydney, AEDT
const NOW = at(3, 7, 10); // 7:10 am on Tue 3 Nov 2026
const FORM = { name: "Podcasts", checkTime: "06:00", hashtags: ["talk"] };

function channel(fields: Partial<ClipChannel>): ClipChannel {
  return { paused: false, next_check_at: null, ...fields } as ClipChannel; // the helpers read only these
}

test("validChannel gives the first problem as a sentence", () => {
  assert.equal(validChannel(FORM), null);
  assert.equal(validChannel({ ...FORM, name: "  " }), "Give the clipping channel a name.");
  assert.equal(validChannel({ ...FORM, name: "x".repeat(61) }), "Keep the name to 60 characters.");
  assert.equal(validChannel({ ...FORM, name: "x".repeat(60) }), null);
  assert.equal(validChannel({ ...FORM, checkTime: "6am" }), "Give the check time as hours and minutes, like 06:00.");
  assert.equal(validChannel({ ...FORM, checkTime: "24:00" }), "Give the check time as hours and minutes, like 06:00.");
  assert.equal(validChannel({ ...FORM, hashtags: Array.from({ length: 11 }, (_, i) => `t${i}`) }), "Give at most 10 hashtags.");
  assert.equal(validChannel({ ...FORM, name: "", checkTime: "x" }), "Give the clipping channel a name."); // the first
});

test("timeLabel says a check time the way a clock does", () => {
  assert.equal(timeLabel("06:00"), "6:00 am");
  assert.equal(timeLabel("18:30"), "6:30 pm");
  assert.equal(timeLabel("00:05"), "12:05 am");
  assert.equal(timeLabel("12:00"), "12:00 pm");
});

test("nextCheckLabel says when the clipping channel checks next, or why it doesn't", () => {
  assert.equal(nextCheckLabel(channel({ next_check_at: at(3, 8) }), NOW), "Next check at 8:00 am today.");
  assert.equal(nextCheckLabel(channel({ next_check_at: at(4, 6) }), NOW), "Next check at 6:00 am tomorrow.");
  assert.equal(nextCheckLabel(channel({ next_check_at: NOW }), NOW), "A check is due now.");
  assert.equal(nextCheckLabel(channel({ paused: true, next_check_at: null }), NOW), "No checks while it is paused.");
  assert.equal(nextCheckLabel(channel({ next_check_at: null }), NOW), "No checks until it watches a YouTube channel.");
});

test("checkNowLabel says when Check now opens again", () => {
  assert.equal(checkNowLabel(null, NOW), null);
  assert.equal(checkNowLabel(at(3, 7, 20), NOW), "Check now opens at 7:20 am.");
  assert.equal(checkNowLabel(at(4, 6), NOW), "Check now opens at 6:00 am tomorrow.");
});

test("linkLines gives each link once, a line each, blank lines left out", () => {
  assert.deepEqual(linkLines(" @one \n\n https://www.youtube.com/@two\n@one\n"), ["@one", "https://www.youtube.com/@two"]);
  assert.deepEqual(linkLines(""), []);
});

test("deleteLine names the drafts that go", () => {
  assert.equal(deleteLine(0), "It has no drafts. Its projects and the posts you approved stay.");
  assert.equal(deleteLine(1), "Its draft is discarded. Its projects and the posts you approved stay.");
  assert.equal(deleteLine(2), "Its 2 drafts are discarded. Its projects and the posts you approved stay.");
});
