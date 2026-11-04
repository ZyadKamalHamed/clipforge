// The editor's Look and Sound tabs. Look: the layout with the AI's own choice and why, the gameplay video
// for a split screen, the caption style and colour, and the switches the settings set for every clip.
// Sound: sound effects on or off, and each one to hear. Their changes wait for the render, except a new
// random gameplay segment, which renders straight away. Sound also picks the music bed and its level under
// the speech, takes a voiceover (components/Voiceover.tsx), and lists the shortlisted TikTok sounds that suit
// the clip, for a post to pick in the Post dialog.

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Shuffle } from "lucide-react";
import { useId } from "react";

import { api, key, sentence } from "../api.ts";
import { coldOpenHint, type Editing, fieldTab } from "../lib/editor.ts";
import { CAPTION_PRESETS, LAYOUTS } from "../lib/options.ts";
import {
  levelText,
  MUSIC_DB,
  MUSIC_DB_MAX,
  MUSIC_DB_MIN,
  soundLabel,
  trackLabel,
} from "../lib/sounds.ts";
import type {
  CaptionPreset,
  ClipDetail,
  LibraryItem,
  ProjectOptions,
  SoundSuggestion,
} from "../types.ts";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { Choice } from "./Choice.tsx";
import { FieldError, Toggle } from "./Field.tsx";
import { SoundList } from "./Sounds.tsx";
import { useToast } from "./Toasts.tsx";
import { Voiceover } from "./Voiceover.tsx";

type Layout = ProjectOptions["layout"];

const SECTION =
  "flex flex-col gap-3 border-t border-line pt-4 first:border-t-0 first:pt-0";

const SELECT =
  "w-full rounded-control border border-line bg-panel-2 px-3 py-2 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

// The switches every clip takes from the settings, which a clip may set for itself.
// SPEC 8.3 to 8.7 for what each one does.
export const SWITCHES: { key: string; label: string; hint: string }[] = [
  {
    key: "punch_ins",
    label: "Punch-ins",
    hint: "A quick zoom on the lines the AI marks for emphasis.",
  },
  {
    key: "pause_trim",
    label: "Pause trimming",
    hint: "Shortens pauses longer than about a third of a second.",
  },
  {
    key: "filler_removal",
    label: "Filler word removal",
    hint: "Cuts um, uh and the like.",
  },
  {
    key: "auto_pace",
    label: "Auto pace",
    hint: "Speeds a slow speaker up a little, voice unchanged.",
  },
  {
    key: "hook_whole_clip",
    label: "Keep the hook title up",
    hint: "The title stays for the whole clip, not 4 s.",
  },
  {
    key: "progress_bar",
    label: "Progress bar",
    hint: "A thin bar showing how far through the clip is.",
  },
  {
    key: "credit_line",
    label: "Credit line",
    hint: "The source's name under the title for the first 4 s.",
  },
];

// The first refused look setting's sentence, shown at the top of the tab.
function lookError(editing: Editing, tab: "look" | "sound"): string | null {
  for (const field of [
    "layout",
    "gameplay_item",
    "gameplay_seed",
    "preset",
    "accent",
    "encoder",
    "sfx",
    "music",
    "music_db",
    "voiceover",
    "voiceover_at",
    "cold_open",
    ...SWITCHES.map((s) => s.key),
  ]) {
    const full = `edits.${field}`;
    if (fieldTab(full) === tab && editing.errorUnder(full))
      return editing.errorUnder(full);
  }
  return null;
}

type LookProps = {
  editing: Editing;
  onShuffle: () => void;
  shuffling: boolean;
};

