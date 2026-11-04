import assert from "node:assert/strict";
import { test } from "node:test";

import {
  droppedText,
  hasLink,
  headerJson,
  isMusicFile,
  isVideoFile,
  MUSIC_ACCEPT,
  rememberCopied,
  wasCopiedHere,
} from "./drop.ts";

const URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

test("a video file is known by what follows its last dot, as the server reads it", () => {
  for (const name of ["team talk.mp4", "A.MOV", "x.mkv", "y.webm", "z.m4v", ".mp4"]) {
    assert.equal(isVideoFile(name), true, name);
  }
  for (const name of ["notes.txt", "mp4", "video.mp4.zip", "talk.", ""]) {
    assert.equal(isVideoFile(name), false, name);
  }
});

test("a music file is one of the types the server takes, a video with sound among them", () => {
  for (const name of ["night drive.mp3", "A.M4A", "x.aac", "y.wav", "z.aif", "z.aiff", "f.flac", "talk.mp4", "t.m4v", "t.MOV"]) {
    assert.equal(isMusicFile(name), true, name);
  }
  for (const name of ["notes.txt", "mp3", "x.mkv", "x.webm", "track.mp3.tmp", "song.", ""]) {
    assert.equal(isMusicFile(name), false, name);
  }
  assert.equal(MUSIC_ACCEPT, ".mp3,.m4a,.aac,.wav,.aif,.aiff,.flac,.mp4,.m4v,.mov");
});

test("a drop's text is its links when it has them, else its plain text", () => {
  const from = (data: Record<string, string>) => droppedText((type) => data[type] ?? "");
  assert.equal(from({ "text/uri-list": `# a comment\r\n${URL}\r\n`, "text/plain": "the title" }), URL);
  assert.equal(from({ "text/uri-list": `${URL}\r\n${URL}&t=5` }), `${URL}\n${URL}&t=5`);
  assert.equal(from({ "text/plain": "some text" }), "some text");
  assert.equal(from({}), "");
});

test("a paste starts a project only when it has a link in it", () => {
  assert.equal(hasLink(`have a look at ${URL} today`, false), true);
  assert.equal(hasLink(`first line\n${URL}`, false), true);
  assert.equal(hasLink("hello there", false), false);
  assert.equal(hasLink("https://vimeo.com/123456", false), false);
  assert.equal(hasLink("https://vimeo.com/123456", true), true);
});

test("options travel in a header as plain ASCII JSON", () => {
  const options = { prompt: `the ${String.fromCodePoint(0x1f525)} bits, ${String.fromCodePoint(0x645, 0x631)}\n\x7f`, n: 3 };
  const header = headerJson(options);
  assert.match(header, /^[\x20-\x7e]*$/);
  assert.deepEqual(JSON.parse(header), options);
});

test("a link copied here is never a new project", () => {
  const live = "https://www.tiktok.com/@t0/video/7300000000000000001";
  assert.equal(wasCopiedHere(live), false);
  rememberCopied(live);
  assert.equal(wasCopiedHere(live), true);
  assert.equal(wasCopiedHere(` ${live}\n`), true); // as a paste brings it, spaces and all
  assert.equal(wasCopiedHere(`${live} and more`), false);
  rememberCopied("");
  assert.equal(wasCopiedHere(""), false);
  assert.equal(wasCopiedHere("   "), false);
});
