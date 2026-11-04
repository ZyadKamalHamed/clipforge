// The Create page (SPEC 14.2): what setup is still needed, a drop zone with a field for links, Paste and
// Choose file, the options the next project starts with (saved as they change, for drops anywhere too), and
// the six newest projects. Today shows it too, titled Today, while there are no clipping channels yet.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, RefreshCw, Upload } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";

import { api, key, sentence } from "../api.ts";
import { Button } from "../components/Button.tsx";
import { Choice } from "../components/Choice.tsx";
import { ProjectCard } from "../components/ProjectCard.tsx";
import { useToast } from "../components/Toasts.tsx";
import { useDropZone, useStart } from "../components/WindowDrop.tsx";
import { checkName, needsFixing } from "../lib/checks.ts";
import { VIDEO_ACCEPT } from "../lib/drop.ts";
import {
  CAPTION_PRESETS,
  CLIP_COUNTS,
  CONTENT_TYPES,
  LAYOUTS,
  LENGTHS,
  PROMPT_MAX,
  QUALITIES,
  optionsSummary,
} from "../lib/options.ts";
import { doctorQuery, settingsQuery } from "../state.ts";
import type {
  CaptionPreset,
  Project,
  ProjectOptions,
  Quality,
  SettingsBody,
} from "../types.ts";

const PASTE_REFUSED = "Paste with Cmd+V instead.";
const NOTHING_COPIED =
  "There's nothing on the clipboard. Copy a YouTube or Drive link first.";

const FIELD =
  "rounded-control border border-line bg-bg px-3 py-2 text-sm text-fg placeholder:text-faint " +
  "hover:border-faint focus-visible:border-accent";

export function Create({
  title = "Create",
  action,
}: {
  title?: string;
  action?: ReactNode;
}) {
  const dragging = useDropZone();
  const start = useStart();
  const toast = useToast();
  const settings = useQuery(settingsQuery);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  async function go(links: string) {
    if (!links.trim() || busy) return;
    setBusy(true);
    try {
      await start.links(links);
    } finally {
      setBusy(false);
    }
  }

  async function paste() {
    let copied: string;
    try {
      copied = await navigator.clipboard.readText();
    } catch {
      toast({ text: PASTE_REFUSED, kind: "warn" });
      return;
    }
    if (copied.trim()) await go(copied);
    else toast({ text: NOTHING_COPIED, kind: "warn" });
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8">
      {action ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">{title}</h1>
          {action}
        </div>
      ) : (
        <h1 className="sr-only">{title}</h1>
      )}
      <SetupNotice />
      <section
        aria-label="Start a project"
        className={
          "flex flex-col gap-5 rounded-card border-2 border-dashed p-8 transition-colors " +
          (dragging ? "border-accent bg-accent/10" : "border-line bg-panel")
        }
      >
        <div className="flex flex-col items-center gap-2 text-center">
          <Upload
            aria-hidden
            size={28}
            className={dragging ? "text-accent" : "text-muted"}
          />
          <p className="text-lg font-semibold">
            {dragging
              ? "Drop to start a project"
              : "Drop a YouTube or Drive link, or a video file"}
          </p>
          <p className="text-sm text-muted">
            {dragging
              ? "Let go anywhere in the window."
              : "Or paste links below, one per line, or pick a file."}
          </p>
        </div>
        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-start"
          onSubmit={(e) => {
            e.preventDefault();
            void go(text);
          }}
        >
          <textarea
            aria-label="Link or file path"
            rows={1}
            value={text}
            spellCheck={false}
            placeholder="https://www.youtube.com/watch?v=..."
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter starts and Shift+Enter adds a line, so links pasted one per line keep their lines.
              if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing)
                return;
              e.preventDefault();
              void go(text);
            }}
            className={`${FIELD} min-h-10 flex-1 resize-none field-sizing-content max-h-40`}
          />
          <div className="flex shrink-0 gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !text.trim()}
            >
              Start
            </Button>
            <Button disabled={busy} onClick={() => void paste()}>
              Paste
            </Button>
            <Button onClick={() => picker.current?.click()}>Choose file</Button>
            <input
              ref={picker}
              type="file"
              multiple
              accept={VIDEO_ACCEPT}
              tabIndex={-1}
              className="hidden"
              onChange={(e) => {
                const files = [...(e.target.files ?? [])];
                e.target.value = ""; // so choosing the same file again still counts
                if (files.length) void start.files(files);
              }}
            />
          </div>
        </form>
        {settings.data && <Options body={settings.data} />}
      </section>
      <Recent />
    </div>
  );
}

