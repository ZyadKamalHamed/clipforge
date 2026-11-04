import assert from "node:assert/strict";
import { test } from "node:test";

import {
  chars,
  changeCount,
  changesLabel,
  coldOpenHint,
  fieldTab,
  keepPending,
  keptPending,
  lengthLabel,
  look,
  parseTags,
  playingWord,
  postText,
  renderBody,
  resetCut,
  same,
  titleWord,
  titleWords,
  toggleStrike,
  withEdit,
  withRange,
} from "./editor.ts";

test("an edit back at its saved value is no change, and a deep copy counts as the same", () => {
  let p = withEdit({ edits: {} }, "punch_ins", false, true);
  assert.deepEqual(p.edits, { punch_ins: false });
  p = withEdit(p, "punch_ins", true, true);
  assert.deepEqual(p.edits, {});
  p = withEdit(p, "word_fixes", { "82": "gonna", "90": "wanna" }, {});
  assert.equal(changeCount(withEdit(p, "word_fixes", { "90": "wanna", "82": "gonna" }, { "82": "gonna", "90": "wanna" })), 0);
  assert.ok(same([84, 85], [84, 85]) && !same([84, 85], [85, 84]));
  assert.ok(same(undefined, null) && same("", null) === false);
});

test("a range back where it's saved is no change, and null puts a moved clip back", () => {
  const saved: [number, number] = [40, 59.9];
  assert.deepEqual(withRange({ edits: {} }, [35, 62.9], saved, false).range, [35, 62.9]);
  assert.equal(withRange({ edits: {}, range: [35, 62.9] }, [40, 59.9], saved, false).range, undefined);
  assert.equal(withRange({ edits: {} }, null, saved, true).range, null);
  assert.equal(withRange({ edits: {} }, null, saved, false).range, undefined);
});

test("Reset cut asks for the original range and no struck words, only where they were changed", () => {
  const saved = { struck: [84, 85] };
  const p = resetCut({ edits: { struck: [84, 85, 86] }, range: [35, 62.9] }, saved, [40, 59.9], true);
  assert.deepEqual(p, { edits: { struck: null }, range: null });
  assert.deepEqual(resetCut({ edits: { struck: [84] }, range: [35, 50] }, {}, [40, 59.9], false), { edits: {} });
});

test("changes are counted one per edit and one for the range, in words", () => {
  assert.equal(changeCount({ edits: { layout: "fit", preset: "neon" }, range: [1, 9] }), 3);
  assert.equal(changesLabel(1), "1 change not rendered yet");
  assert.equal(changesLabel(3), "3 changes not rendered yet");
});

test("the render body sends the range only when it changed, and null to put it back", () => {
  assert.deepEqual(renderBody({ edits: { struck: [84] } }), { edits: { struck: [84] } });
  assert.deepEqual(renderBody({ edits: {}, range: [35, 62.9] }), { edits: {}, range: [35, 62.9] });
  assert.deepEqual(renderBody({ edits: {}, range: null }), { edits: {}, range: null });
});

test("the title's words come once each, shown without their punctuation, as the server matches them", () => {
  assert.deepEqual(titleWords("Hook 1: why we wait"), [
    { value: "Hook", label: "Hook" },
    { value: "1:", label: "1" },
    { value: "why", label: "why" },
    { value: "we", label: "we" },
    { value: "wait", label: "wait" },
  ]);
  assert.deepEqual(
    titleWords("Nobody waits forever, waits").map((w) => w.value),
    ["Nobody", "waits", "forever,"],
  );
  assert.deepEqual(titleWords("“Don't” stop ! ..."), [
    { value: "“Don't”", label: "Don't" },
    { value: "stop", label: "stop" },
  ]);
  assert.equal(titleWord("FOREVER", "Nobody waits forever, waits"), "forever,");
  assert.equal(titleWord("deadline", "Hook 1: why we wait"), "");
  assert.equal(titleWord("", "Hook 1"), "");
});

test("characters count as the server counts them, an emoji as one", () => {
  assert.equal(chars("Hook 1: why we wait"), 19);
  assert.equal(chars("fire 🔥"), 6);
});

test("a post is its caption, credit line and hashtags, as the server puts them together", () => {
  assert.equal(postText("Hi", ["#a", "#b"], "Credit: TED"), "Hi\n\nCredit: TED\n\n#a #b");
  assert.equal(postText("", [], null), "");
  assert.equal(postText("Hi", [], ""), "Hi");
});

test("hashtags are split on spaces and commas, each given its #", () => {
  assert.deepEqual(parseTags("#deadlines  focus,#work, "), ["#deadlines", "#focus", "#work"]);
  assert.deepEqual(parseTags("   "), []);
});

test("the playing word is the last one said by now, and none before the first or outside the render", () => {
  const words = [
    { i: 80, out: 0 },
    { i: 81, out: 0.5 },
    { i: 82, out: null },
    { i: 83, out: 1.5 },
  ];
  assert.equal(playingWord(words, 0.4), 80);
  assert.equal(playingWord(words, 0.5), 81);
  assert.equal(playingWord(words, 1.4999), 81);
  assert.equal(playingWord(words, 2), 83);
  assert.equal(playingWord([{ i: 80, out: 1 }], 0.5), null);
});

