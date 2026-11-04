// Today's Money (SPEC 14.10 item 7): per marketplace and currency what's estimated, pending and paid, which
// are estimates and which your entries. Withdraw opens the marketplace's payout page in your browser and is
// the main button once something is ready to withdraw; I've been paid records what came in.

import { useId, useState } from "react";
import { Link } from "react-router";

import { api, sentence } from "../api.ts";
import { money } from "../lib/campaigns.ts";
import type { MoneyLine } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { PayoutDialog } from "./PayoutDialog.tsx";
import { useToast } from "./Toasts.tsx";

export function MoneyBlock({ lines }: { lines: MoneyLine[] }) {
  const id = useId();
  return (
    <section id="money" aria-labelledby={id} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Money
      </h2>
      {lines.map((line) => (
        <Row key={`${line.marketplace}:${line.currency}`} line={line} />
      ))}
    </section>
  );
}

function Row({ line }: { line: MoneyLine }) {
  const toast = useToast();
  const [paying, setPaying] = useState(false);
  const amount = (n: number | null) => (n === null ? "No rate" : money(n, line.currency));
  const figures = [
    { label: "Estimated", value: amount(line.estimated), note: "estimate" },
    { label: "Pending", value: amount(line.pending), note: "estimate" },
    { label: "Paid", value: money(line.paid, line.currency), note: "your entry" },
  ];
  const link = line.withdraw_link;

  async function withdraw(open: string) {
    try {
      await api(`/api/links/${encodeURIComponent(open)}/open`, { method: "POST" });
      toast({ text: `Opened ${line.marketplace}'s payout page in your browser.`, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <article
      aria-label={`${line.marketplace} in ${line.currency}`}
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 dir="auto" className="text-sm font-semibold">
          {line.marketplace}
        </h3>
        {line.ready && <Badge tone="ok">Ready to withdraw</Badge>}
      </div>
      <dl className="grid grid-cols-3 gap-x-6 gap-y-1 text-sm">
        {figures.map((f) => (
          <div key={f.label}>
            <dt className="text-xs text-faint">{f.label}</dt>
            <dd className="tabular-nums">
              {f.value} <span className="text-xs text-faint">{f.note}</span>
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        {link && (
          <Button variant={line.ready ? "primary" : "secondary"} onClick={() => void withdraw(link)}>
            Withdraw
          </Button>
        )}
        <Button variant="secondary" onClick={() => setPaying(true)}>
          I've been paid
        </Button>
        {!link && (
          <p className="text-xs text-muted">
            Add its Withdraw link in{" "}
            <Link to="/settings" className="text-fg underline underline-offset-2">
              Settings
            </Link>
            .
          </p>
        )}
      </div>
      {paying && (
        <PayoutDialog marketplace={line.marketplace} currency={line.currency} onClose={() => setPaying(false)} />
      )}
    </article>
  );
}
