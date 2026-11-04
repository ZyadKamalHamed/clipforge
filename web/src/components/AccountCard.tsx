// An account's card (SPEC 11): its name and picture, how it posts, how it is, today's posts against its cap,
// its last post, and Check, its own way of reconnecting, Settings, Pause and Remove. A job the account has
// going (its Chrome login, its page check) shows on the card, and its buttons wait for it; a login says so
// in place of the account's status and offers its window and Stop instead.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AppWindow, KeyRound, LogIn, Pause, Play, RefreshCw, SlidersHorizontal, Square, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import {
  accountName,
  capText,
  lastPostText,
  loginWord,
  methodText,
  PLATFORMS,
  reconnectFor,
  statusLabel,
} from "../lib/accounts.ts";
import { formatAgo } from "../lib/format.ts";
import type { Account, ActiveJob, Health } from "../types.ts";
import { AccountSettings } from "./AccountSettings.tsx";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { useConfirm } from "./Confirm.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

type Props = {
  account: Account;
  all: Account[];
  job: Pick<ActiveJob, "kind" | "label" | "status"> | null; // the account's job still to finish
  onFacebook: () => void; // a key from Facebook covers its Pages' accounts, so it's pasted in Add account
};

type Checked = { account: Account; health: Health | null; job_id: number | null };

const CHECK_HINT = {
  browser: "Opens the upload page in Chrome and checks everything posting needs is there. Nothing is uploaded.",
  api: "Asks Instagram whether the key still works.",
};

