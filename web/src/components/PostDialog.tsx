// The Post dialog (SPEC 14.5): Accounts, Details and Review for the clips picked. Every change asks the
// engine for a fresh plan, a moment after the typing stops; only Confirm and post writes anything, and it
// sends once, for the plan on screen. Given drafts (Today's Approve and post, 10.9), it opens on Review for
// exactly those, each draft its own clip and account, and Confirm turns them into scheduled posts.

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ChevronRight, LoaderCircle, LogIn } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError, api, key, sentence } from "../api.ts";
import {
  accountName,
  capText,
  loginWord,
  PLATFORMS,
  statusLabel,
} from "../lib/accounts.ts";
import {
  CAPTION_LIMIT,
  captionUnits,
  dayThenTime,
  firstLine,
  groupAccounts,
  hashtagText,
  isOn,
  localInput,
  parseHashtags,
  parseLocal,
  POST_DEFAULTS,
  sortWarnings,
  switchesFor,
  switchesOff,
  switchOn,
  trialLine,
  VISIBILITY,
  warningTone,
  whyNot,
} from "../lib/plan.ts";
import {
  blockedHint,
  countLabel,
  listedWarnings,
  tickedRules,
} from "../lib/drafts.ts";
import { clipsLabel } from "../lib/projects.ts";
import { pickable, soundLabel } from "../lib/sounds.ts";
import { accountsQuery, activeJobsQuery } from "../state.ts";
import type {
  Account,
  Draft,
  Plan,
  PlannedPost,
  PlanRequest,
  PlanWarning,
  Platform,
  PostEdit,
  PostOptions,
} from "../types.ts";
import { Avatar } from "./AccountCard.tsx";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { Choice } from "./Choice.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import {
  DateTimeField,
  FieldError,
  TIME_FIELD,
  TextField,
  Toggle,
} from "./Field.tsx";
import { RuleChecks } from "./RuleList.tsx";
import { SoundPicker } from "./SoundPicker.tsx";
import { useToast } from "./Toasts.tsx";

export type PostClip = { id: string; title: string };

type Props = { clips: PostClip[]; drafts?: Draft[]; onClose: () => void };

type Step = "accounts" | "details" | "review";
type Mode = PlanRequest["mode"];
type Timing = PlanRequest["timing"];

const STEPS: { value: Step; label: string }[] = [
  { value: "accounts", label: "Accounts" },
  { value: "details", label: "Details" },
  { value: "review", label: "Review" },
];
const SETTLE_MS = 250; // how long the typing rests before the engine plans again
const INTERVAL_MIN = "180"; // Custom's first gap between posts, TikTok's own spacing
const EVERY_ACK =
  "Posting the same clip to several accounts on one platform is what TikTok's spam rule describes and can get " +
  "them restricted together.";
const MODES: { value: Mode; label: string }[] = [
  { value: "spread", label: "Spread" },
  { value: "every", label: "Every clip to every account" },
];
const MODE_TEXT: Record<Mode, string> = {
  spread:
    "Each clip goes to one account on each platform, shared out so no account gets them all.",
  every: "Each clip goes to every account picked.",
};
const TIMINGS: { value: Timing; label: string }[] = [
  { value: "now", label: "Post now" },
  { value: "smart", label: "Smart schedule" },
  { value: "custom", label: "Custom" },
];
const TIMING_TEXT: Record<Timing, string> = {
  now: "The first post on each platform goes now and the rest follow a few minutes apart.",
  smart: "Each post gets its account's next free slot in the posting windows.",
  custom:
    "Pick a start and the minutes between posts, or a time for each post on the next step.",
};
const OPEN_UP_TO = 2; // posts at most for Details to start with every post open

