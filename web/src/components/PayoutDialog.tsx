// What a marketplace paid you (SPEC 14.10 item 7): its approved submissions in this currency, each amount
// filled in from its estimate for you to correct, and Save marks each one paid with its amount, which ticks
// the day's payout item. Titles are only ever text.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import { AMOUNT_NEEDED, amountValue } from "../lib/campaigns.ts";
import { campaignsQuery } from "../state.ts";
import type { Submission } from "../types.ts";
import { Button } from "./Button.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

type Props = { marketplace: string; currency: string; onClose: () => void };

export function PayoutDialog({ marketplace, currency, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const campaigns = useQuery(campaignsQuery);
  const approved = useQuery({
    queryKey: [...key.campaigns, "submissions", "approved"], // refreshed with the campaigns
    queryFn: () => api<{ submissions: Submission[] }>("/api/submissions?status=approved"),
  });
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // The marketplace's campaigns in this currency, named alike whatever the case, as the totals are.
  const names = new Map(
    (campaigns.data?.campaigns ?? [])
      .filter((c) => c.marketplace.toLowerCase() === marketplace.toLowerCase() && c.currency === currency)
      .map((c) => [c.id, c.name]),
  );
  const rows = (approved.data?.submissions ?? []).filter((s) => names.has(s.campaign_id));
  const text = (s: Submission) => typed[s.id] ?? (s.estimate === null ? "" : s.estimate.toFixed(2));
  const loading = campaigns.isPending || approved.isPending;
  const failed = campaigns.error ?? approved.error;

  async function save() {
    const amounts = new Map<string, number>();
    const refused: Record<string, string> = {};
    for (const s of rows) {
      const amount = amountValue(text(s));
      if (typeof amount === "number" && amount >= 0) amounts.set(s.id, amount);
      else refused[s.id] = AMOUNT_NEEDED;
    }
    setProblems(refused);
    if (Object.keys(refused).length) return;
    setBusy(true);
    let done = 0;
    try {
      for (const [id, paid_amount] of amounts) {
        await api(`/api/submissions/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "paid", paid_amount }),
        });
        done += 1;
      }
      toast({ text: `Recorded ${done} ${done === 1 ? "payment" : "payments"} on ${marketplace}.`, kind: "ok" });
      onClose();
    } catch (err) {
      const [id] = [...amounts.keys()].slice(done);
      setProblems({ [id]: sentence(err) });
    } finally {
      setBusy(false);
      void client.invalidateQueries({ queryKey: key.today });
      void client.invalidateQueries({ queryKey: key.campaigns });
    }
  }

  let body;
  if (loading) body = <p className="text-sm text-muted">Loading the approved submissions.</p>;
  else if (failed) body = <p className="text-sm text-bad">{sentence(failed)}</p>;
  else if (!rows.length)
    body = (
      <p className="text-sm text-muted">{`Nothing on ${marketplace} is approved yet. Mark a submission Approved on its campaign's page first.`}</p>
    );
  else
    body = rows.map((s) => (
      <TextField
        key={s.id}
        label={`${s.post?.clip_title || "A post"} (${names.get(s.campaign_id)})`}
        value={text(s)}
        onChange={(value) => setTyped((was) => ({ ...was, [s.id]: value }))}
        inputMode="decimal"
        error={problems[s.id]}
      />
    ));

  return (
    <Dialog
      title={`Paid on ${marketplace}`}
      description="Each amount is filled in from its estimate. Change any to what came in."
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <div className="flex flex-col gap-3">{body}</div>
      <DialogFooter>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy || !rows.length} onClick={() => void save()}>
          Save
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
