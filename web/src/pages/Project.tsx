// The Project page (SPEC 14.3): the video's header with its rights, its steps and what went wrong, then its
// clips as 9:16 cards to pick from, sorted and filtered, with a bar for the ones picked. Post there (or P)
// opens the Post dialog for them, and nothing posts until it is confirmed there.

import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Clapperboard,
  Download,
  FolderX,
  RotateCcw,
  SearchX,
  Send,
  Trash2,
} from "lucide-react";
import { Tooltip } from "radix-ui";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Link, useParams } from "react-router";

import { ApiError, api, key, sentence } from "../api.ts";
import { Badge } from "../components/Badge.tsx";
import { Button, buttonClass } from "../components/Button.tsx";
import { CampaignPicker } from "../components/CampaignPicker.tsx";
import { Choice } from "../components/Choice.tsx";
import { ClipCard } from "../components/ClipCard.tsx";
import { Details } from "../components/Details.tsx";
import { Empty } from "../components/Empty.tsx";
import { type PostClip, PostDialog } from "../components/PostDialog.tsx";
import { Thumb } from "../components/ProjectCard.tsx";
import { RightsBadge } from "../components/RightsBadge.tsx";
import { StepRow } from "../components/StepRow.tsx";
import { useToast } from "../components/Toasts.tsx";
import {
  canRender,
  clipTitle,
  download,
  inTier,
  type Sort,
  sortClips,
  TIER_FILTERS,
  type TierFilter,
} from "../lib/clips.ts";
import { etaSeconds } from "../lib/eta.ts";
import { formatDuration } from "../lib/format.ts";
import { KEYS, matchKey } from "../lib/keys.ts";
import { clipsLabel, displayTitle, statusOf } from "../lib/projects.ts";
import {
  empty,
  extendTo,
  type Selection,
  selectAll,
  selectTier,
  toggle,
} from "../lib/selection.ts";
import { live } from "../state.ts";
import type { Clip, Project as ProjectData } from "../types.ts";

export const NOT_CLEARED =
  "This video isn't cleared for clipping yet. Set its rights when you have permission.";

const SORTS: { value: Sort; label: string }[] = [
  { value: "score", label: "Best first" },
  { value: "time", label: "In video order" },
];

const projectPath = (id: string) => `/api/projects/${encodeURIComponent(id)}`;
const clipPath = (id: string, action: string) =>
  `/api/clips/${encodeURIComponent(id)}/${action}`;

export function Project() {
  const { id = "" } = useParams();
  const query = useQuery({
    queryKey: key.project(id),
    queryFn: ({ signal }) => api<ProjectData>(projectPath(id), { signal }),
  });
  if (query.data) return <ProjectView key={id} project={query.data} />;
  if (!query.isError) return null;
  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <Empty
        icon={FolderX}
        level={1}
        title="This project isn't here."
        action={
          <Link to="/projects" className={buttonClass("primary")}>
            Go to Projects
          </Link>
        }
      >
        It may have been deleted, or the link is out of date.
      </Empty>
    );
  }
  return <p className="text-sm text-muted">{sentence(query.error)}</p>;
}

function ProjectView({ project }: { project: ProjectData }) {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <ProjectHeader project={project} />
      <Gallery project={project} />
    </div>
  );
}

// Draws again every second while ``on``, so a time left counts down between progress events.
function useTick(on: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [on]);
}

function useLiveJobs() {
  useSyncExternalStore(live.subscribe, live.version);
}

