// The Money page's timeline (SPEC 14.12): what the period earned in US$, a bar a day or, past 60 days, a
// week, each bar stacked by marketplace or account with paid solid and estimated the same colour faded.
// Every bar is a button whose tooltip and name read its figures, so nothing here needs the mouse or the
// colours. A series keeps its colour through every period (seriesSlots), from the validated palette in
// app.css; Others is grey.

import { Tooltip } from "radix-ui";
import { useId, useLayoutEffect, useRef, useState } from "react";

import { Choice } from "./Choice.tsx";
import { dayLabel, money } from "../lib/campaigns.ts";
import {
  asIfNote,
  type Bar,
  barText,
  earnings,
  inPeriod,
  periodStart,
  seriesSlots,
  type Split,
  timeline,
} from "../lib/money.ts";
import type { MoneyAccount, MoneyPost } from "../types.ts";

const SPLITS: { value: Split; label: string }[] = [
  { value: "marketplace", label: "Marketplace" },
  { value: "account", label: "Account" },
];
const TICK_EVERY = 7; // bars between the dates under the axis, counted back from the newest

type Colour = (key: string) => string;
const faded = (colour: string) =>
  `color-mix(in oklab, ${colour} 50%, var(--panel))`;

type Props = {
  posts: MoneyPost[]; // every post, the history the colours are kept through
  start: string;
  today: string;
  accounts: MoneyAccount[];
};

export function Timeline({ posts, start, today, accounts }: Props) {
  const id = useId();
  const [split, setSplit] = useState<Split>("marketplace");
  const shown = timeline(posts, start, today, split, accounts);
  const ever = timeline(
    posts,
    periodStart("all", today, posts),
    today,
    split,
    accounts,
  );
  const slots = seriesSlots(shown.series, ever.ranked);
  const colour: Colour = (key) =>
    key in slots ? `var(--series-${slots[key] + 1})` : "var(--series-other)";
  const top = Math.max(0, ...shown.bars.map((bar) => bar.total));
  const note = asIfNote(
    [
      ...new Set(
        inPeriod(posts, start, today)
          .filter((p) => p.as_if)
          .map((p) => p.currency),
      ),
    ].sort(),
  );
  const last = shown.bars.length - 1;
  const row = useRef<HTMLDivElement>(null);
  // When the bars don't fit, the row opens at its newest end: today shows first, and focusing a bar there
  // doesn't scroll the row, which would close the bar's tooltip.
  useLayoutEffect(() => {
    if (row.current) row.current.scrollLeft = row.current.scrollWidth;
  }, [start, shown.step, shown.bars.length]);

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id={id} className="text-base font-semibold">
            {shown.step === "week" ? "Earnings by week" : "Earnings by day"}
          </h2>
          <p className="text-sm text-muted">
            In US$ through your exchange rates.
          </p>
          {note && <p className="text-sm text-muted">{note}</p>}
        </div>
        <Choice
          legend="Split by"
          name="split"
          value={split}
          options={SPLITS}
          onChange={setSplit}
        />
      </div>
      <div className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4">
        <Tooltip.Provider delayDuration={200}>
          <div className="flex items-stretch gap-3">
            <div
              aria-hidden
              className="flex h-40 flex-col justify-between text-right text-xs text-muted tabular-nums"
            >
              <span>{top > 0 ? money(top, "USD") : ""}</span>
              <span>{money(0, "USD")}</span>
            </div>
            <div ref={row} className="min-w-0 flex-1 overflow-x-auto pb-1">
              <div className="flex w-max min-w-full flex-col">
                <div className="flex h-40 items-end border-y border-line">
                  {shown.bars.map((bar) => (
                    <Column
                      key={bar.start}
                      bar={bar}
                      top={top}
                      colour={colour}
                    />
                  ))}
                </div>
                <div aria-hidden className="flex h-5 pt-1">
                  {shown.bars.map((bar, i) => (
                    <span
                      key={bar.start}
                      className="min-w-7 flex-1 text-xs whitespace-nowrap text-muted"
                    >
                      {(last - i) % TICK_EVERY === 0 ? dayLabel(bar.start) : ""}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </Tooltip.Provider>
        {top === 0 && (
          <p className="text-sm text-muted">
            Nothing earned in this period yet.
          </p>
        )}
        {shown.series.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
            <ul aria-label="Series" className="flex flex-wrap gap-x-4 gap-y-1">
              {shown.series.map((s) => (
                <li key={s.key} className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="size-2.5 rounded-sm"
                    style={{ background: colour(s.key) }}
                  />
                  <span dir="auto">{s.name}</span>
                </li>
              ))}
            </ul>
            <p className="text-muted">Solid is paid, faded is estimated.</p>
          </div>
        )}
      </div>
    </section>
  );
}

type ColumnProps = { bar: Bar; top: number; colour: Colour };

// One bar and the space around it, the whole column its button, with the tooltip on hover and focus.
function Column({ bar, top, colour }: ColumnProps) {
  const segments = bar.parts.flatMap((part) => [
    { key: `${part.key} paid`, value: part.paid, fill: colour(part.key) },
    {
      key: `${part.key} estimated`,
      value: part.estimated,
      fill: faded(colour(part.key)),
    },
  ]);
  const height = top > 0 ? (bar.total / top) * 100 : 0;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* The name already reads the figures, so the tooltip isn't added as its description too. */}
        <button
          type="button"
          aria-label={barText(bar)}
          aria-describedby={undefined}
          className="group flex h-full min-w-7 flex-1 items-end justify-center rounded-sm hover:bg-panel-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span
            className="flex w-4 flex-col-reverse gap-0.5 overflow-hidden rounded-t-[4px] group-hover:brightness-110"
            style={{ height: `${height}%` }}
          >
            {segments
              .filter((s) => s.value > 0)
              .map((s) => (
                <span
                  key={s.key}
                  className="w-full"
                  style={{ flex: `${s.value} 1 0`, background: s.fill }}
                />
              ))}
          </span>
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={6}
          className="z-50 max-w-64 rounded-control border border-line bg-panel px-3 py-2 text-xs text-fg shadow-xl"
        >
          <Figures bar={bar} colour={colour} />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

// The tooltip: the bar's date, its figures and then each part's, a short line of its colour beside it.
function Figures({ bar, colour }: { bar: Bar; colour: Colour }) {
  const overall = earnings(
    bar.parts.reduce((sum, p) => sum + p.estimated, 0),
    bar.parts.reduce((sum, p) => sum + p.paid, 0),
  );
  const parts = bar.parts
    .map((part) => ({ part, said: earnings(part.estimated, part.paid) }))
    .filter(({ said }) => said.length > 0);
  return (
    <div className="flex flex-col gap-1">
      <p className="text-muted">{bar.label}</p>
      <p className="font-semibold tabular-nums">
        {overall.length > 0 ? overall.join(", ") : "Nothing earned"}
      </p>
      {parts.map(({ part, said }) => (
        <p key={part.key} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-0.5 w-3 shrink-0 rounded-full"
            style={{ background: colour(part.key) }}
          />{" "}
          <span className="text-muted">{part.name}</span>{" "}
          <span className="tabular-nums">{said.join(" and ")}</span>
        </p>
      ))}
    </div>
  );
}
