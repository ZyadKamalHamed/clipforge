// The Doctor's checks (clipforge/doctor.py) as the window shows them: by the names a person knows them by,
// what needs fixing first.

import type { Check } from "../types.ts";

const NAMES: Record<string, string> = {
  python: "Python",
  ffmpeg: "ffmpeg",
  ffprobe: "ffprobe",
  "yt-dlp": "yt-dlp",
  "js runtime": "JavaScript runtime",
  claude: "Claude Code",
  "speech model": "Speech model",
  disk: "Disk space",
  keychain: "Keychain",
  background: "Background service",
  opencv: "OpenCV",
  "face model": "Face model",
  "video encoder": "Video encoder",
};

export function checkName(name: string): string {
  return NAMES[name] ?? name;
}

// 0: a required check failing, so ClipForge can't work; 1: an optional one failing; 2: fine.
const need = (check: Check) => (check.ok ? 2 : check.required ? 0 : 1);

export function byNeed(checks: Check[]): Check[] {
  return [...checks].sort((a, b) => need(a) - need(b));
}

export function needsFixing(checks: Check[]): Check[] {
  return checks.filter((check) => need(check) === 0);
}