export function LookTab({ editing, onShuffle, shuffling }: LookProps) {
  const { detail, value, change } = editing;
  const layout = value<Layout>("layout");
  const ai = detail.layout as Layout | null;
  const effective = layout === "auto" ? ai : layout;
  const problem = lookError(editing, "look");
  const colourId = useId();
  const accent = value<string>("accent");

  return (
    <div className="flex flex-col gap-6">
      {problem && <FieldError text={problem} />}
      <section className={SECTION}>
        <Choice
          legend="Layout"
          name="layout"
          value={layout}
          options={(Object.keys(LAYOUTS) as Layout[]).map((l) => ({
            value: l,
            label: LAYOUTS[l],
          }))}
          onChange={(l) => change("layout", l)}
        />
        {ai && (
          <p className="text-sm text-muted" dir="auto">
            {`AI's choice: ${LAYOUTS[ai] ?? ai}.`}
            {detail.layout_reason && ` ${detail.layout_reason}`}
          </p>
        )}
        {effective === "gameplay" && (
          <Gameplay
            editing={editing}
            onShuffle={onShuffle}
            shuffling={shuffling}
          />
        )}
      </section>
      <section className={SECTION}>
        <Choice
          legend="Caption style"
          name="preset"
          value={value<CaptionPreset>("preset")}
          options={(Object.keys(CAPTION_PRESETS) as CaptionPreset[]).map(
            (p) => ({ value: p, label: CAPTION_PRESETS[p] }),
          )}
          onChange={(p) => change("preset", p)}
        />
        <div className="flex items-center gap-3">
          <input
            id={colourId}
            type="color"
            value={
              /^#[0-9a-f]{6}$/i.test(accent) ? accent.toLowerCase() : "#ffd60a"
            }
            onChange={(e) => change("accent", e.target.value.toLowerCase())}
            className="h-8 w-12 cursor-pointer rounded-control border border-line bg-panel-2 p-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          />
          <label htmlFor={colourId} className="flex flex-col text-sm">
            Accent colour
            <span className="text-xs text-faint">
              The word being said, and the title's accent word.
            </span>
          </label>
        </div>
      </section>
      <section className={SECTION}>
        {SWITCHES.map((s) => (
          <Toggle
            key={s.key}
            label={s.label}
            hint={s.hint}
            checked={value<boolean>(s.key)}
            onChange={(on) => change(s.key, on)}
          />
        ))}
        <Toggle
          label="Cold open"
          hint={coldOpenHint(detail.cold_open_line)}
          checked={
            detail.cold_open_line !== null && value<boolean>("cold_open")
          }
          disabled={detail.cold_open_line === null}
          onChange={(on) => change("cold_open", on)}
        />
        <Toggle
          label="Best quality"
          hint="A slower render with a sharper picture."
          checked={value<string>("encoder") === "best"}
          onChange={(on) => change("encoder", on ? "best" : "fast")}
        />
      </section>
    </div>
  );
}

function Gameplay({ editing, onShuffle, shuffling }: LookProps) {
  const { value, change } = editing;
  const id = useId();
  const items = useQuery({
    queryKey: key.gameplay,
    queryFn: ({ signal }) =>
      api<{ items: LibraryItem[] }>("/api/library?kind=gameplay", { signal }),
  });
  const picked = value<string | null>("gameplay_item") ?? "";
  const list = items.data?.items ?? [];
  return (
    <div className="flex flex-col gap-2 rounded-card border border-line p-3">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        Gameplay video
      </label>
      <select
        id={id}
        value={picked}
        onChange={(e) => change("gameplay_item", e.target.value || null)}
        className={SELECT}
      >
        <option value="">Any from the library</option>
        {list.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
        {picked && !list.some((item) => item.id === picked) && (
          <option value={picked}>A video no longer in the library</option>
        )}
      </select>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onShuffle} disabled={shuffling}>
          <Shuffle aria-hidden size={14} />
          New random segment
        </Button>
        <span className="text-xs text-faint">
          Renders straight away with another stretch of the same video.
        </span>
      </div>
    </div>
  );
}

export function SoundTab({
  editing,
  onSaved,
}: {
  editing: Editing;
  onSaved: (updated: ClipDetail) => void;
}) {
  const { detail, value, change } = editing;
  const problem = lookError(editing, "sound");
  const suggested = useQuery({
    queryKey: key.clipSounds(detail.id),
    queryFn: ({ signal }) =>
      api<{ sounds: SoundSuggestion[] }>(
        `/api/clips/${encodeURIComponent(detail.id)}/sounds`,
        { signal },
      ),
  });
  const sounds = suggested.data?.sounds ?? [];

  return (
    <div className="flex flex-col gap-6">
      {problem && <FieldError text={problem} />}
      <MusicBed
        editing={editing}
        suited={sounds.filter((s) => s.kind === "music")}
      />
      <section className={SECTION}>
        <Toggle
          label="Sound effects"
          hint="A whoosh into a cold open or a big layout change, a pop on an emoji, a soft hit on the payoff."
          checked={value<boolean>("sfx")}
          onChange={(on) => change("sfx", on)}
        />
        <SoundList />
      </section>
      <Voiceover editing={editing} onSaved={onSaved} />
      <Trending
        sounds={sounds.filter((s) => s.kind !== "music")}
        loaded={suggested.isSuccess}
        error={suggested.error ? sentence(suggested.error) : null}
      />
    </div>
  );
}

