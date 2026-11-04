// Today, the home page (SPEC 14.1, 14.9 and 14.10). With no clipping channels, drafts or anything on the day
// yet it is Create's drop zone, with a way to make one. Otherwise its header says the minutes left out of 15,
// then comes the To do list, then Clips to review: the drafts the clipping channels made, by clipping channel
// and then source video, all ticked at first, Approve and post for the ticked ones (the Post dialog's review,
// nothing posts until Confirm) and Discard with its Undo; with none, when the next check runs. Then Post, the
// exported posts to put up by hand, Submit, the live posts campaigns wait on, Guidelines, each running
// campaign's rules and each account's posts today, Money by marketplace, Yesterday's best, and New briefs with
// Accounts worth adding (10.11), each only when it has any.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Link, useLocation } from "react-router";

import { api, key, sentence } from "../api.ts";
import { BestPosts } from "../components/BestPosts.tsx";
import { Button, buttonClass } from "../components/Button.tsx";
import { CampaignForm, type FormStart } from "../components/CampaignForm.tsx";
import { DraftCard } from "../components/DraftCard.tsx";
import { Guidelines } from "../components/Guidelines.tsx";
import { MoneyBlock } from "../components/MoneyBlock.tsx";
import { NewBriefs } from "../components/NewBriefs.tsx";
import { PathCard } from "../components/PathCard.tsx";
import { PostDialog } from "../components/PostDialog.tsx";
import { PostList } from "../components/PostList.tsx";
import { Thumb } from "../components/ProjectCard.tsx";
import { SubmitList } from "../components/SubmitList.tsx";
import { TodoList } from "../components/TodoList.tsx";
import { useToast } from "../components/Toasts.tsx";
import {
  approveProblem,
  draftTitle,
  groupDrafts,
  groupName,
  groupTick,
  nextCheckLine,
  ticked,
  tickedLabel,
} from "../lib/drafts.ts";
import { formatAgo } from "../lib/format.ts";
import { pathLinks } from "../lib/path.ts";
import { budgetLine } from "../lib/today.ts";
import {
  accountsQuery,
  adviceQuery,
  briefSourcesQuery,
  briefsQuery,
  channelsQuery,
  draftsQuery,
  todayQuery,
} from "../state.ts";
import type {
  Advice,
  Brief,
  BriefSource,
  Draft,
  DraftGroup,
  DraftVideo,
  QueuedPost,
  TodayBody,
} from "../types.ts";
import { Create } from "./Create.tsx";

export function Today() {
  const channels = useQuery(channelsQuery);
  const drafts = useQuery(draftsQuery);
  const today = useQuery(todayQuery);
  const briefs = useQuery(briefsQuery);
  const advice = useQuery(adviceQuery);
  const sources = useQuery(briefSourcesQuery);
  const { hash } = useLocation();
  const [making, setMaking] = useState<FormStart | null>(null); // a campaign started from a brief
  const briefsRead =
    !briefs.isPending && !advice.isPending && !sources.isPending; // loaded, or failed
  const ready =
    channels.isSuccess && drafts.isSuccess && today.isSuccess && briefsRead;
  const tenth = {
    briefs: briefs.data?.briefs ?? [],
    advice: advice.data?.advice ?? NO_ADVICE,
    sources: sources.data?.sources ?? [],
  };
  const showBriefs = briefs.isSuccess && blockTen(tenth);

  // A link from another page to one of these blocks ("/#submit") lands on it once the page has loaded.
  useEffect(() => {
    if (ready && hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash, ready]);

  if (
    channels.isSuccess &&
    drafts.isSuccess &&
    today.isSuccess &&
    channels.data.channels.length === 0 &&
    drafts.data.count === 0 &&
    quiet(today.data) &&
    briefsRead &&
    !showBriefs
  ) {
    return (
      <Create
        title="Today"
        action={
          <Link to="/channels?new=1" className={buttonClass("primary")}>
            New clipping channel
          </Link>
        }
      />
    );
  }

  let body: ReactNode;
  if (channels.isPending || drafts.isPending)
    body = <p className="text-sm text-muted">Loading the drafts.</p>;
  else if (drafts.isError)
    body = <p className="text-sm text-bad">{sentence(drafts.error)}</p>;
  else if (channels.isError)
    body = <p className="text-sm text-bad">{sentence(channels.error)}</p>;
  else if (drafts.data.count === 0)
    body = (
      <p className="text-sm text-muted">{`No clips to review. ${nextCheckLine(drafts.data.next_check, Date.now() / 1000, channels.data.channels.length)}`}</p>
    );
  else body = <Review groups={drafts.data.groups} count={drafts.data.count} />;

  let list: ReactNode;
  if (today.isPending)
    list = <p className="text-sm text-muted">Loading the list.</p>;
  else if (today.isError)
    list = <p className="text-sm text-bad">{sentence(today.error)}</p>;
  else
    list = (
      <TodoList items={today.data.items} links={pathLinks(today.data.path)} />
    );

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <h1 className="text-xl font-semibold">Today</h1>
        {today.isSuccess && <Budget day={today.data} />}
      </div>
      {today.isSuccess && today.data.path.on && (
        <PathCard path={today.data.path} />
      )}
      {list}
      <div id="review" className="scroll-mt-4">
        {body}
      </div>
      {today.isSuccess && today.data.post.length > 0 && (
        <PostList posts={today.data.post} />
      )}
      {today.isSuccess && today.data.submit.length > 0 && (
        <SubmitList entries={today.data.submit} />
      )}
      {today.isSuccess &&
        (today.data.guidelines.length > 0 ||
          today.data.accounts.length > 0) && (
          <Guidelines
            guidelines={today.data.guidelines}
            accounts={today.data.accounts}
          />
        )}
      {today.isSuccess && today.data.money.length > 0 && (
        <MoneyBlock lines={today.data.money} />
      )}
      {today.isSuccess && today.data.best.length > 0 && (
        <BestPosts posts={today.data.best} />
      )}
      {showBriefs && <NewBriefs {...tenth} onStart={setMaking} />}
      {making && (
        <CampaignForm
          campaign={null}
          start={making}
          onClose={() => setMaking(null)}
        />
      )}
    </div>
  );
}

