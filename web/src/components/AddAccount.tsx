// Add account (SPEC 11): four ways in, each with what happens said first. TikTok and Instagram in Chrome open
// a Chrome window on the platform's own login page; Instagram through Facebook or with Instagram login take
// a key pasted from Meta. ClipForge never asks for a password, and a pasted key is never shown again: its
// field is a password field, emptied after every send.

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, api, key, sentence } from "../api.ts";
import { parseList } from "../lib/accounts.ts";
import type { Account, Candidate } from "../types.ts";
import { Button } from "./Button.tsx";
import { Choice } from "./Choice.tsx";
import { Dialog, DialogFooter } from "./Dialog.tsx";
import { FieldError, TextField } from "./Field.tsx";
import { useToast } from "./Toasts.tsx";

export type Route = "tiktok" | "facebook" | "instagram" | "chrome";

const NEVER_SEES = "ClipForge never sees your password.";

const ROUTES: { value: Route; label: string; hint?: string }[] = [
  { value: "tiktok", label: "TikTok in Chrome" },
  { value: "facebook", label: "Instagram through Facebook", hint: "Recommended" },
  { value: "instagram", label: "Instagram with Instagram login" },
  { value: "chrome", label: "Instagram in Chrome", hint: "Experimental" },
];

const STEPS: Record<Route, string[]> = {
  tiktok: [
    "Press Log in with Chrome. A Chrome window opens on TikTok's own login page.",
    `Log in there as you always do. ${NEVER_SEES}`,
    "When TikTok Studio opens, ClipForge reads the handle and closes the window. The login stays on this Mac, for this account alone.",
  ],
  facebook: [
    "In a Business type app at developers.facebook.com, add the Instagram use case API setup with Facebook login. Each Instagram account must be a Creator or Business account linked to a Facebook Page.",
    "In the Graph API Explorer, ask for instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, business_management and instagram_manage_insights, then generate a token.",
    "Press Extend Access Token in the Access Token Debugger and paste the long one here, or paste the short one with your app's ID and secret.",
    `Pick the accounts to add. Any login happens on Meta's own pages. ${NEVER_SEES}`,
  ],
  instagram: [
    "In a Meta app with the Instagram use case API setup with Instagram login, add the account as an Instagram Tester and accept the invite in Instagram.",
    "Press Generate token beside the account. The token lasts 60 days.",
    "Paste it here. Instagram fetches each video from the public address in Settings, so set that first.",
    `Any login happens on Meta's own pages. ${NEVER_SEES}`,
  ],
  chrome: [
    "Type the account's handle, then press Log in with Chrome. A Chrome window opens on Instagram's own login page.",
    `Log in there as you always do. ${NEVER_SEES}`,
    "This way is experimental: Instagram changes its pages often, and Meta's API is steadier for Creator and Business accounts.",
  ],
};

type Draft = { label: string; niche_tags: string; handle: string; token: string; app_id: string; app_secret: string };
type Where = keyof Draft | "form";
type Problems = Partial<Record<Where, string>>;
type Found = { ticket: string; candidates: Candidate[] };

const BLANK: Draft = { label: "", niche_tags: "", handle: "", token: "", app_id: "", app_secret: "" };

function whereFor(field: string | null): Where {
  if (field?.startsWith("niche_tags")) return "niche_tags";
  const known: Where[] = ["label", "handle", "token", "app_id", "app_secret"];
  return known.find((w) => w === field) ?? "form";
}

type Props = { start: Route; onClose: () => void };