test("Delete strikes the picked words, or brings them back when all of them are struck", () => {
  assert.deepEqual(toggleStrike([90], [86, 84, 85]), [84, 85, 86, 90]);
  assert.deepEqual(toggleStrike([84, 85, 86, 90], [84, 85, 86]), [90]);
  assert.deepEqual(toggleStrike([84], [84, 85]), [84, 85]);
});

test("each refused field belongs to the tab that edits it", () => {
  assert.equal(fieldTab("range"), "cut");
  assert.equal(fieldTab("edits.struck"), "cut");
  assert.equal(fieldTab("edits.hook_title"), "text");
  assert.equal(fieldTab("edits.fact_cards.0"), "text");
  assert.equal(fieldTab("edits.word_fixes.82"), "text");
  assert.equal(fieldTab("edits.voiceover"), "sound");
  assert.equal(fieldTab("edits.voiceover_at"), "sound");
  assert.equal(fieldTab("edits.emoji"), "text");
  assert.equal(fieldTab("edits.layout"), "look");
  assert.equal(fieldTab("edits.cold_open"), "look");
  assert.equal(fieldTab("edits.sfx"), "sound");
  assert.equal(fieldTab("edits.music"), "sound");
  assert.equal(fieldTab("edits.music_db"), "sound");
  assert.equal(fieldTab("edits.tiktok_caption"), "copy");
  assert.equal(fieldTab(null), null);
  assert.equal(fieldTab("something"), null);
});

test("the length reads to a tenth of a second", () => {
  assert.equal(lengthLabel([40, 59.9]), "19.9 s");
  assert.equal(lengthLabel([38.5, 65.4]), "26.9 s");
});

test("the look is the settings, then the project's choices, then the clip's own, its accent the preset's", () => {
  const settings = { punch_ins: true, emoji: true, caption_preset: "bold_pop", accent_color: null, encoder: "fast" };
  const base = look(settings, { layout: "auto", caption_preset: null }, {});
  assert.equal(base.punch_ins, true);
  assert.equal(base.preset, "bold_pop");
  assert.equal(base.layout, "auto");
  assert.equal(base.accent, "#FFD60A");
  const project = look(settings, { layout: "fit", caption_preset: "neon" }, { punch_ins: false });
  assert.deepEqual([project.layout, project.preset, project.accent, project.punch_ins], ["fit", "neon", "#00E5FF", false]);
  assert.equal(look({ ...settings, accent_color: "#FF0000" }, { layout: "auto", caption_preset: null }, {}).accent, "#FF0000");
  assert.equal(look(settings, { layout: "auto", caption_preset: null }, { accent: "#123456", layout: "face" }).accent, "#123456");
});

test("the cold open's hint quotes its line, cut to 80 characters", () => {
  assert.equal(coldOpenHint("You only need one."), 'Opens on "You only need one.", then a whoosh into the start.');
  const long = "word ".repeat(30).trim();
  const quoted = coldOpenHint(long).slice('Opens on "'.length, -'", then a whoosh into the start.'.length);
  assert.equal(Array.from(quoted).length, 80);
  assert.ok(quoted.endsWith("\u2026"));
  assert.ok(long.startsWith(quoted.slice(0, -1)));
  assert.equal(coldOpenHint(null), "No line in this clip was picked for a cold open.");
});

test("the cold open comes from the settings like the other switches", () => {
  assert.equal(look({ cold_open: true }, { layout: "auto", caption_preset: null }, {}).cold_open, true);
  assert.equal(look({ cold_open: true }, { layout: "auto", caption_preset: null }, { cold_open: false }).cold_open, false);
});

// The window's session storage, as the tests stand it in.
function storage(stored: Map<string, string>, broken = false) {
  const value = broken
    ? {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
        removeItem: () => {
          throw new Error("blocked");
        },
      }
    : {
        getItem: (k: string) => stored.get(k) ?? null,
        setItem: (k: string, v: string) => void stored.set(k, String(v)),
        removeItem: (k: string) => void stored.delete(k),
      };
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value });
}

test("changes waiting for a render outlast a reload of the window, for their own clip only", () => {
  const stored = new Map<string, string>();
  storage(stored);
  assert.deepEqual(keptPending("c1"), { edits: {} });
  keepPending("c1", { edits: { punch_ins: false }, range: [1.5, 30] });
  assert.deepEqual(keptPending("c1"), { edits: { punch_ins: false }, range: [1.5, 30] });
  assert.deepEqual(keptPending("c2"), { edits: {} });
  keepPending("c1", { edits: { title: "x" }, range: null }); // null: back to the clip's own range
  assert.deepEqual(keptPending("c1"), { edits: { title: "x" }, range: null });
  keepPending("c1", { edits: {} }); // rendered or discarded: nothing is kept
  assert.equal(stored.size, 0);
  keepPending("c3", { edits: { title: "y" } });
  stored.set([...stored.keys()][0], "{not json");
  assert.deepEqual(keptPending("c3"), { edits: {} });
  storage(stored, true); // a private window that refuses storage: nothing kept, nothing thrown
  keepPending("c1", { edits: { title: "x" } });
  assert.deepEqual(keptPending("c1"), { edits: {} });
});
