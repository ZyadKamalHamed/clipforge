// Add source (SPEC 14.9): one more YouTube channel for a clipping channel to watch, by a link to it or to
// one of its videos, and how many of its latest videos to clip at the next check. A link it can't use stays
// in the field with the engine's sentence beside it.

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { BACKFILL_MAX } from "../lib/channels.ts";
import type { ClipChannel, WatchedSourceInfo } from "../types.ts";
import { Button } from "./Button.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

export const SELECT =
  "rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

// "None, only new uploads", "1 video", "2 videos", up to 5.
export function backfillLabel(n: number): string {
  return n === 0 ? "None, only new uploads" : n === 1 ? "1 video" : `${n} videos`;
}

type Props = { channel: ClipChannel; onClose: () => void };

export function AddSource({ channel, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const latest = useId();
  const [link, setLink] = useState("");
  const [backfill, setBackfill] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function add() {
    if (busy) return;
    if (!link.trim()) {
      setProblem("Paste a link to a YouTube channel or one of its videos.");
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const { source } = await api<{ source: WatchedSourceInfo }>(
        `/api/clip-channels/${encodeURIComponent(channel.id)}/sources`,
        { method: "POST", body: JSON.stringify({ link: link.trim(), backfill }) },
      );
      void client.invalidateQueries({ queryKey: key.channels });
      toast({ text: `${channel.name} now watches ${source.title || "that channel"}`, kind: "ok" });
      onClose();
    } catch (err) {
      setProblem(sentence(err)); // every refusal is about the link but the count, which the menu keeps in range
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Add a source to ${channel.name}`}
      description="A YouTube channel it checks every day, by a link to the channel or one of its videos."
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <div className="flex flex-col gap-4">
        <TextField
          label="Link"
          value={link}
          onChange={setLink}
          onEnter={() => void add()}
          error={problem}
          placeholder="https://www.youtube.com/@name"
        />
        <div className="flex flex-col gap-1">
          <label htmlFor={latest} className="text-xs font-medium text-muted">
            Also clip the latest
          </label>
          <select
            id={latest}
            value={backfill}
            onChange={(e) => setBackfill(Number(e.target.value))}
            className={`${SELECT} self-start`}
          >
            {Array.from({ length: BACKFILL_MAX + 1 }, (_, n) => (
              <option key={n} value={n}>
                {backfillLabel(n)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" onClick={() => void add()} disabled={busy}>
          {busy ? "Adding." : "Add"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
