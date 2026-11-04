// Settings' Brief sources (SPEC 10.11): each source with its switch, how it is read, its terms note and its
// last result, and Check now for one ClipForge reads (never for one whose terms forbid reading it, and only as
// often as the site's pacing allows). Add a site takes a site's public list page only once you've ticked that
// its terms allow reading it. Joining, submitting and payouts stay on the marketplaces, by your own clicks.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import { checkAgain, readerLine, resultLine } from "../lib/briefs.ts";
import { briefSourcesQuery } from "../state.ts";
import type { BriefSource, BriefSourcesBody } from "../types.ts";
import { Button } from "./Button.tsx";
import { FieldError, TextField, Toggle } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

const TICK = "I've read this site's terms and they allow automated reading";

export function SourceList() {
  const sources = useQuery(briefSourcesQuery);
  return (
    <>
      <p className="text-sm text-muted">
        Each morning ClipForge reads the briefs from these. A site is read from
        its public list page only, at most one page every 5 seconds and as its
        robots.txt allows. Whop and Vyro don't allow it, so you paste their
        briefs.
      </p>
      {sources.isPending && (
        <p className="text-sm text-muted">Loading the sources.</p>
      )}
      {sources.isError && (
        <p className="text-sm text-bad">{sentence(sources.error)}</p>
      )}
      {sources.isSuccess && (
        <ul className="flex flex-col">
          {sources.data.sources.map((source) => (
            <li
              key={source.id}
              className="border-t border-line py-3 first:border-t-0 first:pt-0"
            >
              <SourceRow source={source} />
            </li>
          ))}
        </ul>
      )}
      <AddSite />
    </>
  );
}

// One source changed, as the engine sent it back, in the list everyone reads.
function useReplace() {
  const client = useQueryClient();
  return (source: BriefSource) =>
    client.setQueryData<BriefSourcesBody>(key.briefSources, (old) =>
      old
        ? { sources: old.sources.map((s) => (s.id === source.id ? source : s)) }
        : old,
    );
}

function SourceRow({ source }: { source: BriefSource }) {
  const client = useQueryClient();
  const toast = useToast();
  const replace = useReplace();
  const [, setTick] = useState(0);
  const [asking, setAsking] = useState(false);
  // What the switch was set to, shown at once until the engine answers (a held-back value would snap back).
  const [wanted, setWanted] = useState<boolean | null>(null);
  const now = Date.now() / 1000;
  const result = resultLine(source, now);
  const again = checkAgain(source.check_now_after, now);
  const bad = source.last_result !== null && source.last_result !== "ok";

  // While Check now is shut, the page looks again each minute, so it opens on time.
  useEffect(() => {
    if (again === null || source.check_now_after === null) return;
    const ms = Math.min(source.check_now_after * 1000 - Date.now(), 60_000);
    const timer = window.setTimeout(
      () => setTick((n) => n + 1),
      Math.max(ms, 1000),
    );
    return () => window.clearTimeout(timer);
  });

  async function use(on: boolean) {
    setWanted(on);
    try {
      const body = await api<{ source: BriefSource }>(
        `/api/brief-sources/${encodeURIComponent(source.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ enabled: on }),
        },
      );
      replace(body.source);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      void client.invalidateQueries({ queryKey: key.briefSources });
    } finally {
      setWanted(null);
    }
  }

  async function check() {
    if (asking) return;
    setAsking(true);
    try {
      const body = await api<{ source: BriefSource }>(
        `/api/brief-sources/${encodeURIComponent(source.id)}/check`,
        {
          method: "POST",
        },
      );
      replace(body.source);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      void client.invalidateQueries({ queryKey: key.briefSources });
    } finally {
      setAsking(false);
    }
  }

  return (
    <article aria-label={source.name} className="flex flex-col gap-1.5">
      <h3 dir="auto" className="text-sm font-semibold">
        {source.name}
      </h3>
      <p dir="auto" className="text-xs break-words text-muted">
        {readerLine(source)}
      </p>
      {source.terms_note && (
        <p dir="auto" className="text-xs text-faint">
          {source.terms_note}
        </p>
      )}
      {result && (
        <p dir="auto" className={`text-xs ${bad ? "text-warn" : "text-muted"}`}>
          {result}
        </p>
      )}
      <Toggle
        label={`Use ${source.name}`}
        checked={wanted ?? source.enabled}
        onChange={(on) => void use(on)}
      />
      {source.reader !== "paste" && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={check}
            disabled={asking || source.reading || again !== null}
          >
            Check now
          </Button>
          {source.reading && (
            <span className="text-xs text-muted">Reading it now.</span>
          )}
          {!source.reading && again && (
            <span className="text-xs text-muted">{again}</span>
          )}
        </div>
      )}
    </article>
  );
}

type Problem = "name" | "list_url" | "terms_read" | "form";

function AddSite() {
  const client = useQueryClient();
  const toast = useToast();
  const tickId = useId();
  const [name, setName] = useState("");
  const [link, setLink] = useState("");
  const [ticked, setTicked] = useState(false);
  const [problems, setProblems] = useState<Partial<Record<Problem, string>>>(
    {},
  );
  const [busy, setBusy] = useState(false);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setProblems({});
    try {
      const body = await api<{ source: BriefSource }>("/api/brief-sources", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          list_url: link.trim(),
          terms_read: ticked,
        }),
      });
      setName("");
      setLink("");
      setTicked(false);
      void client.invalidateQueries({ queryKey: key.briefSources });
      toast({ text: `${body.source.name} added`, kind: "ok" });
    } catch (err) {
      const field = err instanceof ApiError ? err.field : null;
      const where: Problem =
        field === "name" || field === "list_url" || field === "terms_read"
          ? field
          : "form";
      setProblems({ [where]: sentence(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      aria-label="Add a site"
      onSubmit={add}
      noValidate
      className="flex flex-col gap-3 rounded-card border border-line bg-panel-2 p-3"
    >
      <h3 className="text-sm font-semibold">Add a site</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Name"
          value={name}
          onChange={setName}
          maxLength={200}
          error={problems.name}
        />
        <TextField
          label="Public list page"
          value={link}
          onChange={setLink}
          maxLength={2000}
          placeholder="https://"
          error={problems.list_url}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={ticked}
            onChange={(e) => setTicked(e.target.checked)}
            aria-invalid={problems.terms_read ? true : undefined}
            aria-describedby={
              problems.terms_read ? `${tickId}-error` : undefined
            }
            className="mt-0.5 size-4 shrink-0 cursor-pointer accent-accent-strong"
          />
          <span>{TICK}</span>
        </label>
        {problems.terms_read && (
          <FieldError id={`${tickId}-error`} text={problems.terms_read} />
        )}
      </div>
      {problems.form && <FieldError text={problems.form} />}
      <div>
        <Button type="submit" disabled={busy}>
          Add the site
        </Button>
      </div>
    </form>
  );
}
