// The Queue (SPEC 14.6): every post with its status, clip, account, time and sentence, the ones that need
// you (failed ones too) pinned on top, then Up next, then Done. The filters live in the address, so a
// reload or a link keeps them.

import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { type ReactNode, useId } from "react";
import { Link, useSearchParams } from "react-router";

import { sentence } from "../api.ts";
import { buttonClass, Button } from "../components/Button.tsx";
import { Empty } from "../components/Empty.tsx";
import { TIME_FIELD } from "../components/Field.tsx";
import { accountText, PostRow } from "../components/PostRow.tsx";
import { PLATFORMS } from "../lib/accounts.ts";
import { ALL, filterPosts, type QueueFilters, type QueueGroup, queueGroup, queueOrder, STATUS_FILTERS } from "../lib/queue.ts";
import { postsQuery } from "../state.ts";
import type { Platform, QueuedPost } from "../types.ts";

function readFilters(params: URLSearchParams): QueueFilters {
  const status = params.get("status") ?? "all";
  const platform = params.get("platform");
  return {
    status: STATUS_FILTERS.some((f) => f.value === status) ? status : "all",
    account: params.get("account") || "all",
    platform: platform === "tiktok" || platform === "instagram" ? platform : "all",
  };
}

// Each account with posts in the Queue, by name, removed ones too.
function accountsOf(posts: QueuedPost[]): { id: string; name: string }[] {
  const names = new Map<string, string>();
  for (const p of posts) names.set(p.account_id, accountText(p));
  return [...names].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

export function Queue() {
  const posts = useQuery(postsQuery);
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const filtered = filters.status !== ALL.status || filters.account !== ALL.account || filters.platform !== ALL.platform;
  const now = Date.now() / 1000;

  function filter(name: keyof QueueFilters, value: string) {
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        if (value === "all") next.delete(name);
        else next.set(name, value);
        return next;
      },
      { replace: true },
    );
  }

  let body: ReactNode = null;
  if (posts.isPending) body = <p className="text-sm text-muted">Loading the queue.</p>;
  else if (posts.isError) body = <p className="text-sm text-bad">{sentence(posts.error)}</p>;
  else if (posts.data.posts.length === 0)
    body = (
      <Empty
        icon={ListChecks}
        title="No posts yet"
        action={
          <Link to="/projects" className={buttonClass("primary")}>
            Projects
          </Link>
        }
      >
        <p>Nothing is queued yet. Pick clips on a project and press Post.</p>
      </Empty>
    );
  else {
    const shown = queueOrder(filterPosts(posts.data.posts, filters));
    const group = (g: QueueGroup) => shown.filter((p) => queueGroup(p) === g);
    body = (
      <>
        <Filters filters={filters} accounts={accountsOf(posts.data.posts)} onFilter={filter} />
        {shown.length === 0 && (
          <div className="flex flex-col items-start gap-2 py-6">
            <p className="text-sm text-muted">No posts match these filters.</p>
            <Button onClick={() => setParams({}, { replace: true })}>Clear the filters</Button>
          </div>
        )}
        <Posts title="Needs you" posts={group("needs_you")} now={now} />
        <Posts title="Up next" posts={group("next")} now={now} />
        <Posts title="Done" posts={group("done")} now={now} />
        {filtered && shown.length > 0 && (
          <Button variant="ghost" className="self-start" onClick={() => setParams({}, { replace: true })}>
            Clear the filters
          </Button>
        )}
      </>
    );
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <h1 className="text-xl font-semibold">Queue</h1>
      {body}
    </div>
  );
}

// A section of the Queue, left out while it has no posts.
function Posts({ title, posts, now }: { title: string; posts: QueuedPost[]; now: number }) {
  const id = useId();
  if (posts.length === 0) return null;
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        {title}
      </h2>
      {posts.map((post) => (
        <PostRow key={post.id} post={post} now={now} />
      ))}
    </section>
  );
}

type FiltersProps = {
  filters: QueueFilters;
  accounts: { id: string; name: string }[];
  onFilter: (name: keyof QueueFilters, value: string) => void;
};

function Filters({ filters, accounts, onFilter }: FiltersProps) {
  return (
    <div className="flex flex-wrap gap-3">
      <Select label="Status" value={filters.status} onChange={(v) => onFilter("status", v)}>
        {STATUS_FILTERS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </Select>
      <Select label="Platform" value={filters.platform} onChange={(v) => onFilter("platform", v)}>
        <option value="all">Every platform</option>
        {(Object.keys(PLATFORMS) as Platform[]).map((p) => (
          <option key={p} value={p}>
            {PLATFORMS[p]}
          </option>
        ))}
      </Select>
      <Select label="Account" value={filters.account} onChange={(v) => onFilter("account", v)}>
        <option value="all">Every account</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </Select>
    </div>
  );
}

function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={TIME_FIELD}>
        {children}
      </select>
    </div>
  );
}
