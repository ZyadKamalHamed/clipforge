// One submission on a campaign's page (SPEC 10.10 and 14.11): the post it is of, where it stands with the
// marketplace, its views (Enter views when a count is due by hand) and what they should earn, and the moves
// it can make: Rejected asks for the reason, Paid for the amount. On Today a post still to submit also has
// the one button that copies its link and opens the marketplace. Titles and reasons are only ever text.

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import { PLATFORMS } from "../lib/accounts.ts";
import {
  AMOUNT_NEEDED,
  amountValue,
  count,
  dayLabel,
  estimateText,
  money,
  moveLabel,
  NAMES,
  VIEWS_NEEDED,
  viewsValue,
} from "../lib/campaigns.ts";
import type { Submission, SubmissionStatus } from "../types.ts";
import { Button } from "./Button.tsx";
import { FieldError, TextField } from "./Field.tsx";

const TONES: Record<SubmissionStatus, string> = {
  to_submit: "border-line text-muted",
  submitted: "border-line text-fg",
  approved: "border-ok text-ok",
  rejected: "border-bad text-bad",
  paid: "border-ok text-ok",
};

type Asking = "views" | "rejected" | "paid";

type Props = { submission: Submission; currency: string; submit?: { label: string; run: () => void } };

export function SubmissionRow({ submission: s, currency, submit }: Props) {
  const client = useQueryClient();
  const [asking, setAsking] = useState<Asking | null>(null);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const title = s.post?.clip_title || "A post";

  function ask(next: Asking | null) {
    setAsking(next);
    setText("");
    setProblem(null);
  }

  async function send(path: string, body: object) {
    setBusy(true);
    setProblem(null);
    try {
      await api(path, { method: path.startsWith("/api/posts") ? "POST" : "PATCH", body: JSON.stringify(body) });
      ask(null);
      void client.invalidateQueries({ queryKey: key.campaign(s.campaign_id) });
      void client.invalidateQueries({ queryKey: key.campaigns });
      void client.invalidateQueries({ queryKey: key.today });
    } catch (err) {
      setProblem(sentence(err));
    } finally {
      setBusy(false);
    }
  }

  const move = (status: SubmissionStatus, more: object = {}) =>
    send(`/api/submissions/${encodeURIComponent(s.id)}`, { status, ...more });

  function answer() {
    if (asking === "views") {
      const views = viewsValue(text);
      if (views === null) return setProblem(VIEWS_NEEDED);
      return void send(`/api/posts/${encodeURIComponent(s.post_id)}/views`, { views });
    }
    if (asking === "paid") {
      const amount = amountValue(text);
      if (typeof amount !== "number" || amount < 0) return setProblem(AMOUNT_NEEDED);
      return void move("paid", { paid_amount: amount });
    }
    if (asking === "rejected") void move("rejected", { reason: text.trim() || null });
  }

  const where = s.post
    ? [s.post.account_name, PLATFORMS[s.post.platform]].filter(Boolean).join(" on ")
    : "Its post was deleted";
  const asked = {
    views: { label: "Views", action: "Save views", inputMode: "numeric" as const, maxLength: undefined },
    rejected: { label: "Reason", action: "Mark rejected", inputMode: undefined, maxLength: 300 },
    paid: { label: "Amount paid", action: "Mark paid", inputMode: "decimal" as const, maxLength: undefined },
  };

  return (
    <article aria-label={title} className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate text-sm font-semibold" dir="auto">
            {title}
          </h3>
          <p className="text-xs text-muted">
            {where}
            {s.post?.url && (
              <>
                {" · "}
                <a
                  href={s.post.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-fg underline underline-offset-2"
                >
                  Open the post
                </a>
              </>
            )}
          </p>
        </div>
        <span className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONES[s.status]}`}>
          {NAMES[s.status]}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-faint">Views</dt>
          <dd className="tabular-nums">
            {s.views === null ? "None entered" : `${count(s.views)} views`}
            {s.views_source === "insights" && <span className="text-xs text-faint"> from insights</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-faint">Estimate</dt>
          <dd className="tabular-nums">{estimateText(s, currency)}</dd>
        </div>
        {s.status === "paid" && s.paid_amount !== null && (
          <div>
            <dt className="text-xs text-faint">Payment</dt>
            <dd className="tabular-nums">
              Paid {money(s.paid_amount, currency)} on {dayLabel(s.paid_on)}
            </dd>
          </div>
        )}
        {s.status === "rejected" && (
          <div className="col-span-full">
            <dt className="text-xs text-faint">Why it was rejected</dt>
            <dd dir="auto">{s.reason ?? "No reason entered."}</dd>
          </div>
        )}
      </dl>
      {s.views_due && <p className="text-xs text-warn">Its {s.views_due} view count is due. Enter it from the post.</p>}

      {asking ? (
        <form
          className="flex flex-wrap items-end gap-2"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            answer();
          }}
        >
          <div className="min-w-48 flex-1">
            <TextField
              label={asked[asking].label}
              value={text}
              onChange={setText}
              inputMode={asked[asking].inputMode}
              maxLength={asked[asking].maxLength}
              error={problem}
            />
          </div>
          <Button variant="primary" type="submit" disabled={busy}>
            {asked[asking].action}
          </Button>
          <Button variant="ghost" onClick={() => ask(null)} disabled={busy}>
            Cancel
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          {submit && s.status === "to_submit" && (
            <Button variant="primary" onClick={submit.run} disabled={busy}>
              {submit.label}
            </Button>
          )}
          {s.post && (
            <Button variant="secondary" onClick={() => ask("views")} disabled={busy}>
              Enter views
            </Button>
          )}
          {s.moves.map((to) => (
            <Button
              key={to}
              variant={to === "rejected" ? "ghost" : "secondary"}
              disabled={busy}
              onClick={() => (to === "rejected" || to === "paid" ? ask(to) : void move(to))}
            >
              {moveLabel(s.status, to)}
            </Button>
          ))}
        </div>
      )}
      {problem && !asking && <FieldError text={problem} />}
    </article>
  );
}
