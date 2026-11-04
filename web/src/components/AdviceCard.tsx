// Accounts worth adding (SPEC 10.11): up to 3 niches with the most money on offer that no account of yours
// covers, each with why (its live briefs, their budget left and the best 3), the accounts to make, three name
// ideas, a bio, a picture idea, the clipping channel to set up and a posting plan. Set it up opens a new
// clipping channel filled from it, made paused, once an account is tagged with the niche; Not now hides the
// suggestion and Not this niche the whole niche, for 30 days. Below, the briefs an account you have could
// take today. ClipForge never makes or logs into an account; you make them yourself.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { adviceFacts, makeLine, tagHint, worthText } from "../lib/briefs.ts";
import { settingsQuery } from "../state.ts";
import type { Advice, AdviceBody, Suggestion } from "../types.ts";
import { Button } from "./Button.tsx";
import { ChannelDialog, type ChannelStart } from "./ChannelDialog.tsx";
import { useToast } from "./Toasts.tsx";

export function AdviceCard({ advice }: { advice: Advice }) {
  const id = useId();
  const [starting, setStarting] = useState<ChannelStart | null>(null);
  if (advice.suggestions.length === 0 && advice.could_take.length === 0)
    return null;
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Accounts worth adding
      </h2>
      {advice.suggestions.length === 0 && (
        <p className="text-sm text-muted">
          No niche needs a new account today.
        </p>
      )}
      {advice.suggestions.map((s) => (
        <Idea
          key={`${s.niche}:${s.language}`}
          suggestion={s}
          onSetUp={setStarting}
        />
      ))}
      {advice.could_take.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-semibold">
            Your accounts could take today
          </h3>
          <ul className="flex list-disc flex-col gap-0.5 pl-5 text-sm">
            {advice.could_take.map((line, i) => (
              // its place is its key: two lines can read the same
              <li key={i} dir="auto">
                {line}
              </li>
            ))}
          </ul>
        </div>
      )}
      {starting && (
        <ChannelDialog
          channel={null}
          start={starting}
          onClose={() => setStarting(null)}
        />
      )}
    </section>
  );
}

function Idea({
  suggestion: s,
  onSetUp,
}: {
  suggestion: Suggestion;
  onSetUp: (start: ChannelStart) => void;
}) {
  const hintId = useId();
  const client = useQueryClient();
  const toast = useToast();
  const settings = useQuery(settingsQuery);
  const [busy, setBusy] = useState(false);
  const niche = settings.data?.values.briefs.niches.find(
    (n) =>
      n.name.toLowerCase() === s.niche.toLowerCase() &&
      n.language === s.language,
  );
  const ready = s.channel.account_ids.length > 0; // an account is tagged with the niche
  const make = makeLine(s.make);
  const footage = s.channel.sources.length;

  async function hide(kind: "suggestion" | "niche") {
    if (busy) return;
    setBusy(true);
    try {
      const body = await api<AdviceBody>("/api/advice/hide", {
        method: "POST",
        body: JSON.stringify({ kind, niche: s.niche, language: s.language }),
      });
      client.setQueryData(key.advice, body);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      aria-label={s.label}
      className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 dir="auto" className="text-sm font-semibold">
          {s.label}
        </h3>
        {s.niche !== s.label && (
          <span dir="auto" className="text-xs text-muted">
            {s.niche}
          </span>
        )}
      </div>
      <p className="text-sm tabular-nums">{adviceFacts(s)}</p>
      {s.best.length > 0 && (
        <ul
          aria-label={`The best briefs for ${s.label}`}
          className="flex flex-col gap-0.5 text-sm text-muted"
        >
          {s.best.map((b) => (
            <li key={b.id}>
              <span dir="auto">
                {b.brand ? `${b.brand}: ${b.title}` : b.title}
              </span>
              , {worthText(b.worth)}
            </li>
          ))}
        </ul>
      )}
      {make && <p className="text-sm font-medium">{make}</p>}
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-muted">Name ideas</dt>
        <dd dir="auto">{s.names.join(", ")}</dd>
        <dt className="text-muted">Bio</dt>
        <dd dir="auto">{s.bio}</dd>
        <dt className="text-muted">Picture</dt>
        <dd dir="auto">{s.picture}</dd>
        <dt className="text-muted">Clipping channel</dt>
        <dd dir="auto">
          {footage === 0
            ? `${s.channel.name}; its briefs name no footage, so you add the YouTube channels`
            : `${s.channel.name}, from ${footage} footage link${footage === 1 ? "" : "s"} in its briefs`}
        </dd>
        <dt className="text-muted">Posting plan</dt>
        <dd>{s.plan}</dd>
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!ready}
          aria-describedby={ready ? undefined : hintId}
          onClick={() =>
            onSetUp({
              name: s.channel.name,
              accountIds: s.channel.account_ids,
              sources: s.channel.sources,
              paused: true,
            })
          }
        >
          Set it up
        </Button>
        <Button onClick={() => hide("suggestion")} disabled={busy}>
          Not now
        </Button>
        <Button variant="ghost" onClick={() => hide("niche")} disabled={busy}>
          Not this niche
        </Button>
      </div>
      {!ready && (
        <p id={hintId} className="text-xs text-muted">
          {tagHint(niche?.tags ?? [])}
        </p>
      )}
    </article>
  );
}
