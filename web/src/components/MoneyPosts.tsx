// The Money page's posts (SPEC 14.12), last on the page. The filters come first and scope all under them:
// Best, the posts with the most views and that earned the most; the period by platform and by niche in
// US$; and the sheet, a row a post, every column sorting. Export CSV saves every row the filters keep, in
// the order shown, not only the first 500 the sheet draws.

import { ArrowDown, ArrowUp, Download } from "lucide-react";
import { useId, useState } from "react";

import { SELECT } from "./AddSource.tsx";
import { Button } from "./Button.tsx";
import { PLATFORMS } from "../lib/accounts.ts";
import {
  count,
  dayLabel,
  fold,
  money,
  NAMES,
  type SortDir,
} from "../lib/campaigns.ts";
import {
  best,
  byNiche,
  byPlatform,
  csvName,
  earned,
  filterPosts,
  firstRows,
  type Group,
  NO_POST_FILTER,
  type PostFilter,
  type PostKey,
  REMOVED,
  sortPosts,
  toCsv,
} from "../lib/money.ts";
import type {
  MoneyAccount,
  MoneyPost,
  Platform,
  SubmissionStatus,
} from "../types.ts";

const CELL = "px-3 py-2.5 align-top";
const NUMBER = `${CELL} text-right tabular-nums whitespace-nowrap`;
const HEAD = "px-3 py-2 font-medium whitespace-nowrap";
const SEARCH =
  "w-64 max-w-full rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg " +
  "placeholder:text-faint focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const NONE = <span className="text-faint">None</span>;
const NONE_YET = <span className="text-faint">None yet</span>;
const NO_MATCH = "No posts match these filters.";

type Sort = { key: PostKey; dir: SortDir };
const TEXT_KEYS = new Set<PostKey>([
  "account",
  "platform",
  "campaign",
  "hook",
  "status",
]); // A to Z (or the order a submission moves through) to start

function nextSort(current: Sort, key: PostKey): Sort {
  if (current.key === key)
    return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  return { key, dir: TEXT_KEYS.has(key) ? "asc" : "desc" };
}

const COLUMNS: { key: PostKey; label: string; number?: boolean }[] = [
  { key: "posted", label: "Posted" },
  { key: "account", label: "Account" },
  { key: "platform", label: "Platform" },
  { key: "campaign", label: "Campaign" },
  { key: "hook", label: "Hook" },
  { key: "views_2d", label: "Views at 2 days", number: true },
  { key: "views_7d", label: "Views at 7 days", number: true },
  { key: "views", label: "Views", number: true },
  { key: "status", label: "Status" },
  { key: "estimated", label: "Estimated", number: true },
  { key: "paid", label: "Paid", number: true },
];

type Props = {
  posts: MoneyPost[]; // the period's
  accounts: MoneyAccount[];
  start: string;
  today: string;
};

export function MoneyPosts({ posts, accounts, start, today }: Props) {
  const id = useId();
  const [filter, setFilter] = useState<PostFilter>(NO_POST_FILTER);
  const [sort, setSort] = useState<Sort>({ key: "posted", dir: "desc" });
  const kept = filterPosts(posts, filter);
  const sorted = sortPosts(kept, sort.key, sort.dir);
  const shown = firstRows(sorted);

  const save = () => {
    const url = URL.createObjectURL(
      new Blob([toCsv(sorted)], { type: "text/csv;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = csvName(today);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0); // once the click has started the download
  };

  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={id} className="text-base font-semibold">
          Posts
        </h2>
        <Button onClick={save} disabled={sorted.length === 0}>
          <Download aria-hidden size={16} />
          Export CSV
        </Button>
      </div>
      <Filters
        posts={posts}
        accounts={accounts}
        start={start}
        today={today}
        filter={filter}
        onFilter={setFilter}
      />
      {kept.length === 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-panel px-4 py-6">
          <p className="text-sm text-muted">{NO_MATCH}</p>
          <Button onClick={() => setFilter(NO_POST_FILTER)}>
            Clear filters
          </Button>
        </div>
      ) : (
        <>
          <Best posts={kept} />
          <div className="grid gap-4 lg:grid-cols-2">
            <GroupTable
              title="By platform"
              first="Platform"
              groups={byPlatform(kept)}
            />
            <GroupTable title="By niche" first="Niche" groups={byNiche(kept)} />
          </div>
          <Sheet
            label={id}
            rows={shown.rows}
            sort={sort}
            onSort={(key) => setSort((old) => nextSort(old, key))}
          />
          {shown.note && <p className="text-xs text-muted">{shown.note}</p>}
        </>
      )}
    </section>
  );
}

type FiltersProps = {
  posts: MoneyPost[];
  accounts: MoneyAccount[];
  start: string;
  today: string;
  filter: PostFilter;
  onFilter: (filter: PostFilter) => void;
};

// Each campaign or marketplace in the period once, A to Z; a marketplace by its folded spelling.
function choices(
  posts: MoneyPost[],
  valueOf: (p: MoneyPost) => string,
  labelOf: (p: MoneyPost) => string,
) {
  const seen = new Map<string, string>();
  for (const p of posts)
    if (!seen.has(valueOf(p))) seen.set(valueOf(p), labelOf(p));
  return [...seen]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) =>
      a.label.localeCompare(b.label, "en-AU", { sensitivity: "base" }),
    );
}