function ProjectHeader({ project }: { project: ProjectData }) {
  const client = useQueryClient();
  const toast = useToast();
  const titleId = useId();
  const title = displayTitle(project);
  const status = statusOf(project);
  useLiveJobs();
  const running = live.of(project.id).filter((job) => job.status === "running");
  useTick(running.length > 0);
  const now = Date.now() / 1000;
  const etas = running
    .map((job) => etaSeconds(job.samples, now))
    .filter((s): s is number => s !== null);
  const eta = etas.length ? Math.max(...etas) : null;
  const stopped = project.status === "failed" || project.status === "refused";

  async function retry() {
    try {
      const updated = await api<ProjectData>(
        `${projectPath(project.id)}/retry`,
        { method: "POST" },
      );
      client.setQueryData(key.project(project.id), updated);
      void client.invalidateQueries({ queryKey: key.projects });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-4 rounded-card border border-line bg-panel p-4"
    >
      <div className="flex gap-4 max-[700px]:flex-col">
        <Thumb
          src={project.thumb}
          className="aspect-video w-48 shrink-0 rounded-control"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h1
            id={titleId}
            dir="auto"
            className="text-xl font-semibold break-words"
          >
            {title}
          </h1>
          <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-muted">
            {project.channel && (
              <span dir="auto" className="max-w-full truncate">
                {project.channel}
              </span>
            )}
            {project.channel && project.duration_s !== null && (
              <span aria-hidden>·</span>
            )}
            {project.duration_s !== null && (
              <span className="tabular-nums">
                {formatDuration(project.duration_s)}
              </span>
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={status.tone}>{status.text}</Badge>
            <RightsBadge project={project} />
            <CampaignPicker project={project} />
          </div>
        </div>
      </div>
      {project.rights_basis === "not_cleared" && (
        <p className="rounded-control border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
          {NOT_CLEARED}
        </p>
      )}
      <StepRow steps={project.steps} eta={eta} />
      {stopped ? (
        <div className="flex flex-col gap-2 rounded-control border border-bad/40 bg-bad/10 p-3">
          <p dir="auto" className="text-sm">
            {project.error ??
              "This project stopped. Retry to carry on from where it stopped."}
          </p>
          <Details text={project.error_details ?? "No more details were kept."}>
            <Button variant="primary" onClick={() => void retry()}>
              Retry
            </Button>
          </Details>
        </div>
      ) : project.status === "waiting" ? (
        <div className="flex flex-wrap items-center gap-3 rounded-control border border-warn/40 bg-warn/10 px-3 py-2">
          <p dir="auto" className="min-w-0 flex-1 text-sm">
            {project.message ?? "This project is waiting to carry on."}
          </p>
          <Button onClick={() => void retry()}>Try now</Button>
        </div>
      ) : (
        project.message && (
          <p dir="auto" className="text-sm text-muted">
            {project.message}
          </p>
        )
      )}
    </section>
  );
}

// Asks the engine the same of each clip at once: which it did it for, and the first refusal if any.
async function eachClip(
  ids: string[],
  action: string,
): Promise<{ done: string[]; refused: unknown }> {
  const results = await Promise.allSettled(
    ids.map((id) => api(clipPath(id, action), { method: "POST" })),
  );
  const refused = results.find(
    (r): r is PromiseRejectedResult => r.status === "rejected",
  );
  return {
    done: ids.filter((_, k) => results[k].status === "fulfilled"),
    refused: refused?.reason ?? null,
  };
}

// The browser saves what the link gives, named as the server says.
function save(href: string) {
  const link = document.createElement("a");
  link.href = href;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function Gallery({ project }: { project: ProjectData }) {
  const client = useQueryClient();
  const toast = useToast();
  const headingId = useId();
  const pid = project.id;
  const [sort, setSort] = useState<Sort>("score");
  const [tier, setTier] = useState<TierFilter>("all");
  const [withDiscarded, setWithDiscarded] = useState(false);
  const [sel, setSel] = useState<Selection>(empty);
  const [playing, setPlaying] = useState<string | null>(null);
  const [posting, setPosting] = useState<PostClip[] | null>(null);
  const list = useQuery({
    queryKey: [...key.clips(pid), withDiscarded ? "all" : "kept"],
    queryFn: ({ signal }) =>
      api<{ clips: Clip[] }>(
        `${projectPath(pid)}/clips${withDiscarded ? "?discarded=1" : ""}`,
        { signal },
      ),
    placeholderData: keepPreviousData,
  });
  const all = list.data?.clips;
  const shown = useMemo(
    () =>
      sortClips(
        (all ?? []).filter((c) => inTier(c, tier)),
        sort,
      ),
    [all, tier, sort],
  );
  const order = useMemo(() => shown.map((c) => c.id), [shown]);
  const orderRef = useRef(order);
  const selRef = useRef(sel);
  useEffect(() => {
    orderRef.current = order;
    selRef.current = sel;
  });
  const picked = shown.filter((c) => sel.ids.has(c.id));
  const rerender = picked.filter(canRender); // a clip posted as it is never renders

  // Each clip's running job's progress, from the live jobs, so a card follows it between fetches.
  useLiveJobs();
  const progress = new Map<string, number>();
  for (const job of live.of(pid)) {
    if (job.status === "running" && job.ref_id !== pid)
      progress.set(job.ref_id, job.progress);
  }

  const refresh = useCallback(
    () => client.invalidateQueries({ queryKey: key.clips(pid) }),
    [client, pid],
  );
  const onToggle = useCallback(
    (id: string, extend: boolean) =>
      setSel((s) =>
        extend ? extendTo(s, id, orderRef.current) : toggle(s, id),
      ),
    [],
  );
  const onHover = useCallback(
    (id: string, on: boolean) =>
      setPlaying((p) => (on ? id : p === id ? null : p)),
    [],
  );
  const restore = useCallback(
    async (ids: string[]) => {
      const { refused } = await eachClip(ids, "restore");
      void refresh();
      if (refused) toast({ text: sentence(refused), kind: "bad" });
    },
    [refresh, toast],
  );
  const render = useCallback(
    async (ids: string[], say: boolean) => {
      const { done, refused } = await eachClip(ids, "render");
      void refresh();
      if (say && done.length)
        toast({ text: `Rendering ${clipsLabel(done.length)}`, kind: "ok" });
      if (refused) toast({ text: sentence(refused), kind: "bad" });
    },
    [refresh, toast],
  );
  const onRender = useCallback(
    (id: string) => void render([id], false),
    [render],
  );
  const onRestore = useCallback((id: string) => void restore([id]), [restore]);

  async function discard(ids: string[]) {
    const { done, refused } = await eachClip(ids, "discard");
    setSel(empty());
    await refresh();
    if (done.length) {
      const undo = { label: "Undo", run: () => void restore(done) };
      toast({
        text: `${clipsLabel(done.length)} discarded`,
        kind: "ok",
        action: undo,
      });
    }
    if (refused) toast({ text: sentence(refused), kind: "bad" });
  }

  function downloadPicked() {
    const got = download(picked);
    if ("problem" in got) toast({ text: got.problem, kind: "warn" });
    else save(got.href);
  }

  // The clips picked, in the order they were picked, less any discarded one, which is restored first.
  function openPost() {
    const byId = new Map(shown.map((c) => [c.id, c]));
    const chosen = [...sel.ids].flatMap((id) => byId.get(id) ?? []);
    const kept = chosen.filter((c) => !c.discarded);
    if (kept.length === 0) {
      const text =
        chosen.length > 0
          ? "Restore the discarded clips first to post them."
          : "Select the clips to post first.";
      toast({ text, kind: "info" });
    } else {
      setPosting(kept.map((c) => ({ id: c.id, title: clipTitle(c) })));
    }
  }
  const openPostRef = useRef(openPost);
  useEffect(() => {
    openPostRef.current = openPost;
  });

  // Cmd+A picks every clip shown, Esc lets them go and P posts them.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (matchKey(e, KEYS.post)) {
        e.preventDefault();
        openPostRef.current();
      } else if (matchKey(e, KEYS.selectAll)) {
        e.preventDefault();
        setSel(selectAll(orderRef.current));
      } else if (matchKey(e, KEYS.close) && selRef.current.ids.size > 0) {
        setSel(empty());
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (list.isError && !list.data)
    return <p className="text-sm text-muted">{sentence(list.error)}</p>;
  if (!all) return null;
  const chips = TIER_FILTERS.map((f) => ({
    ...f,
    hint: String(all.filter((c) => inTier(c, f.value)).length),
  }));
  const working = statusOf(project).working;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4">
      <div className="flex items-baseline gap-2">
        <h2 id={headingId} className="text-base font-semibold">
          Clips
        </h2>
        {all.length > 0 && (
          <span className="text-sm text-muted">{clipsLabel(all.length)}</span>
        )}
      </div>
      {all.length === 0 ? (
        <Empty icon={Clapperboard} title="No clips yet.">
          {working
            ? "They show here as soon as ClipForge finds the moments worth clipping."
            : project.status === "ready"
              ? "ClipForge didn't find a moment worth clipping in this video."
              : null}
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="flex flex-wrap items-end gap-4">
              <Choice
                legend="Sort"
                name="sort"
                value={sort}
                options={SORTS}
                onChange={setSort}
              />
              <Choice
                legend="Tier"
                name="tier"
                value={tier}
                options={chips}
                onChange={setTier}
              />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex cursor-pointer items-center gap-2 text-sm text-muted">
                <input
                  type="checkbox"
                  checked={withDiscarded}
                  onChange={(e) => setWithDiscarded(e.target.checked)}
                  className="size-4 accent-accent-strong"
                />
                Show discarded
              </label>
              <Button
                disabled={!shown.some((c) => c.tier === "post")}
                onClick={() => setSel(selectTier(shown, "post"))}
              >
                Select all Post-tier
              </Button>
            </div>
          </div>
          {shown.length === 0 ? (
            <Empty
              icon={SearchX}
              title="No clips in this tier."
              action={<Button onClick={() => setTier("all")}>Show all</Button>}
            />
          ) : (
            <Tooltip.Provider delayDuration={300}>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(11.5rem,1fr))] gap-4">
                {shown.map((clip) => (
                  <ClipCard
                    key={clip.id}
                    clip={clip}
                    selected={sel.ids.has(clip.id)}
                    playing={playing === clip.id}
                    progress={progress.get(clip.id) ?? clip.progress}
                    onToggle={onToggle}
                    onHover={onHover}
                    onRender={onRender}
                    onRestore={onRestore}
                  />
                ))}
              </div>
            </Tooltip.Provider>
          )}
        </>
      )}
      {picked.length > 0 && (
        <div
          role="toolbar"
          aria-label="Selected clips"
          className="sticky bottom-0 z-20 flex flex-wrap items-center gap-2 rounded-card border border-line bg-panel p-2 pl-4 shadow-2xl"
        >
          <span className="mr-auto text-sm font-medium">
            {picked.length} selected
          </span>
          <Button variant="primary" aria-keyshortcuts="p" onClick={openPost}>
            <Send aria-hidden size={16} />
            Post
          </Button>
          <Button onClick={downloadPicked}>
            <Download aria-hidden size={16} />
            Download
          </Button>
          {rerender.length > 0 && (
            <Button
              onClick={() =>
                void render(
                  rerender.map((c) => c.id),
                  true,
                )
              }
            >
              <RotateCcw aria-hidden size={16} />
              Re-render
            </Button>
          )}
          <Button onClick={() => void discard(picked.map((c) => c.id))}>
            <Trash2 aria-hidden size={16} />
            Discard
          </Button>
          <Button variant="ghost" onClick={() => setSel(empty())}>
            Clear
          </Button>
        </div>
      )}
      {posting && (
        <PostDialog clips={posting} onClose={() => setPosting(null)} />
      )}
    </section>
  );
}
