// An account's Settings (SPEC 11): its label (and an Instagram Chrome account's handle), niche tags, default
// hashtags and call to action, posts a day, posting windows and the account it's paired with, on TikTok
// assisted posting and Creator Rewards, and the style kit its posts wear. Only what changed is sent, and the
// engine checks all of it before it keeps any.

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import {
  WINDOW_LINE,
  accountName,
  capWarning,
  listText,
  parseList,
  parseWindows,
  windowLines,
} from "../lib/accounts.ts";
import {
  CAPTION_PRESETS,
  FONT_NAMES,
  GAMEPLAY_MODES,
  PRESET_ACCENTS,
} from "../lib/options.ts";
import type {
  Account,
  CaptionPreset,
  StyleKit,
  AccountSettings as Values,
} from "../types.ts";
import { Button } from "./Button.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField, Toggle } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

export const CAP_WRONG = "Posts a day is a whole number from 1 to 10.";
export const KIT_HINT =
  "Posts to this account wear this look. A clip that looks different renders this account's version before it goes up.";
export const CLIPS_OWN = "The clip's own";
const OWN_COLOUR = "Your own colour";

// In the engine's order, so an unchanged kit reads the same as the one it sent
const KIT_FIELDS = [
  "preset",
  "font",
  "accent",
  "hook_fill",
  "hook_text",
  "credit_format",
  "gameplay_mode",
] as const;
const COLOURS = new Set<keyof StyleKit>(["accent", "hook_fill", "hook_text"]);
type Kit = Record<keyof StyleKit, string>; // "" is the clip's own

const SELECT =
  "w-full rounded-control border border-line bg-panel-2 px-3 py-2 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent aria-[invalid=true]:border-bad";

type Draft = {
  label: string;
  handle: string;
  niche_tags: string;
  hashtags: string;
  cta: string;
  daily_cap: string;
  windows: string;
  pair_id: string;
  assisted: boolean;
  creator_rewards: boolean;
  kit: Kit;
};

type Where =
  | Exclude<keyof Draft, "assisted" | "creator_rewards" | "kit">
  | `kit.${keyof StyleKit}`
  | "form";
type Problems = Partial<Record<Where, string>>;

function draftOf(account: Account): Draft {
  const s = account.settings;
  return {
    label: account.label,
    handle: account.handle ?? "",
    niche_tags: listText(s.niche_tags),
    hashtags: listText(s.hashtags),
    cta: s.cta,
    daily_cap: s.daily_cap === null ? "" : String(s.daily_cap),
    windows: windowLines(s.windows),
    pair_id: s.pair_id ?? "",
    assisted: s.assisted,
    creator_rewards: s.creator_rewards,
    kit: Object.fromEntries(
      KIT_FIELDS.map((f) => [f, s.style_kit[f] ?? ""]),
    ) as Kit,
  };
}

function kitOf(kit: Kit): StyleKit {
  return Object.fromEntries(
    KIT_FIELDS.map((f) => {
      const v = kit[f].trim();
      return [f, v === "" ? null : COLOURS.has(f) ? v.toUpperCase() : v];
    }),
  ) as StyleKit;
}

