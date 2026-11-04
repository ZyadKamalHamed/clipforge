// One draft as Today's Clips to review shows it (SPEC 14.9 and 14.10 item 3): its clip, which plays muted
// while the pointer is on it (the page lets one card play at a time) until its sound is turned on, its
// score and why it was picked, the account it goes to, its caption (edited in place and saved as you leave
// the field), its slot and warnings, the campaign it is for with its rules and Unlink from campaign
// (10.10), and Edit clip, Approve and Discard.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Lightbulb, Volume2, VolumeX } from "lucide-react";
import { memo, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api, key, sentence } from "../api.ts";
import { accountName } from "../lib/accounts.ts";
import { draftTitle, listedWarnings, withCaption } from "../lib/drafts.ts";
import { dayThenTime } from "../lib/plan.ts";
import type { Account, Draft, DraftsBody, QueuedPost } from "../types.ts";
import { Avatar } from "./AccountCard.tsx";
import { Button, buttonClass } from "./Button.tsx";
import { FieldError } from "./Field.tsx";
import { RuleList } from "./RuleList.tsx";
import { ScoreBadge } from "./ScoreBadge.tsx";
import { useToast } from "./Toasts.tsx";

type Props = {
  draft: Draft;
  account: Account | undefined;
  ticked: boolean;
  playing: boolean;
  now: number;
  onTick: (id: string, on: boolean) => void;
  onHover: (id: string, on: boolean) => void;
  onApprove: (draft: Draft) => void;
  onDiscard: (draft: Draft) => void;
};

const ACTION = "px-2.5 py-1 text-xs";