// The music bed: off, the library's best match, or a track of yours, those that suit the clip first.
function MusicBed({
  editing,
  suited,
}: {
  editing: Editing;
  suited: SoundSuggestion[];
}) {
  const { value, change } = editing;
  const id = useId();
  const levelId = useId();
  const items = useQuery({
    queryKey: key.music,
    queryFn: ({ signal }) =>
      api<{ items: LibraryItem[] }>("/api/library?kind=music", { signal }),
  });
  const tracks = items.data?.items ?? [];
  const why = new Map(suited.map((s) => [s.id, s.why]));
  const listed = [
    ...suited.flatMap((s) => tracks.filter((t) => t.id === s.id)),
    ...tracks.filter((t) => !why.has(t.id)),
  ];
  const picked = value<string | null>("music") ?? "";
  const db = value<number | null>("music_db") ?? MUSIC_DB;
  const track = tracks.find((t) => t.id === picked);

  return (
    <section className={SECTION}>
      <div className="flex flex-col gap-2">
        <label htmlFor={id} className="text-xs font-medium text-muted">
          Music
        </label>
        <select
          id={id}
          value={picked}
          onChange={(e) => change("music", e.target.value || null)}
          className={SELECT}
        >
          <option value="">Off</option>
          <option value="auto">Best match from your library</option>
          {listed.map((t) => (
            <option key={t.id} value={t.id}>
              {trackLabel(t.name, why.get(t.id) ?? null)}
            </option>
          ))}
          {picked && picked !== "auto" && !track && (
            <option value={picked}>A track no longer in the library</option>
          )}
        </select>
      </div>
      {picked && (
        <div className="flex flex-col gap-1">
          <label htmlFor={levelId} className="text-xs font-medium text-muted">
            Music level
          </label>
          <input
            id={levelId}
            type="range"
            min={MUSIC_DB_MIN}
            max={MUSIC_DB_MAX}
            step={1}
            value={db}
            onChange={(e) => change("music_db", Number(e.target.value))}
            className="w-full accent-accent-strong"
          />
          <span className="text-xs text-faint tabular-nums">
            {levelText(db)}
          </span>
        </div>
      )}
      {track?.credit_required && (
        <p className="text-xs text-muted">
          Its licence asks for credit, so the caption names it.
        </p>
      )}
    </section>
  );
}

// The shortlisted TikTok sounds that suit the clip, each opening in your browser. A post picks one in
// the Post dialog; ClipForge never adds a sound to the file.
function Trending({
  sounds,
  loaded,
  error,
}: {
  sounds: SoundSuggestion[];
  loaded: boolean;
  error: string | null;
}) {
  const toast = useToast();
  const headingId = useId();

  async function open(sound: SoundSuggestion, link: string) {
    try {
      await api(`/api/links/${encodeURIComponent(link)}/open`, {
        method: "POST",
      });
      toast({ text: `Opened ${sound.title} in your browser.`, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <section className={SECTION}>
      <h3 id={headingId} className="text-sm font-semibold">
        Trending sounds for this clip
      </h3>
      {error && <p className="text-sm text-muted">{error}</p>}
      {sounds.length > 0 && (
        <ul aria-labelledby={headingId} className="flex flex-col gap-2">
          {sounds.map((sound) => (
            <li
              key={sound.id}
              className="flex items-center justify-between gap-3 rounded-card border border-line p-3"
            >
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="truncate text-sm" dir="auto">
                  {soundLabel(sound)}
                </span>
                <span className="text-xs text-muted">{sound.why}</span>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {sound.stale && <Badge tone="warn">Stale</Badge>}
                {sound.link_id && (
                  <Button
                    aria-label={`Open ${sound.title}`}
                    onClick={() => void open(sound, sound.link_id ?? "")}
                  >
                    <ExternalLink aria-hidden size={14} />
                    Open
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {loaded && sounds.length === 0 && (
        <p className="text-sm text-muted">
          No sound on your TikTok shortlist suits this clip yet.
        </p>
      )}
      <p className="text-xs text-faint">
        Pick one for a post in the Post dialog.
      </p>
    </section>
  );
}
