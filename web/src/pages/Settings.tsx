// The Settings page (SPEC 14.7): every setting saves as it changes, a refused one says why at its field,
// and the theme changes at once. Posting ends with the background service and its Install or Remove (10.7). Today holds the daily notification, the switch for the others (off until
// you turn it on, 18), the starting guess for new accounts and the exchange rates (14.10, 10.11). Each marketplace a campaign names gets its Withdraw link and minimum
// payout (10.10), then the brief sources and the niches briefs are sorted into (10.11). Storage shows what takes the space and cleans up old source videos once asked; the Doctor
// shows each check with how to fix it, and updates yt-dlp.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import { Button } from "../components/Button.tsx";
import { Choice } from "../components/Choice.tsx";
import { useConfirm } from "../components/Confirm.tsx";
import { FieldError, TextField, Toggle } from "../components/Field.tsx";
import { SWITCHES } from "../components/LookTab.tsx";
import { SourceList } from "../components/SourceList.tsx";
import { useToast } from "../components/Toasts.tsx";
import { byNeed, checkName } from "../lib/checks.ts";
import { formatBytes } from "../lib/format.ts";
import {
  CAPTION_PRESETS,
  GAMEPLAY_MODES,
  PRESET_ACCENTS,
  QUALITIES,
} from "../lib/options.ts";
import {
  capWarning,
  parseWindows,
  WINDOW_LINE,
  windowLines,
} from "../lib/accounts.ts";
import {
  channelLines,
  marketplaceRows,
  modelsWith,
  nicheLines,
  numberOrText,
  parseChannels,
  parseNiches,
  parseRates,
  RATE_LINE,
  rateLines,
  skipLine,
} from "../lib/settings.ts";
import { campaignsQuery, doctorQuery, settingsQuery } from "../state.ts";
import type {
  CaptionPreset,
  Check,
  Cleaned,
  Marketplace,
  PostingValues,
  SettingsBody,
  ServiceStatus,
  SettingsValues,
  StorageInfo,
  TodayValues,
  UpdateResult,
  WatchValues,
} from "../types.ts";

const SELECT =
  "w-full rounded-control border border-line bg-panel-2 px-3 py-2 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const PROVIDERS: Record<string, string> = {
  claude_cli: "Claude Code (your Claude subscription)",
  anthropic: "Anthropic API key",
  gemini: "Gemini API key",
  openai_compat: "A local AI server",
  heuristic: "No AI (basic picks only)",
};
const PASSES: Record<string, { label: string; hint: string }> = {
  discover: {
    label: "Discover model",
    hint: "Reads the transcript and finds the moments.",
  },
  rerank: {
    label: "Rerank model",
    hint: "Compares the moments and puts the best first.",
  },
  package: {
    label: "Package model",
    hint: "Writes each clip's hook title and caption.",
  },
  review: {
    label: "Review model",
    hint: "Checks each clip makes sense on its own.",
  },
};
const ON_LIMIT: Record<string, string> = {
  wait: "Wait for it to reset",
  basic: "Finish with basic picks",
};
const ENGINES: Record<string, string> = {
  auto: "Auto",
  parakeet: "Parakeet",
  whisper: "Whisper",
  captions: "The video's own captions",
};
const ENCODERS: Record<string, string> = {
  fast: "Fast (the Mac's video chip)",
  best: "Best (slower, sharper)",
};
const AUTO_RENDER: Record<string, string> = {
  top5: "The top 5",
  top10: "The top 10",
  all: "All of them",
  none: "None",
};
const THEMES = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "system", label: "System" },
] as const;

type Problems = Record<string, string>;
// ``clears`` names a problem inside the field (like "posting.windows") that the save fixes once it lands.
type Save = (field: string, value: unknown, clears?: string) => void;

// Each change goes to the engine on its own, one at a time and in order. Until it lands, the page shows it
// from its own copy: the cache tells its readers a moment later, by when a switch would have snapped back.
// A refused change keeps its sentence at its field until a save of that field goes through.
function without(problems: Problems, names: string[]): Problems {
  return Object.fromEntries(
    Object.entries(problems).filter(([name]) => !names.includes(name)),
  );
}

