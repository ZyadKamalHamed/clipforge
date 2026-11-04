// What a drop or a paste on the window carries, read the way the server will read it.

import { looksLikeVideoLink } from "./links.ts";

// As clipforge/server/uploads.py has them, so a file the server would refuse is never sent.
const VIDEO_TYPES = new Set(["mp4", "mov", "mkv", "webm", "m4v"]);
// What a file picker offers, the same types.
export const VIDEO_ACCEPT = [...VIDEO_TYPES].map((type) => `.${type}`).join(",");
export const NOT_A_VIDEO = "That file type isn't a video ClipForge can use. Use MP4, MOV, MKV, WEBM or M4V.";

export function isVideoFile(name: string): boolean {
  return VIDEO_TYPES.has(extension(name));
}

// The music library's types (clipforge/library/music.py MUSIC_TYPES): a video file with sound is music too.
const MUSIC_TYPES = new Set(["mp3", "m4a", "aac", "wav", "aif", "aiff", "flac", "mp4", "m4v", "mov"]);
export const MUSIC_ACCEPT = [...MUSIC_TYPES].map((type) => `.${type}`).join(",");

export function isMusicFile(name: string): boolean {
  return MUSIC_TYPES.has(extension(name));
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

// A dragged link comes as a uri-list (one per line, with "#" lines as comments), anything else as plain text.
export function droppedText(getData: (type: string) => string): string {
  const links = getData("text/uri-list")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  return links.length ? links.join("\n") : getData("text/plain");
}

export function hasLink(text: string, allowOtherSites: boolean): boolean {
  return text.split(/\s+/).some((word) => looksLikeVideoLink(word, allowOtherSites));
}

// Links this window put on the clipboard itself (a live post copied to submit it), so pasting one back never
// starts a project (SPEC 14.10 item 5). Only the last few are kept, for as long as the window is open.
const COPIED_KEPT = 20;
const copied: string[] = [];

export function rememberCopied(text: string): void {
  const kept = text.trim();
  if (!kept) return;
  copied.push(kept);
  if (copied.length > COPIED_KEPT) copied.shift();
}

export function wasCopiedHere(text: string): boolean {
  const pasted = text.trim();
  return pasted !== "" && copied.includes(pasted);
}

// A header value has to be ASCII, so every other character goes as a \u escape (the same JSON once parsed).
export function headerJson(value: unknown): string {
  return JSON.stringify(value).replace(/[^\x20-\x7e]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}