function Filters({
  posts,
  accounts,
  start,
  today,
  filter,
  onFilter,
}: FiltersProps) {
  const search = useId();
  const set = (change: Partial<PostFilter>) =>
    onFilter({ ...filter, ...change });
  return (
    <div className="flex flex-wrap items-end gap-3">
      <DateField
        label="From"
        value={filter.from}
        min={start}
        max={today}
        onChange={(from) => set({ from })}
      />
      <DateField
        label="To"
        value={filter.to}
        min={start}
        max={today}
        onChange={(to) => set({ to })}
      />
      <Select
        label="Account"
        value={filter.account ?? ""}
        options={[
          { value: "", label: "All accounts" },
          ...accounts.map((a) => ({
            value: a.id,
            label: `${a.handle ?? REMOVED} (${PLATFORMS[a.platform]})`,
          })),
        ]}
        onChange={(value) => set({ account: value || null })}
      />
      <Select
        label="Platform"
        value={filter.platform ?? ""}
        options={[
          { value: "", label: "All platforms" },
          ...Object.entries(PLATFORMS).map(([value, label]) => ({
            value,
            label,
          })),
        ]}
        onChange={(value) => set({ platform: (value || null) as Platform })}
      />
      <Select
        label="Campaign"
        value={filter.campaign ?? ""}
        options={[
          { value: "", label: "All campaigns" },
          ...choices(
            posts,
            (p) => p.campaign_id,
            (p) => p.campaign,
          ),
        ]}
        onChange={(value) => set({ campaign: value || null })}
      />
      <Select
        label="Marketplace"
        value={filter.marketplace === null ? "" : fold(filter.marketplace)}
        options={[
          { value: "", label: "All marketplaces" },
          ...choices(
            posts,
            (p) => fold(p.marketplace),
            (p) => p.marketplace,
          ),
        ]}
        onChange={(value) => set({ marketplace: value || null })}
      />
      <Select
        label="Status"
        value={filter.status ?? ""}
        options={[
          { value: "", label: "All statuses" },
          ...Object.entries(NAMES).map(([value, label]) => ({
            value,
            label,
          })),
        ]}
        onChange={(value) =>
          set({ status: (value || null) as SubmissionStatus | null })
        }
      />
      <div className="flex flex-col gap-1">
        <label htmlFor={search} className="text-xs font-medium text-muted">
          Search posts
        </label>
        <input
          id={search}
          type="search"
          value={filter.search}
          placeholder="Hook, campaign, marketplace or account"
          onChange={(e) => set({ search: e.target.value })}
          className={SEARCH}
        />
      </div>
    </div>
  );
}

function DateField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: string | null;
  min: string;
  max: string;
  onChange: (value: string | null) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <input
        id={id}
        type="date"
        value={value ?? ""}
        min={min}
        max={max}
        onChange={(e) => onChange(e.target.value || null)}
        className={SELECT}
      />
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={SELECT}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} dir="auto">
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

// The 5 posts with the most views and the 5 that earned the most, side by side.
function Best({ posts }: { posts: MoneyPost[] }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h3 id={id} className="text-sm font-semibold">
        Best
      </h3>
      <div className="grid gap-4 lg:grid-cols-2">
        <BestList
          title="Most views"
          posts={best(posts, "views")}
          value={(p) => count(p.views ?? 0)}
          none="No views counted yet."
        />
        <BestList
          title="Earned the most"
          posts={best(posts, "earned")}
          value={(p) => money(earned(p), "USD")}
          none="Nothing earned yet."
        />
      </div>
    </section>
  );
}

