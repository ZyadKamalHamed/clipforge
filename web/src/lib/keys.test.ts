import assert from "node:assert/strict";
import { test } from "node:test";

import { KEYS, type KeyPress, inTextField, matchKey } from "./keys.ts";

type Target = KeyPress["target"];

const press = (key: string, more: Partial<KeyPress> = {}): KeyPress => ({
  key,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...more,
});

const at = (tagName: string, more: Record<string, unknown> = {}): Target => ({ tagName, ...more });
const TEXT = at("INPUT", { type: "text" });

test("Cmd+Enter re-renders, and Enter alone or Ctrl+Enter doesn't", () => {
  assert.equal(matchKey(press("Enter", { metaKey: true }), KEYS.rerender), true);
  assert.equal(matchKey(press("Enter"), KEYS.rerender), false);
  assert.equal(matchKey(press("Enter", { ctrlKey: true }), KEYS.rerender), false);
});

test("Delete cuts from either delete key, a Mac's being Backspace", () => {
  assert.equal(matchKey(press("Backspace"), KEYS.cut), true);
  assert.equal(matchKey(press("Delete"), KEYS.cut), true);
  assert.equal(matchKey(press("Backspace", { metaKey: true }), KEYS.cut), false);
});

test("Space plays or pauses, but a focused button or checkbox takes its own Space", () => {
  assert.equal(matchKey(press(" "), KEYS.playPause), true);
  assert.equal(matchKey(press(" ", { target: at("BUTTON") }), KEYS.playPause), false);
  assert.equal(matchKey(press(" ", { target: at("INPUT", { type: "checkbox" }) }), KEYS.playPause), false);
  assert.equal(matchKey(press(" ", { target: at("DIV", { getAttribute: () => "switch" }) }), KEYS.playPause), false);
});

test("keys are ignored inside text fields, except Esc and Cmd+Enter, which type nothing", () => {
  for (const target of [TEXT, at("TEXTAREA"), at("SELECT"), at("DIV", { isContentEditable: true })]) {
    assert.equal(matchKey(press(" ", { target }), KEYS.playPause), false);
    assert.equal(matchKey(press("Backspace", { target }), KEYS.cut), false);
    assert.equal(matchKey(press("p", { target }), KEYS.post), false);
    assert.equal(matchKey(press("a", { target, metaKey: true }), KEYS.selectAll), false);
    assert.equal(matchKey(press("Escape", { target }), KEYS.close), true);
    assert.equal(matchKey(press("Enter", { target, metaKey: true }), KEYS.rerender), true);
  }
  assert.equal(matchKey(press("j", { target: at("INPUT", { type: "range" }) }), KEYS.back), true);
  assert.equal(inTextField(at("TEXTAREA")), true);
  assert.equal(inTextField(at("INPUT", { type: "checkbox" })), false);
  assert.equal(inTextField(null), false);
});

test("a key already handled, still being composed or pressed inside a dialog is left alone", () => {
  assert.equal(matchKey(press("Escape", { defaultPrevented: true }), KEYS.close), false);
  assert.equal(matchKey(press("p", { isComposing: true }), KEYS.post), false);
  const inDialog = at("DIV", { closest: (selector: string) => (selector.includes("dialog") ? {} : null) });
  assert.equal(matchKey(press(" ", { target: inDialog }), KEYS.playPause), false);
  assert.equal(matchKey(press(" ", { target: at("DIV", { closest: () => null }) }), KEYS.playPause), true);
});

test("letters match in either case but never with Shift, Alt or another modifier", () => {
  assert.equal(matchKey(press("P"), KEYS.post), true);
  assert.equal(matchKey(press("P", { shiftKey: true }), KEYS.post), false);
  assert.equal(matchKey(press("p", { altKey: true }), KEYS.post), false);
  assert.equal(matchKey(press("p", { metaKey: true }), KEYS.post), false);
  assert.equal(matchKey(press("a", { metaKey: true }), KEYS.selectAll), true);
  for (const [key, binding] of [
    ["j", KEYS.back],
    ["l", KEYS.forward],
    [",", KEYS.frameBack],
    [".", KEYS.frameForward],
    ["i", KEYS.setIn],
    ["o", KEYS.setOut],
  ] as const) {
    assert.equal(matchKey(press(key), binding), true, key);
  }
});
