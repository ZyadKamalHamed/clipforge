// One clip as the Project page shows it (SPEC 14.4): a 9:16 card with its picture, which plays muted while
// the pointer is on it (the page lets one card play at a time), its score, length, layout and flags, then
// its title, where it is in the video, why it was picked and where it is now. The title opens the editor
// from anywhere on the card; the checkbox and the buttons sit above that. Cards are memoised, so only the
// ones that changed draw again.

import { Flag as FlagIcon, Lightbulb } from "lucide-react";
import { Tooltip } from "radix-ui";
import { memo, type ReactNode, useId, useState } from "react";
import { Link } from "react-router";

import { canRender, clipStatus, clipTitle, timeRange } from "../lib/clips.ts";
import { formatDuration } from "../lib/format.ts";
import { LAYOUTS } from "../lib/options.ts";
import type { Clip, Flag } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { ScoreBadge } from "./ScoreBadge.tsx";

type Props = {
  clip: Clip;
  selected: boolean;
  playing: boolean;
  progress: number | null; // its running job's, from the live jobs
  onToggle: (id: string, extend: boolean) => void;
  onHover: (id: string, on: boolean) => void;
  onRender: (id: string) => void;
  onRestore: (id: string) => void;
};

const ACTION = "relative z-10 px-2.5 py-1 text-xs";

export const ClipCard = memo(function ClipCard(props: Props) {
  const {
    clip,
    selected,
    playing,
    progress,
    onToggle,
    onHover,
    onRender,
    onRestore,
  } = props;
  const titleId = useId();
  const title = clipTitle(clip);
  const status = clipStatus(clip);
  const picture = clip.render?.poster ?? clip.render?.thumb ?? null;
  const video = clip.render?.video ?? null;
  const layout = clip.layout
    ? (LAYOUTS as Record<string, string>)[clip.layout]
    : undefined;
  const working =
    !clip.discarded &&
    (clip.status === "analyzing" || clip.status === "rendering");

  let action: ReactNode = null;
  if (clip.discarded) {
    action = (
      <Button className={ACTION} onClick={() => onRestore(clip.id)}>
        Restore
      </Button>
    );
  } else if (
    canRender(clip) &&
    (clip.status === "idea" || clip.status === "packaged")
  ) {
    action = (
      <Button
        variant="primary"
        className={ACTION}
        onClick={() => onRender(clip.id)}
      >
        Render
      </Button>
    );
  } else if (canRender(clip) && clip.status === "failed") {
    action = (
      <Button className={ACTION} onClick={() => onRender(clip.id)}>
        Try again
      </Button>
    );
  }

  return (
    <article
      aria-labelledby={titleId}
      onPointerEnter={() => onHover(clip.id, true)}
      onPointerLeave={() => onHover(clip.id, false)}
      className={
        "relative flex flex-col overflow-hidden rounded-card border bg-panel " +
        (selected
          ? "border-accent ring-1 ring-accent"
          : "border-line hover:border-faint")
      }
    >
      <div className="relative aspect-[9/16] overflow-hidden bg-panel-2">
        <Picture src={picture} faded={clip.discarded} />
        {playing && video && (
          <video
            src={video}
            poster={picture ?? undefined}
            muted
            loop
            playsInline
            autoPlay
            aria-hidden
            className="absolute inset-0 size-full object-cover"
          />
        )}
        <span className="absolute top-2 left-2 z-10 grid size-7 place-items-center rounded-control bg-black/60">
          <input
            type="checkbox"
            aria-label={`Select ${title}`}
            checked={selected}
            onChange={(e) =>
              onToggle(clip.id, (e.nativeEvent as MouseEvent).shiftKey === true)
            }
            className="size-4 cursor-pointer accent-accent-strong"
          />
        </span>
        <span className="absolute top-2 right-2">
          <ScoreBadge score={clip.final_score} tier={clip.tier} />
        </span>
        <span className="absolute inset-x-2 bottom-2 flex flex-wrap items-center gap-1.5">
          <OnPicture>
            <span className="tabular-nums">
              {formatDuration(clip.duration_s)}
            </span>
          </OnPicture>
          {clip.as_is ? (
            <OnPicture>As it is</OnPicture>
          ) : (
            layout && <OnPicture>{layout}</OnPicture>
          )}
          {clip.flags.length > 0 && <Flags flags={clip.flags} />}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <h3
          id={titleId}
          dir="auto"
          className="line-clamp-2 text-sm font-semibold break-words"
        >
          {/* Its box covers the whole card, so the card opens the editor from anywhere on it. */}
          <Link
            to={`/clips/${clip.id}`}
            className="rounded-sm after:absolute after:inset-0"
          >
            {title}
          </Link>
        </h3>
        <p className="text-xs text-muted tabular-nums">{timeRange(clip)}</p>
        {clip.reason && (
          <p dir="auto" className="line-clamp-2 text-xs text-muted">
            {clip.reason}
          </p>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          <Badge tone={status.tone}>{status.text}</Badge>
          {clip.posted_to.length > 0 && (
            <Badge tone="ok">Posted to {clip.posted_to.length}</Badge>
          )}
          {working && progress !== null && (
            <span className="text-xs text-muted tabular-nums">
              {Math.round(Math.min(1, Math.max(0, progress)) * 100)}%
            </span>
          )}
          {action}
        </div>
        {clip.status === "failed" && clip.error && !clip.discarded && (
          <p dir="auto" className="text-xs text-muted">
            {clip.error}
          </p>
        )}
        {(clip.edited || clip.stale) && (
          <div className="flex flex-wrap gap-1.5">
            {clip.edited && <Badge>Edited</Badge>}
            {clip.stale && (
              <Badge tone="warn">Re-render to see your edits</Badge>
            )}
          </div>
        )}
      </div>
    </article>
  );
});

// The clip's poster, loaded only as it scrolls near; until it's rendered, an idea's mark instead.
// A discarded clip's picture fades, and only the picture: the words on and under it stay readable, and its
// badge and Restore say why.
function Picture({ src, faded }: { src: string | null; faded: boolean }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (src && broken !== src) {
    return (
      <img
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setBroken(src)}
        className={"size-full object-cover" + (faded ? " opacity-50" : "")}
      />
    );
  }
  return (
    <span
      className={
        "grid size-full place-items-center text-faint" +
        (faded ? " opacity-50" : "")
      }
    >
      <Lightbulb aria-hidden size={28} />
    </span>
  );
}

function OnPicture({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-black/70 px-2 py-0.5 text-xs font-medium text-white">
      {children}
    </span>
  );
}

// How many flags the clip has, with each one's sentence on hover or focus, the red ones first.
function Flags({ flags }: { flags: Flag[] }) {
  const red = flags.some((flag) => flag.level === "red");
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          className="relative z-10 inline-flex items-center gap-1 rounded-full bg-black/70 px-2 py-0.5 text-xs font-medium text-white"
        >
          <FlagIcon
            aria-hidden
            size={12}
            className={red ? "text-bad" : "text-warn"}
          />
          {flags.length === 1 ? "1 flag" : `${flags.length} flags`}
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={6}
          className="z-50 max-w-64 rounded-control border border-line bg-panel px-3 py-2 text-xs text-fg shadow-xl"
        >
          <ul className="flex flex-col gap-1">
            {flags.map((flag) => (
              <li key={flag.code} dir="auto" className="flex gap-1.5">
                <span
                  aria-hidden
                  className={`mt-1 size-1.5 shrink-0 rounded-full ${flag.level === "red" ? "bg-bad" : "bg-warn"}`}
                />
                {flag.text}
              </li>
            ))}
          </ul>
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