function useSettings() {
  const client = useQueryClient();
  const toast = useToast();
  const settings = useQuery(settingsQuery);
  const [problems, setProblems] = useState<Problems>({});
  const [unsaved, setUnsaved] = useState<Record<string, unknown>>({});
  const queue = useRef<{
    saving: boolean;
    next: { field: string; value: unknown; clears: string[] }[];
  }>({
    saving: false,
    next: [],
  });

  const send = useCallback(async () => {
    const q = queue.current;
    if (q.saving) return;
    const change = q.next.shift();
    if (!change) return;
    q.saving = true;
    const settled = () => {
      if (!q.next.some((c) => c.field === change.field))
        setUnsaved(({ [change.field]: _sent, ...rest }) => rest);
    };
    try {
      const body = await api<SettingsBody>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ [change.field]: change.value }),
      });
      client.setQueryData(settingsQuery.queryKey, body);
      settled();
      setProblems((p) => without(p, [change.field, ...change.clears]));
      toast({ id: "settings-saved", text: "Saved", kind: "ok", ms: 2000 });
      if (change.field === "delete_sources_after_days")
        void client.invalidateQueries({ queryKey: key.storage });
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setProblems((p) => ({
          ...p,
          [err.field ?? change.field]: sentence(err),
        }));
      } else toast({ text: sentence(err), kind: "bad" });
      settled();
      void client.invalidateQueries({ queryKey: key.settings });
    } finally {
      q.saving = false;
    }
    void send();
  }, [client, toast]);

  const save = useCallback<Save>(
    (field, value, clears) => {
      // A fetch on its way would land with the old value, so it is called off.
      void client.cancelQueries({ queryKey: key.settings });
      setUnsaved((u) => ({ ...u, [field]: value }));
      const q = queue.current;
      const waiting = q.next.find((c) => c.field === field);
      if (waiting) {
        waiting.value = value;
        if (clears && !waiting.clears.includes(clears))
          waiting.clears.push(clears);
      } else q.next.push({ field, value, clears: clears ? [clears] : [] });
      void send();
    },
    [client, send],
  );
  const body = settings.data && {
    ...settings.data,
    values: { ...settings.data.values, ...unsaved } as SettingsValues,
  };
  return { settings, body, save, problems };
}

