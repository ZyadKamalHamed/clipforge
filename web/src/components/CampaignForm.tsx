// New campaign and Edit (SPEC 10.10 and 14.11): paste the campaign's rules and Read the rules, and Claude
// fills what it can of the fields below, each marked "Check this" until you edit it; with anything filled,
// Save waits for "I've checked the filled fields". A new campaign can start filled, from a brief (its fields
// to check, and Save makes the brief this campaign) or from a paste box (the rules and the marketplace).
// Every refusal is the engine's sentence beside its field, and nothing is saved until Save. The pasted rules
// are only ever a field's value, shown as text.

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";

import { api, ApiError, key, sentence } from "../api.ts";
import { PLATFORMS } from "../lib/accounts.ts";
import {
  BANNED,
  blankFields,
  fieldsOf,
  filledMark,
  formBody,
  formState,
  type FormState,
  TEXT_FIELDS,
  type TextKey,
  TICK_FIRST,
} from "../lib/campaigns.ts";
import type {
  Banned,
  Campaign,
  CampaignFields,
  ParseResult,
  Platform,
} from "../types.ts";
import { Button } from "./Button.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

const RULES_MAX = 20_000;
const CHECKBOX = "size-4 accent-accent-strong";

// Where a refusal from the engine is shown, by the field it names: its own field, else under the rules or the form.
const OWN = new Set<string>([
  ...TEXT_FIELDS,
  "platforms",
  "banned",
  "footage.files",
]);
function placeOf(err: unknown): string {
  const field = err instanceof ApiError ? (err.field ?? "") : "";
  if (OWN.has(field)) return field;
  if (field === "text" || field === "rules_text") return "rules";
  if (field.startsWith("footage")) return "footage_links";
  return "form";
}

// Where a new campaign starts: the fields filled and those to check, the brief it is made from and the
// rules to read (else the fields' own), and what Claude said of them.
export type FormStart = {
  fields: Partial<CampaignFields>;
  filled: string[];
  briefId?: string;
  rules?: string;
  note?: string | null;
};

type Props = {
  campaign: Campaign | null;
  start?: FormStart;
  onClose: () => void;
};

