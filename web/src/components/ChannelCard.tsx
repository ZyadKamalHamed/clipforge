// One clipping channel on the Channels page (SPEC 14.9 and 10.9): the YouTube channels it watches with
// each one's newest upload, the accounts its drafts go to, what its last check found and when the next
// runs, the campaign it clips for and why that has ended once it has (10.10), and Pause watching or Resume
// watching, Check now, Add source, History, Edit and Delete. Nothing here posts: its clips wait on Today as
// drafts until you approve them.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { History as HistoryIcon, Pause, Pencil, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { api, ApiError, key, sentence } from "../api.ts";
import { accountName } from "../lib/accounts.ts";
import { checkNowLabel, deleteLine, nextCheckLabel, timeLabel } from "../lib/channels.ts";
import { countLabel } from "../lib/drafts.ts";
import { formatAgo } from "../lib/format.ts";
import type { Account, ClipChannel, WatchedSourceInfo } from "../types.ts";
import { Avatar } from "./AccountCard.tsx";
import { AddSource } from "./AddSource.tsx";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { ChannelDialog } from "./ChannelDialog.tsx";
import { CheckHistory } from "./CheckHistory.tsx";
import { useConfirm } from "./Confirm.tsx";
import { useToast } from "./Toasts.tsx";

type Open = "add" | "history" | "edit" | null;

type Props = { channel: ClipChannel; accounts: Account[] };