// Where the engine's refusal goes: its field (settings.windows.0.end is the windows' field, and
// settings.style_kit.accent the kit's accent), else the form.
function whereFor(field: string | null): Where {
  const parts = field?.startsWith("settings.")
    ? field.slice("settings.".length).split(".")
    : [field];
  if (parts[0] === "style_kit") {
    const f = KIT_FIELDS.find((k) => k === parts[1]);
    return f ? `kit.${f}` : "form";
  }
  const known: Where[] = [
    "label",
    "handle",
    "niche_tags",
    "hashtags",
    "cta",
    "daily_cap",
    "windows",
    "pair_id",
  ];
  return known.find((w) => w === parts[0]) ?? "form";
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

type Props = { account: Account; all: Account[]; onClose: () => void };

export function AccountSettings({ account, all, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>(() => draftOf(account));
  const [problems, setProblems] = useState<Problems>({});
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const pairId = useId();
  const name = accountName(account);
  const tiktok = account.platform === "tiktok";
  const typedHandle =
    account.platform === "instagram" && account.method === "browser";
  const mates = all.filter((a) => a.platform !== account.platform);

  // After a refusal, the focus goes to the first thing to fix.
  useEffect(() => {
    if (refused)
      form.current
        ?.querySelector<HTMLElement>('[aria-invalid="true"]')
        ?.focus();
  }, [refused]);

  function edit<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [field]: value }));
    setProblems((p) => ({ ...p, [field]: undefined, form: undefined }));
  }

  function editKit(field: keyof StyleKit, value: string) {
    setDraft((d) => ({ ...d, kit: { ...d.kit, [field]: value } }));
    setProblems((p) => ({
      ...p,
      [`kit.${field}`]: undefined,
      form: undefined,
    }));
  }

  function refuse(found: Problems) {
    setProblems(found);
    setRefused((n) => n + 1);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    const cap = draft.daily_cap.trim();
    if (cap && !(/^\d+$/.test(cap) && Number(cap) >= 1 && Number(cap) <= 10))
      return refuse({ daily_cap: CAP_WRONG });
    const windows = parseWindows(draft.windows);
    if ("error" in windows) return refuse({ windows: windows.error });

    const s = account.settings;
    const wanted: Partial<Values> = {
      niche_tags: parseList(draft.niche_tags),
      hashtags: parseList(draft.hashtags),
      cta: draft.cta.trim(),
      daily_cap: cap ? Number(cap) : null,
      windows: windows.windows,
      pair_id: draft.pair_id || null,
      ...(tiktok
        ? { assisted: draft.assisted, creator_rewards: draft.creator_rewards }
        : {}),
      style_kit: kitOf(draft.kit),
    };
    const settings = Object.fromEntries(
      Object.entries(wanted).filter(
        ([field, value]) => !same(value, s[field as keyof Values]),
      ),
    );
    const changes: Record<string, unknown> = {};
    if (draft.label.trim() !== account.label)
      changes.label = draft.label.trim();
    if (typedHandle && draft.handle.trim() !== (account.handle ?? ""))
      changes.handle = draft.handle.trim();
    if (Object.keys(settings).length) changes.settings = settings;
    if (Object.keys(changes).length === 0) return onClose();

    setProblems({});
    setSaving(true);
    try {
      await api<{ account: Account }>(
        `/api/accounts/${encodeURIComponent(account.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify(changes),
        },
      );
      await client.invalidateQueries({ queryKey: key.accounts });
      toast({ text: "Saved", kind: "ok", subject: name });
      onClose();
    } catch (err) {
      setSaving(false);
      refuse({
        [whereFor(err instanceof ApiError ? err.field : null)]: sentence(err),
      });
    }
  }

  return (
    <Dialog
      title={`Settings for ${name}`}
      open
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
    >
      <form
        ref={form}
        onSubmit={(e) => void submit(e)}
        noValidate
        className="flex flex-col gap-3"
      >
        <TextField
          label="Label"
          hint="Your own name for it, like Gaming."
          value={draft.label}
          onChange={(v) => edit("label", v)}
          error={problems.label}
          maxLength={60}
        />
        {typedHandle && (
          <TextField
            label="Handle"
            hint="As it shows on the profile. A new handle needs a new login."
            value={draft.handle}
            onChange={(v) => edit("handle", v)}
            error={problems.handle}
            maxLength={40}
          />
        )}
        <TextField
          label="Niche tags"
          hint="What it posts, with commas, like gaming, streamers. Up to 10."
          value={draft.niche_tags}
          onChange={(v) => edit("niche_tags", v)}
          error={problems.niche_tags}
        />
        <TextField
          label="Default hashtags"
          hint="Added to every caption here, with commas. Up to 10."
          value={draft.hashtags}
          onChange={(v) => edit("hashtags", v)}
          error={problems.hashtags}
        />
        <TextField
          label="Call to action"
          hint="A line at the end of every caption here, like Follow for part 2."
          value={draft.cta}
          onChange={(v) => edit("cta", v)}
          error={problems.cta}
          count={{ n: draft.cta.trim().length, max: 150, id: "cta" }}
        />
        <TextField
          label="Posts a day"
          hint="From 1 to 10. Leave it blank for the default in Settings."
          warning={capWarning(
            account.platform,
            draft.daily_cap,
            account.today.ceiling,
          )}
          value={draft.daily_cap}
          onChange={(v) => edit("daily_cap", v)}
          error={problems.daily_cap}
          inputMode="numeric"
        />
        <TextField
          label="Posting windows"
          hint={`${WINDOW_LINE} Leave it blank for the default windows in Settings.`}
          value={draft.windows}
          onChange={(v) => edit("windows", v)}
          error={problems.windows}
          multiline
        />
        <div className="flex flex-col gap-1">
          <label htmlFor={pairId} className="text-xs font-medium text-muted">
            Pair with
          </label>
          <select
            id={pairId}
            value={draft.pair_id}
            onChange={(e) => edit("pair_id", e.target.value)}
            aria-invalid={problems.pair_id ? true : undefined}
            aria-describedby={`${pairId}-hint`}
            className={SELECT}
          >
            <option value="">Not paired</option>
            {mates.map((mate) => (
              <option key={mate.id} value={mate.id}>
                {accountName(mate)}
              </option>
            ))}
          </select>
          <p id={`${pairId}-hint`} className="text-xs text-faint">
            In spread mode, a clip that goes to one of a pair goes to the other
            too.
          </p>
          {problems.pair_id && <FieldError text={problems.pair_id} />}
        </div>
        {tiktok && (
          <>
            <Toggle
              label="Assisted posting"
              hint="ClipForge fills everything in on TikTok and leaves the last Post press to you."
              checked={draft.assisted}
              onChange={(on) => edit("assisted", on)}
            />
            <Toggle
              label="Creator Rewards"
              hint="Only clips over 60 seconds go here, the ones TikTok's Creator Rewards pays for."
              checked={draft.creator_rewards}
              onChange={(on) => edit("creator_rewards", on)}
            />
          </>
        )}
        <StyleKitFields
          kit={draft.kit}
          problems={problems}
          onChange={editKit}
        />
        {problems.form && <FieldError text={problems.form} />}
        <DialogFooter>
          <Button onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={saving}>
            Save
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

type KitProps = {
  kit: Kit;
  problems: Problems;
  onChange: (field: keyof StyleKit, value: string) => void;
};

// The account's look, each field the clip's own until it's set.
function StyleKitFields({ kit, problems, onChange }: KitProps) {
  const id = useId();
  const accent = PRESET_ACCENTS[(kit.preset || "bold_pop") as CaptionPreset];
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-3 border-t border-line pt-3"
    >
      <h3 id={id} className="text-sm font-semibold">
        Style kit
      </h3>
      <p className="text-xs text-faint">{KIT_HINT}</p>
      <KitSelect
        label="Caption style"
        value={kit.preset}
        options={CAPTION_PRESETS}
        onChange={(v) => onChange("preset", v)}
        error={problems["kit.preset"]}
      />
      <KitSelect
        label="Font"
        hint="The captions' font, in place of the caption style's own."
        value={kit.font}
        options={FONT_NAMES}
        onChange={(v) => onChange("font", v)}
        error={problems["kit.font"]}
      />
      <KitColour
        label="Accent"
        value={kit.accent}
        start={accent}
        onChange={(v) => onChange("accent", v)}
        error={problems["kit.accent"]}
      />
      <KitColour
        label="Hook box colour"
        value={kit.hook_fill}
        start="#FFFFFF"
        onChange={(v) => onChange("hook_fill", v)}
        error={problems["kit.hook_fill"]}
      />
      <KitColour
        label="Hook text colour"
        value={kit.hook_text}
        start="#000000"
        onChange={(v) => onChange("hook_text", v)}
        error={problems["kit.hook_text"]}
      />
      <TextField
        label="Credit format"
        hint={`Leave it blank for the clip's own. Put {channel} where the channel's name goes, like Clip: {channel}.`}
        value={kit.credit_format}
        onChange={(v) => onChange("credit_format", v)}
        error={problems["kit.credit_format"]}
        count={{
          n: kit.credit_format.trim().length,
          max: 60,
          id: "credit-format",
        }}
      />
      <KitSelect
        label="Gameplay"
        hint="Gameplay from your Library under the speaker."
        value={kit.gameplay_mode}
        options={GAMEPLAY_MODES}
        onChange={(v) => onChange("gameplay_mode", v)}
        error={problems["kit.gameplay_mode"]}
      />
    </section>
  );
}

type KitSelectProps = {
  label: string;
  value: string;
  options: Record<string, string>;
  onChange: (value: string) => void;
  hint?: string;
  error?: string;
};

function KitSelect({
  label,
  value,
  options,
  onChange,
  hint,
  error,
}: KitSelectProps) {
  const id = useId();
  const described = [error && `${id}-error`, hint && `${id}-hint`]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
        className={SELECT}
      >
        <option value="">{CLIPS_OWN}</option>
        {Object.entries(options).map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-faint">
          {hint}
        </p>
      )}
      {error && <FieldError id={`${id}-error`} text={error} />}
    </div>
  );
}

type KitColourProps = {
  label: string;
  value: string; // "" is the clip's own
  start: string; // the colour a new choice starts at
  onChange: (value: string) => void;
  error?: string;
};

function KitColour({ label, value, start, onChange, error }: KitColourProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <div className="flex items-center gap-3">
        <select
          id={id}
          value={value ? "own" : ""}
          onChange={(e) => onChange(e.target.value ? start : "")}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          className={SELECT}
        >
          <option value="">{CLIPS_OWN}</option>
          <option value="own">{OWN_COLOUR}</option>
        </select>
        {value && (
          <input
            type="color"
            aria-label={`Your ${label.toLowerCase()}`}
            value={
              /^#[0-9a-f]{6}$/i.test(value)
                ? value.toLowerCase()
                : start.toLowerCase()
            }
            onChange={(e) => onChange(e.target.value)}
            className="h-8 w-12 shrink-0 cursor-pointer rounded-control border border-line bg-panel-2 p-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          />
        )}
      </div>
      {error && <FieldError id={`${id}-error`} text={error} />}
    </div>
  );
}