export function CampaignForm({ campaign, start, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const [rules, setRules] = useState(
    campaign?.rules_text ?? start?.rules ?? start?.fields.rules_text ?? "",
  );
  const [form, setForm] = useState<FormState>(() =>
    formState(
      campaign ? fieldsOf(campaign) : { ...blankFields(), ...start?.fields },
    ),
  );
  const [filled, setFilled] = useState<string[]>(
    campaign ? [] : (start?.filled ?? []),
  );
  const [touched, setTouched] = useState<Set<string>>(() => new Set());
  const [checked, setChecked] = useState(false);
  const [note, setNote] = useState<string | null>(start?.note ?? null);
  const briefId = campaign ? undefined : start?.briefId;
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);

  function edited(field: string) {
    setTouched((old) => (old.has(field) ? old : new Set(old).add(field)));
    setProblems((old) => {
      if (!(field in old)) return old;
      const rest = { ...old };
      delete rest[field];
      return rest;
    });
  }

  function setText(field: TextKey, value: string) {
    setForm((old) => ({ ...old, text: { ...old.text, [field]: value } }));
    edited(field);
  }

  function pick<T extends string>(list: T[], value: T, on: boolean): T[] {
    return on
      ? [...list.filter((v) => v !== value), value]
      : list.filter((v) => v !== value);
  }

  async function read() {
    if (reading || busy) return;
    setReading(true);
    setProblems({});
    try {
      const result = await api<ParseResult>("/api/campaigns/parse", {
        method: "POST",
        body: JSON.stringify({ text: rules }),
      });
      // A marketplace already in the form stays when Claude found none in the rules.
      const kept = result.fields.marketplace
        ? result.fields
        : { ...result.fields, marketplace: form.text.marketplace };
      setForm(formState(kept));
      setRules(result.fields.rules_text);
      setFilled(result.filled);
      setTouched(new Set());
      setChecked(false);
      setNote(result.sentence);
    } catch (err) {
      setProblems({
        [placeOf(err) === "form" ? "form" : "rules"]: sentence(err),
      });
    } finally {
      setReading(false);
    }
  }

  async function save(event?: FormEvent) {
    event?.preventDefault();
    if (busy || reading) return;
    if (filled.length > 0 && !checked) {
      setProblems({ tick: TICK_FIRST });
      return;
    }
    setBusy(true);
    setProblems({});
    const body = JSON.stringify(
      briefId
        ? { ...formBody(form, rules), brief_id: briefId }
        : formBody(form, rules),
    );
    let saved: Campaign;
    try {
      saved = campaign
        ? (
            await api<{ campaign: Campaign }>(
              `/api/campaigns/${encodeURIComponent(campaign.id)}`,
              { method: "PATCH", body },
            )
          ).campaign
        : (
            await api<{ campaign: Campaign }>("/api/campaigns", {
              method: "POST",
              body,
            })
          ).campaign;
    } catch (err) {
      setProblems({ [placeOf(err)]: sentence(err) });
      setBusy(false);
      return;
    }
    void client.invalidateQueries({ queryKey: key.campaigns });
    void client.invalidateQueries({ queryKey: key.campaign(saved.id) });
    if (briefId) void client.invalidateQueries({ queryKey: key.briefs }); // the brief is this campaign now
    setBusy(false);
    toast({ text: `${saved.name} saved`, kind: "ok" });
    onClose();
  }

  const mark = (field: string) => filledMark(field, filled, touched);
  const text = (
    field: TextKey,
    label: string,
    more: Partial<Parameters<typeof TextField>[0]> = {},
  ) => (
    <TextField
      label={label}
      value={form.text[field]}
      onChange={(value) => setText(field, value)}
      mark={mark(field)}
      error={problems[field]}
      {...more}
    />
  );

  return (
    <Dialog
      title={campaign ? `Edit ${campaign.name}` : "New campaign"}
      description="Paste the campaign's rules and read them, or fill the fields yourself. Check each field Claude filled before you save."
      open
      wide
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <form onSubmit={save} noValidate className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <TextField
            label="Campaign rules"
            value={rules}
            onChange={setRules}
            multiline
            maxLength={RULES_MAX}
            hint="Paste the brief or rules from the campaign's page. Claude reads them; nothing is saved yet."
            error={problems.rules}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void read()} disabled={reading || busy}>
              {reading ? "Reading the rules." : "Read the rules"}
            </Button>
            {note && <p className="text-sm text-warn">{note}</p>}
          </div>
        </div>

        <Section title="The campaign">
          <div className="grid gap-3 sm:grid-cols-2">
            {text("name", "Name", { maxLength: 80 })}
            {text("marketplace", "Marketplace", {
              maxLength: 40,
              placeholder: "Whop",
            })}
            {text("campaign_url", "Campaign page link", {
              placeholder: "https://",
            })}
            {text("submit_url", "Submit page link", {
              placeholder: "https://",
            })}
          </div>
        </Section>

        <Section title="Pay">
          <div className="grid gap-3 sm:grid-cols-3">
            {text("currency", "Currency", {
              maxLength: 3,
              hint: "Its three letter code, like USD.",
            })}
            {text("rate_per_1000", "Rate per 1,000 views", {
              inputMode: "decimal",
            })}
            {text("min_views", "Minimum views", {
              inputMode: "numeric",
              hint: "A clip under this earns nothing.",
            })}
            {text("max_payout", "Most paid per clip", { inputMode: "decimal" })}
            {text("budget_left", "Budget left", { inputMode: "decimal" })}
            {text("ends_on", "End date", { date: true })}
          </div>
        </Section>

        <Section title="Platforms and footage">
          <Checks
            legend="Platforms"
            mark={mark("platforms")}
            error={problems.platforms}
          >
            {(Object.keys(PLATFORMS) as Platform[]).map((platform) => (
              <Check
                key={platform}
                label={PLATFORMS[platform]}
                checked={form.platforms.includes(platform)}
                onChange={(on) => {
                  setForm((old) => ({
                    ...old,
                    platforms: pick(old.platforms, platform, on),
                  }));
                  edited("platforms");
                }}
              />
            ))}
          </Checks>
          {text("footage_links", "Footage channels", {
            multiline: true,
            hint: "One YouTube channel link a line. Only clips of these count.",
            placeholder: "https://www.youtube.com/@name",
          })}
          {text("footage.videos", "Footage videos", {
            multiline: true,
            hint: "One YouTube video link or id a line.",
          })}
          <Checks
            legend="Files"
            legendHidden
            mark={mark("footage.files")}
            error={problems["footage.files"]}
          >
            <Check
              label="Footage from files and Drive links you add"
              checked={form.files}
              onChange={(on) => {
                setForm((old) => ({ ...old, files: on }));
                edited("footage.files");
              }}
            />
          </Checks>
        </Section>

        <Section title="Captions">
          {text("required.hashtags", "Hashtags every caption needs", {
            placeholder: "#tag",
          })}
          {text("required.mentions", "Mentions every caption needs", {
            placeholder: "@name",
          })}
          {text("required.text", "Text every caption needs", {
            multiline: true,
            hint: "One line each.",
          })}
        </Section>

        <Section title="Rules">
          <Checks
            legend="Clips can't use"
            mark={mark("banned")}
            error={problems.banned}
          >
            {BANNED.map((option) => (
              <Check
                key={option.value}
                label={option.label}
                checked={form.banned.includes(option.value)}
                onChange={(on) => {
                  setForm((old) => ({
                    ...old,
                    banned: pick<Banned>(old.banned, option.value, on),
                  }));
                  edited("banned");
                }}
              />
            ))}
          </Checks>
          {text("hand_rules", "Rules you check by hand", {
            multiline: true,
            hint: "One rule a line.",
          })}
          <div className="grid gap-3 sm:grid-cols-2">
            {text("min_s", "Shortest clip in seconds", {
              inputMode: "decimal",
            })}
            {text("max_s", "Longest clip in seconds", { inputMode: "decimal" })}
          </div>
          {text("notes", "Notes", { multiline: true, maxLength: 4000 })}
        </Section>

        {problems.form && <FieldError text={problems.form} />}
        <DialogFooter>
          {filled.length > 0 && (
            <label className="mr-auto flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className={CHECKBOX}
                checked={checked}
                onChange={(e) => {
                  setChecked(e.target.checked);
                  if (e.target.checked) edited("tick");
                }}
              />
              I've checked the filled fields
            </label>
          )}
          {problems.tick && <FieldError text={problems.tick} />}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={busy || reading}>
            {busy ? "Saving." : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

type ChecksProps = {
  legend: string;
  legendHidden?: boolean;
  mark: string | null;
  error?: string;
  children: ReactNode;
};

function Checks({ legend, legendHidden, mark, error, children }: ChecksProps) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      <legend
        className={
          legendHidden && !mark
            ? "sr-only"
            : "mb-1 flex items-baseline gap-2 text-xs font-medium text-muted"
        }
      >
        <span className={legendHidden ? "sr-only" : undefined}>{legend}</span>
        {mark && <span className="text-xs font-medium text-warn">{mark}</span>}
      </legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">{children}</div>
      {error && <FieldError text={error} />}
    </fieldset>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <input
        type="checkbox"
        className={CHECKBOX}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
