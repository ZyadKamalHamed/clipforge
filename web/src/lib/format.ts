// Times, sizes and ages the way the app shows them.

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const ss = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

// Decimal units, as macOS shows sizes: one decimal place under 10, none from 10 up.
export function formatBytes(n: number): string {
  let value = Math.max(0, n);
  let unit = 0;
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  if (unit === 0) return `${Math.round(value)} B`;
  const shown = value < 10 ? value.toFixed(1).replace(/\.0$/, "") : String(Math.round(value));
  return `${shown} ${UNITS[unit]}`;
}

// What Instagram and TikTok count a caption's length in: UTF-16 code units, so an emoji is often 2.
export function utf16Length(text: string): number {
  return text.length;
}

// How long ago a time the engine sent (seconds since 1970, as all its times are) was, in plain words.
export function formatAgo(at: number, now: number): string {
  const s = Math.max(0, now - at);
  if (!Number.isFinite(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const days = Math.floor(s / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