export function PostDialog({ clips, drafts, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const accounts = useQuery(accountsQuery);
  const [step, setStep] = useState<Step>(drafts ? "review" : "accounts");
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(drafts?.map((d) => d.account_id)),
  );
  const [mode, setMode] = useState<Mode>("spread");
  const [ack, setAck] = useState(false);
  const [timing, setTiming] = useState<Timing>("smart");
  const [start, setStart] = useState("");
  const [gap, setGap] = useState(INTERVAL_MIN);
  const [times, setTimes] = useState<Record<string, number>>({});
  const [edits, setEdits] = useState<Record<string, PostEdit>>({});
  const [shared, setShared] = useState<
    Partial<Record<Platform, Partial<PostOptions>>>
  >({});
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [tagText, setTagText] = useState<Record<string, string>>({});
  const [changed, setChanged] = useState<string | null>(null);

  const all = accounts.data?.accounts ?? [];
  const chosen = all.filter((a) => picked.has(a.id));
  const crowded =
    mode === "every" &&
    chosen.some(
      (a) => chosen.filter((b) => b.platform === a.platform).length > 1,
    );
  const names = useMemo(
    () => new Map(all.map((a) => [a.id, accountName(a)])),
    [all],
  );
  const titles = useMemo(
    () => new Map(clips.map((c) => [c.id, c.title])),
    [clips],
  );

  const request: PlanRequest = {
    clip_ids: clips.map((c) => c.id),
    account_ids: drafts ? [...picked] : chosen.map((a) => a.id), // a draft's account, even before they load
    mode,
    timing,
    custom_start: timing === "custom" ? parseLocal(start) : null,
    custom_interval_min:
      timing === "custom" && /^\d+$/.test(gap.trim()) ? Number(gap) : null,
    custom_times: timing === "custom" ? times : {},
    edits,
    platform_options: shared,
    acknowledged_every: ack,
    ...(drafts && { draft_ids: drafts.map((d) => d.id) }),
  };
  const body = JSON.stringify(request);
  const sent = useSettled(body);
  const planned = useQuery({
    queryKey: ["plan", sent],
    queryFn: ({ signal }) =>
      api<Plan>("/api/posts/plan", { method: "POST", body: sent, signal }),
    enabled: (JSON.parse(sent) as PlanRequest).account_ids.length > 0,
    placeholderData: keepPreviousData,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const plan = request.account_ids.length > 0 ? planned.data : undefined;
  const current =
    plan !== undefined &&
    sent === body &&
    !planned.isPlaceholderData &&
    !planned.isFetching;

  const sending = useRef(false);
  const send = useMutation({
    mutationFn: (sentPlan: { plan_id: string; request: PlanRequest }) =>
      api<{ batch_id: string; posts: unknown[] }>("/api/posts/confirm", {
        method: "POST",
        body: JSON.stringify(sentPlan),
      }),
    onSuccess: ({ posts }) => {
      const text = drafts
        ? `${countLabel(posts.length)} approved`
        : `${posts.length} post${posts.length === 1 ? "" : "s"} scheduled`;
      toast({ text, kind: "ok" });
      if (drafts) void client.invalidateQueries({ queryKey: key.drafts });
      void client.invalidateQueries({ queryKey: key.accounts });
      void client.invalidateQueries({ queryKey: key.activeJobs });
      onClose();
      void navigate("/queue");
    },
    onError: (err) => {
      sending.current = false;
      const fresh =
        err instanceof ApiError && err.code === "plan_changed"
          ? (err.body as { plan?: Plan } | null)?.plan
          : null;
      if (fresh) client.setQueryData(["plan", sent], fresh);
      setChanged(sentence(err));
    },
  });

  function post() {
    if (sending.current || !plan || !current || plan.blocked) return;
    sending.current = true;
    setChanged(null);
    send.mutate({
      plan_id: plan.plan_id,
      request: JSON.parse(sent) as PlanRequest,
    });
  }

  function toggle(id: string, on: boolean) {
    setPicked((p) => {
      const next = new Set(p);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function edit(k: string, change: PostEdit) {
    setEdits((e) => ({ ...e, [k]: { ...e[k], ...change } }));
  }

  // A switch changed in a platform's block goes on every post there, so each post drops its own value for it.
  function share(platform: Platform, change: Partial<PostOptions>) {
    setShared((s) => ({ ...s, [platform]: { ...s[platform], ...change } }));
    const on = new Set(
      all.filter((a) => a.platform === platform).map((a) => a.id),
    );
    setEdits((e) => {
      const next: Record<string, PostEdit> = {};
      for (const [k, one] of Object.entries(e)) {
        if (!one.options || !on.has(k.slice(k.indexOf(":") + 1))) {
          next[k] = one;
          continue;
        }
        const options: Partial<PostOptions> = { ...one.options };
        for (const field of Object.keys(change) as (keyof PostOptions)[])
          delete options[field];
        next[k] = { ...one, options };
      }
      return next;
    });
  }

  function moveTo(k: string, at: number) {
    setTiming("custom");
    setTimes((t) => ({ ...t, [k]: at }));
  }

  const index = STEPS.findIndex((s) => s.value === step);
  // Details waits for the posts it shows; Review then waits for the plan it confirms.
  const canNext =
    step === "accounts"
      ? chosen.length > 0 && (!crowded || ack)
      : plan !== undefined;
  return (
    <Dialog
      title={
        drafts
          ? `Approve ${countLabel(drafts.length)}`
          : `Post ${clipsLabel(clips.length)}`
      }
      description="Nothing posts until you press Confirm and post."
      open
      wide
      onOpenChange={(next) => {
        if (!next && !send.isPending) onClose();
      }}
      steps={
        !drafts && (
          <ol aria-label="Steps" className="flex gap-4 text-sm">
            {STEPS.map((s, i) => (
              <li
                key={s.value}
                aria-current={s.value === step ? "step" : undefined}
                className={
                  s.value === step ? "font-medium text-fg" : "text-muted"
                }
              >
                {i + 1}. {s.label}
              </li>
            ))}
          </ol>
        )
      }
    >
      {step === "accounts" && (
        <AccountsStep
          accounts={all}
          loading={accounts.isPending}
          picked={picked}
          onPick={toggle}
          mode={mode}
          onMode={(m) => {
            setMode(m);
            setAck(false);
          }}
          crowded={crowded}
          ack={ack}
          onAck={setAck}
        />
      )}
      {step === "details" && (
        <div className="flex flex-col gap-4">
          <TimingFields
            timing={timing}
            onTiming={setTiming}
            start={start}
            onStart={setStart}
            gap={gap}
            onGap={setGap}
          />
          {plan ? (
            <>
              {platformsOf(plan).map((platform) => (
                <PlatformFields
                  key={platform}
                  platform={platform}
                  method={methodOf(plan, all, platform)}
                  options={{ ...POST_DEFAULTS, ...shared[platform] }}
                  onChange={(change) => share(platform, change)}
                />
              ))}
              {byClip(plan).map(([clipId, posts]) => {
                const title = titles.get(clipId) ?? clipId;
                return (
                  <section
                    key={clipId}
                    aria-label={title}
                    className="flex flex-col gap-2"
                  >
                    <h4 className="text-sm font-medium" dir="auto">
                      {title}
                    </h4>
                    {posts.map((p) => (
                      <PostFields
                        key={p.key}
                        post={p}
                        title={title}
                        name={names.get(p.account_id) ?? p.account_id}
                        account={all.find((a) => a.id === p.account_id)}
                        block={{ ...POST_DEFAULTS, ...shared[p.platform] }}
                        edit={edits[p.key]}
                        tags={tagText[p.key]}
                        open={opened[p.key] ?? plan.posts.length <= OPEN_UP_TO}
                        onOpen={(on) =>
                          setOpened((o) => ({ ...o, [p.key]: on }))
                        }
                        onEdit={(change) => edit(p.key, change)}
                        onTags={(text) => {
                          setTagText((t) => ({ ...t, [p.key]: text }));
                          edit(p.key, { hashtags: parseHashtags(text) });
                        }}
                      />
                    ))}
                  </section>
                );
              })}
            </>
          ) : (
            !planned.isError && <Planning />
          )}
        </div>
      )}
      {step === "review" && plan && (
        <Review
          plan={plan}
          titles={titles}
          names={names}
          timing={timing}
          edits={edits}
          onTime={(k, at) => setTimes((t) => ({ ...t, [k]: at }))}
          onMove={moveTo}
          onTicks={(k, ticks) => edit(k, { rule_ticks: ticks })}
        />
      )}
      {step === "review" && !plan && !planned.isError && <Planning />}
      {planned.isError && request.account_ids.length > 0 && (
        <FieldError text={sentence(planned.error)} />
      )}
      {changed && <FieldError text={changed} />}
      <DialogFooter>
        {step === "review" && plan?.blocked && (
          <p className="mr-auto text-sm text-bad">
            {blockedHint(plan.warnings)}
          </p>
        )}
        <Button variant="ghost" onClick={onClose} disabled={send.isPending}>
          Cancel
        </Button>
        {index > 0 && !drafts && (
          <Button
            onClick={() => setStep(STEPS[index - 1].value)}
            disabled={send.isPending}
          >
            Back
          </Button>
        )}
        {step !== "review" ? (
          <Button
            variant="primary"
            onClick={() => setStep(STEPS[index + 1].value)}
            disabled={!canNext}
          >
            Next
          </Button>
        ) : (
          <Button
            variant="primary"
            onClick={post}
            disabled={!current || plan?.blocked !== false || send.isPending}
          >
            Confirm and post
          </Button>
        )}
      </DialogFooter>
    </Dialog>
  );
}

// The platforms with more than one post, TikTok first: one of them gets a block that sets them all.
function platformsOf(plan: Plan): Platform[] {
  const count = (platform: Platform) =>
    plan.posts.filter((p) => p.platform === platform).length;
  return (["tiktok", "instagram"] as const).filter(
    (platform) => count(platform) > 1,
  );
}

// The posts under each clip, in the plan's order.
function byClip(plan: Plan): [string, PlannedPost[]][] {
  const out = new Map<string, PlannedPost[]>();
  for (const p of plan.posts)
    out.set(p.clip_id, [...(out.get(p.clip_id) ?? []), p]);
  return [...out];
}

// What Details and Review show while the posts are planned.
function Planning() {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted">
      <LoaderCircle
        aria-hidden
        size={16}
        className="motion-safe:animate-spin"
      />
      Planning the posts.
    </p>
  );
}

// The value a moment after it last changed, so typing in a caption plans once, not once a key.
function useSettled(value: string): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [value]);
  return settled;
}

type AccountsProps = {
  accounts: Account[];
  loading: boolean;
  picked: Set<string>;
  onPick: (id: string, on: boolean) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  crowded: boolean;
  ack: boolean;
  onAck: (on: boolean) => void;
};

function AccountsStep({
  accounts,
  loading,
  picked,
  onPick,
  mode,
  onMode,
  crowded,
  ack,
  onAck,
}: AccountsProps) {
  const id = useId();
  if (loading)
    return <p className="text-sm text-muted">Loading the accounts.</p>;
  if (accounts.length === 0) {
    return (
      <p className="text-sm text-muted">
        No accounts yet.{" "}
        <Link to="/accounts" className="text-fg underline underline-offset-2">
          Add one on the Accounts page
        </Link>{" "}
        to post.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {groupAccounts(accounts).map(({ platform, accounts: group }) => (
        <fieldset
          key={platform}
          data-platform={platform}
          className="flex min-w-0 flex-col gap-1.5"
        >
          <legend className="mb-1.5 text-xs font-medium text-muted">
            {PLATFORMS[platform]}
          </legend>
          {group.map((a) => (
            <AccountRow
              key={a.id}
              account={a}
              checked={picked.has(a.id)}
              onPick={onPick}
            />
          ))}
        </fieldset>
      ))}
      <div className="flex flex-col gap-1.5">
        <Choice
          legend="How the clips go out"
          name={`${id}-mode`}
          value={mode}
          options={MODES}
          onChange={onMode}
        />
        <p className="text-xs text-faint">{MODE_TEXT[mode]}</p>
      </div>
      {crowded && (
        <div className="flex items-start gap-2 rounded-card border border-warn/40 bg-panel-2 p-3 text-sm">
          <input
            id={`${id}-ack`}
            type="checkbox"
            checked={ack}
            onChange={(e) => onAck(e.target.checked)}
            aria-describedby={`${id}-ack-why`}
            className="mt-0.5 accent-accent-strong"
          />
          <div className="flex flex-col gap-1">
            <label htmlFor={`${id}-ack`} className="font-medium">
              Post every clip to every account anyway
            </label>
            <p id={`${id}-ack-why`} className="text-muted">
              {EVERY_ACK}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function AccountRow({
  account,
  checked,
  onPick,
}: {
  account: Account;
  checked: boolean;
  onPick: AccountsProps["onPick"];
}) {
  const id = useId();
  const name = accountName(account);
  const status = statusLabel(account);
  const why = whyNot(account);
  const about = [`${id}-status`, `${id}-cap`, why && `${id}-why`]
    .filter(Boolean)
    .join(" ");
  const client = useQueryClient();
  const toast = useToast();
  const jobs = useQuery(activeJobsQuery);
  // A Chrome account logged out logs in from its row, so the dialog needn't be left; it ticks once it's back.
  const relogin =
    account.method === "browser" && account.status === "needs_login";
  const job = (jobs.data?.jobs ?? []).find(
    (j) => j.kind === "account.login" && j.ref_id === account.id,
  );
  const login = useMutation({
    mutationFn: () =>
      api<undefined>(`/api/accounts/${encodeURIComponent(account.id)}/login`, {
        method: "POST",
      }),
    onSuccess: () =>
      toast({
        text: `Log in to ${PLATFORMS[account.platform]} in the Chrome window that opens.`,
        kind: "info",
        subject: name,
      }),
    onError: (err) =>
      toast({ text: sentence(err), kind: "bad", subject: name }),
    onSettled: () =>
      void client.invalidateQueries({ queryKey: key.activeJobs }),
  });
  // A row that can't be picked has a dashed border rather than dimmed text, which would fall under AA.
  return (
    <div
      className={
        "flex flex-wrap items-center gap-3 rounded-card border border-line px-3 py-2 text-sm " +
        (why ? "border-dashed" : "has-[:checked]:border-accent-strong")
      }
    >
      <input
        id={id}
        type="checkbox"
        checked={checked && !why}
        disabled={why !== null}
        onChange={(e) => onPick(account.id, e.target.checked)}
        aria-describedby={about}
        className="accent-accent-strong"
      />
      <label htmlFor={id} className="flex min-w-0 items-center gap-2">
        <Avatar account={account} name={name} size="small" />
        <span className="truncate font-medium" dir="auto">
          {name}
        </span>
      </label>
      <span id={`${id}-status`}>
        {job ? (
          <Badge tone="info">{loginWord(job)}</Badge>
        ) : (
          <Badge tone={status.tone}>{status.text}</Badge>
        )}
      </span>
      {account.paused && <Badge tone="neutral">Paused</Badge>}
      <span id={`${id}-cap`} className="text-xs text-muted tabular-nums">
        {capText(account)}
      </span>
      {relogin && !job && (
        <Button
          variant="primary"
          className="ml-auto"
          onClick={() => login.mutate()}
          disabled={login.isPending}
        >
          <LogIn aria-hidden size={14} />
          Log in
        </Button>
      )}
      {why && (
        <span id={`${id}-why`} className="basis-full text-xs text-warn">
          {why}
        </span>
      )}
    </div>
  );
}

type TimingProps = {
  timing: Timing;
  onTiming: (timing: Timing) => void;
  start: string;
  onStart: (text: string) => void;
  gap: string;
  onGap: (text: string) => void;
};

function TimingFields({
  timing,
  onTiming,
  start,
  onStart,
  gap,
  onGap,
}: TimingProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-2">
      <Choice
        legend="When"
        name={`${id}-timing`}
        value={timing}
        options={TIMINGS}
        onChange={onTiming}
      />
      <p className="text-xs text-faint">{TIMING_TEXT[timing]}</p>
      {timing === "custom" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <DateTimeField label="Start" value={start} onChange={onStart} />
          <TextField
            label="Minutes between posts"
            value={gap}
            onChange={onGap}
            inputMode="numeric"
            maxLength={5}
          />
        </div>
      )}
    </div>
  );
}

type PostProps = {
  post: PlannedPost;
  title: string; // the clip's
  name: string; // the account's
  account: Account | undefined;
  block: PostOptions; // the switches its platform's block sets
  edit: PostEdit | undefined;
  tags: string | undefined;
  open: boolean;
  onOpen: (open: boolean) => void;
  onEdit: (change: PostEdit) => void;
  onTags: (text: string) => void;
};

// One post as a line that opens on a click: its caption, hashtags, switches and, on a route that takes
// one, its sound, filled from the clip's packaging and the account. The text shown is what was typed, so
// the field never jumps while the engine plans again, and its line names each switch that differs from
// the platform's block.
function PostFields({
  post,
  title,
  name,
  account,
  block,
  edit,
  tags,
  open,
  onOpen,
  onEdit,
  onTags,
}: PostProps) {
  const caption = edit?.caption ?? post.caption;
  const hashtags = edit?.hashtags ?? post.hashtags;
  const options = { ...post.options, ...block, ...edit?.options };
  const route = account ? pickable(account) : null;
  const limit = CAPTION_LIMIT[post.platform];
  const long = captionUnits(caption, hashtags) > limit;
  return (
    <details
      open={open}
      onToggle={(e) => onOpen(e.currentTarget.open)}
      aria-label={`${title} to ${name} on ${PLATFORMS[post.platform]}`}
      className="group rounded-card border border-line"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-card px-3 py-2 text-sm hover:bg-panel-2 [&::-webkit-details-marker]:hidden">
        <ChevronRight
          aria-hidden
          size={16}
          className="shrink-0 text-muted transition-transform group-open:rotate-90"
        />
        <span className="shrink-0 font-medium" dir="auto">
          {name}
        </span>
        <span className="shrink-0 text-muted">
          on {PLATFORMS[post.platform]}
        </span>
        <span className="min-w-0 flex-1 truncate text-muted" dir="auto">
          {firstLine(caption)}
        </span>
        {long && <Badge tone="bad">Caption too long</Badge>}
        {switchesOff(post.platform, options, block).map((text) => (
          <Badge key={text}>{text}</Badge>
        ))}
      </summary>
      <div className="flex flex-col gap-3 border-t border-line p-3">
        <TextField
          label="Caption"
          value={caption}
          onChange={(text) => onEdit({ caption: text })}
          count={{
            n: captionUnits(caption, hashtags),
            max: limit,
            id: `caption-${post.key}`,
          }}
          hint={`Counted with the hashtags, as ${PLATFORMS[post.platform]} counts it.`}
          multiline
          maxLength={10_000}
        />
        <TextField
          label="Hashtags"
          value={tags ?? hashtagText(post.hashtags)}
          onChange={onTags}
          hint="Separate them with spaces."
          maxLength={2000}
        />
        <Switches
          platform={post.platform}
          method={account?.method ?? "browser"}
          options={options}
          onChange={(change) =>
            onEdit({ options: { ...edit?.options, ...change } })
          }
        />
        {account && route && (
          <SoundPicker
            post={post}
            account={account}
            route={route}
            sound={options.sound ?? null}
            open={open}
            onPick={(sound) => onEdit({ options: { ...edit?.options, sound } })}
          />
        )}
      </div>
    </details>
  );
}

type SwitchesProps = {
  platform: Platform;
  method: Account["method"];
  options: PostOptions;
  onChange: (change: Partial<PostOptions>) => void;
};

// A platform's block offers Meta's API switches when any of its posts goes up through the API; a Chrome post
// that then asks for one gets the plan's sentence saying why it can't.
function methodOf(
  plan: Plan,
  accounts: Account[],
  platform: Platform,
): Account["method"] {
  const api = plan.posts.some(
    (p) =>
      p.platform === platform &&
      accounts.find((a) => a.id === p.account_id)?.method === "api",
  );
  return api ? "api" : "browser";
}

// The block at the top of Details for a platform's posts: what it sets, every post there gets.
function PlatformFields({
  platform,
  method,
  options,
  onChange,
}: SwitchesProps) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1 rounded-card border border-line p-3">
      <legend className="px-1 text-sm font-medium">
        Every {PLATFORMS[platform]} post
      </legend>
      <p className="text-xs text-faint">
        Captions and hashtags come from each clip. Open a post below to change
        its own.
      </p>
      <Switches
        platform={platform}
        method={method}
        options={options}
        onChange={onChange}
      />
    </fieldset>
  );
}

function Switches({ platform, method, options, onChange }: SwitchesProps) {
  const id = useId();
  return (
    <div className="flex flex-col">
      {platform === "tiktok" && (
        <div className="flex items-center justify-between gap-4 py-1.5">
          <label htmlFor={`${id}-who`} className="text-sm">
            Who can watch
          </label>
          <select
            id={`${id}-who`}
            value={options.visibility}
            onChange={(e) =>
              onChange({
                visibility: e.target.value as PostOptions["visibility"],
              })
            }
            className={TIME_FIELD}
          >
            {VISIBILITY.map((v) => (
              <option key={v.value} value={v.value}>
                {v.label}
              </option>
            ))}
          </select>
        </div>
      )}
      {switchesFor(platform, method, options).map(
        ({ key: field, label, hint }) => (
          <Toggle
            key={field}
            label={label}
            hint={hint}
            checked={isOn(options, field)}
            onChange={(on) => onChange(switchOn(field, on))}
          />
        ),
      )}
    </div>
  );
}

type ReviewProps = {
  plan: Plan;
  titles: Map<string, string>;
  names: Map<string, string>;
  timing: Timing;
  edits: Record<string, PostEdit>;
  onTime: (key: string, at: number) => void;
  onMove: (key: string, at: number) => void;
  onTicks: (key: string, ticks: string[]) => void;
};

function Review({
  plan,
  titles,
  names,
  timing,
  edits,
  onTime,
  onMove,
  onTicks,
}: ReviewProps) {
  const now = Date.now() / 1000;
  const general = sortWarnings(plan.warnings.filter((w) => w.key === null));
  return (
    <div className="flex flex-col gap-3">
      {general.length > 0 && (
        <ul className="flex flex-col gap-1 text-sm">
          {general.map((w) => (
            <li key={w.code} className={warningTone(w)}>
              {w.sentence}
            </li>
          ))}
        </ul>
      )}
      <div className="overflow-x-auto">
        <table aria-label="Posts" className="w-full text-left text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-1.5 pr-3 font-medium">Clip</th>
              <th className="py-1.5 pr-3 font-medium">Account</th>
              <th className="py-1.5 pr-3 font-medium">When</th>
              <th className="py-1.5 pr-3 font-medium">Caption</th>
              <th className="py-1.5 font-medium">Warnings for this post</th>
            </tr>
          </thead>
          <tbody>
            {plan.posts.map((p) => {
              const title = titles.get(p.clip_id) ?? p.clip_id;
              const name = names.get(p.account_id) ?? p.account_id;
              const ticks = edits[p.key]?.rule_ticks; // ticked here, shown before the next plan has them
              const own = plan.warnings.filter((w) => w.key === p.key);
              const trial = trialLine(p.platform, p.options);
              return (
                <tr key={p.key} className="border-t border-line align-top">
                  <td className="max-w-48 py-2 pr-3" dir="auto">
                    {title}
                  </td>
                  <td data-col="account" className="py-2 pr-3">
                    <span className="block" dir="auto">
                      {name}
                    </span>
                    <span className="block text-xs text-muted">
                      {PLATFORMS[p.platform]}
                    </span>
                  </td>
                  <td data-col="when" className="py-2 pr-3 whitespace-nowrap">
                    {timing === "custom" ? (
                      <div className="flex flex-col gap-1">
                        <span>{dayThenTime(p.scheduled_at, now)}</span>
                        <input
                          type="datetime-local"
                          aria-label={`Time for ${title} to ${name}`}
                          value={localInput(p.scheduled_at)}
                          onChange={(e) => {
                            const at = parseLocal(e.target.value);
                            if (at !== null) onTime(p.key, at);
                          }}
                          className={TIME_FIELD}
                        />
                      </div>
                    ) : (
                      dayThenTime(p.scheduled_at, now)
                    )}
                  </td>
                  <td className="min-w-56 py-2 pr-3">
                    <p className="line-clamp-3 text-muted" dir="auto">
                      {p.caption}
                    </p>
                    {p.options.sound && (
                      <p className="mt-1 text-xs text-muted" dir="auto">
                        {`Sound: ${soundLabel(p.options.sound)}`}
                      </p>
                    )}
                    {trial && (
                      <p className="mt-1 text-xs text-muted">{trial}</p>
                    )}
                  </td>
                  <td data-col="warnings" className="max-w-56 py-2">
                    {p.campaign && p.rules.length > 0 && (
                      <div className="mb-1.5">
                        <RuleChecks
                          rules={ticks ? tickedRules(p.rules, ticks) : p.rules}
                          campaign={p.campaign.name}
                          onTick={(next) => onTicks(p.key, next)}
                        />
                      </div>
                    )}
                    <Warnings
                      warnings={listedWarnings({
                        warnings: own,
                        rules: p.rules,
                      })}
                      quiet={p.rules.length > 0}
                      now={now}
                      onMove={(at) => onMove(p.key, at)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type WarningsProps = {
  warnings: PlanWarning[];
  quiet?: boolean;
  now: number;
  onMove: (at: number) => void;
};

// A post's warnings, or None; quiet when its rules list stands above them.
function Warnings({ warnings, quiet = false, now, onMove }: WarningsProps) {
  if (warnings.length === 0)
    return quiet ? null : <span className="text-faint">None</span>;
  return (
    <ul className="flex flex-col gap-1.5">
      {sortWarnings(warnings).map((w) => (
        <li key={w.code} className={warningTone(w)}>
          {w.sentence}
          {w.move_to !== null && (
            <Button
              className="mt-1"
              onClick={() => onMove(w.move_to as number)}
            >
              Move to {dayThenTime(w.move_to, now)}
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}
