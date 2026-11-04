// The Briefs tab (SPEC 10.11): the filters by niche and marketplace, then a card a brief, the richest first,
// and a box for each marketplace whose briefs you paste today (one whose terms forbid reading it, or a site
// that couldn't be read today), which starts a new campaign from the rules you paste there.

import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";

import { sentence } from "../api.ts";
import {
  type BriefFilter,
  filterBriefs,
  filterOptions,
  NO_BRIEFS,
  NONE_MATCH,
} from "../lib/briefs.ts";
import { briefSourcesQuery, briefsQuery } from "../state.ts";
import type { BriefSource } from "../types.ts";
import { SELECT } from "./AddSource.tsx";
import { BriefRow } from "./BriefRow.tsx";
import { Button } from "./Button.tsx";
import type { FormStart } from "./CampaignForm.tsx";
import { TextField } from "./Field.tsx";

const RULES_MAX = 20_000;

export function BriefList({
  onStart,
}: {
  onStart: (start: FormStart) => void;
}) {
  const briefs = useQuery(briefsQuery);
  const sources = useQuery(briefSourcesQuery);
  const [filter, setFilter] = useState<BriefFilter>({
    niche: null,
    marketplace: null,
  });
  const all = briefs.data?.briefs ?? [];
  const options = filterOptions(all);
  // A pick the briefs no longer have (the last of a niche hidden, say) is no filter at all.
  const shown: BriefFilter = {
    niche:
      filter.niche !== null && options.niches.includes(filter.niche)
        ? filter.niche
        : null,
    marketplace:
      filter.marketplace !== null &&
      options.marketplaces.includes(filter.marketplace)
        ? filter.marketplace
        : null,
  };
  const picked = filterBriefs(all, shown);
  const pasted = (sources.data?.sources ?? []).filter((s) => s.paste);

  let body;
  if (briefs.isPending)
    body = <p className="text-sm text-muted">Loading the briefs.</p>;
  else if (briefs.isError)
    body = <p className="text-sm text-bad">{sentence(briefs.error)}</p>;
  else if (all.length === 0)
    body = <p className="text-sm text-muted">{NO_BRIEFS}</p>;
  else
    body = (
      <>
        <div className="flex flex-wrap gap-3">
          <Filter
            label="Niche"
            every="All niches"
            value={shown.niche}
            options={options.niches}
            onChange={(niche) => setFilter((old) => ({ ...old, niche }))}
          />
          <Filter
            label="Marketplace"
            every="All marketplaces"
            value={shown.marketplace}
            options={options.marketplaces}
            onChange={(marketplace) =>
              setFilter((old) => ({ ...old, marketplace }))
            }
          />
        </div>
        {picked.length === 0 ? (
          <p className="text-sm text-muted">{NONE_MATCH}</p>
        ) : (
          <ul aria-label="Briefs" className="flex flex-col gap-3">
            {picked.map((brief) => (
              <li key={brief.id}>
                <BriefRow brief={brief} onStart={onStart} />
              </li>
            ))}
          </ul>
        )}
      </>
    );

  return (
    <div className="flex flex-col gap-4">
      {body}
      {pasted.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Paste briefs</h2>
          <div className="grid gap-3 lg:grid-cols-2">
            {pasted.map((source) => (
              <PasteBox key={source.id} source={source} onStart={onStart} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

type FilterProps = {
  label: string;
  every: string;
  value: string | null;
  options: string[];
  onChange: (value: string | null) => void;
};

function Filter({ label, every, value, options, onChange }: FilterProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        className={SELECT}
      >
        <option value="">{every}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

// A marketplace's briefs to paste, also on Today for a site that couldn't be read (by its id, which the To do
// list's Paste briefs goes to).
export function PasteBox({
  source,
  onStart,
  id,
}: {
  source: BriefSource;
  onStart: (start: FormStart) => void;
  id?: string;
}) {
  const [text, setText] = useState("");
  const why =
    source.reader === "paste"
      ? (source.terms_note ?? undefined)
      : `${source.name} couldn't be read today, so paste its briefs here.`;

  function start(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    onStart({
      fields: { marketplace: source.marketplace },
      filled: [],
      rules: text,
    });
    setText("");
  }

  return (
    <form
      id={id}
      aria-label={`Paste from ${source.marketplace}`}
      onSubmit={start}
      noValidate
      className="flex scroll-mt-4 flex-col gap-2 rounded-card border border-line bg-panel p-4"
    >
      <TextField
        label={`Paste a campaign's rules from ${source.marketplace}`}
        value={text}
        onChange={setText}
        multiline
        maxLength={RULES_MAX}
        hint={why}
      />
      <div>
        <Button type="submit" disabled={!text.trim()}>
          Make a campaign
        </Button>
      </div>
    </form>
  );
}