export function Settings() {
  const { settings, body, save, problems } = useSettings();
  const theme = body?.values.theme;
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <h1 className="text-xl font-semibold">Settings</h1>
      {settings.isPending && (
        <p className="text-sm text-muted">Loading the settings.</p>
      )}
      {settings.isError && !body && (
        <p className="text-sm text-bad">{sentence(settings.error)}</p>
      )}
      {body && (
        <>
          <AiSection body={body} save={save} problems={problems} />
          <SpeechSection values={body.values} save={save} problems={problems} />
          <DownloadsSection
            values={body.values}
            save={save}
            problems={problems}
          />
          <RenderingSection values={body.values} save={save} />
          <LookSection values={body.values} save={save} problems={problems} />
          <TodaySection values={body.values} save={save} problems={problems} />
          <PostingSection
            values={body.values}
            save={save}
            problems={problems}
          />
          <WatchSection values={body.values} save={save} problems={problems} />
          <MarketplacesSection
            values={body.values}
            save={save}
            problems={problems}
          />
          <Section title="Brief sources">
            <SourceList />
          </Section>
          <NichesSection values={body.values} save={save} problems={problems} />
          <StorageSection
            values={body.values}
            save={save}
            problems={problems}
          />
          <Section title="Appearance">
            <Choice
              legend="Theme"
              name="theme"
              value={body.values.theme}
              options={[...THEMES]}
              onChange={(t) => save("theme", t)}
            />
          </Section>
          <DoctorSection />
        </>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <h2 id={id} className="text-base font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

type FieldProps = {
  label: string;
  value: string;
  onSave: (draft: string) => void;
  error?: string;
  hint?: string;
  warning?: string | null;
  placeholder?: string;
  multiline?: boolean;
  inputMode?: "numeric" | "decimal";
  tidy?: (draft: string) => string; // what the draft becomes as it is saved
};

// A text setting: saved on Enter or on leaving the field, and only when it changed. A refused draft stays
// to fix rather than being put back.
function SettingText({
  label,
  value,
  onSave,
  error,
  hint,
  warning,
  placeholder,
  multiline,
  inputMode,
  tidy,
}: FieldProps) {
  const [draft, setDraft] = useState(value);
  const sent = useRef<string | null>(null);
  useEffect(() => {
    if (error) return; // the refused draft stays, and isn't sent again until it changes
    sent.current = null;
    setDraft(value);
  }, [value, error]);
  function commit() {
    const next = tidy ? tidy(draft) : draft;
    if (next !== draft) setDraft(next);
    if (next === value || next === sent.current) return;
    sent.current = next;
    onSave(next);
  }
  return (
    <TextField
      label={label}
      value={draft}
      onChange={setDraft}
      onBlur={commit}
      onEnter={commit}
      error={error}
      hint={hint}
      warning={warning}
      placeholder={placeholder}
      multiline={multiline}
      inputMode={inputMode}
    />
  );
}

function SettingNumber(props: {
  label: string;
  field: string;
  values: SettingsValues;
  save: Save;
  problems: Problems;
  hint?: string;
}) {
  const { label, field, values, save, problems, hint } = props;
  return (
    <SettingText
      label={label}
      hint={hint}
      inputMode="decimal"
      value={String(values[field] ?? "")}
      error={problems[field]}
      onSave={(draft) => save(field, numberOrText(draft))}
    />
  );
}

function SettingSelect(props: {
  label: string;
  field: string;
  values: SettingsValues;
  save: Save;
  options: Record<string, string>;
  hint?: string;
}) {
  const { label, field, values, save, options, hint } = props;
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select
        id={id}
        value={String(values[field])}
        onChange={(e) => save(field, e.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className={SELECT}
      >
        {Object.entries(options).map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-faint">
          {hint}
        </p>
      )}
    </div>
  );
}

function SettingToggle(props: {
  label: string;
  field: string;
  values: SettingsValues;
  save: Save;
  hint?: string;
}) {
  const { label, field, values, save, hint } = props;
  return (
    <Toggle
      label={label}
      hint={hint}
      checked={values[field] === true}
      onChange={(on) => save(field, on)}
    />
  );
}

type SectionProps = { values: SettingsValues; save: Save; problems: Problems };

function AiSection({
  body,
  save,
  problems,
}: {
  body: SettingsBody;
  save: Save;
  problems: Problems;
}) {
  const { values, models, economy_models } = body;
  const provider = values.llm_provider;
  const defaults = models[provider];
  const economy = economy_models[provider];
  return (
    <Section title="AI">
      <SettingSelect
        label="AI provider"
        field="llm_provider"
        values={values}
        save={save}
        options={PROVIDERS}
      />
      {provider === "openai_compat" && (
        <SettingText
          label="Local AI server's address"
          hint="Its OpenAI-style address, like http://127.0.0.1:11434/v1."
          value={String(values.openai_compat_base_url ?? "")}
          error={problems.openai_compat_base_url}
          onSave={(draft) => save("openai_compat_base_url", draft.trim())}
        />
      )}
      {defaults && (
        <div className="grid gap-3 sm:grid-cols-2">
          {body.passes.map((pass) => (
            <SettingText
              key={`${provider}-${pass}`}
              label={PASSES[pass]?.label ?? `${pass} model`}
              hint={PASSES[pass]?.hint}
              placeholder={
                (values.llm_economy && economy) ||
                defaults[pass] ||
                defaults["*"]
              }
              value={values.llm_models[provider]?.[pass] ?? ""}
              error={undefined}
              onSave={(draft) =>
                save(
                  "llm_models",
                  modelsWith(values.llm_models, provider, pass, draft),
                )
              }
            />
          ))}
        </div>
      )}
      {problems.llm_models && <FieldError text={problems.llm_models} />}
      {economy && (
        <SettingToggle
          label="Economy"
          hint={`Uses ${economy} for every pass you haven't set, for less usage.`}
          field="llm_economy"
          values={values}
          save={save}
        />
      )}
      <SettingNumber
        label="AI calls at once"
        hint="From 1 to 10."
        field="llm_parallel"
        values={values}
        save={save}
        problems={problems}
      />
      <SettingSelect
        label="On a usage limit"
        field="on_llm_limit"
        values={values}
        save={save}
        options={ON_LIMIT}
      />
    </Section>
  );
}

function SpeechSection({ values, save, problems }: SectionProps) {
  return (
    <Section title="Speech">
      <SettingSelect
        label="Speech engine"
        hint="Auto uses Parakeet for English and Whisper for other languages."
        field="asr_engine"
        values={values}
        save={save}
        options={ENGINES}
      />
      <SettingText
        label="Speech language"
        hint="Auto, or a code like en or es when the detected language is wrong."
        value={String(values.asr_language ?? "")}
        error={problems.asr_language}
        onSave={(draft) => save("asr_language", draft.trim())}
      />
    </Section>
  );
}

function DownloadsSection({ values, save, problems }: SectionProps) {
  return (
    <Section title="Downloads">
      <SettingSelect
        label="Download quality"
        field="download_quality"
        values={values}
        save={save}
        options={QUALITIES}
      />
      <SettingNumber
        label="Whole-video limit"
        hint="In GB. A bigger video downloads only the parts its clips need."
        field="full_download_max_gb"
        values={values}
        save={save}
        problems={problems}
      />
      <SettingNumber
        label="Longest video"
        hint="In hours."
        field="max_source_hours"
        values={values}
        save={save}
        problems={problems}
      />
      <SettingToggle
        label="Comments"
        hint="Reads the top comments for the moments viewers point to."
        field="comments"
        values={values}
        save={save}
      />
      <SettingToggle
        label="Live chat"
        hint="Reads a stream's chat replay for the moments it spikes."
        field="live_chat"
        values={values}
        save={save}
      />
      <SettingToggle
        label="Allow other sites"
        hint="Links from sites other than YouTube, when you have the right to use them."
        field="allow_other_sites"
        values={values}
        save={save}
      />
      <SettingText
        label="Your channels"
        hint="One per line, like @yourname. Videos from these count as your own."
        multiline
        value={channelLines(values.own_channels)}
        error={problems.own_channels}
        tidy={(draft) => channelLines(parseChannels(draft))}
        onSave={(draft) => save("own_channels", parseChannels(draft))}
      />
    </Section>
  );
}

function RenderingSection({ values, save }: Omit<SectionProps, "problems">) {
  return (
    <Section title="Rendering">
      <SettingSelect
        label="Encoder"
        field="encoder"
        values={values}
        save={save}
        options={ENCODERS}
      />
      <SettingToggle
        label="Cap at 30 fps"
        hint="Smaller files from 60 fps sources."
        field="fps_cap_30"
        values={values}
        save={save}
      />
      <SettingSelect
        label="Render on their own"
        hint="How many of a project's best clips render as soon as they're found."
        field="auto_render"
        values={values}
        save={save}
        options={AUTO_RENDER}
      />
    </Section>
  );
}

function LookSection({ values, save, problems }: SectionProps) {
  const id = useId();
  const own = PRESET_ACCENTS[values.caption_preset as CaptionPreset];
  const accent = values.accent_color;
  return (
    <Section title="Default look">
      <p className="text-sm text-muted">
        New clips start with this look. Each clip can change it in the editor.
      </p>
      <SettingSelect
        label="Caption style"
        field="caption_preset"
        values={values}
        save={save}
        options={CAPTION_PRESETS}
      />
      <div className="flex flex-wrap items-center gap-3">
        <input
          id={id}
          type="color"
          value={(accent ?? own ?? "#ffd60a").toLowerCase()}
          onChange={(e) => save("accent_color", e.target.value.toLowerCase())}
          className="h-8 w-12 cursor-pointer rounded-control border border-line bg-panel-2 p-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        />
        <label htmlFor={id} className="flex flex-col text-sm">
          Accent colour
          <span className="text-xs text-faint">
            {accent
              ? "Your own, for the word being said."
              : "The style's own, for the word being said."}
          </span>
        </label>
        {accent && (
          <Button variant="ghost" onClick={() => save("accent_color", null)}>
            Use the style's own
          </Button>
        )}
      </div>
      {problems.accent_color && <FieldError text={problems.accent_color} />}
      <div className="flex flex-col">
        {SWITCHES.map((s) => (
          <SettingToggle
            key={s.key}
            label={s.label}
            hint={s.hint}
            field={s.key}
            values={values}
            save={save}
          />
        ))}
        <SettingToggle
          label="Emoji"
          hint="An emoji over the words it fits, now and then."
          field="emoji"
          values={values}
          save={save}
        />
        <SettingToggle
          label="Sound effects"
          hint="A whoosh, pop or hit on the big moments."
          field="sfx"
          values={values}
          save={save}
        />
        <SettingToggle
          label="Start clips with a cold open when a strong line is found"
          hint="Plays the clip's strongest 2 to 4 seconds first, then a whoosh into the start."
          field="cold_open"
          values={values}
          save={save}
        />
      </div>
      <SettingSelect
        label="Gameplay under the speaker"
        hint="Uses the gameplay videos in your Library."
        field="gameplay_mode"
        values={values}
        save={save}
        options={GAMEPLAY_MODES}
      />
    </Section>
  );
}

// Storage

const KINDS: { key: keyof StorageInfo["usage"]; label: string }[] = [
  { key: "sources", label: "Source videos" },
  { key: "work", label: "Work files" },
  { key: "renders", label: "Renders" },
  { key: "library", label: "Library" },
  { key: "uploads", label: "Dropped files" },
  { key: "assets", label: "Models and fonts" },
  { key: "logs", label: "Logs" },
];

const MAX_WINDOWS = 8;

// When and how often each platform gets a post (SPEC 10.4). Each field sends the whole set with its one
// change; Pause posting is left to the switch at the top of the window, which the engine keeps as it is.
// Today (SPEC 14.10, 18, 10.11): each field sends the others as they are.
function TodaySection({ values, save, problems }: SectionProps) {
  const today = values.today;
  const [ratesProblem, setRatesProblem] = useState<string | null>(null);
  function put<K extends keyof TodayValues>(
    name: K,
    value: TodayValues[K] | number | string,
  ) {
    save("today", { ...today, [name]: value }, `today.${name}`);
  }
  function saveRates(draft: string) {
    const typed = parseRates(draft);
    if ("error" in typed) return setRatesProblem(typed.error);
    setRatesProblem(null);
    put("rates", typed.rates);
  }
  return (
    <Section title="Today">
      <p className="text-sm text-muted">
        Each morning Today puts your 15 minutes in order, highest earning first,
        and one notification says it is ready.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <SettingText
          label="Daily notification at"
          hint="In the posting time zone, like 07:30."
          placeholder="07:30"
          value={today.notify_at}
          error={problems["today.notify_at"]}
          onSave={(draft) => put("notify_at", draft.trim())}
        />
        <SettingText
          label="Starting guess for new accounts (views at 7 days)"
          hint="Used until an account has 5 posts with a 7-day count."
          inputMode="numeric"
          value={String(today.starting_views)}
          error={problems["today.starting_views"]}
          onSave={(draft) => put("starting_views", numberOrText(draft))}
        />
      </div>
      <Toggle
        label="Show the path to your first money"
        hint="Six levels of steps from your first account to your first payout, each one on the list as it opens."
        checked={today.guide}
        onChange={(on) => put("guide", on)}
      />
      <Toggle
        label="Send the daily notification"
        hint="Opens Today when you click it."
        checked={today.daily_notification}
        onChange={(on) => put("daily_notification", on)}
      />
      <Toggle
        label="Notify me about every post, draft and project"
        hint="A project ready, drafts made, and a post that needs you, failed or went up. When it's off, they show on Today's list instead."
        checked={today.event_notifications}
        onChange={(on) => put("event_notifications", on)}
      />
      <SettingText
        label="Exchange rates to US$"
        multiline
        hint={`${RATE_LINE} Without one, an amount in another currency is compared as if it were US$.`}
        placeholder="AUD 0.65"
        value={rateLines(today.rates)}
        error={ratesProblem ?? problems["today.rates"]}
        tidy={(draft) => {
          const typed = parseRates(draft);
          return "rates" in typed ? rateLines(typed.rates) : draft;
        }}
        onSave={saveRates}
      />
    </Section>
  );
}

function PostingSection({ values, save, problems }: SectionProps) {
  const posting = values.posting;
  const [windowsProblem, setWindowsProblem] = useState<string | null>(null);
  function put<K extends keyof PostingValues>(
    name: K,
    value: PostingValues[K] | string,
  ) {
    save("posting", { ...posting, [name]: value }, `posting.${name}`);
  }
  function text(label: string, name: keyof PostingValues, hint?: string) {
    return (
      <SettingText
        label={label}
        hint={hint}
        value={String(posting[name] ?? "")}
        error={problems[`posting.${name}`]}
        onSave={(draft) => put(name, draft.trim())}
      />
    );
  }
  function number(
    label: string,
    name: keyof PostingValues,
    hint: string,
    warning?: string | null,
  ) {
    return (
      <SettingText
        label={label}
        hint={hint}
        warning={warning}
        inputMode="decimal"
        value={String(posting[name])}
        error={problems[`posting.${name}`]}
        onSave={(draft) => put(name, numberOrText(draft))}
      />
    );
  }
  function saveWindows(draft: string) {
    const typed = parseWindows(draft);
    if ("error" in typed) return setWindowsProblem(typed.error);
    if (typed.windows === null)
      return setWindowsProblem(`Add at least one window. ${WINDOW_LINE}`);
    if (typed.windows.length > MAX_WINDOWS)
      return setWindowsProblem(`Keep it to ${MAX_WINDOWS} windows or fewer.`);
    setWindowsProblem(null);
    put("windows", typed.windows);
  }
  return (
    <Section title="Posting">
      <p className="text-sm text-muted">
        Smart times fall inside these windows. Pause posting is the switch at
        the top of the window.
      </p>
      <SettingText
        label="Posting windows"
        multiline
        hint={WINDOW_LINE}
        value={windowLines(posting.windows)}
        error={windowsProblem ?? problems["posting.windows"]}
        tidy={(draft) => {
          const typed = parseWindows(draft);
          return "windows" in typed && typed.windows
            ? windowLines(typed.windows)
            : draft;
        }}
        onSave={saveWindows}
      />
      {text(
        "Time zone",
        "timezone",
        "The windows and the Queue's times are in this zone.",
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {number(
          "TikTok posts a day",
          "tiktok_daily_cap",
          "For each account, from 1 to 10.",
          capWarning("tiktok", posting.tiktok_daily_cap),
        )}
        {number(
          "Instagram posts a day",
          "instagram_daily_cap",
          "For each account, from 1 to 10.",
          capWarning("instagram", posting.instagram_daily_cap),
        )}
        {number(
          "Hours between TikTok posts",
          "tiktok_spacing_h",
          "On one account, from 0 to 24.",
        )}
        {number(
          "Hours between Instagram posts",
          "instagram_spacing_h",
          "On one account, from 0 to 24.",
        )}
        {number(
          "Minutes between platforms",
          "platform_gap_min",
          "When one clip goes to both, from 0 to 240.",
        )}
        {number(
          "Random shift in minutes",
          "jitter_min",
          "Moves each smart time a little, from 0 to 60.",
        )}
        {number(
          "Minutes a post waits for you",
          "away_min",
          "When a site needs you, from 1 to 120.",
        )}
      </div>
      <Toggle
        label="TikTok's own schedule"
        hint="A TikTok post that needs your click goes up in your 15 minutes with TikTok's schedule set to its slot, for you to press Schedule. Off, it waits for its time."
        checked={posting.tiktok_schedule}
        onChange={(on) => put("tiktok_schedule", on)}
      />
      {text(
        "Graph API version",
        "graph_version",
        "Meta's API for Instagram, like v25.0.",
      )}
      <SettingText
        label="Public address"
        hint="Only for Instagram's second way in: the https address in front of this Mac. Leave it empty otherwise."
        placeholder="https://"
        value={posting.public_base_url ?? ""}
        error={problems["posting.public_base_url"]}
        onSave={(draft) => put("public_base_url", draft.trim() || null)}
      />
      <Toggle
        label="Alerts on Telegram"
        hint="A post that needs you or failed, sent through OpenClaw to the chat below."
        checked={posting.telegram_alerts}
        onChange={(on) => put("telegram_alerts", on)}
      />
      <SettingText
        label="Telegram chat"
        hint="A chat ID or an @name."
        value={posting.telegram_target ?? ""}
        error={problems["posting.telegram_target"]}
        onSave={(draft) => put("telegram_target", draft.trim() || null)}
      />
      <BackgroundService />
    </Section>
  );
}

// The background service (SPEC 10.7): launchd starts the engine when you log in, so the daily run and the
// scheduled posts go on with the window closed. Remove asks first.
function BackgroundService() {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const label = useId();
  const status = useQuery({
    queryKey: key.service,
    queryFn: ({ signal }) => api<ServiceStatus>("/api/service", { signal }),
  });
  const change = useMutation({
    mutationFn: (action: "install" | "uninstall") =>
      api<ServiceStatus>(`/api/service/${action}`, { method: "POST" }),
    onSuccess: (done) => {
      client.setQueryData(key.service, done);
      if (done.message) toast({ text: done.message, kind: "ok" });
    },
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.doctor }),
  });

  async function remove() {
    const yes = await ask({
      title: "Remove the background service?",
      description:
        "ClipForge then runs from when you open it until you log out or restart the Mac, so after a restart" +
        " the daily run and scheduled posts wait until you open it again.",
      action: "Remove",
      danger: true,
    });
    if (yes) change.mutate("uninstall");
  }

  const now = status.data;
  return (
    <div
      role="group"
      aria-labelledby={label}
      className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3"
    >
      <div className="flex min-w-0 flex-col gap-0.5 text-sm">
        <span id={label} className="font-medium">
          Background service
        </span>
        {status.isError && !now ? (
          <span className="text-bad">{sentence(status.error)}</span>
        ) : (
          <span className="text-muted">{now ? now.sentence : "Checking."}</span>
        )}
      </div>
      {now &&
        (now.installed ? (
          <Button
            variant="ghost"
            onClick={() => void remove()}
            disabled={change.isPending}
          >
            Remove
          </Button>
        ) : (
          <Button
            onClick={() => change.mutate("install")}
            disabled={change.isPending}
          >
            Install
          </Button>
        ))}
    </div>
  );
}

// Clipping channels (SPEC 10.9): the lengths every clipping channel skips. Each field sends the other as it is.
function WatchSection({ values, save, problems }: SectionProps) {
  const watch = values.watch;
  function number(label: string, name: keyof WatchValues, hint: string) {
    return (
      <SettingText
        label={label}
        hint={hint}
        inputMode="decimal"
        value={String(watch[name])}
        error={problems[`watch.${name}`]}
        onSave={(draft) =>
          save(
            "watch",
            { ...watch, [name]: numberOrText(draft) },
            `watch.${name}`,
          )
        }
      />
    );
  }
  return (
    <Section title="Clipping channels">
      <p className="text-sm text-muted">
        {skipLine(watch)} Shorts are always skipped.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {number(
          "Shortest video to clip",
          "min_minutes",
          "In minutes, from 0.5 to 60. Shorter uploads are skipped.",
        )}
        {number(
          "Longest video to clip",
          "max_hours",
          "In hours, from 0.5 to 12. Longer uploads are skipped.",
        )}
      </div>
    </Section>
  );
}

// A row a marketplace: what its Withdraw button opens in your browser and the least it pays out. Each
// change saves the whole list; a refused one says why at the field that sent it.
function MarketplacesSection({ values, save, problems }: SectionProps) {
  const campaigns = useQuery(campaignsQuery);
  const [last, setLast] = useState<{
    name: string;
    field: keyof Marketplace;
  } | null>(null);
  const saved = values.marketplaces;
  // Oldest first, so a row is spelt as the totals spell it
  const rows = marketplaceRows(
    [...(campaigns.data?.campaigns ?? [])].reverse().map((c) => c.marketplace),
    saved,
  );
  const savedAs = (name: string) =>
    Object.entries(saved).find(
      ([n]) => n.trim().toLowerCase() === name.trim().toLowerCase(),
    )?.[1] ?? {
      withdraw_url: null,
      min_payout: null,
    };

  function change(name: string, field: keyof Marketplace, value: unknown) {
    setLast({ name, field });
    const others = Object.fromEntries(
      Object.entries(saved).filter(
        ([n]) => n.trim().toLowerCase() !== name.trim().toLowerCase(),
      ),
    );
    save("marketplaces", {
      ...others,
      [name]: { ...savedAs(name), [field]: value },
    });
  }
  const errorAt = (name: string, field: keyof Marketplace) =>
    last?.name === name && last.field === field
      ? problems.marketplaces
      : undefined;

  return (
    <Section title="Marketplaces">
      <p className="text-sm text-muted">
        ClipForge opens a Withdraw link in your browser and never signs in to a
        marketplace or keeps payment details.
      </p>
      {campaigns.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">Loading the campaigns.</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">
          Marketplaces show here once a campaign names one.
        </p>
      ) : (
        rows.map((name) => {
          const row = savedAs(name);
          return (
            <fieldset key={name} className="flex flex-col gap-3">
              <legend className="mb-2 text-sm font-medium" dir="auto">
                {name}
              </legend>
              <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
                <SettingText
                  label="Withdraw link"
                  value={row.withdraw_url ?? ""}
                  placeholder="https://"
                  hint="Its payout page, opened in your browser when a payout is ready."
                  error={errorAt(name, "withdraw_url")}
                  onSave={(draft) =>
                    change(name, "withdraw_url", draft.trim() || null)
                  }
                />
                <SettingText
                  label="Minimum payout"
                  inputMode="decimal"
                  value={row.min_payout === null ? "" : String(row.min_payout)}
                  hint="A payout counts as ready once pending reaches this."
                  error={errorAt(name, "min_payout")}
                  onSave={(draft) =>
                    change(
                      name,
                      "min_payout",
                      draft.trim() ? numberOrText(draft) : null,
                    )
                  }
                />
              </div>
            </fieldset>
          );
        })
      )}
    </Section>
  );
}

// The niches briefs and the advice are sorted into (10.11), one a line, as the posting windows are written.
function NichesSection({ values, save, problems }: SectionProps) {
  const briefs = values.briefs;
  const [problem, setProblem] = useState<string | null>(null);
  function saveNiches(draft: string) {
    const typed = parseNiches(draft);
    if ("error" in typed) return setProblem(typed.error);
    setProblem(null);
    save("briefs", { ...briefs, niches: typed.niches }, "briefs.niches");
  }
  return (
    <Section title="Niches">
      <SettingText
        label="Niches and their tags"
        multiline
        hint="One a line, its language in brackets and its tags after the colon. A brief goes in the niche its tags match, and an account tagged with one covers it."
        placeholder="Gaming (en): gaming, games"
        value={nicheLines(briefs.niches)}
        error={problem ?? problems["briefs.niches"] ?? problems.briefs}
        tidy={(draft) => {
          const typed = parseNiches(draft);
          return "niches" in typed ? nicheLines(typed.niches) : draft;
        }}
        onSave={saveNiches}
      />
    </Section>
  );
}

function StorageSection({ values, save, problems }: SectionProps) {
  const client = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const storage = useQuery({
    queryKey: key.storage,
    queryFn: ({ signal }) => api<StorageInfo>("/api/storage", { signal }),
  });
  const clean = useMutation({
    mutationFn: () => api<Cleaned>("/api/storage/clean", { method: "POST" }),
    onSuccess: (done) =>
      toast({ text: `Freed ${formatBytes(done.freed_bytes)}`, kind: "ok" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.storage }),
  });

  const info = storage.data;
  const cleanable = info?.cleanable;
  const days =
    info?.delete_sources_after_days ?? Number(values.delete_sources_after_days);

  async function cleanUp() {
    if (!cleanable) return;
    const projects =
      cleanable.projects === 1 ? "1 project" : `${cleanable.projects} projects`;
    const yes = await ask({
      title: "Clean up now?",
      description:
        `This deletes the downloaded video and audio of ${projects} older than ${days} days, freeing ` +
        `${formatBytes(cleanable.bytes)}. Their clips and renders stay.`,
      action: "Clean up",
      danger: true,
    });
    if (yes) clean.mutate();
  }

  let usage;
  if (storage.isError && !info)
    usage = <p className="text-sm text-bad">{sentence(storage.error)}</p>;
  else if (!info) usage = <p className="text-sm text-muted">Measuring.</p>;
  else
    usage = (
      <>
        <p className="text-sm">
          {formatBytes(info.usage.free_bytes)} free of{" "}
          {formatBytes(info.usage.total_bytes)}
        </p>
        <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
          {KINDS.map(({ key: kind, label }) => (
            <div key={kind} className="contents">
              <dt className="text-muted">{label}</dt>
              <dd className="text-right tabular-nums">
                {formatBytes(info.usage[kind])}
              </dd>
            </div>
          ))}
        </dl>
      </>
    );
  const none = !cleanable || cleanable.projects === 0;
  return (
    <Section title="Storage">
      {usage}
      <SettingNumber
        label="Delete source videos after"
        hint="In days. Clips and renders stay; 0 keeps source videos for good."
        field="delete_sources_after_days"
        values={values}
        save={save}
        problems={problems}
      />
      <SettingNumber
        label="Free space to keep"
        hint="In GB. Downloads wait when the disk gets this full."
        field="min_free_gb"
        values={values}
        save={save}
        problems={problems}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={() => void cleanUp()}
          disabled={none || clean.isPending}
        >
          Clean up now
        </Button>
        {cleanable && (
          <span className="text-sm text-muted">
            {none
              ? "Nothing to clean up yet."
              : `${formatBytes(cleanable.bytes)} of source videos can go now.`}
          </span>
        )}
      </div>
    </Section>
  );
}

// Doctor

function DoctorSection() {
  const doctor = useQuery(doctorQuery);
  return (
    <Section title="Doctor">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">
          Checks the tools ClipForge needs on this Mac.
        </p>
        <Button
          variant="ghost"
          onClick={() => void doctor.refetch()}
          disabled={doctor.isFetching}
        >
          <RefreshCw
            aria-hidden
            size={14}
            className={doctor.isFetching ? "animate-spin" : undefined}
          />
          Run again
        </Button>
      </div>
      {doctor.isPending && <p className="text-sm text-muted">Checking.</p>}
      {doctor.isError && !doctor.data && (
        <p className="text-sm text-bad">{sentence(doctor.error)}</p>
      )}
      {doctor.data && (
        <ul className="flex flex-col divide-y divide-line">
          {byNeed(doctor.data.checks).map((check) => (
            <CheckRow key={check.name} check={check} />
          ))}
        </ul>
      )}
    </Section>
  );
}

function CheckRow({ check }: { check: Check }) {
  const client = useQueryClient();
  const update = useMutation({
    mutationFn: () =>
      api<UpdateResult>("/api/doctor/update-ytdlp", { method: "POST" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.doctor }),
  });
  let status = { text: "OK", tone: "text-ok" };
  if (!check.ok)
    status = check.required
      ? { text: "Needs fixing", tone: "text-bad" }
      : { text: "Optional", tone: "text-warn" };
  const result = update.isError
    ? { ok: false, message: sentence(update.error) }
    : update.data;
  return (
    <li className="flex flex-col gap-1 py-2.5 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium">{checkName(check.name)}</span>
        <span className={`text-xs font-medium ${status.tone}`}>
          {status.text}
        </span>
      </div>
      {check.detail && (
        <p className="text-muted break-words" dir="auto">
          {check.detail}
        </p>
      )}
      {!check.ok && check.fix && <p dir="auto">{check.fix}</p>}
      {check.name === "yt-dlp" && (
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Button onClick={() => update.mutate()} disabled={update.isPending}>
            {update.isPending ? "Updating yt-dlp" : "Update yt-dlp"}
          </Button>
          {result && !update.isPending && (
            <span role="status" className={result.ok ? "text-ok" : "text-bad"}>
              {result.message}
            </span>
          )}
        </div>
      )}
    </li>
  );
}
