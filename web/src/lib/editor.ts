// The clip editor's changes waiting for a render, and the rules it shares with the engine: how a title's
// words are matched (clipforge/brain/text.py), how a post is put together and counted
// (clipforge/brain/wording.py) and how a clip's look is worked out (clipforge/render/styles.py style_for).

import type { CaptionPreset, ClipDetail, ProjectOptions, TranscriptWindow } from "../types.ts";
import { PRESET_ACCENTS } from "./options.ts";

export type Range = [number, number];
export type Edits = Record<string, unknown>;

// Cut, Text and Look changes, sent together by one render. An edit set to null takes the clip's own edit
// away; a null range puts the clip back where the AI cut it, and no range leaves it where it is.
export type Pending = { edits: Edits; range?: Range | null };

// What a tab of the editor works with: the clip, each edit as it stands (waiting or saved) and the
// engine's sentence for a field it refused.
export type Editing = {
  detail: ClipDetail;
  transcript: TranscriptWindow | undefined;
  value: <T>(key: string) => T;
  change: (key: string, value: unknown) => void;
  error: (field: string) => string | null;
  errorUnder: (prefix: string) => string | null;
};

export type EditorTab = "cut" | "text" | "look" | "sound" | "copy";

export const TITLE_MAX = 42;
export const CARD_MAX = 70;
export const CARDS_MAX = 3;
export const FIX_MAX = 30;
export const POST_MAX = 2200;

const TAB_OF: Record<string, EditorTab> = {
  struck: "cut",
  hook_title: "text",
  accent_word: "text",
  fact_cards: "text",
  word_fixes: "text",
  emoji: "text",
  sfx: "sound",
  music: "sound",
  music_db: "sound",
  voiceover: "sound",
  voiceover_at: "sound",
  tiktok_caption: "copy",
  tiktok_hashtags: "copy",
  instagram_caption: "copy",
  instagram_hashtags: "copy",
};

const LOOK_KEYS = [
  "layout",
  "gameplay_item",
  "gameplay_seed",
  "preset",
  "accent",
  "punch_ins",
  "pause_trim",
  "filler_removal",
  "auto_pace",
  "credit_line",
  "progress_bar",
  "hook_whole_clip",
  "cold_open",
  "encoder",
];

// The settings a clip's look copies, under its project's choices and its own edits.
const COPIED = [
  "gameplay_mode",
  "punch_ins",
  "pause_trim",
  "filler_removal",
  "auto_pace",
  "emoji",
  "sfx",
  "encoder",
  "fps_cap_30",
  "hook_whole_clip",
  "credit_line",
  "progress_bar",
  "cold_open",
];

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, stable(v)]),
    );
  }
  return value ?? null;
}

// Two values the same once a missing one counts as null and an object's keys are in order.
export function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(stable(a)) === JSON.stringify(stable(b));
}

export function withEdit(p: Pending, key: string, value: unknown, saved: unknown): Pending {
  const edits = { ...p.edits };
  if (same(value, saved)) delete edits[key];
  else edits[key] = value;
  return { ...p, edits };
}

// ``moved`` says the saved clip is no longer where the AI cut it, so null has somewhere to go back to.
export function withRange(p: Pending, range: Range | null, saved: Range, moved: boolean): Pending {
  const next: Pending = { ...p };
  if (range === null ? !moved : same(range, saved)) delete next.range;
  else next.range = range;
  return next;
}

// Back to the AI's cut: the original range and no words struck, asked for only where they were changed.
export function resetCut(p: Pending, saved: Edits, range: Range, moved: boolean): Pending {
  const next = withEdit(withRange(p, null, range, moved), "struck", null, saved.struck ?? null);
  if (!(saved.struck as unknown[] | undefined)?.length) delete next.edits.struck;
  return next;
}

// The changes waiting for a render, kept in the window's session storage so a reload (the engine restarted
// and the window took its new session cookie) doesn't lose them. Without storage nothing is kept.
const KEPT = "clipforge.pending.";

export function keptPending(id: string): Pending {
  try {
    const kept = JSON.parse(sessionStorage.getItem(KEPT + id) ?? "null") as Pending | null;
    if (kept && typeof kept.edits === "object" && kept.edits !== null) return kept;
  } catch {
    // unreadable or refused: start clean
  }
  return { edits: {} };
}

