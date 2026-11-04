// Today's Submit block (SPEC 14.10 item 5): a row for each live post a campaign is waiting on, as its
// campaign's page has it. One still to submit copies its live link (so pasting it back here starts
// nothing) and opens the marketplace's submit page in your browser, then waits for Mark submitted.

import { useId } from "react";

import { api, sentence } from "../api.ts";
import { rememberCopied } from "../lib/drop.ts";
import { submitAction } from "../lib/today.ts";
import type { SubmitEntry } from "../types.ts";
import { SubmissionRow } from "./SubmissionRow.tsx";
import { useToast } from "./Toasts.tsx";

export function SubmitList({ entries }: { entries: SubmitEntry[] }) {
  const id = useId();
  const toast = useToast();

  async function run(action: NonNullable<ReturnType<typeof submitAction>>) {
    try {
      if (action.copy) {
        await navigator.clipboard.writeText(action.copy);
        rememberCopied(action.copy);
      }
      if (action.open) await api(`/api/links/${encodeURIComponent(action.open)}/open`, { method: "POST" });
      toast({ text: action.done, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <section id="submit" aria-labelledby={id} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Submit
      </h2>
      {entries.map((entry) => {
        const action = submitAction(entry);
        return (
          <SubmissionRow
            key={entry.id}
            submission={entry}
            currency={entry.currency}
            submit={action ? { label: action.label, run: () => void run(action) } : undefined}
          />
        );
      })}
    </section>
  );
}
