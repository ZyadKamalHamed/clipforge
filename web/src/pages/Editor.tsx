// The clip editor (SPEC 14.4): the 9:16 player with its keys, loop and safe zone, and the clip's tabs. Cut,
// Text and Look changes wait together for one render (Re-render or Cmd+Enter), and the version playing
// keeps playing until the new one is ready; post copy saves as each field is left. Nothing posts from here.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RefreshCw,
  RotateCcw,
  SearchX,
  Send,
} from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link, useBlocker, useParams } from "react-router";

import { ApiError, api, key, sentence } from "../api.ts";
import { Badge } from "../components/Badge.tsx";
import { Button, buttonClass } from "../components/Button.tsx";
import { CopyTab } from "../components/CopyTab.tsx";
import { Dialog, DialogFooter } from "../components/Dialog.tsx";
import { Empty } from "../components/Empty.tsx";
import { FieldError } from "../components/Field.tsx";
import { LookTab, SoundTab } from "../components/LookTab.tsx";
import { PostDialog } from "../components/PostDialog.tsx";
import { Player, playPause, seekTo } from "../components/Player.tsx";
import { Tabs } from "../components/Tabs.tsx";
import { TextTab } from "../components/TextTab.tsx";
import { useToast } from "../components/Toasts.tsx";
import { inRange, Transcript } from "../components/Transcript.tsx";
import { canRender, clipStatus, clipTitle, TIERS } from "../lib/clips.ts";
import {
  changeCount,
  changesLabel,
  type Editing,
  type EditorTab,
  type Edits,
  fieldTab,
  keepPending,
  keptPending,
  lengthLabel,
  look,
  type Pending,
  playingWord,
  type Range,
  renderBody,
  resetCut,
  same,
  titleWord,
  toggleStrike,
  withEdit,
  withRange,
} from "../lib/editor.ts";
import { KEYS, matchKey } from "../lib/keys.ts";
import { live, settingsQuery } from "../state.ts";
import type { ClipDetail, Project, TranscriptWindow } from "../types.ts";

export const LEAVE = "Leave without rendering?";

// SPEC 9's nine criteria, each scored 0 to 10.
const CRITERIA: Record<string, string> = {
  C1: "Hook",
  C2: "Stands alone",
  C3: "Payoff",
  C4: "Emotion or novelty",
  C5: "Shareability",
  C6: "Audience fit",
  C7: "Search clarity",
  C8: "Production usability",
  C9: "Transformation potential",
};

// The edits whose value is the clip's look as the engine works it out: the settings, then the project's
// choices, then the clip's own edits.
const LOOKED = new Set([
  "preset",
  "accent",
  "layout",
  "punch_ins",
  "pause_trim",
  "filler_removal",
  "auto_pace",
  "emoji",
  "sfx",
  "encoder",
  "credit_line",
  "progress_bar",
  "hook_whole_clip",
  "cold_open",
  "gameplay_mode",
  "fps_cap_30",
]);

const enc = encodeURIComponent;
const transcriptKey = (id: string) => [...key.clip(id), "transcript"];

export function Editor() {
  const { id = "" } = useParams();
  const detail = useQuery({
    queryKey: key.clip(id),
    queryFn: ({ signal }) =>
      api<ClipDetail>(`/api/clips/${enc(id)}`, { signal }),
  });
  const pid = detail.data?.project_id ?? "";
  const project = useQuery({
    queryKey: key.project(pid),
    queryFn: ({ signal }) =>
      api<Project>(`/api/projects/${enc(pid)}`, { signal }),
    enabled: pid !== "",
  });
  const settings = useQuery(settingsQuery);
  if (detail.data && project.data && settings.data) {
    return (
      <EditorView
        key={id}
        detail={detail.data}
        project={project.data}
        settings={settings.data.values}
      />
    );
  }
  const failed = detail.error ?? project.error ?? settings.error;
  if (!failed) return null;
  if (failed instanceof ApiError && failed.status === 404) {
    return (
      <Empty
        icon={SearchX}
        level={1}
        title="This clip isn't here."
        action={
          <Link to="/projects" className={buttonClass("primary")}>
            Go to Projects
          </Link>
        }
      >
        It may have been deleted with its project, or the link is out of date.
      </Empty>
    );
  }
  return <p className="text-sm text-muted">{sentence(failed)}</p>;
}