const NO_ADVICE: Advice = { suggestions: [], could_take: [] };

// Block 10 shows once there is a live brief, a suggestion or a line of advice, or a site to paste from.
function blockTen(b: {
  briefs: Brief[];
  advice: Advice;
  sources: BriefSource[];
}): boolean {
  return (
    b.briefs.length > 0 ||
    b.advice.suggestions.length > 0 ||
    b.advice.could_take.length > 0 ||
    b.sources.some((s) => s.paste && s.reader === "general")
  );
}

// Nothing on the day at all: no item on the list, done or not, no post to put up, no submission waiting and
// no campaign running.
function quiet(day: TodayBody): boolean {
  return (
    !day.items.length &&
    !day.post.length &&
    !day.submit.length &&
    !day.guidelines.length &&
    !day.money.length &&
    !day.best.length
  );
}

// The header's minutes: what is left of the 15, and what the rest of the list needs.
function Budget({ day }: { day: TodayBody }) {
  const { big, small } = budgetLine(day.used_s, day.left_s);
  return (
    <div className="flex flex-col items-end gap-0.5 text-right">
      <p className="text-lg font-semibold tabular-nums">{big}</p>
      <p className="text-sm text-muted">{small}</p>
    </div>
  );
}

// Clips to review: what is ticked, Approve and post, and every group of drafts.
function Review({ groups, count }: { groups: DraftGroup[]; count: number }) {
  const id = useId();
  const client = useQueryClient();
  const toast = useToast();
  const accounts = useQuery(accountsQuery);
  const [unticked, setUnticked] = useState<Set<string>>(() => new Set());
  const [playing, setPlaying] = useState<string | null>(null);
  const [approving, setApproving] = useState<Draft[] | null>(null);
  const byId = new Map((accounts.data?.accounts ?? []).map((a) => [a.id, a]));
  const chosen = ticked(groups, unticked);
  const problem = approveProblem(chosen.length);
  const now = Date.now() / 1000;

  const refresh = useCallback(
    () => client.invalidateQueries({ queryKey: key.drafts }),
    [client],
  );
  const restore = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ posts: QueuedPost[] }>("/api/drafts/restore", {
        method: "POST",
        body: JSON.stringify({ post_ids: ids }),
      }),
    onSettled: refresh,
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
  });
  const discard = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ posts: QueuedPost[] }>("/api/drafts/discard", {
        method: "POST",
        body: JSON.stringify({ post_ids: ids }),
      }),
    onSettled: refresh,
    onSuccess: (_, ids) =>
      toast({
        text: "Draft discarded",
        kind: "ok",
        action: { label: "Undo", run: () => restore.mutate(ids) },
      }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
  });
  const discardMutate = discard.mutate;

  const onTick = useCallback(
    (draftId: string, on: boolean) => tick([draftId], on),
    [],
  );
  const onHover = useCallback(
    (draftId: string, on: boolean) =>
      setPlaying((was) => (on ? draftId : was === draftId ? null : was)),
    [],
  );
  const onApprove = useCallback((draft: Draft) => setApproving([draft]), []);
  const onDiscard = useCallback(
    (draft: Draft) => discardMutate([draft.id]),
    [discardMutate],
  );

  function tick(ids: string[], on: boolean) {
    setUnticked((old) => {
      const next = new Set(old);
      for (const one of ids) {
        if (on) next.delete(one);
        else next.add(one);
      }
      return next;
    });
  }

  // A dialog's clips: each clip once, by the name its drafts show.
  const clips = approving && [
    ...new Map(
      approving.map((d) => [
        d.clip_id,
        { id: d.clip_id, title: draftTitle(d) },
      ]),
    ).values(),
  ];

  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={id} className="text-base font-semibold">
          Clips to review
        </h2>
        <span className="text-sm text-muted">
          {tickedLabel(chosen.length, count)}
        </span>
        <span className="ml-auto flex items-center gap-3">
          {problem && <span className="text-sm text-muted">{problem}</span>}
          <Button
            variant="primary"
            onClick={() => setApproving(chosen)}
            disabled={problem !== null}
          >
            Approve and post
          </Button>
        </span>
      </div>
      {groups.map((group) => (
        <Group
          key={group.channel?.id ?? ""}
          group={group}
          unticked={unticked}
          onGroup={(on) =>
            tick(
              groupDrafts(group).map((d) => d.id),
              on,
            )
          }
        >
          {(video) =>
            video.drafts.map((draft) => (
              <DraftCard
                key={draft.id}
                draft={draft}
                account={byId.get(draft.account_id)}
                ticked={!unticked.has(draft.id)}
                playing={playing === draft.id}
                now={now}
                onTick={onTick}
                onHover={onHover}
                onApprove={onApprove}
                onDiscard={onDiscard}
              />
            ))
          }
        </Group>
      ))}
      {approving && clips && (
        <PostDialog
          clips={clips}
          drafts={approving}
          onClose={() => setApproving(null)}
        />
      )}
    </section>
  );
}

