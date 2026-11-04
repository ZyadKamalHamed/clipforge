// The day's To do list (SPEC 14.10 items 1 and 2): what needs you, in the order the server sends it (highest
// value first), each with what it could earn, protect or unblock, about how long it takes and its one action,
// which starts the item's timer on the way there (a campaign to join has two: Open and I've joined). Its tick
// marks it done by hand; done items fold under Done today. A step of the path goes to its page from ``links``.
// Titles and details are only ever text.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { api, key, sentence } from "../api.ts";
import { estimateLabel, itemAction, valueLabel } from "../lib/today.ts";
import type { TodoItem } from "../types.ts";
import { Badge } from "./Badge.tsx";
import { buttonClass } from "./Button.tsx";
import { JoinButtons } from "./JoinButtons.tsx";
import { useToast } from "./Toasts.tsx";

const EMPTY =
  "Nothing to do right now. New drafts and posts show up here as they come.";

const itemPath = (itemId: string, action: "start" | "tick") =>
  `/api/today/items/${encodeURIComponent(itemId)}/${action}`;

export function TodoList({
  items,
  links = {},
}: {
  items: TodoItem[];
  links?: Record<string, string | null>;
}) {
  const id = useId();
  const client = useQueryClient();
  const toast = useToast();
  // Ticked here and not yet back from the server, so each box shows its tick at once
  const [ticking, setTicking] = useState<Set<string>>(() => new Set());
  const undone = items.filter((i) => !i.done);
  const done = items.filter((i) => i.done);

  const refresh = () => client.invalidateQueries({ queryKey: key.today });
  const tick = useMutation({
    mutationFn: (itemId: string) =>
      api<TodoItem>(itemPath(itemId, "tick"), { method: "POST" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
    onSettled: async (_, __, itemId) => {
      await refresh();
      setTicking((was) => {
        const next = new Set(was);
        next.delete(itemId);
        return next;
      });
    },
  });
  const start = useMutation({
    mutationFn: (itemId: string) =>
      api<TodoItem>(itemPath(itemId, "start"), { method: "POST" }),
    onError: (err) => toast({ text: sentence(err), kind: "bad" }),
  });

  function onTick(itemId: string) {
    setTicking((was) => new Set(was).add(itemId));
    tick.mutate(itemId);
  }

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-base font-semibold">
        To do
      </h2>
      {undone.length === 0 ? (
        <p className="text-sm text-muted">{EMPTY}</p>
      ) : (
        <ul
          aria-label="Still to do"
          className="flex flex-col divide-y divide-line rounded-card border border-line"
        >
          {undone.map((item) => (
            <Row
              key={item.id}
              item={item}
              links={links}
              ticked={ticking.has(item.id)}
              onTick={() => onTick(item.id)}
              onStart={() => start.mutate(item.id)}
            />
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted hover:text-fg">{`Done today (${done.length})`}</summary>
          <ul aria-label="Done today" className="mt-2 flex flex-col gap-1.5">
            {done.map((item) => (
              <li key={item.id} className="flex items-center gap-2">
                <Check aria-hidden size={14} className="shrink-0 text-ok" />
                <span dir="auto">{item.title}</span>
                <span className="text-xs text-muted">
                  {item.done_how === "hand" ? "ticked" : "done in ClipForge"}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

type RowProps = {
  item: TodoItem;
  links: Record<string, string | null>;
  ticked: boolean;
  onTick: () => void;
  onStart: () => void;
};

// One item: its tick (named by its title), what it is worth and takes, its badges and its action.
function Row({ item, links, ticked, onTick, onStart }: RowProps) {
  const id = useId();
  const act = itemAction(item, links);
  const facts = [valueLabel(item), estimateLabel(item.estimate_s)]
    .filter(Boolean)
    .join(" · ");
  const action = buttonClass("secondary", "shrink-0");
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <input
        id={id}
        type="checkbox"
        checked={ticked}
        disabled={ticked}
        onChange={onTick}
        className="size-4 shrink-0 cursor-pointer accent-accent-strong"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <label
          htmlFor={id}
          dir="auto"
          className="cursor-pointer text-sm font-medium"
        >
          {item.title}
        </label>
        {item.detail && (
          <p dir="auto" className="text-xs text-muted">
            {item.detail}
          </p>
        )}
        <p className="text-xs text-muted tabular-nums">{facts}</p>
      </div>
      {(item.optional || item.carried_over) && (
        <span className="flex shrink-0 gap-1.5">
          {item.optional && <Badge>Optional</Badge>}
          {item.carried_over && <Badge tone="warn">Carried over</Badge>}
        </span>
      )}
      {act === null ? (
        <JoinButtons campaignId={item.ref} onOpen={onStart} />
      ) : act.to.startsWith("#") ? (
        <a href={act.to} onClick={onStart} className={action}>
          {act.label}
        </a>
      ) : (
        <Link to={act.to} onClick={onStart} className={action}>
          {act.label}
        </Link>
      )}
    </li>
  );
}
