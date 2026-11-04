// A campaign made from a brief, still to join (SPEC 10.11): Open shows its page in your default browser, by
// the link ClipForge stored for it, and I've joined says you joined it there, which ends the To do item and
// the triangle's line. Joining is always your own click on the marketplace; ClipForge never joins for you.

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import { Button } from "./Button.tsx";
import { useToast } from "./Toasts.tsx";

const OPENED = "Opened the campaign page in your browser.";

export function JoinButtons({
  campaignId,
  onOpen,
}: {
  campaignId: string;
  onOpen?: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function open() {
    onOpen?.();
    try {
      await api(`/api/links/${encodeURIComponent(`${campaignId}:page`)}/open`, {
        method: "POST",
      });
      toast({ text: OPENED, kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  async function joined() {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/api/campaigns/${encodeURIComponent(campaignId)}`, {
        method: "PATCH",
        body: JSON.stringify({ joined: true }),
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: key.today }),
        client.invalidateQueries({ queryKey: key.campaigns }),
        client.invalidateQueries({ queryKey: key.campaign(campaignId) }),
      ]);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex shrink-0 gap-2">
      <Button onClick={open}>Open</Button>
      <Button onClick={joined} disabled={busy}>
        I've joined
      </Button>
    </span>
  );
}
