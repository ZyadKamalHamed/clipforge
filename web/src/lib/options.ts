// The Create page's options and a look's choices with the words the window shows for them, in the engine's
// order (clipforge/models.py ProjectOptions and LENGTH_RANGES, clipforge/render/styles.py PRESETS,
// clipforge/assets.py FONTS).

import type {
  CaptionPreset,
  FontName,
  GameplayMode,
  ProjectOptions,
  Quality,
} from "../types.ts";
import { clipsLabel } from "./projects.ts";

export const LENGTHS: {
  value: ProjectOptions["length"];
  label: string;
  range: string;
}[] = [
  { value: "auto", label: "Auto", range: "15 to 75 s" },
  { value: "short", label: "Short", range: "15 to 35 s" },
  { value: "standard", label: "Standard", range: "30 to 60 s" },
  { value: "long", label: "Long", range: "60 to 90 s" },
];

export const CONTENT_TYPES: {
  value: ProjectOptions["content_type"];
  label: string;
}[] = [
  { value: "auto", label: "Auto" },
  { value: "podcast", label: "Podcast" },
  { value: "solo", label: "Solo" },
  { value: "stream", label: "Stream" },
  { value: "other", label: "Other" },
];

export const LAYOUTS: Record<ProjectOptions["layout"], string> = {
  auto: "AI decides",
  face: "Face crop",
  stacked: "Stacked speakers",
  gameplay: "Gameplay split",
  stream: "Facecam and game",
  fit: "Blurred fit",
  motion: "Follow motion",
};

export const CAPTION_PRESETS: Record<CaptionPreset, string> = {
  bold_pop: "Bold pop",
  hormozi: "Hormozi",
  beast: "Beast",
  pill: "Pill",
  clean: "Clean",
  one_word: "One word",
  neon: "Neon",
};

// Each caption style's own accent colour, the active word's (clipforge/render/styles.py PRESETS).
export const PRESET_ACCENTS: Record<CaptionPreset, string> = {
  bold_pop: "#FFD60A",
  hormozi: "#3CFF5A",
  beast: "#FFD60A",
  pill: "#FFD60A",
  clean: "#FFD60A",
  one_word: "#FFD60A",
  neon: "#00E5FF",
};

export const FONT_NAMES: Record<FontName, string> = {
  "montserrat-black": "Montserrat Black",
  "montserrat-extrabold": "Montserrat ExtraBold",
  "montserrat-bold": "Montserrat Bold",
  "poppins-extrabold": "Poppins ExtraBold",
  anton: "Anton",
  bangers: "Bangers",
  "inter-extrabold": "Inter ExtraBold",
  "bebas-neue": "Bebas Neue",
  "archivo-black": "Archivo Black",
  "luckiest-guy": "Luckiest Guy",
};

export const GAMEPLAY_MODES: Record<GameplayMode, string> = {
  auto: "When the AI picks it",
  off: "Never",
  always: "Always",
};

export const QUALITIES: Record<Quality, string> = {
  best: "Best",
  balanced: "Balanced",
  fast: "Fast",
};

export const CLIP_COUNTS = Array.from({ length: 30 }, (_, i) => i + 1);

export const PROMPT_MAX = 300; // clipforge/models.py, where a longer one is cut

// What the closed options say: only what was changed from the defaults, so a glance shows it.
// Post as it is cuts nothing, so only the download quality and the credit still apply.
export function optionsSummary(options: ProjectOptions): string {
  const parts: string[] = [];
  if (options.as_is) {
    parts.push("Post as it is");
    if (options.quality) parts.push(`${QUALITIES[options.quality]} quality`);
    if (options.credit !== null)
      parts.push(options.credit ? "Credit on" : "Credit off");
    return parts.join(", ");
  }
  const length = LENGTHS.find((l) => l.value === options.length);
  if (length && length.value !== "auto")
    parts.push(`${length.label} (${length.range})`);
  if (options.clip_count !== null) parts.push(clipsLabel(options.clip_count));
  const content = CONTENT_TYPES.find((c) => c.value === options.content_type);
  if (content && content.value !== "auto") parts.push(content.label);
  if (options.caption_preset)
    parts.push(`${CAPTION_PRESETS[options.caption_preset]} captions`);
  if (options.layout !== "auto") parts.push(LAYOUTS[options.layout]);
  if (options.quality) parts.push(`${QUALITIES[options.quality]} quality`);
  if (options.credit !== null)
    parts.push(options.credit ? "Credit on" : "Credit off");
  const about = options.prompt?.trim();
  if (about) parts.push(`About "${about}"`);
  return parts.join(", ");
}
