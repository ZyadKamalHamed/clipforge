// New clipping channel and Edit (SPEC 14.9 and 10.9): its name, the YouTube channels it watches (one link a
// line, added once it is saved, with how many of their latest videos to clip), the accounts its drafts go
// to, the options each video's project starts with, its rights and the campaign it clips for (10.10), and
// its daily check and limits. Every refusal is a sentence beside its field; nothing is saved until Save. Set
// it up on an account worth adding (10.11) starts it filled from the advice, and that one is made paused.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api, ApiError, key, sentence } from "../api.ts";
import { accountName, PLATFORMS } from "../lib/accounts.ts";
import {
  BACKFILL_MAX,
  CHECK_TIME,
  linkLines,
  NAME_LONG,
  NAME_NEEDED,
  ON_LIMIT,
  RIGHTS,
  TOO_MANY_TAGS,
  validChannel,
} from "../lib/channels.ts";
import {
  CAPTION_PRESETS,
  CLIP_COUNTS,
  LAYOUTS,
  LENGTHS,
  PROMPT_MAX,
} from "../lib/options.ts";
import { hashtagText, parseHashtags } from "../lib/plan.ts";
import { accountsQuery, campaignsQuery } from "../state.ts";
import type {
  CaptionPreset,
  ClipChannel,
  ProjectOptions,
  WatchedSourceInfo,
} from "../types.ts";
import { Avatar } from "./AccountCard.tsx";
import { Button } from "./Button.tsx";
import { Choice } from "./Choice.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField, TIME_FIELD } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

const SELECT =
  "rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

type Field =
  | "name"
  | "links"
  | "accounts"
  | "hashtags"
  | "check_time"
  | "campaign"
  | "form";

// Where a refusal from the engine is shown, by the field it names.
function fieldOf(err: unknown): Field {
  const field = err instanceof ApiError ? (err.field ?? "") : "";
  if (field === "name" || field === "check_time") return field;
  if (field === "account_ids") return "accounts";
  if (field === "options.hashtags") return "hashtags";
  if (field === "campaign_id") return "campaign";
  return "form";
}

const PROBLEM_FIELD: Record<string, Field> = {
  [NAME_NEEDED]: "name",
  [NAME_LONG]: "name",
  [CHECK_TIME]: "check_time",
  [TOO_MANY_TAGS]: "hashtags",
};

// A new channel filled from an account worth adding: its name, the briefs' footage and the tagged accounts.
export type ChannelStart = {
  name: string;
  accountIds: string[];
  sources: string[];
  paused: true;
};

type Props = {
  channel: ClipChannel | null;
  start?: ChannelStart;
  onClose: () => void;
};

