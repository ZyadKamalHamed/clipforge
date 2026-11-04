// Today's Guidelines (SPEC 14.10 item 6): each running campaign's rules in short, with its page here and its
// own page in your browser, then each account's posts today against its cap. Names and rules are only ever
// text.

import { useId } from "react";
import { Link } from "react-router";

import { api, sentence } from "../api.ts";
import { accountLine, payLine, ruleLines } from "../lib/today.ts";
import type { AccountToday, Guideline } from "../types.ts";
import { Button } from "./Button.tsx";
import { useToast } from "./Toasts.tsx";

type Props = { guidelines: Guideline[]; accounts: AccountToday[] };

export function Guidelines({ guidelines, accounts }: Props) {
  const id = useId();
  const each = useId();
  return (
    <section id="guidelines" aria-labelledby={id} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Guidelines
      </h2>
      {guidelines.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {guidelines.map((g) => (
            <Card key={g.id} rules={g} />
          ))}
        </div>
      )}
      {accounts.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h3 id={each} className="text-sm font-semibold">
            Today on each account
          </h3>
          <ul aria-labelledby={each} className="flex flex-col gap-0.5 text-sm text-muted tabular-nums">
            {accounts.map((a) => (
              <li key={a.id} dir="auto">
                {accountLine(a)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Card({ rules: g }: { rules: Guideline }) {
  const toast = useToast();
  const page = g.page_link;

  async function open(link: string) {
    try {
      await api(`/api/links/${encodeURIComponent(link)}/open`, { method: "POST" });
      toast({ text: "Opened the campaign page in your browser.", kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <article aria-label={g.name} className="flex flex-col gap-2 rounded-card border border-line bg-panel p-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h3 className="truncate text-sm font-semibold">
          <Link to={`/campaigns/${encodeURIComponent(g.id)}`} dir="auto" className="text-fg underline underline-offset-2">
            {g.name}
          </Link>
        </h3>
        <p dir="auto" className="text-xs text-muted">
          {g.marketplace}
        </p>
      </div>
      <p className="text-sm tabular-nums">{payLine(g)}</p>
      <ul className="flex flex-col gap-0.5 text-xs text-muted">
        {ruleLines(g).map((line) => (
          <li key={line} dir="auto">
            {line}
          </li>
        ))}
      </ul>
      {page && (
        <Button variant="secondary" className="self-start" onClick={() => void open(page)}>
          Open campaign page
        </Button>
      )}
    </article>
  );
}