export function AccountCard({ account, all, job, onFacebook }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [editing, setEditing] = useState(false);
  const [rekeying, setRekeying] = useState(false);
  const name = accountName(account);
  const path = `/api/accounts/${encodeURIComponent(account.id)}`;
  const status = statusLabel(account);
  const reconnect = reconnectFor(account);
  const now = Date.now() / 1000;

  function refreshed(jobs = false) {
    void client.invalidateQueries({ queryKey: key.accounts });
    if (jobs) void client.invalidateQueries({ queryKey: key.activeJobs });
  }

  const check = useMutation({
    mutationFn: () => api<Checked>(`${path}/check`, { method: "POST" }),
    onSuccess: ({ health }) => {
      if (health) toast({ text: health.sentence, kind: health.status === "connected" ? "ok" : "warn", subject: name });
    },
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: (done) => refreshed(Boolean(done?.job_id)),
  });

  const login = useMutation({
    mutationFn: () => api<{ account: Account; job_id: number }>(`${path}/login`, { method: "POST" }),
    onSuccess: () =>
      toast({ text: `Log in to ${PLATFORMS[account.platform]} in the Chrome window that opens.`, kind: "info", subject: name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: () => refreshed(true),
  });

  const front = useMutation({
    mutationFn: () => api<undefined>("/api/posting/front", { method: "POST" }),
    onSuccess: () => toast({ text: "Bringing the window to the front.", kind: "info", subject: name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
  });

  const stop = useMutation({
    mutationFn: () => api<undefined>(`${path}/login/stop`, { method: "POST" }),
    onSuccess: () => toast({ text: "Stopped the login. Its Chrome window closes.", kind: "info", subject: name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: () => refreshed(true),
  });

  const pause = useMutation({
    mutationFn: (paused: boolean) =>
      api<{ account: Account }>(path, { method: "PATCH", body: JSON.stringify({ paused }) }),
    onSuccess: ({ account: changed }) =>
      toast(
        changed.paused
          ? { text: `Paused. Nothing posts to ${name} until you resume it.`, kind: "info" }
          : { text: "Resumed. Its posts go at their times again.", kind: "ok", subject: name },
      ),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: () => refreshed(),
  });

  const remove = useMutation({
    mutationFn: () => api<void>(path, { method: "DELETE" }),
    onSuccess: () => toast({ text: "Removed from ClipForge", kind: "ok", subject: name }),
    onError: (err) => toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: () => refreshed(),
  });

  async function removeIt() {
    const where = PLATFORMS[account.platform];
    const kept = account.method === "browser" ? "its Chrome login" : "its key";
    const yes = await ask({
      title: `Remove ${name} from ClipForge?`,
      description:
        `Its posts still to go are cancelled, and ${kept} is deleted from this Mac. ` +
        `The account stays on ${where} with the posts it already made.`,
      action: "Remove",
      danger: true,
    });
    if (yes) remove.mutate();
  }

  function reconnectIt() {
    if (reconnect.kind === "login") login.mutate();
    else if (reconnect.kind === "token") setRekeying(true);
    else onFacebook();
  }

  const busy = job !== null;
  const loggingIn = job?.kind === "account.login";
  const last = account.last_post;
  return (
    <article aria-label={name} className="flex w-full flex-col gap-3 rounded-card border border-line bg-panel p-4 text-sm">
      <div className="flex items-center gap-3">
        <Avatar account={account} name={name} />
        <div className="min-w-0">
          <h3 className="truncate font-medium" dir="auto">
            {name}
          </h3>
          <p className="text-xs text-muted">
            {PLATFORMS[account.platform]} · {methodText(account)}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {loggingIn ? <Badge tone="info">{loginWord(job)}</Badge> : <Badge tone={status.tone}>{status.text}</Badge>}
        {account.paused && <Badge tone="neutral">Paused</Badge>}
        {account.experimental && <Badge tone="neutral">Experimental</Badge>}
      </div>
      {account.handle && account.label && (
        <p className="text-muted" dir="auto">
          {account.label}
        </p>
      )}
      {account.status_note && <p dir="auto">{account.status_note}</p>}
      {job && <p>{job.label}</p>}
      <div className="flex flex-col gap-0.5 text-xs text-muted">
        <p className="tabular-nums">{capText(account)}</p>
        <p className="min-w-0 truncate">
          {lastPostText(account, now)}
          {last?.clip_title && (
            <>
              {" · "}
              {last.url ? (
                <a href={last.url} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2" dir="auto">
                  {last.clip_title}
                </a>
              ) : (
                <span dir="auto">{last.clip_title}</span>
              )}
            </>
          )}
        </p>
        {account.last_checked_at !== null && <p>Checked {formatAgo(account.last_checked_at, now)}</p>}
      </div>
      <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
        {loggingIn ? (
          <>
            {job.status === "running" && (
              <Button onClick={() => front.mutate()} disabled={front.isPending}>
                <AppWindow aria-hidden size={14} />
                Show the login window
              </Button>
            )}
            <Button onClick={() => stop.mutate()} disabled={stop.isPending}>
              <Square aria-hidden size={14} />
              Stop
            </Button>
          </>
        ) : (
          <>
            <Button title={CHECK_HINT[account.method]} onClick={() => check.mutate()} disabled={busy || check.isPending}>
              <RefreshCw aria-hidden size={14} />
              Check
            </Button>
            <Button onClick={reconnectIt} disabled={busy || login.isPending}>
              {reconnect.kind === "login" ? <LogIn aria-hidden size={14} /> : <KeyRound aria-hidden size={14} />}
              {reconnect.label}
            </Button>
          </>
        )}
        <Button variant="ghost" onClick={() => setEditing(true)}>
          <SlidersHorizontal aria-hidden size={14} />
          Settings
        </Button>
        <Button variant="ghost" onClick={() => pause.mutate(!account.paused)} disabled={pause.isPending}>
          {account.paused ? <Play aria-hidden size={14} /> : <Pause aria-hidden size={14} />}
          {account.paused ? "Resume" : "Pause"}
        </Button>
        <Button variant="ghost" onClick={() => void removeIt()} disabled={remove.isPending}>
          <Trash2 aria-hidden size={14} />
          Remove
        </Button>
      </div>
      {editing && <AccountSettings account={account} all={all} onClose={() => setEditing(false)} />}
      {rekeying && <NewKey account={account} name={name} onClose={() => setRekeying(false)} />}
    </article>
  );
}

// The account's picture, else its first letter; small in the Post dialog's list.
export function Avatar({ account, name, size }: { account: Account; name: string; size?: "small" }) {
  const box = size === "small" ? "size-7 text-xs" : "size-10";
  if (account.avatar) return <img src={account.avatar} alt="" className={`${box} shrink-0 rounded-full object-cover`} />;
  const letter = (account.handle || account.label || PLATFORMS[account.platform]).charAt(0).toUpperCase();
  return (
    <span
      aria-hidden
      title={name}
      className={`grid ${box} shrink-0 place-items-center rounded-full bg-panel-2 font-medium text-muted`}
    >
      {letter}
    </span>
  );
}

// A new key for an account linked with Instagram login (Mode B), pasted once and never shown again: the
// field is emptied after every send, refused or not.
function NewKey({ account, name, onClose }: { account: Account; name: string; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [token, setToken] = useState("");
  const [problem, setProblem] = useState<{ token?: string; form?: string }>({});
  const save = useMutation({
    mutationFn: (sent: string) =>
      api<{ account: Account }>(`/api/accounts/${encodeURIComponent(account.id)}/token`, {
        method: "POST",
        body: JSON.stringify({ token: sent }),
      }),
    onSuccess: () => {
      toast({ text: "New key saved", kind: "ok", subject: name });
      onClose();
    },
    onError: (err) => setProblem(err instanceof ApiError && err.field === "token" ? { token: sentence(err) } : { form: sentence(err) }),
    onSettled: () => client.invalidateQueries({ queryKey: key.accounts }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (save.isPending) return;
    const sent = token.trim();
    setToken("");
    setProblem({});
    save.mutate(sent);
  }

  return (
    <Dialog
      title={`New key for ${name}`}
      description="Press Generate token beside the account in your Meta app, then paste it here. It lasts 60 days."
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <TextField
          label="Access token"
          value={token}
          onChange={(v) => {
            setToken(v);
            setProblem({});
          }}
          error={problem.token}
          maxLength={4096}
          secret
        />
        {problem.form && <FieldError text={problem.form} />}
        <DialogFooter>
          <Button onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save key
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
