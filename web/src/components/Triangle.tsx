// The warning triangle on a campaign's row (SPEC 14.11): what the campaign waits on today, a line a thing,
// each with its one button. Hovering or focusing it opens the list beside it, and Tab goes on into it; Esc or a
// click elsewhere closes it. A campaign's page shows the same lines at its top. A campaign to join has Open
// and I've joined on its line instead of a link.

import { TriangleAlert } from "lucide-react";
import { Popover } from "radix-ui";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { triangleLines, triangleName } from "../lib/campaigns.ts";
import type { Campaign } from "../types.ts";
import { buttonClass } from "./Button.tsx";
import { JoinButtons } from "./JoinButtons.tsx";

const LEAVE_MS = 200; // long enough to cross from the triangle to its list

type Line = { title: string; label: string; to: string | null; join?: true };

export function Triangle({
  campaign,
}: {
  campaign: Pick<Campaign, "id" | "name" | "todo">;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const lines = triangleLines(campaign);
  if (lines.length === 0) return null;

  const stay = () => window.clearTimeout(timer.current);
  const show = () => {
    stay();
    setOpen(true);
  };
  // The pointer gone, the list closes unless the focus is in it or on the triangle.
  const leave = () => {
    stay();
    timer.current = window.setTimeout(() => {
      const at = document.activeElement;
      if (at !== trigger.current && !content.current?.contains(at))
        setOpen(false);
    }, LEAVE_MS);
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        stay();
        setOpen(next);
      }}
    >
      <Popover.Trigger asChild>
        <button
          ref={trigger}
          type="button"
          aria-label={triangleName(lines.length)}
          onPointerEnter={show}
          onPointerLeave={leave}
          onFocus={show}
          onClick={(e) => {
            e.preventDefault(); // a click keeps it open rather than closing it
            show();
          }}
          className="inline-flex rounded-control p-0.5 text-warn hover:bg-panel-2"
        >
          <TriangleAlert aria-hidden size={18} />
        </button>
      </Popover.Trigger>
      {/* Not in a portal, so Tab from the triangle goes into the list; it is drawn over the sheet all the same. */}
      <Popover.Content
        ref={content}
        aria-labelledby={id}
        align="start"
        sideOffset={6}
        onPointerEnter={stay}
        onPointerLeave={leave}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()} // focus put back on the triangle would open it again
        onEscapeKeyDown={() => {
          if (content.current?.contains(document.activeElement))
            trigger.current?.focus();
        }}
        className="z-50 flex w-80 flex-col gap-2 rounded-card border border-line bg-panel p-3 text-sm shadow-2xl"
      >
        <p id={id} className="text-xs font-medium text-muted">
          <span dir="auto">{campaign.name}</span> needs you
        </p>
        <NeedsLines campaignId={campaign.id} lines={lines} />
      </Popover.Content>
    </Popover.Root>
  );
}

// The same lines at the top of a campaign's page. A line whose button would open this page goes to its
// submissions instead, where views and decisions go in; one for an ending campaign keeps no button here.
export function NeedsList({
  campaign,
}: {
  campaign: Pick<Campaign, "id" | "todo">;
}) {
  const id = useId();
  const page = `/campaigns/${encodeURIComponent(campaign.id)}`;
  const lines = triangleLines(campaign).map((line, i) =>
    line.to !== page || line.join
      ? line
      : {
          ...line,
          to: campaign.todo[i].kind === "ends" ? null : "#submissions",
        },
  );
  if (lines.length === 0) return null;
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-3 rounded-card border border-warn/40 bg-panel p-4"
    >
      <h2 id={id} className="flex items-center gap-2 text-base font-semibold">
        <TriangleAlert aria-hidden size={18} className="text-warn" />
        Needs you
      </h2>
      <NeedsLines campaignId={campaign.id} lines={lines} />
    </section>
  );
}

function NeedsLines({
  campaignId,
  lines,
}: {
  campaignId: string;
  lines: Line[];
}) {
  const action = buttonClass("secondary", "shrink-0");
  return (
    <ul className="flex flex-col gap-2">
      {lines.map((line, i) => (
        // its place is its key: a title can repeat (two accounts with one handle) and the lines never move
        <li key={i} className="flex items-center justify-between gap-3">
          <span dir="auto" className="min-w-0">
            {line.title}
          </span>
          {line.join ? (
            <JoinButtons campaignId={campaignId} />
          ) : line.to?.startsWith("#") ? (
            <a href={line.to} className={action}>
              {line.label}
            </a>
          ) : line.to ? (
            <Link to={line.to} className={action}>
              {line.label}
            </Link>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