export function keepPending(id: string, p: Pending): void {
  try {
    if (changeCount(p) === 0) sessionStorage.removeItem(KEPT + id);
    else sessionStorage.setItem(KEPT + id, JSON.stringify(p));
  } catch {
    // refused: the changes live only in the window, as before
  }
}

export function changeCount(p: Pending): number {
  return Object.keys(p.edits).length + (p.range !== undefined ? 1 : 0);
}

export function changesLabel(n: number): string {
  return `${n} ${n === 1 ? "change" : "changes"} not rendered yet`;
}

export function renderBody(p: Pending): { edits: Edits; range?: Range | null } {
  return p.range === undefined ? { edits: p.edits } : { edits: p.edits, range: p.range };
}

// A word as the engine compares words: its letters and digits, in lower case.
function plain(text: string): string {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

// Each of the title's words once, as the engine stores it (punctuation and all) and as it reads.
export function titleWords(title: string): { value: string; label: string }[] {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const token of title.split(/\s+/)) {
    const key = plain(token);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push({ value: token, label: token.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "") });
  }
  return out;
}

// The word of ``title`` that ``word`` names, whatever its case and punctuation, or "" for none.
export function titleWord(word: string | null | undefined, title: string): string {
  const key = plain(word ?? "");
  if (!key) return "";
  return title.split(/\s+/).find((token) => plain(token) === key) ?? "";
}

// Characters as the engine counts a title or a card: an emoji is one.
export function chars(text: string): number {
  return [...text].length;
}

export function postText(caption: string, tags: string[], credit: string | null | undefined): string {
  return [caption, credit ?? "", tags.join(" ")].filter((part) => part).join("\n\n");
}

export function parseTags(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .filter((tag) => tag)
    .map((tag) => (tag.startsWith("#") ? tag : `#${tag}`));
}

// The word being said at ``t`` in the render: the last one started by then.
export function playingWord(words: { i: number; out: number | null }[], t: number): number | null {
  let found: { i: number; out: number } | null = null;
  for (const { i, out } of words) {
    if (out !== null && out <= t + 1e-6 && (found === null || out >= found.out)) found = { i, out };
  }
  return found?.i ?? null;
}

export function toggleStrike(struck: number[], picked: number[]): number[] {
  const all = new Set(struck);
  if (picked.every((i) => all.has(i))) for (const i of picked) all.delete(i);
  else for (const i of picked) all.add(i);
  return [...all].sort((a, b) => a - b);
}

// The tab whose field the engine refused: "range" or "edits.<key>..." as its error names it.
export function fieldTab(field: string | null | undefined): EditorTab | null {
  if (!field) return null;
  if (field === "range") return "cut";
  const key = field.startsWith("edits.") ? field.slice(6).split(".")[0] : "";
  if (LOOK_KEYS.includes(key)) return "look";
  return TAB_OF[key] ?? null;
}

const COLD_LINE_MAX = 80;

// The Cold open switch's hint: the line the clip would open on, cut to 80 characters, or why there is none.
export function coldOpenHint(line: string | null): string {
  if (line === null) return "No line in this clip was picked for a cold open.";
  const chars = Array.from(line);
  const shown = chars.length > COLD_LINE_MAX ? `${chars.slice(0, COLD_LINE_MAX - 1).join("").trimEnd()}\u2026` : line;
  return `Opens on "${shown}", then a whoosh into the start.`;
}

export function lengthLabel([start, end]: Range): string {
  return `${(end - start).toFixed(1)} s`;
}

type LookOptions = Pick<ProjectOptions, "layout" | "caption_preset">;

// The clip's look as the engine will render it: the settings, then the project's layout and caption
// style, then the clip's own edits. The accent is the clip's, else the settings', else its preset's.
export function look(settings: Record<string, unknown>, options: LookOptions, edits: Edits): Edits {
  const out: Edits = Object.fromEntries(COPIED.map((key) => [key, settings[key]]));
  out.preset = options.caption_preset ?? settings.caption_preset ?? "bold_pop";
  out.layout = options.layout ?? "auto";
  for (const [key, value] of Object.entries(edits)) if (value !== null && value !== undefined) out[key] = value;
  const preset = (out.preset as CaptionPreset) in PRESET_ACCENTS ? (out.preset as CaptionPreset) : "bold_pop";
  out.accent = edits.accent || settings.accent_color || PRESET_ACCENTS[preset];
  return out;
}