type GroupProps = {
  group: DraftGroup;
  unticked: Set<string>;
  onGroup: (on: boolean) => void;
  children: (video: DraftVideo) => ReactNode;
};

// One clipping channel's drafts, by source video, with a box that ticks all of them (or, once all are
// ticked, none).
function Group({ group, unticked, onGroup, children }: GroupProps) {
  const id = useId();
  const box = useRef<HTMLInputElement>(null);
  const name = groupName(group);
  const state = groupTick(group, unticked);
  useEffect(() => {
    if (box.current) box.current.indeterminate = state === "some";
  });
  return (
    <div
      role="group"
      aria-labelledby={id}
      className="flex flex-col gap-3 rounded-card border border-line p-4"
    >
      <div className="flex items-center gap-3">
        <input
          ref={box}
          type="checkbox"
          aria-label={`Select all in ${name}`}
          checked={state === "all"}
          onChange={() => onGroup(state !== "all")}
          className="size-4 cursor-pointer accent-accent-strong"
        />
        <h3 id={id} dir="auto" className="text-sm font-semibold">
          {name}
        </h3>
      </div>
      {group.videos.map((video) => (
        <div key={video.project.id} className="flex flex-col gap-2">
          <div className="flex items-center gap-3">
            <Thumb
              src={video.project.thumb}
              className="aspect-video w-24 shrink-0 rounded-control"
            />
            <div className="min-w-0">
              <h4 dir="auto" className="text-sm font-medium">
                {video.project.title || "Untitled video"}
              </h4>
              <Source video={video} />
            </div>
          </div>
          {children(video)}
        </div>
      ))}
    </div>
  );
}

// The source video's YouTube channel and when it went up.
function Source({ video }: { video: DraftVideo }) {
  const { channel, published_at } = video.project;
  const parts = [
    channel,
    published_at
      ? `published ${formatAgo(published_at, Date.now() / 1000)}`
      : null,
  ];
  const text = parts.filter(Boolean).join(", ");
  return text ? (
    <p dir="auto" className="text-xs text-muted">
      {text}
    </p>
  ) : null;
}