type ViewProps = {
  detail: ClipDetail;
  project: Project;
  settings: Record<string, unknown>;
};

function EditorView({ detail, project, settings }: ViewProps) {
  const id = detail.id;
  const client = useQueryClient();
  const toast = useToast();
  const video = useRef<HTMLVideoElement>(null);
  const transcript = useQuery({
    queryKey: transcriptKey(id),
    queryFn: ({ signal }) =>
      api<TranscriptWindow>(`/api/clips/${enc(id)}/transcript`, { signal }),
    enabled: !detail.as_is, // nothing listened to a video posted as it is
  });
  const words = transcript.data?.words ?? [];
  const [pending, setPending] = useState<Pending>(() => keptPending(id));
  useEffect(() => keepPending(id, pending), [id, pending]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<EditorTab>("cut");
  const [selection, setSelection] = useState<{
    anchor: number;
    span: [number, number];
  } | null>(null);
  const [playing, setPlaying] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null); // a version picked to watch; null for the current one
  const [shuffling, setShuffling] = useState(false);
  const [posting, setPosting] = useState(false);
  const busy = useRef(false);
  useSyncExternalStore(live.subscribe, live.version);

  // Each edit as it's saved, and as it stands with the changes waiting over it.
  const saved: Edits = detail.edits ?? {};
  const packaging = detail.packaging;
  const lookSaved = look(settings, project.options, saved);
  const lookNow = look(settings, project.options, {
    ...saved,
    ...pending.edits,
  });

  function savedOf(name: string): unknown {
    switch (name) {
      case "hook_title":
        return packaging?.hook_title ?? "";
      case "accent_word":
        return titleWord(packaging?.accent_word, packaging?.hook_title ?? "");
      case "fact_cards":
        return packaging?.fact_cards ?? [];
      case "word_fixes":
        return saved.word_fixes ?? {};
      case "struck":
        return saved.struck ?? [];
      case "gameplay_item":
        return saved.gameplay_item ?? null;
      case "voiceover":
        return saved.voiceover ?? null;
      case "voiceover_at":
        return saved.voiceover_at ?? 0;
      case "accent":
        return String(lookSaved.accent).toLowerCase();
      default:
        return lookSaved[name];
    }
  }

  function value<T>(name: string): T {
    if (name === "accent") return String(lookNow.accent).toLowerCase() as T;
    if (LOOKED.has(name)) return lookNow[name] as T;
    if (name in pending.edits)
      return (pending.edits[name] ?? (name === "struck" ? [] : null)) as T;
    return savedOf(name) as T;
  }

  function change(name: string, next: unknown) {
    setPending((p) => withEdit(p, name, next, savedOf(name)));
    setErrors({});
  }

  const editing: Editing = {
    detail,
    transcript: transcript.data,
    value,
    change,
    error: (field) => errors[field] ?? null,
    errorUnder: (prefix) =>
      Object.entries(errors).find(
        ([field]) => field === prefix || field.startsWith(`${prefix}.`),
      )?.[1] ?? null,
  };

  // The clip's range: where it's saved, where it was before it first moved, and where it stands now.
  const savedRange: Range = [detail.start_s, detail.end_s];
  const moved = Array.isArray(saved.orig_range);
  const origRange = moved ? (saved.orig_range as Range) : savedRange;
  const range: Range =
    pending.range === undefined ? savedRange : (pending.range ?? origRange);
  const struck = value<number[]>("struck");

  function setRange(next: Range) {
    setPending((p) => withRange(p, next, savedRange, moved));
    setErrors({});
  }

  function onWord(i: number, extend: boolean) {
    setSelection((s) =>
      extend && s
        ? {
            anchor: s.anchor,
            span: [Math.min(s.anchor, i), Math.max(s.anchor, i)],
          }
        : { anchor: i, span: [i, i] },
    );
    const w = words.find((x) => x.i === i);
    if (!extend && w?.out != null && video.current)
      seekTo(video.current, w.out);
  }

  // I and O: the start (or end) at the first (or last) word picked, else at the word playing.
  function setEdge(which: "start" | "end") {
    const at = selection ? selection.span[which === "start" ? 0 : 1] : playing;
    const w = words.find((x) => x.i === at);
    if (!w) return;
    if (which === "start" && w.start < range[1]) setRange([w.start, range[1]]);
    if (which === "end" && w.end > range[0]) setRange([range[0], w.end]);
  }

  function strike() {
    if (!selection) return;
    const [lo, hi] = selection.span;
    const picked = words
      .filter((w) => w.i >= lo && w.i <= hi && inRange(w, range))
      .map((w) => w.i);
    if (picked.length) change("struck", toggleStrike(struck, picked));
  }

  function settle(updated: ClipDetail) {
    client.setQueryData(key.clip(id), updated);
    void client.invalidateQueries({ queryKey: key.clips(detail.project_id) });
  }

  async function render() {
    if (busy.current || !canRender(detail)) return;
    busy.current = true;
    const sent = pending;
    try {
      const updated = await api<ClipDetail>(`/api/clips/${enc(id)}/render`, {
        method: "POST",
        body: JSON.stringify(renderBody(sent)),
      });
      settle(updated);
      void client.invalidateQueries({ queryKey: transcriptKey(id) });
      setPending((p) => (same(p, sent) ? { edits: {} } : p)); // a change made while it was sent still waits
      setErrors({});
      toast({ text: "Rendering the clip", kind: "info" });
    } catch (err) {
      const field = err instanceof ApiError ? err.field : null;
      const where = fieldTab(field);
      if (field && where) {
        setErrors({ [field]: (err as ApiError).message });
        setTab(where);
      } else toast({ text: sentence(err), kind: "bad" });
    } finally {
      busy.current = false;
    }
  }

  async function shuffle() {
    setShuffling(true);
    try {
      settle(
        await api<ClipDetail>(`/api/clips/${enc(id)}/gameplay/shuffle`, {
          method: "POST",
        }),
      );
      toast({
        text: "Rendering with another stretch of gameplay",
        kind: "info",
      });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setShuffling(false);
    }
  }

  // The editor's keys, read from the window so they work wherever the focus is, except where a field or
  // a focused control takes the key itself (lib/keys.ts).
  const onKey = useRef<(e: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    onKey.current = (e) => {
      const v = video.current;
      const fps = transcript.data?.fps || 30;
      const step = (by: number) => {
        if (v) seekTo(v, v.currentTime + by);
      };
      let handled = true;
      if (matchKey(e, KEYS.rerender)) void render();
      else if (matchKey(e, KEYS.close) && selection) setSelection(null);
      else if (matchKey(e, KEYS.playPause) && v) playPause(v);
      else if (matchKey(e, KEYS.back)) step(-1);
      else if (matchKey(e, KEYS.forward)) step(1);
      else if (matchKey(e, KEYS.frameBack) || matchKey(e, KEYS.frameForward)) {
        v?.pause();
        step(matchKey(e, KEYS.frameBack) ? -1 / fps : 1 / fps);
      } else if (matchKey(e, KEYS.setIn)) setEdge("start");
      else if (matchKey(e, KEYS.setOut)) setEdge("end");
      else if (matchKey(e, KEYS.cut)) strike();
      else if (matchKey(e, KEYS.post)) openPost();
      else handled = false;
      if (handled) e.preventDefault();
    };
  });
  useEffect(() => {
    const listen = (e: KeyboardEvent) => onKey.current(e);
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, []);

  // Changes not rendered yet hold any move to another page until they're discarded. Discarding clears them
  // here too, so a Back pressed before the editor has gone neither asks again nor finds them still waiting.
  const count = changeCount(pending);
  const discarded = useRef<Pending | null>(null);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      count > 0 &&
      pending !== discarded.current &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  const current = detail.render;
  const shown =
    (picked !== null
      ? detail.renders.find((r) => r.version === picked)
      : null) ?? current;
  const job = live
    .of(id)
    .find((j) => j.ref_id === id && j.status === "running");
  const status = clipStatus(detail);
  const title = clipTitle(detail);

  // A post takes the version rendered, so changes still waiting are rendered or dropped first.
  function openPost() {
    if (count > 0)
      toast({
        text: "Re-render or discard your changes first, so the post has them.",
        kind: "info",
      });
    else if (detail.discarded)
      toast({ text: "Restore this clip first to post it.", kind: "info" });
    else setPosting(true);
  }
  const fileName = detail.rank !== null ? `${detail.rank} ${title}` : title;
  const cutProblem =
    editing.error("range") ?? editing.errorUnder("edits.struck");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="flex flex-col gap-3">
        <Link
          to={`/projects/${enc(detail.project_id)}`}
          className={
            "inline-flex w-fit items-center gap-1 rounded-control text-sm text-muted hover:text-fg " +
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          }
        >
          <ArrowLeft aria-hidden size={14} />
          Back to project
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1 dir="auto" className="text-xl font-semibold break-words">
              {detail.rank !== null && (
                <span className="text-muted">#{detail.rank} </span>
              )}
              {title}
            </h1>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span data-testid="clip-status">
                <Badge tone={status.tone}>{status.text}</Badge>
              </span>
              {job && (
                <span
                  data-testid="render-progress"
                  className="text-xs text-muted tabular-nums"
                >
                  {Math.round(Math.min(1, Math.max(0, job.progress)) * 100)}%
                </span>
              )}
              {count > 0 && (
                <span
                  data-testid="changes-waiting"
                  className="text-xs text-warn"
                >
                  {changesLabel(count)}
                </span>
              )}
              {detail.project.title && (
                <span dir="auto" className="text-xs text-faint">
                  From {detail.project.title}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {current && (
              <select
                aria-label="Version"
                value={shown?.version ?? current.version}
                onChange={(e) => {
                  const version = Number(e.target.value);
                  setPicked(version === current.version ? null : version);
                }}
                className={
                  "rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg " +
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                }
              >
                {[...detail.renders].reverse().map((r) => (
                  <option key={r.version} value={r.version}>
                    {r.version === current.version
                      ? `Version ${r.version} (current)`
                      : `Version ${r.version}`}
                  </option>
                ))}
              </select>
            )}
            {shown?.video && (
              <a
                href={`${shown.video}?download=${enc(fileName)}`}
                download
                className={buttonClass("secondary")}
              >
                <Download aria-hidden size={14} />
                Download
              </a>
            )}
            {canRender(detail) && (
              <Button
                variant="primary"
                aria-keyshortcuts="Meta+Enter"
                onClick={() => void render()}
              >
                <RefreshCw aria-hidden size={14} />
                Re-render
              </Button>
            )}
            <Button aria-keyshortcuts="p" onClick={openPost}>
              <Send aria-hidden size={14} />
              Post
            </Button>
          </div>
        </div>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[auto_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-4">
          <Player
            video={video}
            src={shown?.video ?? null}
            poster={shown?.poster ?? null}
            onTime={(t) => setPlaying(playingWord(words, t))}
          />
        </div>
        {detail.as_is ? (
          <AsIs />
        ) : (
          <Tabs
            label="Clip editor"
            value={tab}
            onChange={setTab}
            tabs={[
              {
                value: "cut",
                label: "Cut",
                content: () => (
                  <div className="flex flex-col gap-4">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="text-sm text-muted">
                        Length{" "}
                        <span
                          data-testid="clip-length"
                          className="font-semibold text-fg tabular-nums"
                        >
                          {lengthLabel(range)}
                        </span>
                      </span>
                      <Button
                        variant="ghost"
                        disabled={same(range, origRange) && struck.length === 0}
                        onClick={() => {
                          setPending((p) =>
                            resetCut(p, saved, savedRange, moved),
                          );
                          setErrors({});
                        }}
                      >
                        <RotateCcw aria-hidden size={14} />
                        Reset cut
                      </Button>
                      <span className="text-xs text-faint">
                        Click a word to go to it. I and O set the start and end
                        there; Delete cuts the words picked.
                      </span>
                    </div>
                    {cutProblem && <FieldError text={cutProblem} />}
                    {transcript.data ? (
                      <Transcript
                        words={words}
                        range={range}
                        struck={new Set(struck)}
                        fixes={value<Record<string, string>>("word_fixes")}
                        selection={selection?.span ?? null}
                        playing={playing}
                        onWord={onWord}
                        onRange={setRange}
                      />
                    ) : (
                      <p className="text-sm text-muted">
                        {transcript.isError
                          ? sentence(transcript.error)
                          : "Loading the transcript..."}
                      </p>
                    )}
                    <Score detail={detail} />
                  </div>
                ),
              },
              {
                value: "text",
                label: "Text",
                content: () => <TextTab editing={editing} range={range} />,
              },
              {
                value: "look",
                label: "Look",
                content: () => (
                  <LookTab
                    editing={editing}
                    onShuffle={() => void shuffle()}
                    shuffling={shuffling}
                  />
                ),
              },
              {
                value: "sound",
                label: "Sound",
                content: () => <SoundTab editing={editing} onSaved={settle} />,
              },
              {
                value: "copy",
                label: "Post copy",
                content: () => <CopyTab detail={detail} onSaved={settle} />,
              },
            ]}
          />
        )}
      </div>
      <Dialog
        title={LEAVE}
        description="The changes waiting for a render will be lost."
        open={blocker.state === "blocked"}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.();
        }}
      >
        <DialogFooter>
          <Button onClick={() => blocker.reset?.()}>Keep editing</Button>
          <Button
            variant="danger"
            onClick={() => {
              discarded.current = pending;
              keepPending(id, { edits: {} }); // now, as the editor may be gone before its effect runs
              setPending({ edits: {} });
              setErrors({});
              blocker.proceed?.();
            }}
          >
            Discard changes
          </Button>
        </DialogFooter>
      </Dialog>
      {posting && (
        <PostDialog clips={[{ id, title }]} onClose={() => setPosting(false)} />
      )}
    </div>
  );
}

