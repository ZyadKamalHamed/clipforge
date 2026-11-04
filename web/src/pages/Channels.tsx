// The Channels page (SPEC 14.9 and 10.9): the clipping channels, a card each, and New clipping channel
// (also opened by a link to /channels?new=1, as Today's is). Each checks its YouTube channels once a day and
// leaves drafts on Today; nothing posts until you approve it there.

import { useQuery } from "@tanstack/react-query";
import { Plus, Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { sentence } from "../api.ts";
import { Button } from "../components/Button.tsx";
import { ChannelCard } from "../components/ChannelCard.tsx";
import { ChannelDialog } from "../components/ChannelDialog.tsx";
import { Empty } from "../components/Empty.tsx";
import { accountsQuery, channelsQuery } from "../state.ts";

const ABOUT = "Each one checks its YouTube channels once a day and leaves new clips on Today as drafts.";

export function Channels() {
  const channels = useQuery(channelsQuery);
  const accounts = useQuery(accountsQuery);
  const [params, setParams] = useSearchParams();
  const [making, setMaking] = useState(false);

  // ?new=1 opens New clipping channel once, then leaves the address, so a reload doesn't open it again.
  useEffect(() => {
    if (params.get("new") !== "1") return;
    setMaking(true);
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        next.delete("new");
        return next;
      },
      { replace: true },
    );
  }, [params, setParams]);

  const add = (
    <Button variant="primary" onClick={() => setMaking(true)}>
      <Plus aria-hidden size={16} />
      New clipping channel
    </Button>
  );
  const all = channels.data?.channels ?? [];

  let body;
  if (channels.isPending) body = <p className="text-sm text-muted">Loading the clipping channels.</p>;
  else if (channels.isError) body = <p className="text-sm text-bad">{sentence(channels.error)}</p>;
  else if (all.length === 0)
    body = (
      <Empty icon={Radio} title="No clipping channels yet." action={add}>
        <p>{ABOUT} Nothing posts until you approve it.</p>
      </Empty>
    );
  else
    body = (
      <div className="flex flex-col gap-4">
        {all.map((channel) => (
          <ChannelCard key={channel.id} channel={channel} accounts={accounts.data?.accounts ?? []} />
        ))}
      </div>
    );

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">Channels</h1>
          {all.length > 0 && <p className="text-sm text-muted">{ABOUT}</p>}
        </div>
        {all.length > 0 && add}
      </div>
      {body}
      {making && <ChannelDialog channel={null} onClose={() => setMaking(false)} />}
    </div>
  );
}
