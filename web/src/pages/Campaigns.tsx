// The Campaigns page (SPEC 14.11 and 10.10): a sheet with a row a paid clipping campaign, its money in its
// own currency, headers that sort, and a totals line a currency, never added across currencies. A row with
// something waiting for you today leads with a triangle and comes first. New campaign reads pasted rules
// into the form. Every number here is worked out from your own entries. The Briefs tab, kept in the
// address as ?tab=briefs, lists the campaigns the marketplaces offer, each one a click from the form. Above
// the sheet, a search, three filters and a Columns menu pick the rows and columns shown, and the totals add
// up the rows shown; the hidden columns are kept in this browser for next time.

import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  BadgeDollarSign,
  Check,
  Columns3,
  Plus,
  TriangleAlert,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { Fragment, type ReactNode, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { sentence } from "../api.ts";
import { BriefList } from "../components/BriefList.tsx";
import { SELECT } from "../components/AddSource.tsx";
import { Button } from "../components/Button.tsx";
import { CampaignForm, type FormStart } from "../components/CampaignForm.tsx";
import { Empty } from "../components/Empty.tsx";
import { Tabs } from "../components/Tabs.tsx";
import { Triangle } from "../components/Triangle.tsx";
import {
  count,
  dayLabel,
  EMPTY,
  ESTIMATES,
  filterRows,
  HIDEABLE,
  hiddenColumns,
  keepHidden,
  marketplaceNames,
  money,
  nextSort,
  NO_FILTER,
  PROGRESS,
  progressCounts,
  type SheetFilter,
  type Sort,
  type SortKey,
  sortRows,
  type StateFilter,
  totalsByCurrency,
} from "../lib/campaigns.ts";
import { campaignsQuery } from "../state.ts";
import type { Campaign } from "../types.ts";

const ABOUT =
  "Each campaign follows its clips from made to paid, with what they should earn at its rate.";

type Column = {
  key: SortKey;
  label: string;
  number?: boolean;
  icon?: boolean;
};

const COLUMNS: Column[] = [
  { key: "needs", label: "Needs you", icon: true }, // the triangle's column, its name read out only
  { key: "marketplace", label: "Marketplace" },
  { key: "name", label: "Campaign" },
  { key: "rate", label: "Rate per 1,000", number: true },
  { key: "budget", label: "Budget left", number: true },
  { key: "ends", label: "Ends" },
  { key: "channels", label: "Channels" },
  { key: "progress", label: "Progress" },
  { key: "views", label: "Views", number: true },
  { key: "estimated", label: "Estimated", number: true },
  { key: "pending", label: "Pending", number: true },
  { key: "paid", label: "Paid", number: true },
];

const CELL = "px-3 py-2.5 align-top";
const NUMBER = `${CELL} text-right tabular-nums whitespace-nowrap`;
const NOT_SET = <span className="text-faint">Not set</span>;
const NO_RATE = <span className="text-faint">No rate</span>;
const NONE_YET = <span className="text-faint">None yet</span>;
const TOTALLED = new Set<SortKey>(["views", "estimated", "pending", "paid"]); // the totals row's own cells
const SEARCH =
  "w-64 max-w-full rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg " +
  "placeholder:text-faint focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const ITEM =
  "flex cursor-default items-center gap-1 rounded-[6px] px-2 py-1.5 text-sm outline-none select-none " +
  "data-[highlighted]:bg-panel-2";
const STATES: { value: StateFilter; label: string }[] = [
  { value: "all", label: "Any state" },
  { value: "active", label: "Active" },
  { value: "ending", label: "Ending within 3 days" },
  { value: "ended", label: "Ended" },
];
const NO_MATCH = "No campaigns match these filters.";

// The browser's storage, or null where it is blocked.
function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

type Tab = "sheet" | "briefs";

export function Campaigns() {
  const campaigns = useQuery(campaignsQuery);
  const [making, setMaking] = useState<FormStart | null>(null);
  const [sort, setSort] = useState<Sort>({ key: "needs", dir: "desc" });
  const [filter, setFilter] = useState<SheetFilter>(NO_FILTER);
  const [hidden, setHidden] = useState(() => hiddenColumns(storage()));
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "briefs" ? "briefs" : "sheet";

  function pickTab(next: Tab) {
    setParams(
      (old) => {
        const changed = new URLSearchParams(old);
        if (next === "briefs") changed.set("tab", next);
        else changed.delete("tab");
        return changed;
      },
      { replace: true },
    );
  }

  const add = (
    <Button
      variant="primary"
      onClick={() => setMaking({ fields: {}, filled: [] })}
    >
      <Plus aria-hidden size={16} />
      New campaign
    </Button>
  );
  const all = campaigns.data?.campaigns ?? [];

  let body;
  if (campaigns.isPending)
    body = <p className="text-sm text-muted">Loading the campaigns.</p>;
  else if (campaigns.isError)
    body = <p className="text-sm text-bad">{sentence(campaigns.error)}</p>;
  else if (all.length === 0)
    body = (
      <Empty icon={BadgeDollarSign} title={EMPTY} action={add}>
        <p>{ABOUT}</p>
      </Empty>
    );
  else
    body = (
      <Sheet
        rows={all}
        sort={sort}
        onSort={(k) => setSort((old) => nextSort(old, k))}
        filter={filter}
        onFilter={setFilter}
        hidden={hidden}
        onHide={(key, hide) => {
          const next = new Set(hidden);
          if (hide) next.add(key);
          else next.delete(key);
          setHidden(next);
          keepHidden(storage(), next);
        }}
      />
    );

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">Campaigns</h1>
          {all.length > 0 && <p className="text-sm text-muted">{ABOUT}</p>}
        </div>
        {all.length > 0 && add}
      </div>
      <Tabs<Tab>
        label="Campaigns"
        value={tab}
        onChange={pickTab}
        tabs={[
          { value: "sheet", label: "Sheet", content: () => body },
          {
            value: "briefs",
            label: "Briefs",
            content: () => <BriefList onStart={setMaking} />,
          },
        ]}
      />
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

type SheetProps = {
  rows: Campaign[];
  sort: Sort;
  onSort: (key: SortKey) => void;
  filter: SheetFilter;
  onFilter: (filter: SheetFilter) => void;
  hidden: Set<SortKey>;
  onHide: (key: SortKey, hide: boolean) => void;
};

function Sheet({
  rows,
  sort,
  onSort,
  filter,
  onFilter,
  hidden,
  onHide,
}: SheetProps) {
  const shown = filterRows(rows, filter);
  const columns = COLUMNS.filter((column) => !hidden.has(column.key));
  const sorted = sortRows(shown, sort.key, sort.dir);
  const totals = totalsByCurrency(shown);
  const span = columns.filter((column) => !TOTALLED.has(column.key)).length;
  const has = (key: SortKey) => !hidden.has(key);
  return (
    <div className="flex flex-col gap-3">
      <Controls
        rows={rows}
        filter={filter}
        onFilter={onFilter}
        hidden={hidden}
        onHide={onHide}
      />
      {shown.length === 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-panel px-4 py-6">
          <p className="text-sm text-muted">{NO_MATCH}</p>
          <Button onClick={() => onFilter(NO_FILTER)}>Clear filters</Button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-card border border-line bg-panel">
          <table aria-label="Campaigns" className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                {columns.map((column) => {
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
                      className={`px-3 py-2 font-medium whitespace-nowrap ${column.number ? "text-right" : ""}`}
                    >
                      <button
                        type="button"
                        onClick={() => onSort(column.key)}
                        className={`inline-flex items-center gap-1 rounded-control hover:text-fg ${on ? "text-fg" : ""}`}
                      >
                        {column.icon ? (
                          <>
                            <TriangleAlert aria-hidden size={14} />
                            <span className="sr-only">{column.label}</span>
                          </>
                        ) : (
                          column.label
                        )}
                        {on && <Arrow aria-hidden size={12} />}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <Row key={row.id} row={row} columns={columns} />
              ))}
            </tbody>
            <tfoot className="border-t border-line bg-panel-2">
              {totals.map((line) => (
                <tr key={line.currency}>
                  <th
                    scope="row"
                    colSpan={span}
                    className={`${CELL} font-medium`}
                  >
                    Total {line.currency}
                  </th>
                  {has("views") && (
                    <td className={NUMBER}>
                      {line.views === null ? NONE_YET : count(line.views)}
                    </td>
                  )}
                  {has("estimated") && (
                    <td className={NUMBER}>
                      {line.estimated === null
                        ? NO_RATE
                        : money(line.estimated, line.currency)}
                    </td>
                  )}
                  {has("pending") && (
                    <td className={NUMBER}>
                      {line.pending === null
                        ? NO_RATE
                        : money(line.pending, line.currency)}
                    </td>
                  )}
                  {has("paid") && (
                    <td className={NUMBER}>
                      {money(line.paid, line.currency)}
                    </td>
                  )}
                </tr>
              ))}
            </tfoot>
          </table>
        </div>
      )}
      <p className="text-xs text-faint">
        {ESTIMATES}. Progress counts the clips{" "}
        {PROGRESS.map((p) => p.label).join(", ")}.
      </p>
    </div>
  );
}

function Controls({
  rows,
  filter,
  onFilter,
  hidden,
  onHide,
}: Omit<SheetProps, "sort" | "onSort">) {
  const search = useId();
  const set = (change: Partial<SheetFilter>) =>
    onFilter({ ...filter, ...change });
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label htmlFor={search} className="text-xs font-medium text-muted">
          Search campaigns
        </label>
        <input
          id={search}
          type="search"
          value={filter.search}
          placeholder="Name, marketplace or channel"
          onChange={(e) => set({ search: e.target.value })}
          className={SEARCH}
        />
      </div>
      <Select
        label="Marketplace"
        value={filter.marketplace ?? ""}
        options={[
          { value: "", label: "All marketplaces" },
          ...marketplaceNames(rows).map((name) => ({
            value: name,
            label: name,
          })),
        ]}
        onChange={(value) => set({ marketplace: value || null })}
      />
      <Select
        label="State"
        value={filter.state}
        options={STATES}
        onChange={(value) => set({ state: value as StateFilter })}
      />
      <label className="flex cursor-pointer items-center gap-2 py-1.5 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-accent-strong"
          checked={filter.needsMe}
          onChange={(e) => set({ needsMe: e.target.checked })}
        />
        Needs me
      </label>
      <DropdownMenu.Root modal={false}>
        <DropdownMenu.Trigger asChild>
          <Button className="ml-auto">
            <Columns3 aria-hidden size={16} />
            Columns
          </Button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={4}
            className="z-50 min-w-44 rounded-control border border-line bg-panel p-1 shadow-2xl"
          >
            {COLUMNS.filter((column) => HIDEABLE.includes(column.key)).map(
              (column) => (
                <DropdownMenu.CheckboxItem
                  key={column.key}
                  className={ITEM}
                  checked={!hidden.has(column.key)}
                  onCheckedChange={(on) => onHide(column.key, !on)}
                  onSelect={(e) => e.preventDefault()} // stays open for the next column
                >
                  <span className="inline-flex w-5 shrink-0">
                    <DropdownMenu.ItemIndicator>
                      <Check aria-hidden size={14} />
                    </DropdownMenu.ItemIndicator>
                  </span>
                  {column.label}
                </DropdownMenu.CheckboxItem>
              ),
            )}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
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
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function Row({ row, columns }: { row: Campaign; columns: Column[] }) {
  const progress = progressCounts(row);
  const m = row.money;
  const cells: Record<SortKey, ReactNode> = {
    needs: (
      <td className={`${CELL} w-8`}>
        <Triangle campaign={row} />
      </td>
    ),
    marketplace: (
      <td className={CELL} dir="auto">
        {row.marketplace}
      </td>
    ),
    name: (
      <th scope="row" className={`${CELL} min-w-40 font-medium`}>
        <Link
          to={`/campaigns/${encodeURIComponent(row.id)}`}
          className="text-fg underline underline-offset-2"
          dir="auto"
        >
          {row.name}
        </Link>
        {row.ended && (
          <span className="ml-2 text-xs font-normal text-faint">Ended</span>
        )}
      </th>
    ),
    rate: (
      <td className={NUMBER}>
        {row.rate_per_1000 === null
          ? NO_RATE
          : money(row.rate_per_1000, row.currency)}
      </td>
    ),
    budget: (
      <td className={NUMBER}>
        {row.budget_left === null ? (
          NOT_SET
        ) : (
          <>
            {money(row.budget_left, row.currency)}
            {row.budget_entered_on && (
              <span className="block text-xs text-faint">
                entered {dayLabel(row.budget_entered_on)}
              </span>
            )}
          </>
        )}
      </td>
    ),
    ends: (
      <td className={`${CELL} whitespace-nowrap`}>
        {row.ends_on ? dayLabel(row.ends_on) : NOT_SET}
      </td>
    ),
    channels: (
      <td className={CELL} dir="auto">
        {row.channels.length
          ? row.channels.map((c) => c.name).join(", ")
          : NONE_YET}
      </td>
    ),
    progress: (
      <td className={`${CELL} min-w-32`}>
        <div
          aria-hidden
          className="mt-1.5 h-1 overflow-hidden rounded-full bg-line"
        >
          <div
            className="h-full rounded-full bg-accent-strong"
            style={{ width: `${progress.share * 100}%` }}
          />
        </div>
        <span
          aria-hidden
          className="mt-1 block text-xs text-muted tabular-nums"
        >
          {progress.parts.map((p) => p.n).join(" · ")}
        </span>
        <span className="sr-only">{progress.sentence}</span>
      </td>
    ),
    views: (
      <td className={NUMBER}>{m.views === null ? NONE_YET : count(m.views)}</td>
    ),
    estimated: (
      <td className={NUMBER}>
        {m.estimated === null ? NO_RATE : money(m.estimated, row.currency)}
      </td>
    ),
    pending: (
      <td className={NUMBER}>
        {m.pending === null ? NO_RATE : money(m.pending, row.currency)}
      </td>
    ),
    paid: <td className={NUMBER}>{money(m.paid, row.currency)}</td>,
  };
  return (
    <tr className="border-b border-line last:border-b-0">
      {columns.map((column) => (
        <Fragment key={column.key}>{cells[column.key]}</Fragment>
      ))}
    </tr>
  );
}