export function AddAccount({ start, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const [route, setRoute] = useState<Route>(start);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [problems, setProblems] = useState<Problems>({});
  const [found, setFound] = useState<Found | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState(0);
  const form = useRef<HTMLFormElement>(null);

  // After a refusal, the focus goes to the first thing to fix.
  useEffect(() => {
    if (refused) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [refused]);

  function edit(field: keyof Draft, value: string) {
    setDraft((d) => ({ ...d, [field]: value }));
    setProblems((p) => ({ ...p, [field]: undefined, form: undefined }));
  }

  function refuse(err: unknown) {
    setProblems({ [whereFor(err instanceof ApiError ? err.field : null)]: sentence(err) });
    setRefused((n) => n + 1);
  }

  async function added(text: string, subject?: string) {
    await client.invalidateQueries({ queryKey: key.accounts });
    void client.invalidateQueries({ queryKey: key.activeJobs });
    toast({ text, kind: "ok", subject });
    onClose();
  }

  // Sends one request, the pasted key and secret emptied whatever the answer.
  async function send<T>(path: string, body: object): Promise<T | null> {
    setDraft((d) => ({ ...d, token: "", app_secret: "" }));
    setProblems({});
    setBusy(true);
    try {
      return await api<T>(path, { method: "POST", body: JSON.stringify(body) });
    } catch (err) {
      refuse(err);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const named = { label: draft.label.trim(), niche_tags: parseList(draft.niche_tags) };
    if (route === "tiktok" || route === "chrome") {
      const body =
        route === "tiktok"
          ? { platform: "tiktok", method: "browser", ...named }
          : { platform: "instagram", method: "browser", handle: draft.handle.trim(), ...named };
      const made = await send<{ account: Account }>("/api/accounts", body);
      const where = route === "tiktok" ? "TikTok" : "Instagram";
      if (made) await added(`Log in to ${where} in the Chrome window that opens.`);
    } else if (route === "instagram") {
      const body = { platform: "instagram", method: "api", token: draft.token.trim(), ...named };
      const made = await send<{ account: Account }>("/api/accounts", body);
      if (made) await added("Added", made.account.handle ? `@${made.account.handle}` : undefined);
    } else if (!found) {
      const app = draft.app_id.trim() || draft.app_secret.trim();
      const body = { token: draft.token.trim(), ...(app ? { app_id: draft.app_id.trim(), app_secret: draft.app_secret.trim() } : {}) };
      const list = await send<Found>("/api/accounts/facebook/pages", body);
      if (list) {
        setFound(list);
        setPicked(new Set(list.candidates.filter((c) => !c.account_id).map((c) => c.ig_user_id)));
      }
    } else {
      const made = await send<{ accounts: Account[] }>("/api/accounts/facebook/link", {
        ticket: found.ticket,
        ig_user_ids: [...picked],
      });
      if (made) {
        const n = made.accounts.length;
        await added(n === 1 ? "Added 1 account" : `Added ${n} accounts`);
      } else {
        setFound(null); // a ticket links once, and an old one is refused, so the key is pasted again
      }
    }
  }

  function pick(id: string, on: boolean) {
    setPicked((now) => {
      const next = new Set(now);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const n = picked.size;
  let submitText = "Log in with Chrome";
  if (route === "instagram") submitText = "Add account";
  else if (route === "facebook") submitText = found ? `Add ${n} account${n === 1 ? "" : "s"}` : "Find accounts";

  return (
    <Dialog
      title="Add account"
      open
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <form ref={form} onSubmit={(e) => void submit(e)} noValidate className="flex flex-col gap-4">
        {!found && (
          <Choice
            legend="Account"
            name="route"
            value={route}
            options={ROUTES}
            onChange={(next) => {
              setRoute(next);
              setProblems({});
            }}
          />
        )}
        <ol aria-label="What happens" className="flex list-decimal flex-col gap-1.5 pl-5 text-sm text-muted">
          {STEPS[route].map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {route === "chrome" && (
          <TextField
            label="Handle"
            hint="As it shows on the profile, like clips_daily."
            value={draft.handle}
            onChange={(v) => edit("handle", v)}
            error={problems.handle}
            maxLength={40}
          />
        )}
        {(route === "instagram" || route === "facebook") && !found && (
          <TextField
            label="Access token"
            value={draft.token}
            onChange={(v) => edit("token", v)}
            error={problems.token}
            maxLength={4096}
            secret
          />
        )}
        {route === "facebook" && !found && (
          <>
            <TextField label="App ID" hint="Optional, with the app secret." value={draft.app_id} onChange={(v) => edit("app_id", v)} error={problems.app_id} maxLength={64} />
            <TextField
              label="App secret"
              hint="Optional. With both, ClipForge swaps the key for one that lasts about 60 days and never keeps the secret."
              value={draft.app_secret}
              onChange={(v) => edit("app_secret", v)}
              error={problems.app_secret}
              maxLength={256}
              secret
            />
          </>
        )}
        {route === "facebook" && found && <Candidates found={found} picked={picked} onPick={pick} />}
        {route !== "facebook" && (
          <>
            <TextField
              label="Label"
              hint="Optional. Your own name for it, like Gaming."
              value={draft.label}
              onChange={(v) => edit("label", v)}
              error={problems.label}
              maxLength={60}
            />
            <TextField
              label="Niche tags"
              hint="Optional. What it posts, with commas, like gaming, streamers."
              value={draft.niche_tags}
              onChange={(v) => edit("niche_tags", v)}
              error={problems.niche_tags}
            />
          </>
        )}
        {problems.form && <FieldError text={problems.form} />}
        <DialogFooter>
          {found && (
            <Button onClick={() => setFound(null)} disabled={busy}>
              Back
            </Button>
          )}
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy || Boolean(found && n === 0)}>
            {submitText}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

function Candidates({ found, picked, onPick }: { found: Found; picked: Set<string>; onPick: (id: string, on: boolean) => void }) {
  if (found.candidates.length === 0)
    return (
      <p className="text-sm">
        No Instagram accounts are linked to the Pages this key can see. Link one to a Page in Instagram's settings, then
        paste the key again.
      </p>
    );
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-xs font-medium text-muted">Accounts found</legend>
      {found.candidates.map((c) => (
        <label key={c.ig_user_id} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={picked.has(c.ig_user_id)}
            onChange={(e) => onPick(c.ig_user_id, e.target.checked)}
            className="mt-0.5 accent-accent-strong"
          />
          <span className="flex flex-col">
            <bdi className="font-medium">@{c.username}</bdi>
            <span className="text-xs text-muted">
              On <bdi>{c.page_name}</bdi>
            </span>
            {c.account_id && <span className="text-xs text-faint">Already added. Linking it again gives it the new key.</span>}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
