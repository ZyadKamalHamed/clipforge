// The path from nothing to your first payout on Today (SPEC 14.10): the level you are on out of six, the
// steps done, what you have earned, posted and linked so far, the account to make while you are on the
// first two levels, and each level's steps with where each stands. The level you are on, and any with a
// step on your list, is open and shows what those steps ask; the others fold. Steps and the account's words
// are only ever text.

import { Check, Circle, Hourglass, Lock } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";

import { money } from "../lib/campaigns.ts";
import {
  accountLines,
  levelLine,
  levelOpen,
  progressLine,
  stateLabel,
  stepLink,
  workShown,
} from "../lib/path.ts";
import type { PathLevel, PathOn, PathStep } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { buttonClass } from "./Button.tsx";

export function PathCard({ path }: { path: PathOn }) {
  const id = useId();
  const percent = path.total ? Math.round((path.done / path.total) * 100) : 0;
  const making = path.level !== null && path.level <= 2;
  const figures = [
    {
      label: "Earned",
      value: money(path.metrics.earned, path.metrics.currency),
    },
    { label: "Posts live", value: String(path.metrics.posts) },
    { label: "Accounts", value: String(path.metrics.accounts) },
  ];
  return (
    <section
      id="path"
      aria-labelledby={id}
      className="flex scroll-mt-4 flex-col gap-4 rounded-card border border-line p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-1">
        <div className="flex flex-col gap-0.5">
          <h2 id={id} className="text-base font-semibold">
            Your path to first money
          </h2>
          <p className="text-sm text-muted">{levelLine(path)}</p>
        </div>
        <p className="text-sm text-muted tabular-nums">{progressLine(path)}</p>
      </div>
      <div
        role="progressbar"
        aria-label="Steps done"
        aria-valuemin={0}
        aria-valuemax={path.total}
        aria-valuenow={path.done}
        className="h-2 overflow-hidden rounded-full bg-line"
      >
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${percent}%` }}
        />
      </div>
      <dl className="grid grid-cols-3 gap-x-6 gap-y-1 text-sm">
        {figures.map((f) => (
          <div key={f.label}>
            <dt className="text-xs text-faint">{f.label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{f.value}</dd>
          </div>
        ))}
      </dl>
      {making && <Account path={path} />}
      <ol aria-label="Levels" className="flex flex-col gap-2">
        {path.levels.map((lv) => (
          <Level key={lv.n} level={lv} current={lv.n === path.level} />
        ))}
      </ol>
    </section>
  );
}

// The account to make first, its name ideas, bio, picture and tags to copy from.
function Account({ path }: { path: PathOn }) {
  const { account } = path;
  const what = account.broad
    ? "A broad clips account, as the money in the briefs is spread over many niches"
    : `A ${(account.niche ?? "").toLowerCase()} clips account, where most of the money in the briefs is`;
  return (
    <div className="flex flex-col gap-2 rounded-card bg-panel-2 p-3">
      <h3 className="text-sm font-semibold">Your first account</h3>
      <p dir="auto" className="text-sm text-muted">
        {what}
      </p>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        {accountLines(account).map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{name}</dt>
            <dd dir="auto" className="select-all">
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// One level: its name, goal and steps done, open while it is the one you are on or has a step on your list.
function Level({ level, current }: { level: PathLevel; current: boolean }) {
  const done = level.steps.filter((s) => s.state === "done").length;
  const all = done === level.steps.length;
  return (
    <li className="rounded-card border border-line">
      <details open={levelOpen(level, current)} className="group">
        <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
          <span className="font-medium">{`Level ${level.n}: ${level.name}`}</span>
          <span className="text-xs text-muted">{level.goal}</span>
          <span className="ml-auto flex items-center gap-2">
            {current && <Badge tone="accent">You are here</Badge>}
            {all && <Badge tone="ok">Done</Badge>}
            <span className="text-xs text-muted tabular-nums">{`${done} of ${level.steps.length}`}</span>
          </span>
        </summary>
        <ul
          aria-label={`Level ${level.n} steps`}
          className="flex flex-col divide-y divide-line border-t border-line"
        >
          {level.steps.map((s) => (
            <Step key={s.id} step={s} current={current} />
          ))}
        </ul>
      </details>
    </li>
  );
}

const ICONS = {
  done: Check,
  todo: Circle,
  listed: Circle,
  waiting: Hourglass,
  locked: Lock,
} as const;

// One step: where it stands and, while it is to do here or on your list, what it asks and the page it is
// done on.
function Step({ step, current }: { step: PathStep; current: boolean }) {
  const Icon = ICONS[step.state];
  const open = workShown(step, current);
  const link = step.to && open ? stepLink(step.to) : null;
  return (
    <li className="flex flex-wrap items-start gap-3 px-3 py-2">
      <Icon
        aria-hidden
        size={16}
        className={`mt-0.5 shrink-0 ${step.state === "done" ? "text-ok" : "text-faint"}`}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p
          dir="auto"
          className={`text-sm ${step.state === "locked" ? "text-muted" : "font-medium"}`}
        >
          {step.title}
        </p>
        {open && (
          <p dir="auto" className="text-xs whitespace-pre-line text-muted">
            {step.detail}
          </p>
        )}
        <p className="text-xs text-faint">{stateLabel(step)}</p>
      </div>
      {link && (
        <Link to={link.to} className={buttonClass("secondary", "shrink-0")}>
          {link.label}
        </Link>
      )}
    </li>
  );
}
