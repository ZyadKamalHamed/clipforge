// The Trends page (SPEC 13 and 14.1): the sounds to pick for a post and where they come from. The filters
// over the sounds; what Instagram says is trending for each account linked through Facebook, read each
// morning, with Check now; the accounts Instagram gives no list; your TikTok shortlist
// (components/Shortlist.tsx); the four weekly lists that open in your browser, with the New list out badge
// and the switch for the feed that lights it; and your hashtag sets (components/HashtagSets.tsx).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useId, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { Badge } from "../components/Badge.tsx";
import { Button } from "../components/Button.tsx";
import { Toggle } from "../components/Field.tsx";
import { HashtagSets } from "../components/HashtagSets.tsx";
import { MOODS } from "../components/MusicList.tsx";
import { CELL, HEAD, NONE, Note, OpenSound, SelectField, Shortlist } from "../components/Shortlist.tsx";
import { useToast } from "../components/Toasts.tsx";
import { formatDuration } from "../lib/format.ts";
import {
  ANY,
  matches,
  modeBLine,
  NO_MATCH,
  readLine,
  type SoundFilters,
  showsPlatform,
  soundType,
} from "../lib/trends.ts";
import { settingsQuery } from "../state.ts";
import type { SettingsBody, Trends as TrendsView } from "../types.ts";

export const NO_FACEBOOK = "No Instagram account is linked through Facebook yet.";
export const PING = "Check SocialPilot's feed once a week for a new list";

type Block = TrendsView["instagram"][number];
type List = TrendsView["lists"][number];

export function Trends() {
  const trends = useQuery({
    queryKey: key.trends,
    queryFn: ({ signal }) => api<TrendsView>("/api/trends", { signal }),
  });
  const [filters, setFilters] = useState<SoundFilters>(ANY);

  let body;
  if (trends.isPending) body = <p className="text-sm text-muted">Loading the trends.</p>;
  else if (trends.isError) body = <p className="text-sm text-bad">{sentence(trends.error)}</p>;
  else {
    const data = trends.data;
    body = (
      <>
        <Filters filters={filters} onChange={(f) => setFilters((was) => ({ ...was, ...f }))} />
        {showsPlatform(filters, "instagram") && (
          <Instagram blocks={data.instagram} modeB={data.mode_b} filters={filters} />
        )}
        {showsPlatform(filters, "tiktok") && <Shortlist sounds={data.shortlist} filters={filters} />}
        <WeeklyLists lists={data.lists} badge={data.badge} />
        <HashtagSets sets={data.sets} />
      </>
    );
  }
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">Trends</h1>
        <p className="text-sm text-muted">
          Sounds to pick for a post in the Post dialog, the weekly lists of what's trending, and your hashtag sets.
        </p>
      </div>
      {body}
    </div>
  );
}

function Filters({ filters, onChange }: { filters: SoundFilters; onChange: (f: Partial<SoundFilters>) => void }) {
  return (
    <div
      role="group"
      aria-label="Filter sounds"
      className="flex flex-wrap items-end gap-x-5 gap-y-3 rounded-card border border-line bg-panel px-4 py-3"
    >
      <SelectField
        label="Platform"
        value={filters.platform}
        onChange={(v) => onChange({ platform: v as SoundFilters["platform"] })}
        options={[
          ["", "TikTok and Instagram"],
          ["tiktok", "TikTok"],
          ["instagram", "Instagram"],
        ]}
      />
      <SelectField
        label="Mood"
        value={filters.mood}
        onChange={(v) => onChange({ mood: v })}
        options={[["", "Any mood"], ...Object.entries(MOODS)]}
      />
      <SelectField
        label="Region"
        value={filters.region}
        onChange={(v) => onChange({ region: v as SoundFilters["region"] })}
        options={[
          ["", "Any region"],
          ["AU", "AU"],
          ["US", "US"],
        ]}
      />
      <div className="min-w-36">
        <Toggle label="Hide stale" checked={filters.hideStale} onChange={(on) => onChange({ hideStale: on })} />
      </div>
    </div>
  );
}

function Instagram({ blocks, modeB, filters }: { blocks: Block[]; modeB: (string | null)[]; filters: SoundFilters }) {
  const id = useId();
  const noList = modeBLine(modeB);
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        Trending on Instagram
      </h2>
      <p className="text-sm text-muted">
        What Instagram says is trending for each account linked through Facebook, read each morning. Instagram adds
        the one you pick for a post.
      </p>
      {blocks.length > 0 && (filters.mood || filters.region) && (
        <p className="text-xs text-muted">Instagram doesn't say a sound's mood or region, so those filters hide its lists.</p>
      )}
      {blocks.length === 0 ? (
        <Note>{NO_FACEBOOK}</Note>
      ) : (
        <div className="flex flex-col gap-4">
          {blocks.map((b) => (
            <AccountBlock key={b.account.id} block={b} filters={filters} />
          ))}
        </div>
      )}
      {noList && <p className="text-sm text-muted">{noList}</p>}
    </section>
  );
}

