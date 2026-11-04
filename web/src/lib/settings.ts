// What the Settings page's fields send: a list of channels from lines, a number when the draft is one, and
// the model overrides with one pass changed; the line saying which uploads clipping channels skip; the
// marketplaces that get a row of their own; Today's exchange rates, one currency a line; and the niches the
// briefs are sorted into, one a line.

import type { Marketplace, NicheValues, WatchValues } from "../types.ts";

export function parseChannels(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function channelLines(channels: string[]): string {
  return channels.join("\n");
}

// A draft that isn't a number goes as it is, so the engine's sentence says what the field takes.
export function numberOrText(draft: string): number | string {
  const n = Number(draft.trim());
  return draft.trim() !== "" && Number.isFinite(n) ? n : draft;
}

type Models = Record<string, Record<string, string>>;

// A cleared pass goes back to the default, so it is left out, and so is a provider with none left.
export function modelsWith(
  models: Models,
  provider: string,
  pass: string,
  name: string,
): Models {
  const { [pass]: _old, ...others } = models[provider] ?? {};
  const own = name.trim() ? { ...others, [pass]: name.trim() } : others;
  const { [provider]: _was, ...rest } = models;
  return Object.keys(own).length ? { ...rest, [provider]: own } : rest;
}

export function skipLine(watch: WatchValues): string {
  const minutes =
    watch.min_minutes === 1 ? "1 minute" : `${watch.min_minutes} minutes`;
  const hours = watch.max_hours === 1 ? "1 hour" : `${watch.max_hours} hours`;
  return `Clipping channels skip videos under ${minutes} and over ${hours}.`;
}

// Each marketplace once, matched as the engine matches them (ignoring case): the ones campaigns name and the
// ones already saved, spelt as saved or else as the first of ``named`` spells it, in name order.
export function marketplaceRows(
  named: string[],
  saved: Record<string, Marketplace>,
): string[] {
  const rows = new Map<string, string>();
  for (const name of Object.keys(saved))
    rows.set(name.trim().toLowerCase(), name);
  for (const raw of named) {
    const name = raw.trim();
    if (name && !rows.has(name.toLowerCase()))
      rows.set(name.toLowerCase(), name);
  }
  return [...rows.values()].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: "base" }),
  );
}

export const RATE_LINE =
  "Write one currency a line with what one of it is worth in US$, like AUD 0.65.";
const RATE = /^([A-Za-z]{3})\s*[:=]?\s*(\d+(?:\.\d+)?)$/;

// Each line a code and a value over 0, the code in capitals; blank lines are skipped.
export function parseRates(
  text: string,
): { rates: Record<string, number> } | { error: string } {
  const rates: Record<string, number> = {};
  for (const line of text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)) {
    const m = RATE.exec(line);
    const value = m ? Number(m[2]) : 0;
    if (!m || !(value > 0)) return { error: RATE_LINE };
    rates[m[1].toUpperCase()] = value;
  }
  return { rates };
}

export function rateLines(rates: Record<string, number>): string {
  return Object.entries(rates)
    .map(([code, value]) => `${code} ${value}`)
    .join("\n");
}

export const NICHE_LINE =
  "Write each niche like Gaming (en): gaming, games, one a line.";
const NICHE = /^([^:(][^:]*?)\s*(?:\(\s*([A-Za-z]{2,8})\s*\))?\s*:\s*(.+)$/;
const NICHES_MAX = 30;

// Each line a niche: its name, its language in brackets (en when it has none) and its tags after the colon,
// lowercased and once each. Blank lines are skipped; the engine checks the rest.
export function parseNiches(
  text: string,
): { niches: NicheValues[] } | { error: string } {
  const niches: NicheValues[] = [];
  for (const line of text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)) {
    const m = NICHE.exec(line);
    const tags = m
      ? m[3]
          .split(",")
          .map((t) => t.trim().replace(/\s+/g, " ").toLowerCase())
          .filter(Boolean)
      : [];
    if (!m || tags.length === 0) return { error: NICHE_LINE };
    niches.push({
      name: m[1].trim(),
      language: (m[2] ?? "en").toLowerCase(),
      tags: [...new Set(tags)],
    });
  }
  if (niches.length === 0)
    return { error: `Add at least one niche. ${NICHE_LINE}` };
  if (niches.length > NICHES_MAX)
    return { error: `Keep it to ${NICHES_MAX} niches or fewer.` };
  const names = niches.map((n) => n.name.toLowerCase());
  if (new Set(names).size !== names.length)
    return { error: "Two niches can't share a name." };
  return { niches };
}

export function nicheLines(niches: NicheValues[]): string {
  return niches
    .map((n) => `${n.name} (${n.language}): ${n.tags.join(", ")}`)
    .join("\n");
}
