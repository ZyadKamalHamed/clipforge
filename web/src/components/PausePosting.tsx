// The kill switch in the header (SPEC 14.1, 17): while it's on nothing new posts, and a banner under the
// header says so on every page. It is kept by the engine, so it survives a reload and a restart.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import { settingsQuery } from "../state.ts";
import type { SettingsBody } from "../types.ts";
import { Toggle } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

export const PAUSED = "Posting is paused. Nothing goes out until you turn it back on.";

export function PausePosting() {
  const client = useQueryClient();
  const toast = useToast();
  const settings = useQuery(settingsQuery);
  // What the switch was set to, shown at once until the engine answers (a held-back value would snap back).
  const [wanted, setWanted] = useState<boolean | null>(null);
  const change = useMutation({
    mutationFn: (on: boolean) => api<{ paused: boolean }>(`/api/posting/${on ? "pause" : "resume"}`, { method: "POST" }),
    onSuccess: ({ paused }) => {
      client.setQueryData<SettingsBody>(settingsQuery.queryKey, (old) =>
        old ? { ...old, values: { ...old.values, posting: { ...old.values.posting, paused } } } : old,
      );
      void client.invalidateQueries({ queryKey: key.posts }); // resuming moves posts that missed their time
    },
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: () => setWanted(null),
  });
  const posting = settings.data?.values.posting;
  if (!posting) return null;
  return (
    <Toggle
      label="Pause posting"
      checked={wanted ?? posting.paused}
      onChange={(next) => {
        setWanted(next);
        change.mutate(next);
      }}
    />
  );
}

export function PausedBanner() {
  const settings = useQuery(settingsQuery);
  if (settings.data?.values.posting?.paused !== true) return null;
  return (
    <p role="status" className="flex items-center gap-2 border-b border-line bg-panel-2 px-6 py-2 text-sm text-fg">
      <PauseCircle aria-hidden size={16} className="shrink-0 text-warn" />
      {PAUSED}
    </p>
  );
}
