// A campaign's page (SPEC 14.11 and 10.10): its rules and pay, Edit, its campaign and submit pages opened in
// your browser by macOS (never in ClipForge's window), Mark ended or Delete, what it waits on today as its
// triangle lists it, the clipping channels linked to it, and its submissions with a filter by where they
// stand. Its text is only ever shown as text.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, SearchX } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, api, key, sentence } from "../api.ts";
import { Button, buttonClass } from "../components/Button.tsx";
import { CampaignForm } from "../components/CampaignForm.tsx";
import { Choice } from "../components/Choice.tsx";
import { useConfirm } from "../components/Confirm.tsx";
import { Empty } from "../components/Empty.tsx";
import { SubmissionRow } from "../components/SubmissionRow.tsx";
import { useToast } from "../components/Toasts.tsx";
import { NeedsList } from "../components/Triangle.tsx";
import { PLATFORMS } from "../lib/accounts.ts";
import {
  BANNED,
  count,
  dayLabel,
  money,
  NAMES,
  progressCounts,
} from "../lib/campaigns.ts";
import { hashtagText } from "../lib/plan.ts";
import { campaignQuery } from "../state.ts";
import type {
  Campaign as CampaignRow,
  Submission,
  SubmissionStatus,
} from "../types.ts";

type Show = "all" | SubmissionStatus;

const SHOW: { value: Show; label: string }[] = [
  { value: "all", label: "All" },
  ...(Object.keys(NAMES) as SubmissionStatus[]).map((status) => ({
    value: status,
    label: NAMES[status],
  })),
];

const OPENED = {
  page: "Opened the campaign page in your browser.",
  submit: "Opened the submit page in your browser.",
};

export function Campaign() {
  const { id = "" } = useParams();
  const found = useQuery(campaignQuery(id));
  if (found.error instanceof ApiError && found.error.status === 404) {
    return (
      <Empty
        icon={SearchX}
        level={1}
        title="This campaign isn't here."
        action={
          <Link to="/campaigns" className={buttonClass("primary")}>
            Go to Campaigns
          </Link>
        }
      >
        It may have been deleted, or the link is out of date.
      </Empty>
    );
  }

  let body;
  if (found.isPending)
    body = <p className="text-sm text-muted">Loading the campaign.</p>;
  else if (found.isError)
    body = <p className="text-sm text-bad">{sentence(found.error)}</p>;
  else
    body = (
      <Page
        campaign={found.data.campaign}
        submissions={found.data.submissions}
      />
    );

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <Link
        to="/campaigns"
        className="self-start text-sm text-fg underline underline-offset-2"
      >
        All campaigns
      </Link>
      {body}
    </div>
  );
}