// Saves the options as they change: into the cache first, so a drop straight after uses them, then to the
// engine one save at a time, so an older save never lands after a newer one.
function useSaveOptions() {
  const client = useQueryClient();
  const toast = useToast();
  const queue = useRef<{ saving: boolean; next: ProjectOptions | null }>({
    saving: false,
    next: null,
  });

  const send = useCallback(async () => {
    const q = queue.current;
    if (q.saving || !q.next) return;
    const options = q.next;
    q.next = null;
    q.saving = true;
    try {
      const body = await api<SettingsBody>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ create_options: options }),
      });
      if (!q.next) client.setQueryData(settingsQuery.queryKey, body);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      if (!q.next) void client.invalidateQueries({ queryKey: key.settings });
    } finally {
      q.saving = false;
    }
    void send();
  }, [client, toast]);

  const save = useCallback(
    (options: ProjectOptions) => {
      // A fetch already on its way would put the old options back over the new ones when it lands, so it
      // is called off first (which puts back what the cache had, at once).
      void client.cancelQueries({ queryKey: key.settings });
      const body = client.getQueryData(settingsQuery.queryKey);
      if (body) {
        client.setQueryData(settingsQuery.queryKey, {
          ...body,
          values: { ...body.values, create_options: options },
        });
      }
      queue.current.next = options;
      void send();
    },
    [client, send],
  );
  const saving = useCallback(
    () => queue.current.saving || queue.current.next !== null,
    [],
  );
  return { save, saving };
}

type Change = (change: Partial<ProjectOptions>) => void;

