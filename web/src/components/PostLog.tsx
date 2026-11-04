// A post's log (SPEC 14.6): each step its route took, to the second in the posting time zone with what it
// noted, and the screenshots it kept along the way. What the steps say came from the sites, so it is only
// ever text.

import { useQuery } from "@tanstack/react-query";

import { api, sentence } from "../api.ts";
import { logTime } from "../lib/queue.ts";
import type { PostLog as Log, PostLogStep, QueuedPost } from "../types.ts";
import { Dialog } from "./Dialog.tsx";

const SKIPPED = new Set(["step", "at", "path"]); // said already, or a file on this Mac

function noted(step: PostLogStep): string[] {
  return Object.entries(step)
    .filter(([name, value]) => !SKIPPED.has(name) && value !== null && value !== undefined && value !== "")
    .map(([name, value]) => `${name}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
}

// "/media/.../01-login_wall.png" is screenshot 1, of the login_wall step.
function shotName(url: string, n: number): string {
  const file = decodeURIComponent(url.split("?")[0].split("/").pop() ?? "");
  const step = /^\d{2}-(.+)\.png$/.exec(file)?.[1];
  return step ? `Screenshot ${n}: ${step}` : `Screenshot ${n}`;
}

export function PostLog({ post, title, onClose }: { post: QueuedPost; title: string; onClose: () => void }) {
  const log = useQuery({
    queryKey: ["post-log", post.id, post.updated_at],
    queryFn: ({ signal }) => api<Log>(`/api/posts/${post.id}/log`, { signal }),
  });
  const empty = log.data && log.data.steps.length === 0 && log.data.screenshots.length === 0;
  return (
    <Dialog
      title={`${title}: the log`}
      open
      wide
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {log.isPending && <p className="text-sm text-muted">Loading the log.</p>}
      {log.isError && <p className="text-sm text-bad">{sentence(log.error)}</p>}
      {empty && <p className="text-sm text-muted">Nothing logged yet.</p>}
      {log.data && log.data.steps.length > 0 && (
        <ol aria-label="Steps" className="flex flex-col gap-1.5">
          {log.data.steps.map((step, i) => (
            <li key={i} className="flex flex-wrap gap-x-3 gap-y-0.5 text-sm">
              <span className="text-muted tabular-nums">{typeof step.at === "number" ? logTime(step.at) : ""}</span>
              <span className="font-medium">{String(step.step)}</span>
              {noted(step).map((line) => (
                <span key={line} dir="auto" className="min-w-0 break-words text-muted">
                  {line}
                </span>
              ))}
            </li>
          ))}
        </ol>
      )}
      {log.data && log.data.screenshots.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {log.data.screenshots.map((url, i) => (
            <img key={url} src={url} alt={shotName(url, i + 1)} className="w-full rounded-control border border-line" />
          ))}
        </div>
      )}
    </Dialog>
  );
}