function BestList({
  title,
  posts,
  value,
  none,
}: {
  title: string;
  posts: MoneyPost[];
  value: (post: MoneyPost) => string;
  none: string;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-2 rounded-card border border-line bg-panel p-4">
      <h4 id={id} className="text-xs font-medium text-muted">
        {title}
      </h4>
      {posts.length === 0 ? (
        <p className="text-sm text-muted">{none}</p>
      ) : (
        <ol aria-labelledby={id} className="flex flex-col gap-1.5 text-sm">
          {posts.map((p) => (
            <li
              key={`${p.post_id} ${p.campaign_id}`}
              className="flex items-baseline gap-2"
            >
              <span className="min-w-0 flex-1 truncate">
                <Hook post={p} />
              </span>
              <span className="text-muted" dir="auto">
                {p.handle ?? REMOVED}
              </span>
              <span className="tabular-nums">{value(p)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// Posts, views, estimated and paid in US$ a platform or a niche.
function GroupTable({
  title,
  first,
  groups,
}: {
  title: string;
  first: string;
  groups: Group[];
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      <div className="overflow-x-auto rounded-card border border-line bg-panel">
        <table aria-labelledby={id} className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th scope="col" className={HEAD}>
                {first}
              </th>
              {["Posts", "Views", "Estimated", "Paid"].map((label) => (
                <th key={label} scope="col" className={`${HEAD} text-right`}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.name} className="border-b border-line last:border-0">
                <th scope="row" className={`${CELL} font-medium`} dir="auto">
                  {g.name}
                </th>
                <td className={NUMBER}>{count(g.posts)}</td>
                <td className={NUMBER}>
                  {g.views === null ? NONE_YET : count(g.views)}
                </td>
                <td className={NUMBER}>{money(g.estimated, "USD")}</td>
                <td className={NUMBER}>{money(g.paid, "USD")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type SheetProps = {
  label: string; // the id of the heading that names the table
  rows: MoneyPost[];
  sort: Sort;
  onSort: (key: PostKey) => void;
};

function Sheet({ label, rows, sort, onSort }: SheetProps) {
  const views = (n: number | null) => (n === null ? NONE_YET : count(n));
  return (
    <div className="overflow-x-auto rounded-card border border-line bg-panel">
      <table aria-labelledby={label} className="w-full text-left text-sm">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            {COLUMNS.map((column) => {
              const on = sort.key === column.key;
              const Arrow = sort.dir === "asc" ? ArrowUp : ArrowDown;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={
                    on
                      ? sort.dir === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  className={`${HEAD} ${column.number ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => onSort(column.key)}
                    className={`inline-flex items-center gap-1 rounded-control hover:text-fg ${on ? "text-fg" : ""}`}
                  >
                    {column.label}
                    {on && <Arrow aria-hidden size={12} />}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={`${p.post_id} ${p.campaign_id}`}
              className="border-b border-line last:border-0"
            >
              <td className={`${CELL} whitespace-nowrap`}>
                {dayLabel(p.posted_on)}
              </td>
              <td className={CELL} dir="auto">
                {p.handle ?? REMOVED}
              </td>
              <td className={CELL}>{PLATFORMS[p.platform]}</td>
              <td className={CELL} dir="auto">
                {p.campaign}
              </td>
              <th scope="row" className={`${CELL} min-w-48 font-medium`}>
                <Hook post={p} />
              </th>
              <td className={NUMBER}>{views(p.views_2d)}</td>
              <td className={NUMBER}>{views(p.views_7d)}</td>
              <td className={NUMBER}>{views(p.views)}</td>
              <td className={CELL}>
                {NAMES[p.status]}
                {p.status === "rejected" && p.reason && (
                  <span className="block text-xs text-muted">{p.reason}</span>
                )}
              </td>
              <td className={NUMBER}>
                {p.estimated === null ? NONE : money(p.estimated, p.currency)}
              </td>
              <td className={NUMBER}>
                {p.paid === null ? NONE : money(p.paid, p.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// A post's hook, a link to the live post when it has one.
export function Hook({ post }: { post: MoneyPost }) {
  if (!post.url) return <span dir="auto">{post.hook}</span>;
  return (
    <a
      dir="auto"
      href={post.url}
      target="_blank"
      rel="noreferrer"
      className="text-fg underline underline-offset-2 hover:decoration-2"
    >
      {post.hook}
    </a>
  );
}
