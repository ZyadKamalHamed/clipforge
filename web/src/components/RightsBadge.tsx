// Why a project's clips may be posted (SPEC 6.2), as a badge in its header that opens the five reasons, each
// with one line of what it means. Picking one saves it at once and closes the list.

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ShieldAlert, ShieldCheck } from "lucide-react";
import { Popover } from "radix-ui";
import { useId, useState } from "react";

import { api, key, sentence } from "../api.ts";
import type { Project } from "../types.ts";
import { useToast } from "./Toasts.tsx";

export type RightsBasis = "not_cleared" | "own" | "cc" | "permission" | "clipping_programme";

export const BASES: { value: RightsBasis; label: string; meaning: string }[] = [
  { value: "not_cleared", label: "Not cleared", meaning: "Someone else's video you haven't cleared yet." },
  { value: "own", label: "Own channel", meaning: "Your own video, from your own channel." },
  { value: "cc", label: "Creative Commons", meaning: "Its creator lets anyone reuse it with credit." },
  { value: "permission", label: "Permission", meaning: "The creator said you can clip it." },
  { value: "clipping_programme", label: "Clipping programme", meaning: "The creator invites clippers, often for pay." },
];

export function rightsLabel(basis: string): string {
  return BASES.find((b) => b.value === basis)?.label ?? basis;
}

export function RightsBadge({ project }: { project: Project }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const [open, setOpen] = useState(false);
  // The pick shows as soon as it's made, while it saves; the cache only changes once it's saved.
  const [value, setValue] = useState(project.rights_basis);
  const label = rightsLabel(project.rights_basis);
  const cleared = project.rights_basis !== "not_cleared";
  const Icon = cleared ? ShieldCheck : ShieldAlert;

  function show(next: boolean) {
    if (next) setValue(project.rights_basis);
    setOpen(next);
  }

  async function pick(basis: RightsBasis) {
    setValue(basis);
    try {
      const updated = await api<Project>(`/api/projects/${encodeURIComponent(project.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ rights_basis: basis }),
      });
      client.setQueryData(key.project(project.id), updated);
      void client.invalidateQueries({ queryKey: key.projects });
      setOpen(false);
    } catch (err) {
      setValue(project.rights_basis);
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  return (
    <Popover.Root open={open} onOpenChange={show}>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={`Rights: ${label}`}
          className={
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium text-fg " +
            (cleared ? "border-line bg-panel-2 hover:border-faint" : "border-warn/60 bg-warn/10 hover:border-warn")
          }
        >
          <Icon aria-hidden size={14} className={cleared ? "text-ok" : "text-warn"} />
          {label}
          <ChevronDown aria-hidden size={14} className="text-muted" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          aria-label="Rights"
          align="start"
          sideOffset={6}
          className="z-50 w-80 rounded-card border border-line bg-panel p-2 shadow-2xl"
        >
          <fieldset className="flex flex-col gap-0.5">
            <legend className="px-2 pt-1 pb-2 text-xs font-medium text-muted">Why its clips may be posted</legend>
            {BASES.map((basis) => (
              <label
                key={basis.value}
                className={
                  "flex cursor-pointer items-start gap-3 rounded-control px-2 py-1.5 hover:bg-panel-2 " +
                  "has-[:checked]:bg-panel-2"
                }
              >
                <input
                  type="radio"
                  name="rights"
                  value={basis.value}
                  checked={value === basis.value}
                  onChange={() => void pick(basis.value)}
                  aria-labelledby={`${id}-${basis.value}`}
                  aria-describedby={`${id}-${basis.value}-means`}
                  className="mt-0.5 size-4 shrink-0 accent-accent-strong"
                />
                <span className="flex flex-col">
                  <span id={`${id}-${basis.value}`} className="text-sm font-medium">
                    {basis.label}
                  </span>
                  <span id={`${id}-${basis.value}-means`} className="text-xs text-muted">
                    {basis.meaning}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
