// History (SPEC 14.9 and 10.9): a clipping channel's last 30 checks, newest first, each with the videos it
// saw, what became of each, and why any was skipped. Titles come from YouTube, so they are only ever text.

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, key, sentence } from "../api.ts";
import type { ClipChannel, HistoryCheck, WatchItemInfo } from "../types.ts";
import { Badge, type Tone } from "./Badge.tsx";
import { Dialog } from "./Dialog.tsx";

const STATUS: Record<WatchItemInfo["status"], { text: string; tone: Tone }> = {
  new: { text: "New", tone: "neutral" },
  skipped: { text: "Skipped", tone: "neutral" },
  waiting: { text: "Waiting", tone: "info" },
  clipping: { text: "Clipping", tone: "info" },
  drafted: { text: "Drafted", tone: "ok" },
  failed: { text: "Failed", tone: "bad" },
};

type Props = { channel: ClipChannel; onClose: () => void };

export function CheckHistory({ channel, onClose }: Props) {
  const history = useQuery({
    queryKey: [...key.channels, channel.id, "history"],
    queryFn: ({ signal }) =>
      api<{ checks: HistoryCheck[] }>(`/api/clip-channels/${encodeURIComponent(channel.id)}/history`, { signal }),
  });

  let body;
  if (history.isPending) body = <p className="text-sm text-muted">Loading the checks.</p>;
  else if (history.isError) body = <p className="text-sm text-bad">{sentence(history.error)}</p>;
  else if (history.data.checks.length === 0) body = <p className="text-sm text-muted">No checks yet.</p>;
  else
    body = history.data.checks.map((check) => (
      <section key={check.id} aria-labelledby={`check-${check.id}`} className="flex flex-col gap-2">
        <h3 id={`check-${check.id}`} className="text-sm font-medium">
          {check.line}
          {check.manual && <span className="font-normal text-muted"> (Check now)</span>}
        </h3>
        {check.error && <p className="text-sm text-bad">{check.error}</p>}
        {check.items.length > 0 && (
          <ul className="flex flex-col gap-1.5">
            {check.items.map((item) => (
              <Item key={item.id} item={item} />
            ))}
          </ul>
        )}
      </section>
    ));

  return (
    <Dialog
      title={`${channel.name} history`}
      open
      wide
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {body}
    </Dialog>
  );
}

function Item({ item }: { item: WatchItemInfo }) {
  const status = STATUS[item.status];
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
      <Badge tone={status.tone}>{status.text}</Badge>
      <a href={item.url} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2" dir="auto">
        {item.title || "Untitled video"}
      </a>
      {item.reason && <span className="text-muted">{item.reason}</span>}
      {item.project_id && (
        <Link to={`/projects/${encodeURIComponent(item.project_id)}`} className="text-muted underline underline-offset-2">
          Open project
        </Link>
      )}
    </li>
  );
}