function Page({
  campaign: c,
  submissions,
}: {
  campaign: CampaignRow;
  submissions: Submission[];
}) {
  const client = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [show, setShow] = useState<Show>("all");
  const [busy, setBusy] = useState(false);

  function refresh() {
    void client.invalidateQueries({ queryKey: key.campaign(c.id) });
    void client.invalidateQueries({ queryKey: key.campaigns });
  }

  async function open(kind: "page" | "submit") {
    const link = c.links[kind];
    if (!link) return;
    try {
      await api(`/api/links/${encodeURIComponent(link)}/open`, {
        method: "POST",
      });
      toast({ text: OPENED[kind], kind: "ok" });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  async function setStatus(status: "active" | "ended") {
    setBusy(true);
    try {
      await api(`/api/campaigns/${encodeURIComponent(c.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      refresh();
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const yes = await ask({
      title: `Delete ${c.name}?`,
      description:
        "Its clipping channels and projects stay, unlinked from it. This can't be undone.",
      action: "Delete",
      danger: true,
    });
    if (!yes) return;
    setBusy(true);
    try {
      await api(`/api/campaigns/${encodeURIComponent(c.id)}`, {
        method: "DELETE",
      });
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
      setBusy(false);
      return;
    }
    toast({ text: `${c.name} deleted`, kind: "ok" });
    client.removeQueries({ queryKey: key.campaign(c.id) });
    void client.invalidateQueries({ queryKey: key.campaigns });
    void navigate("/campaigns");
  }

  const m = c.money;
  const shown =
    show === "all" ? submissions : submissions.filter((s) => s.status === show);
  const facts = [
    c.marketplace,
    c.rate_per_1000 === null
      ? "No rate yet"
      : `${money(c.rate_per_1000, c.currency)} per 1,000 views`,
    c.ends_on ? `Ends ${dayLabel(c.ends_on)}` : null,
    c.budget_left === null
      ? null
      : `${money(c.budget_left, c.currency)} budget left${c.budget_entered_on ? ` (entered ${dayLabel(c.budget_entered_on)})` : ""}`,
  ].filter(Boolean);

  return (
    <>
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold" dir="auto">
            {c.name}
          </h1>
          <p className="text-sm text-muted">{facts.join(" · ")}</p>
        </div>
        {c.ended && (
          <p className="rounded-control border border-warn/40 bg-panel-2 px-3 py-2 text-sm text-warn">
            {c.ended}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            onClick={() => setEditing(true)}
            disabled={busy}
          >
            Edit
          </Button>
          {c.links.page && (
            <Button variant="secondary" onClick={() => void open("page")}>
              <ExternalLink aria-hidden size={14} />
              Open campaign page
            </Button>
          )}
          {c.links.submit && (
            <Button variant="secondary" onClick={() => void open("submit")}>
              <ExternalLink aria-hidden size={14} />
              Open submit page
            </Button>
          )}
          {c.status === "active" ? (
            <Button
              variant="ghost"
              onClick={() => void setStatus("ended")}
              disabled={busy}
            >
              Mark ended
            </Button>
          ) : (
            <Button
              variant="ghost"
              onClick={() => void setStatus("active")}
              disabled={busy}
            >
              Mark active
            </Button>
          )}
          <Button variant="ghost" onClick={() => void remove()} disabled={busy}>
            Delete
          </Button>
        </div>
      </div>

      <NeedsList campaign={c} />

      <div className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4">
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Stat label="Views">
            {m.views === null ? "None yet" : count(m.views)}
          </Stat>
          <Stat label="Estimated">
            {m.estimated === null ? "No rate" : money(m.estimated, c.currency)}
          </Stat>
          <Stat label="Pending">
            {m.pending === null ? "No rate" : money(m.pending, c.currency)}
          </Stat>
          <Stat label="Paid">{money(m.paid, c.currency)}</Stat>
        </dl>
        <p className="text-xs text-faint">
          {progressCounts(c).sentence}. Estimates from your entries.
        </p>
      </div>

      <Rules campaign={c} />

      <section id="submissions" className="flex scroll-mt-4 flex-col gap-3">
        <h2 className="text-base font-semibold">Submissions</h2>
        {submissions.length === 0 ? (
          <p className="text-sm text-muted">
            No submissions yet. Each post of a clip made for this campaign gets
            one here once it's live.
          </p>
        ) : (
          <>
            <Choice
              legend="Show"
              name="show"
              value={show}
              options={SHOW}
              onChange={setShow}
            />
            {shown.length === 0 ? (
              <p className="text-sm text-muted">
                No submissions are {NAMES[show as SubmissionStatus]}.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {shown.map((s) => (
                  <SubmissionRow
                    key={s.id}
                    submission={s}
                    currency={c.currency}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {editing && (
        <CampaignForm campaign={c} onClose={() => setEditing(false)} />
      )}
    </>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-faint">{label}</dt>
      <dd className="text-base font-medium tabular-nums">{children}</dd>
    </div>
  );
}

function Rules({ campaign: c }: { campaign: CampaignRow }) {
  const banned = BANNED.filter((b) => c.banned.includes(b.value)).map(
    (b) => b.label,
  );
  const lengths =
    c.min_s !== null || c.max_s !== null
      ? [
          c.min_s !== null && `at least ${c.min_s} s`,
          c.max_s !== null && `at most ${c.max_s} s`,
        ]
          .filter(Boolean)
          .join(", ")
      : null;
  const footage = [
    ...c.footage.channels.map((ch) => ch.title || ch.url),
    ...(c.footage.videos.length
      ? [
          `${c.footage.videos.length} named video${c.footage.videos.length === 1 ? "" : "s"}`,
        ]
      : []),
    ...(c.footage.files ? ["files and Drive links you add"] : []),
  ];
  const rows: [string, ReactNode][] = [
    ["Platforms", c.platforms.map((p) => PLATFORMS[p]).join(" and ")],
    [
      "Clipping channels",
      c.channels.length
        ? c.channels.map((ch) => ch.name).join(", ")
        : "None linked yet",
    ],
    ["Footage", footage.length ? footage.join(", ") : "Any"],
    [
      "Hashtags",
      c.required.hashtags.length ? hashtagText(c.required.hashtags) : null,
    ],
    [
      "Mentions",
      c.required.mentions.length ? c.required.mentions.join(" ") : null,
    ],
    [
      "Caption text",
      c.required.text.length ? c.required.text.join(" / ") : null,
    ],
    ["Clips can't use", banned.length ? banned.join(", ") : null],
    ["Clip length", lengths],
    ["Minimum views", c.min_views === null ? null : count(c.min_views)],
    [
      "Most paid per clip",
      c.max_payout === null ? null : money(c.max_payout, c.currency),
    ],
  ];
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">Rules</h2>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
        {rows
          .filter(([, value]) => value !== null)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted">{label}</dt>
              <dd dir="auto">{value}</dd>
            </div>
          ))}
      </dl>
      {c.hand_rules.length > 0 && (
        <div className="flex flex-col gap-1 text-sm">
          <p className="text-muted">Rules you check by hand</p>
          <ul className="list-disc pl-5">
            {c.hand_rules.map((rule) => (
              <li key={rule} dir="auto">
                {rule}
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.notes && (
        <p className="text-sm whitespace-pre-wrap text-muted" dir="auto">
          {c.notes}
        </p>
      )}
      {c.rules_text && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">
            The rules as pasted
          </summary>
          <p className="mt-2 whitespace-pre-wrap" dir="auto">
            {c.rules_text}
          </p>
        </details>
      )}
    </section>
  );
}
