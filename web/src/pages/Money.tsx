// The Money page (SPEC 14.12): what your campaign posts earn over the period you pick. Totals a line a
// currency, never added across currencies; the timeline of earnings in US$ split by marketplace or account;
// a row for every account, the ones with posts first; and the posts themselves (MoneyPosts). Every sum is worked here from GET /api/money's
// rows (lib/money.ts), so the page, its tooltips and its CSV always agree.

import { useQuery } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { Fragment, useId, useState } from "react";
import { Link } from "react-router";

import { sentence } from "../api.ts";
import { Choice } from "../components/Choice.tsx";
import { Empty } from "../components/Empty.tsx";
import { Hook, MoneyPosts } from "../components/MoneyPosts.tsx";
import { Timeline } from "../components/Timeline.tsx";
import { PLATFORMS } from "../lib/accounts.ts";
import { count, money } from "../lib/campaigns.ts";
import {
  accountRows,
  addAmounts,
  type Amount,
  DEFAULT_PERIOD,
  inPeriod,
  NO_NICHE,
  type Period,
  PERIODS,
  periodStart,
  REMOVED,
  totals,
} from "../lib/money.ts";
import { moneyQuery } from "../state.ts";
import type { MoneyBody, MoneyPost } from "../types.ts";

const ABOUT =
  "What your campaign posts earn: paid is what you entered, estimated and pending are ClipForge's estimates from the views.";
const EMPTY = "No campaign posts yet";
const EMPTY_TEXT =
  "Money fills in once a post on a campaign goes up: its views, what it should earn and what you're paid.";
const CELL = "px-3 py-2.5 align-top";
const NUMBER = `${CELL} text-right tabular-nums whitespace-nowrap`;
const AMOUNTS = `${CELL} min-w-24 text-right tabular-nums`;
const HEAD = "px-3 py-2 font-medium whitespace-nowrap";
const NONE = <span className="text-faint">None</span>;
const NONE_YET = <span className="text-faint">None yet</span>;

export function Money() {
  const query = useQuery(moneyQuery);
  const [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
  const any = (query.data?.posts.length ?? 0) > 0;

  let body;
  if (query.isPending)
    body = <p className="text-sm text-muted">Loading your money.</p>;
  else if (query.isError)
    body = <p className="text-sm text-bad">{sentence(query.error)}</p>;
  else if (!any)
    body = (
      <Empty
        icon={Wallet}
        title={EMPTY}
        action={
          <Link
            to="/campaigns?tab=briefs"
            className="text-sm text-fg underline underline-offset-2"
          >
            Find a campaign on the Briefs tab
          </Link>
        }
      >
        <p>{EMPTY_TEXT}</p>
      </Empty>
    );
  else body = <Sheets data={query.data} period={period} onPeriod={setPeriod} />;

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">Money</h1>
        {any && <p className="text-sm text-muted">{ABOUT}</p>}
      </div>
      {body}
    </div>
  );
}

type SheetsProps = {
  data: MoneyBody;
  period: Period;
  onPeriod: (period: Period) => void;
};

function Sheets({ data, period, onPeriod }: SheetsProps) {
  const start = periodStart(period, data.today, data.posts);
  const posts = inPeriod(data.posts, start, data.today);
  return (
    <>
      <Choice
        legend="Period"
        name="period"
        value={period}
        options={PERIODS}
        onChange={onPeriod}
      />
      <Totals posts={posts} />
      <Timeline
        posts={data.posts}
        start={start}
        today={data.today}
        accounts={data.accounts}
      />
      <Accounts posts={posts} accounts={data.accounts} />
      <MoneyPosts
        posts={posts}
        accounts={data.accounts}
        start={start}
        today={data.today}
      />
    </>
  );
}

