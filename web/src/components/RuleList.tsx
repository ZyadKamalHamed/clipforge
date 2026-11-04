// A campaign post's rules (SPEC 10.10): each one ClipForge checks, with whether the post keeps it and, when
// it doesn't, what to change; and each one you check yourself as a tick box. On a draft's card a tick saves
// at once; in the Post dialog it goes with the post. Posting waits until every rule is kept. The rules'
// text is only ever shown as text.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { api, key, sentence } from "../api.ts";
import { handTicks, tickedRules, withTicks } from "../lib/drafts.ts";
import type { Draft, DraftsBody, RuleCheck } from "../types.ts";
import { FieldError } from "./Field.tsx";

export function RuleList({ draft, campaign }: { draft: Draft; campaign: string }) {
  const client = useQueryClient();
  const [problem, setProblem] = useState<string | null>(null);
  // The ticks on their way, shown as soon as the box is clicked, until the drafts' answer has them
  const [pending, setPending] = useState<string[] | null>(null);
  useEffect(() => setPending(null), [draft.rules]);
  const rules = pending ? tickedRules(draft.rules, pending) : draft.rules;

  const tick = useMutation({
    mutationFn: (ticks: string[]) =>
      api<unknown>(`/api/posts/${encodeURIComponent(draft.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ rule_ticks: ticks }),
      }),
    onSuccess: (_, ticks) => client.setQueryData<DraftsBody>(key.drafts, (old) => old && withTicks(old, draft.id, ticks)),
    onError: (err) => {
      setPending(null);
      setProblem(sentence(err));
    },
    onSettled: () => void client.invalidateQueries({ queryKey: key.drafts }), // its warnings, worked out again
  });

  return (
    <div className="flex flex-col gap-1">
      <RuleChecks
        rules={rules}
        campaign={campaign}
        disabled={tick.isPending}
        onTick={(ticks) => {
          setProblem(null);
          setPending(ticks);
          tick.mutate(ticks);
        }}
      />
      {problem && <FieldError text={problem} />}
    </div>
  );
}

type ChecksProps = { rules: RuleCheck[]; campaign: string; disabled?: boolean; onTick: (ticks: string[]) => void };

// The list itself: a tick hands on every hand rule ticked once it is made, whole.
export function RuleChecks({ rules, campaign, disabled = false, onTick }: ChecksProps) {
  return (
    <ul aria-label={`${campaign} rules`} className="flex flex-col gap-1 text-xs">
      {rules.map((rule) =>
        rule.kind === "hand" ? (
          <HandRule
            key={rule.key}
            rule={rule}
            disabled={disabled}
            onTick={(on) => onTick(handTicks(rules, rule.key, on))}
          />
        ) : (
          <AutoRule key={rule.key} rule={rule} />
        ),
      )}
    </ul>
  );
}

function AutoRule({ rule }: { rule: RuleCheck }) {
  const Icon = rule.ok ? CircleCheck : CircleAlert;
  return (
    <li className="flex items-start gap-1.5">
      <Icon aria-hidden size={14} className={`mt-px shrink-0 ${rule.ok ? "text-ok" : "text-bad"}`} />
      <span className="flex flex-col">
        <span dir="auto">
          <span className="sr-only">{rule.ok ? "Kept: " : "Not kept: "}</span>
          {rule.label}
        </span>
        {!rule.ok && rule.sentence && <span className="text-bad">{rule.sentence}</span>}
      </span>
    </li>
  );
}

function HandRule({ rule, disabled, onTick }: { rule: RuleCheck; disabled: boolean; onTick: (on: boolean) => void }) {
  return (
    <li className="flex flex-col">
      <label className="flex cursor-pointer items-start gap-1.5">
        <input
          type="checkbox"
          checked={rule.ok}
          disabled={disabled}
          onChange={(e) => onTick(e.target.checked)}
          className="mt-px size-3.5 shrink-0 cursor-pointer accent-accent-strong"
        />
        <span dir="auto">{rule.label}</span>
      </label>
      {!rule.ok && rule.sentence && <span className="pl-5 text-warn">{rule.sentence}</span>}
    </li>
  );
}