function Options({ body }: { body: SettingsBody }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const { save, saving } = useSaveOptions();
  const saved = body.values.create_options;
  // The panel shows its own copy, changed at once; the cache follows a moment later.
  const [options, setOptions] = useState(saved);
  const latest = useRef(saved);
  const summary = optionsSummary(options);

  // What the engine kept, once no save of ours is on its way, so a save that failed shows what was kept.
  useEffect(() => {
    if (saving()) return;
    latest.current = saved;
    setOptions(saved);
  }, [saved, saving]);

  const change = useCallback<Change>(
    (patch) => {
      const next = { ...latest.current, ...patch };
      latest.current = next;
      setOptions(next);
      save(next);
    },
    [save],
  );
  const credit =
    options.credit === null ? "default" : options.credit ? "on" : "off";

  return (
    <div className="flex flex-col gap-4 border-t border-line pt-4">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panel}
          onClick={() => setOpen(!open)}
          className="inline-flex shrink-0 items-center gap-1 rounded-control text-sm font-medium text-fg"
        >
          <ChevronRight
            aria-hidden
            size={16}
            className={
              open ? "rotate-90 transition-transform" : "transition-transform"
            }
          />
          Options
        </button>
        {!open && summary && (
          <p dir="auto" className="min-w-0 truncate text-sm text-muted">
            {summary}
          </p>
        )}
      </div>
      <div id={panel} hidden={!open} className="grid gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-1.5 md:col-span-2">
          <Choice
            legend="Post as it is"
            name="as_is"
            value={options.as_is ? "on" : "off"}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "On" },
            ]}
            onChange={(next) => change({ as_is: next === "on" })}
          />
          <p className="text-xs text-faint">
            On: each video is one clip, uncut, with no captions or hook title,
            for briefs that ask you to repost their videos. Up to 10 minutes
            long.
          </p>
        </div>
        {!options.as_is && (
          <>
            <Choice
              legend="Clip length"
              name="length"
              value={options.length}
              options={LENGTHS.map((l) => ({
                value: l.value,
                label: l.label,
                hint: l.range,
              }))}
              onChange={(length) => change({ length })}
            />
            <Choice
              legend="Content type"
              name="content_type"
              value={options.content_type}
              options={CONTENT_TYPES}
              onChange={(content_type) => change({ content_type })}
            />
            <Labelled label="Number of clips">
              {(id) => (
                <select
                  id={id}
                  value={options.clip_count ?? ""}
                  onChange={(e) =>
                    change({
                      clip_count: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                  className={FIELD}
                >
                  <option value="">Auto</option>
                  {CLIP_COUNTS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              )}
            </Labelled>
            <Labelled label="Caption style">
              {(id) => (
                <select
                  id={id}
                  value={options.caption_preset ?? ""}
                  onChange={(e) =>
                    change({
                      caption_preset: (e.target.value ||
                        null) as CaptionPreset | null,
                    })
                  }
                  className={FIELD}
                >
                  <option value="">
                    Default ({CAPTION_PRESETS[body.values.caption_preset]})
                  </option>
                  {Object.entries(CAPTION_PRESETS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              )}
            </Labelled>
            <Labelled label="Layout">
              {(id) => (
                <select
                  id={id}
                  value={options.layout}
                  onChange={(e) =>
                    change({
                      layout: e.target.value as ProjectOptions["layout"],
                    })
                  }
                  className={FIELD}
                >
                  {Object.entries(LAYOUTS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              )}
            </Labelled>
          </>
        )}
        <Labelled label="Quality">
          {(id) => (
            <select
              id={id}
              value={options.quality ?? ""}
              onChange={(e) =>
                change({ quality: (e.target.value || null) as Quality | null })
              }
              className={FIELD}
            >
              <option value="">
                Default ({QUALITIES[body.values.download_quality]})
              </option>
              {Object.entries(QUALITIES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          )}
        </Labelled>
        <div className="flex flex-col gap-1.5">
          <Choice
            legend="Credit"
            name="credit"
            value={credit}
            options={[
              { value: "default", label: "Default" },
              { value: "on", label: "On" },
              { value: "off", label: "Off" },
            ]}
            onChange={(next) =>
              change({ credit: next === "default" ? null : next === "on" })
            }
          />
          <p className="text-xs text-faint">
            Default credits the original creator unless the video is your own.
          </p>
        </div>
        {!options.as_is && (
          <About
            saved={options.prompt ?? ""}
            change={change}
            basic={body.values.llm_provider === "heuristic"}
          />
        )}
      </div>
    </div>
  );
}

function Labelled({
  label,
  children,
}: {
  label: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
    </div>
  );
}

// The "about" text saves once typing stops, or when the field is left. The field keeps what was typed:
// the engine trims what it saves, and that copy never comes back into the field mid-word. Basic picks
// (No AI) don't read it, and the field says so.
function About({
  saved,
  change,
  basic,
}: {
  saved: string;
  change: Change;
  basic: boolean;
}) {
  const [text, setText] = useState(saved);
  const typed = useRef(saved);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (timer.current === null) return;
    clearTimeout(timer.current);
    timer.current = null;
    change({ prompt: typed.current.trim() || null });
  }, [change]);

  useEffect(() => flush, [flush]); // leaving the page mid-word still saves it

  return (
    <div className="flex flex-col gap-1.5 md:col-span-2">
      <Labelled label="What should clips be about?">
        {(id) => (
          <textarea
            id={id}
            rows={2}
            value={text}
            maxLength={PROMPT_MAX}
            placeholder="Optional. For example: the money advice, or the funniest moments."
            onChange={(e) => {
              setText(e.target.value);
              typed.current = e.target.value;
              if (timer.current !== null) clearTimeout(timer.current);
              timer.current = setTimeout(flush, 400);
            }}
            onBlur={flush}
            className={`${FIELD} resize-none field-sizing-content min-h-16 max-h-40`}
          />
        )}
      </Labelled>
      <div className="flex items-start justify-between gap-3">
        {basic ? (
          <p className="text-xs text-muted">
            Basic picks don't read this. Choose an AI provider in{" "}
            <Link
              to="/settings"
              className="text-fg underline underline-offset-2"
            >
              Settings
            </Link>{" "}
            for clips about a topic.
          </p>
        ) : (
          <span />
        )}
        <p className="shrink-0 text-xs text-faint tabular-nums">
          {text.length}/{PROMPT_MAX}
        </p>
      </div>
    </div>
  );
}

// The checks ClipForge can't work without that fail, shown where a project starts, so a missing tool shows
// up before a run rather than as weaker clips after it. Optional ones wait in Settings.
function SetupNotice() {
  const heading = useId();
  const doctor = useQuery(doctorQuery);
  const failing = needsFixing(doctor.data?.checks ?? []);
  if (failing.length === 0) return null;
  return (
    <section
      aria-labelledby={heading}
      className="flex flex-col gap-2 rounded-card border border-bad/50 bg-panel p-4 text-sm"
    >
      {/* The Doctor's answer is kept 5 minutes, so the notice can ask again itself after a fix in Terminal. */}
      <div className="flex items-center justify-between gap-3">
        <h2 id={heading} className="font-semibold text-bad">
          Setup needed
        </h2>
        <div className="flex items-center gap-3">
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
            Check again
          </Button>
          <Link
            to="/settings"
            className="rounded-sm text-fg underline underline-offset-2 hover:decoration-2"
          >
            Open Settings
          </Link>
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {failing.map((check) => (
          <li key={check.name} className="flex flex-col gap-0.5">
            <p>
              <span className="font-medium">{checkName(check.name)}</span>
              {check.detail && (
                <span className="text-muted break-words" dir="auto">
                  {" "}
                  ({check.detail})
                </span>
              )}
            </p>
            {check.fix && <p dir="auto">{check.fix}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Recent() {
  const heading = useId();
  const recent = useQuery({
    queryKey: [...key.projects, { limit: 6 }],
    queryFn: ({ signal }) =>
      api<{ projects: Project[] }>("/api/projects?limit=6", { signal }),
  });
  if (recent.isError && !recent.data)
    return <p className="text-sm text-muted">{sentence(recent.error)}</p>;
  const projects = recent.data?.projects ?? [];
  if (projects.length === 0) return null;
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id={heading} className="text-base font-semibold">
          Recent projects
        </h2>
        <Link
          to="/projects"
          className="rounded-sm text-sm text-muted hover:text-fg"
        >
          See all
        </Link>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4">
        {projects.map((project) => (
          <ProjectCard key={project.id} project={project} level={3} />
        ))}
      </div>
    </section>
  );
}
