// The campaign a project's clips are made for (SPEC 10.10), picked in its header. Picking one saves it at
// once: linked, its clips follow the campaign's rules and its rights are a clipping programme; None
// unlinks it. A campaign that doesn't allow this video's footage says so beside the picker, which goes
// back to what it was.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { campaignsQuery } from "../state.ts";
import type { Project } from "../types.ts";
import { FieldError } from "./Field.tsx";

const SELECT =
  "rounded-control border border-line bg-panel-2 px-2 py-1 text-xs text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

export function CampaignPicker({ project }: { project: Project }) {
  const client = useQueryClient();
  const campaigns = useQuery(campaignsQuery);
  const id = useId();
  // The pick shows while it saves; otherwise the picker shows what is saved.
  const [pending, setPending] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const all = campaigns.data?.campaigns ?? [];
  const offered = all.filter((c) => c.status === "active" || c.id === project.campaign_id);
  if (all.length === 0 && !project.campaign_id) return null; // nothing to pick until there is a campaign

  async function pick(next: string) {
    setPending(next);
    setProblem(null);
    try {
      const updated = await api<Project>(`/api/projects/${encodeURIComponent(project.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ campaign_id: next || null }),
      });
      client.setQueryData(key.project(project.id), updated);
      void client.invalidateQueries({ queryKey: key.projects });
      void client.invalidateQueries({ queryKey: key.campaigns });
    } catch (err) {
      setProblem(sentence(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-xs font-medium text-muted">
          Campaign
        </label>
        <select
          id={id}
          value={pending ?? project.campaign_id ?? ""}
          disabled={pending !== null}
          onChange={(e) => void pick(e.target.value)}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-error` : undefined}
          className={SELECT}
        >
          <option value="">None</option>
          {offered.map((c) => (
            <option key={c.id} value={c.id}>
              {c.status === "ended" ? `${c.name} (ended)` : c.name}
            </option>
          ))}
        </select>
      </div>
      {problem && <FieldError id={`${id}-error`} text={problem} />}
    </div>
  );
}
