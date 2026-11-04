// Today's block 10 (SPEC 14.10): the new briefs worth a look, the richest 5 that fit an account or a niche the
// advice suggests, each as on the Briefs tab, with See all briefs and I've looked, which marks the ones shown
// as seen. Then Accounts worth adding, and a box to paste the briefs of each site that couldn't be read
// today. The niche and marketplace filters are on the Briefs tab.

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router";

import { api, key, sentence } from "../api.ts";
import { NO_NEW, worthALook } from "../lib/briefs.ts";
import type { Advice, Brief, BriefSource } from "../types.ts";
import { AdviceCard } from "./AdviceCard.tsx";
import { PasteBox } from "./BriefList.tsx";
import { BriefRow } from "./BriefRow.tsx";
import { Button } from "./Button.tsx";
import type { FormStart } from "./CampaignForm.tsx";
import { useToast } from "./Toasts.tsx";

type Props = {
  briefs: Brief[];
  advice: Advice;
  sources: BriefSource[];
  onStart: (start: FormStart) => void;
};

export function NewBriefs({ briefs, advice, sources, onStart }: Props) {
  const id = useId();
  const client = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const rows = worthALook(
    briefs,
    advice.suggestions.map((s) => s.niche),
  );
  const unread = sources.filter((s) => s.paste && s.reader === "general"); // a site that couldn't be read today

  async function looked() {
    if (busy) return;
    setBusy(true);
    try {
      await api("/api/briefs/seen", {
        method: "POST",
        body: JSON.stringify({ ids: rows.map((b) => b.id) }),
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: key.briefs }),
        client.invalidateQueries({ queryKey: key.today }),
        client.invalidateQueries({ queryKey: key.advice }),
      ]);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div id="briefs" className="flex scroll-mt-4 flex-col gap-5">
      <section aria-labelledby={id} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id={id} className="text-base font-semibold">
            New briefs
          </h2>
          <span className="ml-auto flex items-center gap-3">
            <Link
              to="/campaigns?tab=briefs"
              className="text-sm text-fg underline underline-offset-2"
            >
              See all briefs
            </Link>
            {rows.length > 0 && (
              <Button onClick={looked} disabled={busy}>
                I've looked
              </Button>
            )}
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="text-sm text-muted">{NO_NEW}</p>
        ) : (
          <ul
            aria-label="New briefs worth a look"
            className="flex flex-col gap-3"
          >
            {rows.map((brief) => (
              <li key={brief.id}>
                <BriefRow brief={brief} onStart={onStart} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <AdviceCard advice={advice} />
      {unread.map((source) => (
        <PasteBox
          key={source.id}
          id={`paste-${source.id}`}
          source={source}
          onStart={onStart}
        />
      ))}
    </div>
  );
}