export function ChannelCard({ channel, accounts }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const opensId = useId();
  const [open, setOpen] = useState<Open>(null);
  const path = `/api/clip-channels/${encodeURIComponent(channel.id)}`;
  const now = Date.now() / 1000;
  const byId = new Map(accounts.map((a) => [a.id, a]));
  const linked = channel.account_ids.map((id) => byId.get(id)).filter((a): a is Account => a !== undefined);
  const opens = checkNowLabel(channel.check_now_after, now);

  function refreshed(drafts = false) {
    void client.invalidateQueries({ queryKey: key.channels });
    if (drafts) void client.invalidateQueries({ queryKey: key.drafts });
  }

  const pause = useMutation({
    mutationFn: (paused: boolean) =>
      api<{ channel: ClipChannel }>(path, { method: "PATCH", body: JSON.stringify({ paused }) }),
    onSuccess: ({ channel: changed }) =>
      toast(
        changed.paused
          ? { text: `${changed.name} paused. It checks nothing until you resume it.`, kind: "info" }
          : { text: `${changed.name} resumed. ${nextCheckLabel(changed, Date.now() / 1000)}`, kind: "ok" },
      ),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: channel.name }),
    onSettled: () => refreshed(),
  });

  const check = useMutation({
    mutationFn: () => api<unknown>(`${path}/check`, { method: "POST" }),
    onSuccess: () => toast({ text: `Checking ${channel.name} now.`, kind: "info" }),
    onError: (err) =>
      toast({ text: sentence(err), kind: err instanceof ApiError && err.status === 429 ? "info" : "bad", subject: channel.name }),
    onSettled: () => refreshed(),
  });

  const remove = useMutation({
    mutationFn: () => api<void>(path, { method: "DELETE" }),
    onSuccess: () => toast({ text: `${channel.name} deleted`, kind: "ok" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: channel.name }),
    onSettled: () => refreshed(true),
  });

  const unwatch = useMutation({
    mutationFn: (source: WatchedSourceInfo) =>
      api<void>(`${path}/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" }),
    onSuccess: (_, source) => toast({ text: `${channel.name} no longer watches ${source.title || "that channel"}`, kind: "ok" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: channel.name }),
    onSettled: () => refreshed(),
  });

  async function deleteIt() {
    const yes = await ask({
      title: `Delete ${channel.name}?`,
      description: `It stops watching and its history goes. ${deleteLine(channel.drafts)}`,
      action: "Delete",
      danger: true,
    });
    if (yes) remove.mutate();
  }

  async function unwatchIt(source: WatchedSourceInfo) {
    const title = source.title || "this YouTube channel";
    const yes = await ask({
      title: `Stop watching ${title}?`,
      description: `${channel.name} stops checking it. The drafts and projects it already made stay.`,
      action: "Stop watching",
      danger: true,
    });
    if (yes) unwatch.mutate(source);
  }

  return (
    <article aria-label={channel.name} className="flex flex-col gap-4 rounded-card border border-line bg-panel p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold" dir="auto">
          {channel.name}
        </h2>
        {channel.paused && <Badge tone="warn">Paused</Badge>}
        {channel.drafts > 0 && (
          <Link to="/" className="text-xs text-fg underline underline-offset-2">
            {`${countLabel(channel.drafts)} on Today`}
          </Link>
        )}
      </div>

      {channel.campaign && (
        <div className="flex flex-col gap-1">
          <p className="text-muted">
            For{" "}
            <Link
              to={`/campaigns/${encodeURIComponent(channel.campaign.id)}`}
              className="text-fg underline underline-offset-2"
              dir="auto"
            >
              {channel.campaign.name}
            </Link>
          </p>
          {channel.campaign.ended && (
            <p className="text-warn">
              {channel.campaign.ended} It won't check again until you link it to another campaign or unlink it.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted">Watches</h3>
          {channel.sources.length === 0 ? (
            <p className="text-muted">No YouTube channels yet. Add one to start.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {channel.sources.map((source) => (
                <Source key={source.id} source={source} now={now} onRemove={() => void unwatchIt(source)} />
              ))}
            </ul>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted">Drafts for</h3>
          {linked.length === 0 ? (
            <p className="text-warn">No accounts, so it makes no drafts. Pick some in Edit.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {linked.map((account) => (
                <li key={account.id} className="flex items-center gap-1.5">
                  <Avatar account={account} name={accountName(account)} size="small" />
                  <span dir="auto">{accountName(account)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-0.5">
        <p>{channel.last_line ?? "Not checked yet."}</p>
        <p className="text-muted">
          {nextCheckLabel(channel, now)} It checks every day at {timeLabel(channel.check_time)}.
        </p>
        {opens && (
          <p id={opensId} className="text-muted">
            {opens}
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Button
          onClick={() => check.mutate()}
          disabled={opens !== null || check.isPending || channel.sources.length === 0}
          aria-describedby={opens ? opensId : undefined}
        >
          <RefreshCw aria-hidden size={14} />
          Check now
        </Button>
        <Button variant="ghost" onClick={() => pause.mutate(!channel.paused)} disabled={pause.isPending}>
          {channel.paused ? <Play aria-hidden size={14} /> : <Pause aria-hidden size={14} />}
          {channel.paused ? "Resume watching" : "Pause watching"}
        </Button>
        <Button variant="ghost" onClick={() => setOpen("add")}>
          <Plus aria-hidden size={14} />
          Add source
        </Button>
        <Button variant="ghost" onClick={() => setOpen("history")}>
          <HistoryIcon aria-hidden size={14} />
          History
        </Button>
        <Button variant="ghost" onClick={() => setOpen("edit")}>
          <Pencil aria-hidden size={14} />
          Edit
        </Button>
        <Button variant="ghost" onClick={() => void deleteIt()} disabled={remove.isPending}>
          <Trash2 aria-hidden size={14} />
          Delete
        </Button>
      </div>

      {open === "add" && <AddSource channel={channel} onClose={() => setOpen(null)} />}
      {open === "history" && <CheckHistory channel={channel} onClose={() => setOpen(null)} />}
      {open === "edit" && <ChannelDialog channel={channel} onClose={() => setOpen(null)} />}
    </article>
  );
}

// A watched YouTube channel: its letter, its name, its newest upload and when, and why a check couldn't
// read it, if one couldn't.
function Source({ source, now, onRemove }: { source: WatchedSourceInfo; now: number; onRemove: () => void }) {
  const title = source.title || "A YouTube channel";
  return (
    <li className="flex items-start gap-2">
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-panel-2 text-xs font-medium text-muted">
        {source.letter}
      </span>
      <div className="min-w-0 flex-1">
        <a href={source.url} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2" dir="auto">
          {title}
        </a>
        {source.newest_title && (
          <p className="truncate text-xs text-muted">
            Newest: <span dir="auto">{source.newest_title}</span>
            {source.newest_at !== null && `, ${formatAgo(source.newest_at, now)}`}
          </p>
        )}
        {source.error && <p className="text-xs text-bad">{source.error}</p>}
      </div>
      <button
        type="button"
        aria-label={`Stop watching ${title}`}
        onClick={onRemove}
        className="-m-1 rounded-control p-1 text-muted hover:bg-panel-2 hover:text-fg"
      >
        <X aria-hidden size={14} />
      </button>
    </li>
  );
}
