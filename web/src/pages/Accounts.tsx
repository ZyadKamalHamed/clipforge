// The Accounts page (SPEC 11 and 14.1): the TikTok and Instagram accounts clips post to, a card each, and
// Add account. Nothing posts from here: a post always waits for Confirm and post.

import { useQuery } from "@tanstack/react-query";
import { Plus, Users } from "lucide-react";
import { useState } from "react";

import { sentence } from "../api.ts";
import { AccountCard } from "../components/AccountCard.tsx";
import { AddAccount, type Route } from "../components/AddAccount.tsx";
import { Button } from "../components/Button.tsx";
import { Empty } from "../components/Empty.tsx";
import { PLATFORMS } from "../lib/accounts.ts";
import { accountsQuery, activeJobsQuery } from "../state.ts";
import type { Account, ActiveJob, Platform } from "../types.ts";

const NOTHING_POSTS = "Nothing posts until you press Confirm and post.";
const ACCOUNT_JOBS = new Set(["account.login", "account.selftest"]);

export function Accounts() {
  const accounts = useQuery(accountsQuery);
  const jobs = useQuery(activeJobsQuery);
  const [adding, setAdding] = useState<Route | null>(null);

  // Each account's job still to finish, said in the words the jobs list uses.
  const working = new Map<string, ActiveJob>();
  for (const job of jobs.data?.jobs ?? []) {
    if (ACCOUNT_JOBS.has(job.kind) && job.ref_id) working.set(job.ref_id, job);
  }

  const add = (
    <Button variant="primary" onClick={() => setAdding("tiktok")}>
      <Plus aria-hidden size={16} />
      Add account
    </Button>
  );
  const all = accounts.data?.accounts ?? [];

  let body;
  if (accounts.isPending) body = <p className="text-sm text-muted">Loading the accounts.</p>;
  else if (accounts.isError) body = <p className="text-sm text-bad">{sentence(accounts.error)}</p>;
  else if (all.length === 0)
    body = (
      <Empty icon={Users} title="No accounts yet." action={add}>
        <p>Add the TikTok and Instagram accounts your clips go to. {NOTHING_POSTS}</p>
      </Empty>
    );
  else
    body = (
      <>
        {(Object.keys(PLATFORMS) as Platform[]).map((platform) => (
          <Section
            key={platform}
            platform={platform}
            accounts={all.filter((a) => a.platform === platform)}
            all={all}
            working={working}
            onFacebook={() => setAdding("facebook")}
          />
        ))}
      </>
    );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">Accounts</h1>
          {all.length > 0 && <p className="text-sm text-muted">{NOTHING_POSTS}</p>}
        </div>
        {all.length > 0 && add}
      </div>
      {body}
      {adding && <AddAccount start={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}

type SectionProps = {
  platform: Platform;
  accounts: Account[];
  all: Account[];
  working: Map<string, ActiveJob>;
  onFacebook: () => void;
};

function Section({ platform, accounts, all, working, onFacebook }: SectionProps) {
  if (accounts.length === 0) return null;
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">{PLATFORMS[platform]}</h2>
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(19rem,1fr))] gap-4">
        {accounts.map((account) => (
          <li key={account.id} className="flex">
            <AccountCard account={account} all={all} job={working.get(account.id) ?? null} onFacebook={onFacebook} />
          </li>
        ))}
      </ul>
    </section>
  );
}