export const DraftCard = memo(function DraftCard(props: Props) {
  const { draft, account, ticked, playing, now, onTick, onHover, onApprove, onDiscard } = props;
  const captionId = useId();
  const problemId = useId();
  const client = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState<string | null>(null); // the caption while it's being edited
  const [problem, setProblem] = useState<string | null>(null);
  const [sound, setSound] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const who = account ? accountName(account) : (draft.account_name ?? "a removed account");
  const name = `${draftTitle(draft)} to ${who}`;
  const picture = draft.media?.poster ?? draft.media?.thumb ?? null;
  const slot = draft.slot_moved?.at ?? draft.scheduled_at;

  const save = useMutation({
    mutationFn: (caption: string) =>
      api<{ post: QueuedPost }>(`/api/posts/${encodeURIComponent(draft.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ caption }),
      }),
    onSuccess: ({ post }) => {
      client.setQueryData<DraftsBody>(key.drafts, (old) => old && withCaption(old, draft.id, post.caption));
      setText(null);
      setProblem(null);
    },
    onError: (err) => setProblem(sentence(err)),
  });

  // Off its campaign it is an ordinary draft: no rules to keep, and no submission once it's posted.
  const unlink = useMutation({
    mutationFn: (off: boolean) =>
      api<unknown>(`/api/posts/${encodeURIComponent(draft.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ off_campaign: off }),
      }),
    onSuccess: (_, off) => {
      if (off && draft.campaign) {
        toast({
          text: `Unlinked from ${draft.campaign.name}`,
          kind: "ok",
          action: { label: "Undo", run: () => unlink.mutate(false) },
        });
      }
    },
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => void client.invalidateQueries({ queryKey: key.drafts }),
  });
  const warnings = listedWarnings(draft);

  useEffect(() => {
    if (video.current) video.current.muted = !sound; // React sets it only as the video first shows
  });

  function leave() {
    if (text === null || save.isPending) return;
    if (text === draft.caption) setText(null);
    else save.mutate(text);
  }

  return (
    <article
      aria-label={name}
      onPointerEnter={() => onHover(draft.id, true)}
      onPointerLeave={() => {
        onHover(draft.id, false);
        setSound(false);
      }}
      className={
        "flex gap-4 rounded-card border bg-panel p-3 " + (ticked ? "border-accent" : "border-line hover:border-faint")
      }
    >
      <div className="relative aspect-[9/16] w-32 shrink-0 overflow-hidden rounded-control bg-panel-2">
        <Picture src={picture} />
        {playing && draft.media?.video && (
          <video
            ref={video}
            src={draft.media.video}
            poster={picture ?? undefined}
            muted={!sound}
            loop
            playsInline
            autoPlay
            aria-hidden
            className="absolute inset-0 size-full object-cover"
          />
        )}
        <span className="absolute top-1.5 left-1.5 grid size-7 place-items-center rounded-control bg-black/60">
          <input
            type="checkbox"
            aria-label={`Select ${name}`}
            checked={ticked}
            onChange={(e) => onTick(draft.id, e.target.checked)}
            className="size-4 cursor-pointer accent-accent-strong"
          />
        </span>
        <span className="absolute top-1.5 right-1.5">
          <ScoreBadge score={draft.score} tier={null} />
        </span>
        {playing && draft.media?.video && (
          <button
            type="button"
            aria-label={sound ? "Turn the sound off" : "Turn the sound on"}
            onClick={() => setSound((on) => !on)}
            className="absolute right-1.5 bottom-1.5 grid size-7 place-items-center rounded-full bg-black/70 text-white"
          >
            {sound ? <Volume2 aria-hidden size={14} /> : <VolumeX aria-hidden size={14} />}
          </button>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <h5 dir="auto" className="text-sm font-semibold break-words">
            {draftTitle(draft)}
          </h5>
          <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
            {account && <Avatar account={account} name={who} size="small" />}
            <span dir="auto">{who}</span>
          </span>
        </div>
        {draft.reasons.map((reason) => (
          <p key={reason} dir="auto" className="line-clamp-2 text-xs text-muted">
            {reason}
          </p>
        ))}
        <label htmlFor={captionId} className="sr-only">
          Caption
        </label>
        <textarea
          id={captionId}
          dir="auto"
          rows={3}
          value={text ?? draft.caption}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? problemId : undefined}
          onChange={(e) => setText(e.target.value)}
          onBlur={leave}
          className={
            "w-full resize-y rounded-control border border-line bg-bg px-2.5 py-1.5 text-sm text-fg " +
            "hover:border-faint focus-visible:border-accent"
          }
        />
        {problem && <FieldError id={problemId} text={problem} />}
        {draft.hashtags.length > 0 && (
          <p dir="auto" className="text-xs text-muted">
            {draft.hashtags.join(" ")}
          </p>
        )}
        <p className="text-xs text-muted">
          <span data-slot className="font-medium text-fg tabular-nums">
            {dayThenTime(slot, now)}
          </span>
        </p>
        {draft.slot_moved && <p className="text-xs text-warn">{draft.slot_moved.sentence}</p>}
        {draft.campaign && (
          <div className="flex flex-col gap-1.5 rounded-control border border-line bg-panel-2 px-2.5 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted">
                For{" "}
                <Link
                  to={`/campaigns/${encodeURIComponent(draft.campaign.id)}`}
                  className="font-medium text-fg underline underline-offset-2"
                  dir="auto"
                >
                  {draft.campaign.name}
                </Link>
              </p>
              <Button
                variant="ghost"
                className={ACTION}
                onClick={() => unlink.mutate(true)}
                disabled={unlink.isPending}
              >
                Unlink from campaign
              </Button>
            </div>
            {draft.rules.length > 0 && <RuleList draft={draft} campaign={draft.campaign.name} />}
          </div>
        )}
        {warnings.length > 0 && (
          <ul className="flex flex-col gap-1">
            {warnings.map((w) => (
              <li key={`${w.code}:${w.sentence}`} className={`text-xs ${w.blocking ? "text-bad" : "text-warn"}`}>
                {w.sentence}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          <Button variant="primary" className={ACTION} onClick={() => onApprove(draft)}>
            Approve
          </Button>
          <Button variant="ghost" className={ACTION} onClick={() => onDiscard(draft)}>
            Discard
          </Button>
          <Link to={`/clips/${encodeURIComponent(draft.clip_id)}`} className={buttonClass("ghost", ACTION)}>
            Edit clip
          </Link>
        </div>
      </div>
    </article>
  );
});

// The clip's poster; until there is one, an idea's mark.
function Picture({ src }: { src: string | null }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (src && broken !== src) {
    return (
      <img
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setBroken(src)}
        className="size-full object-cover"
      />
    );
  }
  return (
    <span className="grid size-full place-items-center text-faint">
      <Lightbulb aria-hidden size={24} />
    </span>
  );
}
