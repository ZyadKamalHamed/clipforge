import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DENIED,
  RECORD_MAX_S,
  recorder,
  type RecorderParts,
  takeName,
  timerText,
  VOICE_ACCEPT,
} from "./recorder.ts";

class FakeRecorder {
  mimeType = "audio/webm;codecs=opus";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  started = false;
  start() {
    this.started = true;
  }
  stop() {
    this.ondataavailable?.({ data: new Blob(["take"]) });
    this.onstop?.();
  }
}

function parts(allow = true) {
  const made: FakeRecorder[] = [];
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
  let stopped = 0;
  let clock = 1000;
  const stream = {
    getTracks: () => [{ stop: () => void (stopped += 1) }],
  } as unknown as MediaStream;
  const given: RecorderParts = {
    getUserMedia: () =>
      allow
        ? Promise.resolve(stream)
        : Promise.reject(new Error("NotAllowedError")),
    makeRecorder: () => {
      const r = new FakeRecorder();
      made.push(r);
      return r;
    },
    setTimer: (fn, ms) => timers.push({ fn, ms, cleared: false }) - 1,
    clearTimer: (handle) => void (timers[handle as number].cleared = true),
    now: () => clock,
  };
  return {
    given,
    made,
    timers,
    tick: (ms: number) => void (clock += ms),
    stopped: () => stopped,
  };
}

test("a take goes idle, asking, recording, stopped, and lets the microphone go", async () => {
  const p = parts();
  const rec = recorder(p.given);
  const seen: string[] = [];
  rec.subscribe(() => seen.push(rec.state()));
  assert.equal(rec.state(), "idle");
  const asked = rec.start();
  assert.equal(rec.state(), "asking");
  await asked;
  assert.equal(rec.state(), "recording");
  assert.ok(p.made[0].started);
  p.tick(2500);
  assert.equal(rec.elapsed(), 2.5);
  rec.stop();
  assert.equal(rec.state(), "stopped");
  assert.deepEqual(seen, ["asking", "recording", "stopped"]);
  assert.equal(rec.take()?.type, "audio/webm;codecs=opus");
  assert.equal(await rec.take()?.text(), "take");
  assert.equal(p.stopped(), 1);
  assert.ok(p.timers[0].cleared);
  rec.reset();
  assert.equal(rec.state(), "idle");
  assert.equal(rec.take(), null);
});

test("recording stops on its own at 3 minutes", async () => {
  const p = parts();
  const rec = recorder(p.given);
  await rec.start();
  assert.equal(p.timers[0].ms, RECORD_MAX_S * 1000);
  assert.equal(RECORD_MAX_S, 180);
  p.timers[0].fn();
  assert.equal(rec.state(), "stopped");
  assert.ok(rec.take());
});

test("a refused microphone is denied, with what to do", async () => {
  const p = parts(false);
  const rec = recorder(p.given);
  await rec.start();
  assert.equal(rec.state(), "denied");
  assert.equal(p.made.length, 0);
  assert.equal(
    DENIED,
    "ClipForge can't use the microphone. Allow it from the icon in Chrome's address bar, or choose a recording file.",
  );
  await rec.start(); // asking again is allowed once it is allowed in Chrome
  assert.equal(rec.state(), "denied");
});

test("closing the tab mid-take lets the microphone go without a take", async () => {
  const p = parts();
  const rec = recorder(p.given);
  await rec.start();
  rec.dispose();
  assert.equal(p.stopped(), 1);
  assert.ok(p.timers[0].cleared);
  assert.equal(rec.take(), null);
  const late = recorder(p.given);
  const asked = late.start();
  late.dispose(); // gone before Chrome answered
  await asked;
  assert.equal(p.stopped(), 2);
  assert.equal(late.state(), "idle");
});

test("a take is named for what the recorder made, and the timer counts to 3:00", () => {
  assert.equal(takeName("audio/webm;codecs=opus"), "voiceover.webm");
  assert.equal(takeName("audio/ogg"), "voiceover.ogg");
  assert.equal(takeName("audio/mp4"), "voiceover.m4a");
  assert.equal(takeName(""), "voiceover.webm");
  assert.equal(timerText(7.9), "0:07 of 3:00");
  assert.equal(timerText(125), "2:05 of 3:00");
  assert.equal(VOICE_ACCEPT, ".webm,.ogg,.m4a,.mp4,.mp3,.wav,.aac,.caf");
});
