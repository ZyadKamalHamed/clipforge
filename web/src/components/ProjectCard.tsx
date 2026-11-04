// One project as the Create and Projects pages show it: its thumbnail, title, where it is and its clips.
// The whole card opens the project; its menu opens it too, finds its clips again or deletes it, asking first
// whenever that deletes something.

import { useQueryClient } from "@tanstack/react-query";
import { Film, MoreHorizontal } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useId, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, key, sentence } from "../api.ts";
import { formatAgo, formatDuration } from "../lib/format.ts";
import { clipsLabel, displayTitle, statusOf } from "../lib/projects.ts";
import type { Project } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { useConfirm } from "./Confirm.tsx";
import { useToast } from "./Toasts.tsx";

const ITEM =
  "flex cursor-default items-center rounded-[6px] px-2 py-1.5 text-sm outline-none select-none " +
  "data-[highlighted]:bg-panel-2";

// What deleting takes with it, said the way the dialog asks it.
function whatGoes(clips: number): string {
  const what = clips === 0 ? "Its video and transcript" : `Its video, transcript and ${clipsLabel(clips)}`;
  return `${what} are removed from this Mac. This can't be undone.`;
}

// What finding clips again takes with it: the rendered clips nobody has edited or posted, which new ones replace.
function whatRerunDeletes(clips: number): string {
  const what =
    clips === 1 ? "Its rendered clip you haven't edited or posted is" : `Its ${clips} rendered clips you haven't edited or posted are`;
  return `${what} deleted, videos and all, and new ones take their place. Clips you've edited or posted stay.`;
}

export function ProjectCard({ project, level = 2 }: { project: Project; level?: 2 | 3 }) {
  const titleId = useId();
  const title = displayTitle(project);
  const status = statusOf(project);
  const Heading = level === 2 ? "h2" : "h3";
  const percent = Math.round(Math.min(1, Math.max(0, project.progress)) * 100);
  const age = formatAgo(project.created_at, Date.now() / 1000);

  return (
    <article
      aria-labelledby={titleId}
      className="relative flex flex-col overflow-hidden rounded-card border border-line bg-panel hover:border-faint"
    >
      <Thumb src={project.thumb} />
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <div className="flex items-start gap-2">
          <Heading id={titleId} dir="auto" className="line-clamp-2 min-w-0 flex-1 text-sm font-semibold break-words">
            {/* Its box covers the whole card, so the card opens the project from anywhere on it. */}
            <Link to={`/projects/${project.id}`} className="rounded-sm after:absolute after:inset-0">
              {title}
            </Link>
          </Heading>
          <Menu project={project} title={title} />
        </div>
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
          {project.channel && (
            <>
              <span dir="auto" className="max-w-full truncate">
                {project.channel}
              </span>
              <span aria-hidden>·</span>
            </>
          )}
          {project.duration_s !== null && (
            <>
              <span className="tabular-nums">{formatDuration(project.duration_s)}</span>
              <span aria-hidden>·</span>
            </>
          )}
          <span>{age}</span>
        </p>
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          <Badge tone={status.tone}>{status.text}</Badge>
          {project.status === "running" ? (
            <div className="flex min-w-24 flex-1 items-center gap-2">
              <div
                role="progressbar"
                aria-label={`${title}: ${status.text}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={percent}
                className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-2"
              >
                <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
              </div>
              <span className="text-xs text-muted tabular-nums">{percent}%</span>
            </div>
          ) : (
            !status.working &&
            (project.clip_count > 0 || project.status === "ready") && (
              <span className="text-xs text-muted">{clipsLabel(project.clip_count)}</span>
            )
          )}
        </div>
        {status.detail && <p className="line-clamp-3 text-xs text-muted">{status.detail}</p>}
      </div>
    </article>
  );
}

// A video's thumbnail, or a film icon when it has none or it won't load.
export function Thumb({ src, className = "aspect-video" }: { src: string | null; className?: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className={`grid place-items-center overflow-hidden bg-panel-2 ${className}`}>
      {src && !broken ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setBroken(true)}
          className="size-full object-cover"
        />
      ) : (
        <Film aria-hidden size={24} className="text-faint" />
      )}
    </div>
  );
}

function Menu({ project, title }: { project: Project; title: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const ask = useConfirm();

  async function again() {
    if (project.rerun_drops > 0) {
      const sure = await ask({
        title: "Find clips again?",
        description: whatRerunDeletes(project.rerun_drops),
        action: "Find clips again",
        danger: true,
      });
      if (!sure) return;
    }
    try {
      const updated = await api<Project>(`/api/projects/${project.id}/rerun`, { method: "POST", body: "{}" });
      client.setQueryData(key.project(project.id), updated);
      void client.invalidateQueries({ queryKey: key.projects });
      toast({ text: "Finding clips again", kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  async function remove() {
    const sure = await ask({
      title: "Delete this project?",
      description: whatGoes(project.clip_count),
      action: "Delete",
      danger: true,
    });
    if (!sure) return;
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      client.removeQueries({ queryKey: key.project(project.id) });
      void client.invalidateQueries({ queryKey: key.projects });
      toast({ text: "Project deleted", kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    // Not modal, so a dialog opened from it gets the pointer and the focus as soon as it shows.
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={`More for ${title}`}
          className="relative z-10 -m-1 shrink-0 rounded-control p-1 text-muted hover:bg-panel-2 hover:text-fg"
        >
          <MoreHorizontal aria-hidden size={16} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          className="z-50 min-w-44 rounded-control border border-line bg-panel p-1 shadow-2xl"
        >
          <DropdownMenu.Item className={ITEM} onSelect={() => navigate(`/projects/${project.id}`)}>
            Open
          </DropdownMenu.Item>
          <DropdownMenu.Item className={ITEM} onSelect={() => void again()}>
            Find clips again
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-line" />
          <DropdownMenu.Item className={`${ITEM} text-bad`} onSelect={() => void remove()}>
            Delete
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
