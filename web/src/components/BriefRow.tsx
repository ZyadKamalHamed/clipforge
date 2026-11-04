// One brief on the Briefs tab (SPEC 10.11): its brand and title, tagged Reposting when it asks you to post its
// ready-made videos as they are, its marketplace and platforms, the rate, the budget left with the day that
// was read, the accounts it fits and what it could earn a day. Open shows its page in your browser, only when
// ClipForge may open it; Use this campaign fills the New campaign form from it (and opens its page); Hide
// puts it away, with Undo. Campaign Scout's notes stay closed until you open them. Every word of it is the
// marketplace's, shown as text.

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import {
  budgetText,
  fitText,
  platformText,
  rateText,
  worthText,
} from "../lib/briefs.ts";
import type { Brief, BriefsBody, UseResult } from "../types.ts";
import { Button } from "./Button.tsx";
import type { FormStart } from "./CampaignForm.tsx";
import { useToast } from "./Toasts.tsx";

const OPENED = "Opened the brief's page in your browser.";

export function BriefRow({
  brief,
  onStart,
}: {
  brief: Brief;
  onStart: (start: FormStart) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [using, setUsing] = useState(false);
  const notes = Object.entries(brief.notes);

  const path = `/api/briefs/${encodeURIComponent(brief.id)}`;

  async function open(link: string) {
    try {
      await api(`/api/links/${encodeURIComponent(link)}/open`, {
        method: "POST",
      });
      toast({ text: OPENED, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  async function use() {
    if (using) return;
    setUsing(true);
    try {
      const result = await api<UseResult>(`${path}/use`, { method: "POST" });
      onStart({
        fields: result.fields,
        filled: result.filled,
        briefId: result.brief_id,
        rules: result.fields.rules_text,
        note: result.sentence,
      });
      if (result.opened) toast({ text: OPENED, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      void client.invalidateQueries({ queryKey: key.briefs }); // it may be a campaign now, or gone
    } finally {
      setUsing(false);
    }
  }

  async function change(status: "hidden" | "seen") {
    await api(path, { method: "PATCH", body: JSON.stringify({ status }) });
    void client.invalidateQueries({ queryKey: key.briefs });
  }

  async function hide() {
    client.setQueryData<BriefsBody>(key.briefs, (old) =>
      old ? { briefs: old.briefs.filter((b) => b.id !== brief.id) } : old,
    );
    try {
      await change("hidden");
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      void client.invalidateQueries({ queryKey: key.briefs });
      return;
    }
    toast({
      text: "Brief hidden.",
      kind: "ok",
      action: {
        label: "Undo",
        run: () =>
          void change("seen").catch((err: unknown) =>
            toast({ text: sentence(err), kind: "bad" }),
          ),
      },
    });
  }

  const meta = [brief.marketplace, platformText(brief.platforms), brief.niche]
    .filter(Boolean)
    .join(" · ");
  return (
    <article
      aria-label={brief.title}
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-col gap-0.5">
          {brief.brand && (
            <p className="text-xs text-muted" dir="auto">
              {brief.brand}
            </p>
          )}
          <h3 className="font-medium">
            <span dir="auto">{brief.title}</span>
            {brief.status === "new" && (
              <span className="ml-2 text-xs font-normal text-warn">New</span>
            )}
            {brief.reposting && (
              <span className="ml-2 rounded-full border border-ok px-2 py-0.5 text-xs font-medium text-ok">
                Reposting
              </span>
            )}
          </h3>
          <p className="text-xs text-muted" dir="auto">
            {meta}
          </p>
        </div>
        <p className="text-sm font-medium tabular-nums">
          {worthText(brief.worth)}
        </p>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        <Fact term="Rate" text={rateText(brief)} />
        <Fact term="Budget" text={budgetText(brief)} />
        <Fact term="Accounts" text={fitText(brief)} />
      </dl>
      {notes.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted hover:text-fg">
            {brief.source_id === "bs_scout"
              ? "Campaign Scout's notes"
              : "Notes"}
          </summary>
          <dl className="mt-2 flex flex-col gap-1">
            {notes.map(([term, text]) => (
              <div key={term} className="flex flex-wrap gap-x-2">
                <dt className="text-muted" dir="auto">
                  {term}
                </dt>
                <dd dir="auto">{text}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <div className="flex flex-wrap gap-2">
        {brief.link_id && (
          <Button onClick={() => void open(brief.link_id as string)}>
            <ExternalLink aria-hidden size={16} />
            Open
          </Button>
        )}
        <Button variant="primary" onClick={() => void use()} disabled={using}>
          {using ? "Reading the brief." : "Use this campaign"}
        </Button>
        <Button variant="ghost" onClick={() => void hide()}>
          Hide
        </Button>
      </div>
    </article>
  );
}

function Fact({ term, text }: { term: string; text: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted">{term}</dt>
      <dd className="tabular-nums">{text}</dd>
    </div>
  );
}