function AccountBlock({ block, filters }: { block: Block; filters: SoundFilters }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const handle = `@${block.account.handle}`;
  const line = readLine(block.read, Date.now() / 1000);
  const shown = block.sounds.filter((s) => matches(s, filters));
  const check = useMutation({
    mutationFn: () =>
      api<Block>(`/api/trends/instagram/${encodeURIComponent(block.account.id)}/check`, { method: "POST" }),
    onSuccess: () => toast({ text: `Read the trending sounds for ${handle}.`, kind: "ok" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => client.invalidateQueries({ queryKey: key.trends }),
  });

  let rows;
  if (block.sounds.length === 0)
    rows = <p className="text-sm text-muted">{block.read ? "Instagram listed no sounds for it." : "No sounds read yet."}</p>;
  else if (shown.length === 0) rows = <p className="text-sm text-muted">{NO_MATCH}</p>;
  else
    rows = (
      <div className="overflow-x-auto rounded-card border border-line">
        <table aria-label={`Trending for ${handle}`} className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              {["Sound", "Artist", "Type"].map((label) => (
                <th key={label} scope="col" className={HEAD}>
                  {label}
                </th>
              ))}
              <th scope="col" className={`${HEAD} text-right`}>
                Length
              </th>
              <th scope="col" className={HEAD}>
                <span className="sr-only">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((sound) => (
              <tr key={sound.id} className="border-b border-line last:border-0">
                <th scope="row" className={`${CELL} font-medium`} dir="auto">
                  {sound.title}
                </th>
                <td className={CELL} dir="auto">
                  {sound.artist ?? NONE}
                </td>
                <td className={CELL}>{soundType(sound) ?? NONE}</td>
                <td className={`${CELL} text-right tabular-nums`}>
                  {sound.duration_s === null ? NONE : formatDuration(sound.duration_s)}
                </td>
                <td className={`${CELL} text-right`}>
                  <OpenSound sound={sound} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );

  return (
    <article aria-labelledby={id} className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 id={id} className="text-sm font-semibold" dir="auto">
            {handle}
          </h3>
          <p className={`flex flex-wrap items-center gap-2 text-xs ${line.bad ? "text-bad" : "text-muted"}`}>
            {line.text}
            {block.sounds.some((s) => s.stale) && <Badge tone="warn">Stale</Badge>}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Button onClick={() => check.mutate()} disabled={!block.can_check || check.isPending}>
            <RefreshCw aria-hidden size={14} />
            Check now
          </Button>
          {!block.can_check && (
            <p className="text-xs text-muted">Link it again on the Accounts page so ClipForge can read its list.</p>
          )}
        </div>
      </div>
      {rows}
    </article>
  );
}

function WeeklyLists({ lists, badge }: { lists: List[]; badge: boolean }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const settings = useQuery(settingsQuery);
  const [ping, setPing] = useState<boolean | null>(null); // what you just switched it to, while it saves
  const trends = settings.data?.values.trends;
  const on = ping ?? trends?.list_ping ?? true;

  async function open(list: List) {
    try {
      await api(`/api/links/${encodeURIComponent(list.link_id)}/open`, { method: "POST" });
      toast({ text: `Opened ${list.label} in your browser.`, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      return;
    }
    try {
      await api<void>("/api/trends/lists/seen", { method: "POST" });
    } finally {
      await client.invalidateQueries({ queryKey: key.trends });
    }
  }

  async function switchPing(next: boolean) {
    setPing(next);
    try {
      const saved = await api<SettingsBody>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ trends: { ...trends, list_ping: next } }),
      });
      client.setQueryData(settingsQuery.queryKey, saved);
      toast({ text: "Saved", kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setPing(null);
    }
  }

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={id} className="text-base font-semibold">
          Weekly lists
        </h2>
        {badge && <Badge tone="info">New list out</Badge>}
      </div>
      <p className="text-sm text-muted">
        Later, Buffer and SocialPilot each post a list of the sounds trending on TikTok and Instagram. They open in your
        browser; ClipForge never reads them.
      </p>
      <div className="flex flex-wrap gap-2">
        {lists.map((list) => (
          <Button key={list.name} onClick={() => void open(list)}>
            <ExternalLink aria-hidden size={14} />
            {list.label}
          </Button>
        ))}
      </div>
      <div className="max-w-2xl rounded-card border border-line bg-panel px-4 py-2">
        <Toggle
          label={PING}
          hint="Only the date of its newest post is read, to say when a list is newer than your last look."
          checked={on}
          onChange={(next) => void switchPing(next)}
        />
      </div>
    </section>
  );
}