export function ChannelDialog({ channel, start, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const accounts = useQuery(accountsQuery);
  const campaigns = useQuery(campaignsQuery);
  const project = channel?.options.project ?? {};
  const [name, setName] = useState(channel?.name ?? start?.name ?? "");
  const [links, setLinks] = useState(start?.sources.join("\n") ?? "");
  const [backfill, setBackfill] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(channel?.account_ids ?? start?.accountIds),
  );
  const [clipCount, setClipCount] = useState<number | null>(
    project.clip_count ?? null,
  );
  const [length, setLength] = useState<ProjectOptions["length"]>(
    project.length ?? "auto",
  );
  const [layout, setLayout] = useState<ProjectOptions["layout"]>(
    project.layout ?? "auto",
  );
  const [preset, setPreset] = useState<CaptionPreset | null>(
    project.caption_preset ?? null,
  );
  const [prompt, setPrompt] = useState(project.prompt ?? "");
  const [hashtags, setHashtags] = useState(
    hashtagText(channel?.options.hashtags ?? []),
  );
  const [rights, setRights] = useState(channel?.rights_basis ?? "not_cleared");
  const [campaignId, setCampaignId] = useState(channel?.campaign?.id ?? "");
  const [checkTime, setCheckTime] = useState(channel?.check_time ?? "06:00");
  const [videoLimit, setVideoLimit] = useState(channel?.daily_video_limit ?? 3);
  const [clipsPerDay, setClipsPerDay] = useState<number | null>(
    channel?.clips_per_day ?? null,
  );
  const [onLimit, setOnLimit] = useState(channel?.on_limit ?? "wait");
  const [problems, setProblems] = useState<Partial<Record<Field, string>>>({});
  const [busy, setBusy] = useState(false);
  const made = useRef<string | null>(channel?.id ?? null); // once saved, Save changes it rather than make another
  const linked = accounts.data?.accounts ?? [];
  // Active campaigns, and the ended one it is linked to so the picker can show it
  const offered = (campaigns.data?.campaigns ?? []).filter(
    (c) => c.status === "active" || c.id === channel?.campaign?.id,
  );

  async function save(event?: FormEvent) {
    event?.preventDefault();
    if (busy) return;
    const tags = parseHashtags(hashtags);
    const problem = validChannel({ name, checkTime, hashtags: tags });
    if (problem) {
      setProblems({ [PROBLEM_FIELD[problem] ?? "form"]: problem });
      return;
    }
    setBusy(true);
    setProblems({});
    const body = JSON.stringify({
      name: name.trim(),
      // Until the accounts have loaded none can show as ticked, so the channel keeps the ones it has
      account_ids: accounts.isSuccess
        ? linked.filter((a) => picked.has(a.id)).map((a) => a.id)
        : undefined,
      options: {
        project: {
          clip_count: clipCount,
          length,
          layout,
          caption_preset: preset,
          prompt: prompt.trim() || null,
        },
        hashtags: tags,
      },
      rights_basis: rights,
      check_time: checkTime,
      daily_video_limit: videoLimit,
      clips_per_day: clipsPerDay,
      on_limit: onLimit,
      // Only a change is sent: linking sets the rights to a clipping programme, unlinking clears them
      campaign_id:
        campaignId !== (channel?.campaign?.id ?? "")
          ? campaignId || null
          : undefined,
      paused: start && !made.current ? true : undefined, // made paused, never paused again by a later Save
    });
    try {
      const id = made.current;
      const saved = id
        ? await api<{ channel: ClipChannel }>(
            `/api/clip-channels/${encodeURIComponent(id)}`,
            { method: "PATCH", body },
          )
        : await api<{ channel: ClipChannel }>("/api/clip-channels", {
            method: "POST",
            body,
          });
      made.current = saved.channel.id;
    } catch (err) {
      setProblems({ [fieldOf(err)]: sentence(err) });
      setBusy(false);
      return;
    }
    // Then each YouTube channel; one that can't be added stays in the field with why.
    const left: string[] = [];
    const why: string[] = [];
    for (const link of linkLines(links)) {
      try {
        await api<{ source: WatchedSourceInfo }>(
          `/api/clip-channels/${encodeURIComponent(made.current)}/sources`,
          {
            method: "POST",
            body: JSON.stringify({ link, backfill }),
          },
        );
      } catch (err) {
        left.push(link);
        why.push(`${link}: ${sentence(err)}`);
      }
    }
    void client.invalidateQueries({ queryKey: key.channels });
    void client.invalidateQueries({ queryKey: key.campaigns }); // its channels column
    void client.invalidateQueries({ queryKey: key.advice }); // a niche it posts for needs no account now
    setBusy(false);
    if (left.length) {
      setLinks(left.join("\n"));
      setProblems({ links: why.join(" ") });
      return;
    }
    toast({ text: `${name.trim()} saved`, kind: "ok" });
    onClose();
  }

  function tick(id: string, on: boolean) {
    setPicked((old) => {
      const next = new Set(old);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <Dialog
      title={channel ? `Edit ${channel.name}` : "New clipping channel"}
      description="It checks each YouTube channel once a day and leaves drafts on Today. Nothing posts until you approve it."
      open
      wide
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <form onSubmit={save} noValidate className="flex flex-col gap-5">
        <TextField
          label="Name"
          value={name}
          onChange={setName}
          maxLength={200}
          error={problems.name}
        />
        {start && !channel && (
          <p className="text-sm text-muted">
            It starts paused: turn it on once you've looked it over.
          </p>
        )}

        <Section title="Sources">
          <TextField
            label="YouTube channels"
            value={links}
            onChange={setLinks}
            multiline
            hint="One link a line: a channel, or one of its videos. They are added when you save."
            error={problems.links}
            placeholder="https://www.youtube.com/@name"
          />
          <Select
            label="Also clip the latest"
            value={String(backfill)}
            onChange={(v) => setBackfill(Number(v))}
          >
            {Array.from({ length: BACKFILL_MAX + 1 }, (_, n) => (
              <option key={n} value={n}>
                {n === 0
                  ? "None, only new uploads"
                  : n === 1
                    ? "1 video"
                    : `${n} videos`}
              </option>
            ))}
          </Select>
        </Section>

        <Section title="Accounts">
          {accounts.isPending && (
            <p className="text-sm text-muted">Loading the accounts.</p>
          )}
          {accounts.isSuccess && linked.length === 0 && (
            <p className="text-sm text-muted">
              No accounts are linked yet. Link one on{" "}
              <Link
                to="/accounts"
                className="text-fg underline underline-offset-2"
              >
                Accounts
              </Link>
              , then pick it here.
            </p>
          )}
          {linked.length > 0 && (
            <ul className="flex flex-col gap-2">
              {linked.map((account) => (
                <li key={account.id}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={picked.has(account.id)}
                      onChange={(e) => tick(account.id, e.target.checked)}
                      className="size-4 cursor-pointer accent-accent-strong"
                    />
                    <Avatar
                      account={account}
                      name={accountName(account)}
                      size="small"
                    />
                    <span dir="auto">{accountName(account)}</span>
                    <span className="text-xs text-muted">
                      {PLATFORMS[account.platform]}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {problems.accounts && <FieldError text={problems.accounts} />}
        </Section>

        <Section title="Clips">
          <div className="flex flex-wrap gap-4">
            <Select
              label="Clips per video"
              value={clipCount === null ? "" : String(clipCount)}
              onChange={(v) => setClipCount(v ? Number(v) : null)}
            >
              <option value="">Auto</option>
              {CLIP_COUNTS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
            <Select
              label="Layout"
              value={layout}
              onChange={(v) => setLayout(v as ProjectOptions["layout"])}
            >
              {Object.entries(LAYOUTS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Select
              label="Caption style"
              value={preset ?? ""}
              onChange={(v) => setPreset((v || null) as CaptionPreset | null)}
            >
              <option value="">Default</option>
              {Object.entries(CAPTION_PRESETS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </div>
          <Choice
            legend="Clip length"
            name="channel_length"
            value={length}
            options={LENGTHS}
            onChange={setLength}
          />
          <TextField
            label="Focus"
            value={prompt}
            onChange={setPrompt}
            maxLength={PROMPT_MAX}
            hint="What to look for in each video, if anything."
          />
          <TextField
            label="Hashtags"
            value={hashtags}
            onChange={setHashtags}
            hint="First on every draft's caption, up to 10."
            error={problems.hashtags}
          />
          <Choice
            legend="Rights"
            name="channel_rights"
            value={rights}
            options={RIGHTS}
            onChange={setRights}
          />
          <Select
            label="Campaign"
            value={campaignId}
            onChange={(v) => {
              setCampaignId(v);
              setRights(v ? "clipping_programme" : "not_cleared"); // as the engine sets them, shown before Save
            }}
            hint="Its drafts follow the campaign's rules, and each post is tracked on its page."
            error={problems.campaign}
          >
            <option value="">None</option>
            {offered.map((c) => (
              <option key={c.id} value={c.id}>
                {c.status === "ended" ? `${c.name} (ended)` : c.name}
              </option>
            ))}
          </Select>
        </Section>

        <Section title="Checks">
          <div className="flex flex-wrap gap-4">
            <Labelled label="Check time" error={problems.check_time}>
              {(id, described) => (
                <input
                  id={id}
                  type="time"
                  value={checkTime}
                  onChange={(e) => setCheckTime(e.target.value)}
                  aria-invalid={problems.check_time ? true : undefined}
                  aria-describedby={described}
                  className={TIME_FIELD}
                />
              )}
            </Labelled>
            <Select
              label="New videos a day"
              value={String(videoLimit)}
              onChange={(v) => setVideoLimit(Number(v))}
            >
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
            <Select
              label="Drafts a day"
              value={clipsPerDay === null ? "" : String(clipsPerDay)}
              onChange={(v) => setClipsPerDay(v ? Number(v) : null)}
            >
              <option value="">Auto, what the accounts can post</option>
              {Array.from({ length: 50 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </div>
          <Choice
            legend="At Claude's usage limit"
            name="channel_limit"
            value={onLimit}
            options={ON_LIMIT}
            onChange={setOnLimit}
          />
        </Section>

        {problems.form && <FieldError text={problems.form} />}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? "Saving." : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Labelled({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: (id: string, described: string | undefined) => ReactNode;
}) {
  const id = useId();
  const described =
    [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") ||
    undefined;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children(id, described)}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-faint">
          {hint}
        </p>
      )}
      {error && <FieldError id={`${id}-error`} text={error} />}
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  hint,
  error,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <Labelled label={label} hint={hint} error={error}>
      {(id, described) => (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={described}
          className={SELECT}
        >
          {children}
        </select>
      )}
    </Labelled>
  );
}