// A clip posted as it is is the whole video, untouched, so there is nothing here to change (SPEC 14.2).
function AsIs() {
  return (
    <div
      data-testid="as-is-notice"
      className="flex max-w-prose flex-col gap-2 rounded-card border border-line bg-panel p-4"
    >
      <h2 className="text-sm font-semibold">Posted as it is</h2>
      <p className="text-sm text-muted">
        This clip is the whole video as it is, with no cuts, captions or hook
        title, so there is nothing to edit or render. Write each post's caption
        when you post it. To cut clips from this video instead, add it again
        with Post as it is off in Create's Options.
      </p>
    </div>
  );
}

// Why the AI picked the clip: its score and tier, its reason and flags, and each criterion's score with
// the lines it was judged on.
function Score({ detail }: { detail: ClipDetail }) {
  const tier = detail.tier ? TIERS[detail.tier] : null;
  const quotes = new Map<string, string[]>();
  for (const e of detail.evidence)
    quotes.set(e.criterion, [...(quotes.get(e.criterion) ?? []), e.quote]);
  return (
    <section
      aria-label="Score"
      className="flex flex-col gap-3 rounded-card border border-line p-4"
    >
      <div className="flex items-center gap-3">
        <span className="text-2xl font-semibold tabular-nums">
          {detail.final_score === null
            ? "Not scored"
            : Math.round(detail.final_score)}
        </span>
        {tier && <Badge tone={tier.tone}>{tier.label}</Badge>}
        {detail.final_score !== null && (
          <span className="sr-only">out of 100</span>
        )}
      </div>
      {detail.reason && (
        <p dir="auto" className="text-sm">
          {detail.reason}
        </p>
      )}
      {detail.flags.map((flag) => (
        <p
          key={flag.code}
          dir="auto"
          className={`text-sm ${flag.level === "red" ? "text-bad" : "text-warn"}`}
        >
          {flag.text}
        </p>
      ))}
      <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {Object.entries(CRITERIA).map(([c, label]) =>
          detail.scores[c] === undefined ? null : (
            <li key={c} className="flex flex-col gap-0.5 text-sm">
              <span className="flex justify-between gap-2">
                <span>{label}</span>
                <span className="text-muted tabular-nums">
                  {detail.scores[c]} of 10
                </span>
              </span>
              {quotes.get(c)?.map((quote, k) => (
                // A quote is its place in the list; the same line can back a criterion twice.
                <q key={k} dir="auto" className="text-xs text-faint">
                  {quote}
                </q>
              ))}
            </li>
          ),
        )}
      </ul>
    </section>
  );
}