// A line a currency: what was paid, what is pending and estimated, the views and the posts.
function Totals({ posts }: { posts: MoneyPost[] }) {
  const id = useId();
  const lines = totals(posts);
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="text-base font-semibold">
        Totals
      </h2>
      {lines.length === 0 ? (
        <p className="text-sm text-muted">No campaign posts in this period.</p>
      ) : (
        <div className="overflow-x-auto rounded-card border border-line bg-panel">
          <table aria-labelledby={id} className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                <th scope="col" className={HEAD}>
                  Currency
                </th>
                {["Paid", "Pending", "Estimated", "Views", "Posts"].map(
                  (label) => (
                    <th
                      key={label}
                      scope="col"
                      className={`${HEAD} text-right`}
                    >
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr
                  key={line.currency}
                  className="border-b border-line last:border-0"
                >
                  <th scope="row" className={`${CELL} font-medium`}>
                    {line.currency}
                  </th>
                  <td className={NUMBER}>{money(line.paid, line.currency)}</td>
                  <td className={NUMBER}>
                    {line.pending === null
                      ? NONE
                      : money(line.pending, line.currency)}
                  </td>
                  <td className={NUMBER}>
                    {line.estimated === null
                      ? NONE
                      : money(line.estimated, line.currency)}
                  </td>
                  <td className={NUMBER}>
                    {line.views === null ? NONE_YET : count(line.views)}
                  </td>
                  <td className={NUMBER}>{count(line.posts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Each currency's amount kept whole with the "+" after it, the line free to wrap between them, so the table
// fits a wide window.
const shownAmounts = (list: Amount[]) =>
  list.length > 0
    ? list.map((a, i) => (
        <Fragment key={a.currency}>
          {i > 0 && " "}
          <span className="whitespace-nowrap">
            {money(a.amount, a.currency)}
            {i < list.length - 1 && " +"}
          </span>
        </Fragment>
      ))
    : NONE;

// A row an account, with what its posts in the period earned and its best post, and a Total row.
function Accounts({
  posts,
  accounts,
}: {
  posts: MoneyPost[];
  accounts: MoneyBody["accounts"];
}) {
  const id = useId();
  const rows = accountRows(accounts, posts);
  const views = rows.reduce<number | null>(
    (sum, row) => (row.views === null ? sum : (sum ?? 0) + row.views),
    null,
  );
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="text-base font-semibold">
        Accounts
      </h2>
      <div className="overflow-x-auto rounded-card border border-line bg-panel">
        <table aria-labelledby={id} className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              {["Platform", "Account", "Niche"].map((label) => (
                <th key={label} scope="col" className={HEAD}>
                  {label}
                </th>
              ))}
              {[
                "Posts",
                "Views",
                "Median 7-day views",
                "Estimated",
                "Pending",
                "Paid",
              ].map((label) => (
                <th key={label} scope="col" className={`${HEAD} text-right`}>
                  {label}
                </th>
              ))}
              <th scope="col" className={HEAD}>
                Best post
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.account.id}
                className="border-b border-line last:border-0"
              >
                <td className={CELL}>{PLATFORMS[row.account.platform]}</td>
                <th scope="row" className={`${CELL} font-medium`} dir="auto">
                  {row.account.handle ?? REMOVED}
                </th>
                <td className={CELL} dir="auto">
                  {row.account.niche ?? (
                    <span className="text-faint">{NO_NICHE}</span>
                  )}
                </td>
                <td className={NUMBER}>{count(row.posts)}</td>
                <td className={NUMBER}>
                  {row.views === null ? NONE_YET : count(row.views)}
                </td>
                <td className={NUMBER}>
                  {row.median7 === null
                    ? NONE_YET
                    : count(Math.round(row.median7))}
                </td>
                <td className={AMOUNTS}>{shownAmounts(row.estimated)}</td>
                <td className={AMOUNTS}>{shownAmounts(row.pending)}</td>
                <td className={AMOUNTS}>{shownAmounts(row.paid)}</td>
                <td className={`${CELL} min-w-40`}>
                  {row.best ? <Hook post={row.best} /> : NONE}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-line bg-panel-2">
            <tr>
              <th scope="row" colSpan={3} className={`${CELL} font-medium`}>
                Total
              </th>
              <td className={NUMBER}>
                {count(rows.reduce((sum, row) => sum + row.posts, 0))}
              </td>
              <td className={NUMBER}>
                {views === null ? NONE_YET : count(views)}
              </td>
              <td className={NUMBER} />
              <td className={AMOUNTS}>
                {shownAmounts(addAmounts(rows.map((row) => row.estimated)))}
              </td>
              <td className={AMOUNTS}>
                {shownAmounts(addAmounts(rows.map((row) => row.pending)))}
              </td>
              <td className={AMOUNTS}>
                {shownAmounts(addAmounts(rows.map((row) => row.paid)))}
              </td>
              <td className={CELL} />
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
